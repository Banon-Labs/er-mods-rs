//! The checks a runtime-compiled pixel shader passes before any pipeline is created from it
//! (design 7.6). Nothing here touches D3D12, so all of it is tested on the host.
//!
//! # Why every blob is checked
//!
//! Native D3D12 refuses a pipeline whose pixel shader does not fit with a bare `E_INVALIDARG`, and
//! vkd3d-proton accepts unsigned or mislinked DXIL that native refuses, so a blob that is wrong in
//! one of those ways passes every Proton run and fails on Windows. The editor compiles what the
//! user wrote, so before a variant is created the worker checks the blob against the game pixel
//! shader it stands in for, whose bytes the port record keeps in memory ([`PsInterface::read`]):
//!
//! | Rule | Name | The blob passes when |
//! |---|---|---|
//! | 1 | container | it is a `DXBC` container of exactly its own length and at most [`MAX_BLOB_BYTES`], its parts lie inside it and no part code repeats, and its `DXIL` program is a `ps_6_0` pixel shader |
//! | 2 | signed | its digest is set and is DXC's retail hash of it ([`crate::dxbc::retail_hash`]) |
//! | 3 | linkage | `ISG1` links like the original's element for element ([`SigElement::links_like`]), and `PSV0` interpolates every input as the original's does |
//! | 4 | outputs | `OSG1` likewise, so the pipeline's render targets still fit |
//! | 5 | resources | every register range `PSV0` lists is one the original binds, or a space42 parameter the pipeline's root signature carries ([`Space42Set`]) |
//! | 6 | features | `SFI0` asks for no feature the original's does not |
//! | 7 | root signature | it embeds none, and the variant is created with the record's own root signature ([`check_root_signature`]) |
//!
//! Rule 5 is what keeps a variant to the descriptors the game populated for the draw: the
//! original's own binds are the record of which those are. A bind matches one of the original's
//! by type, space and exact register range, and by kind and flags where both records carry them
//! (a variant may use fewer flags). Rule 6 compares with the original alone for now; from P2 the
//! allowed set widens to every game pixel shader catalogued this session, which a caller does by
//! widening [`PsInterface::sfi0`].
//!
//! # The report
//!
//! [`verify`] runs rules 1 to 6 and the blob's half of rule 7, and returns every failure rather
//! than the first, each naming its rule and the offending part, element or bind ([`RuleFailure`]),
//! so one compile shows everything wrong with it. The other half of rule 7 belongs to the caller,
//! which holds the root signature the variant is created with; [`check_root_signature`] gives its
//! answer the same shape. Bytes that cannot be read as a container stop the checks at rule 1, and
//! [`outcomes`] then calls the later rules not checked rather than passed. [`report`] is the
//! rule-by-rule text for the log, `diag/<id>.txt` and the Diagnostics tab.

use core::fmt;
use std::collections::BTreeMap;
use std::fmt::Write as _;

use crate::dxbc::{
    Container, DxbcError, INPUT_SIGNATURE_PART, OUTPUT_SIGNATURE_PART, PROGRAM_PART,
    PSV_RESOURCE_CBV, PSV_RESOURCE_SAMPLER, PSV_RESOURCE_SRV_RAW, PSV_RESOURCE_SRV_STRUCTURED,
    PSV_RESOURCE_SRV_TYPED, PSV_RESOURCE_UAV_RAW, PSV_RESOURCE_UAV_STRUCTURED,
    PSV_RESOURCE_UAV_STRUCTURED_WITH_COUNTER, PSV_RESOURCE_UAV_TYPED, PSV0_PART, PsvBind,
    PsvInterpolation, ROOT_SIGNATURE_PART, SFI0_PART, ShaderKind, SigElement,
};
use crate::look_binding::{Level, REGISTER_SPACE, register};

/// The largest blob the rules accept. The five ports are under 11 KB each; a blob past this is a
/// runaway include or an unrolled loop, not a layer.
pub const MAX_BLOB_BYTES: usize = 1 << 20;

/// The shader model of every port and every variant: `ps_6_0`, as major and minor.
pub const SHADER_MODEL: (u32, u32) = (6, 0);

/// The patch-constant signature's part code, which names the third group of interpolation modes
/// `PSV0` records. A pixel shader has none.
const PATCH_SIGNATURE_PART: &[u8; 4] = b"PSG1";

/// One of the seven checks, numbered as design 7.6 numbers them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(u8)]
pub enum Rule {
    /// Well formed, at most [`MAX_BLOB_BYTES`], a `ps_6_0` pixel shader.
    Container = 1,
    /// The digest is the retail hash.
    Signed = 2,
    /// The input signature and its interpolation are the original's.
    Linkage = 3,
    /// The output signature and its interpolation are the original's.
    Outputs = 4,
    /// Every bind is the original's or a space42 parameter the root signature carries.
    Resources = 5,
    /// No feature the original does without.
    Features = 6,
    /// No embedded root signature, and the record's own one.
    RootSignature = 7,
}

impl Rule {
    pub const ALL: [Self; 7] = [
        Self::Container,
        Self::Signed,
        Self::Linkage,
        Self::Outputs,
        Self::Resources,
        Self::Features,
        Self::RootSignature,
    ];

    #[must_use]
    pub const fn number(self) -> u8 {
        self as u8
    }

    /// The rule's name in the report.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Container => "container",
            Self::Signed => "signed",
            Self::Linkage => "linkage",
            Self::Outputs => "outputs",
            Self::Resources => "resources",
            Self::Features => "features",
            Self::RootSignature => "root signature",
        }
    }
}

/// What a failure is about.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Offender {
    /// The bytes as a whole, which could not be read as a container, so no later rule was checked.
    Unreadable,
    /// The container as a whole: its size or its digest.
    Container,
    /// A part, by its code: absent, repeated, unreadable, or one a variant must not carry.
    Part([u8; 4]),
    /// The `DXIL` program's shader kind or model.
    Program,
    /// An element of a signature part, by its index there: the candidate's element, or the
    /// original's where the candidate has none at that index.
    Element {
        part: [u8; 4],
        index: usize,
        semantic: String,
        semantic_index: u32,
    },
    /// The interpolation mode `PSV0` records for an element of a signature part.
    Interpolation { part: [u8; 4], index: usize },
    /// A register range the candidate binds.
    Bind(PsvBind),
    /// `SFI0` feature bits the original does not have.
    Features(u64),
    /// The root signature object a variant is created with.
    RootSignature,
}

impl Offender {
    fn element(part: &[u8; 4], index: usize, element: &SigElement) -> Self {
        Self::Element {
            part: *part,
            index,
            semantic: element.name.clone(),
            semantic_index: element.semantic_index,
        }
    }
}

impl fmt::Display for Offender {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unreadable => f.write_str("the bytes"),
            Self::Container => f.write_str("the container"),
            Self::Part(code) => write!(f, "the {} part", code.escape_ascii()),
            Self::Program => f.write_str("the DXIL program"),
            Self::Element {
                part,
                index,
                semantic,
                semantic_index,
            } => write!(
                f,
                "{} element {index} ({})",
                part.escape_ascii(),
                semantic_label(semantic, *semantic_index)
            ),
            Self::Interpolation { part, index } => write!(
                f,
                "the interpolation of {} element {index}",
                part.escape_ascii()
            ),
            Self::Bind(bind) => f.write_str(&bind_name(bind)),
            Self::Features(bits) => write!(f, "SFI0 bits {bits:#x}"),
            Self::RootSignature => f.write_str("the root signature"),
        }
    }
}

/// One rule a blob failed, what it failed on, and why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuleFailure {
    pub rule: Rule,
    pub offender: Offender,
    /// Why, with the original's value beside the candidate's where there is one.
    pub detail: String,
}

impl RuleFailure {
    fn new(rule: Rule, offender: Offender, detail: impl Into<String>) -> Self {
        Self {
            rule,
            offender,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for RuleFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "rule {} ({}): {}: {}",
            self.rule.number(),
            self.rule.name(),
            self.offender,
            self.detail
        )
    }
}

/// One of the editor's parameters appended to the game's root signatures (design 5.1), as a pixel
/// shader that declares it binds it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Space42Param {
    /// A: the parameter block, `b0, space42`.
    Params = 0,
    /// B: the probe, `u1, space42`.
    Probe = 1,
    /// C: the mask plane, `u0, space42`.
    Mask = 2,
    /// D: the draw constants, `b1, space42`.
    DrawConstants = 3,
}

impl Space42Param {
    /// A to D, in the order `look_binding` appends them.
    pub const ALL: [Self; 4] = [Self::Params, Self::Probe, Self::Mask, Self::DrawConstants];

    const fn bit(self) -> u8 {
        1_u8 << (self as u8)
    }

    /// The parameter's letter in design 5.1.
    #[must_use]
    pub const fn letter(self) -> char {
        match self {
            Self::Params => 'A',
            Self::Probe => 'B',
            Self::Mask => 'C',
            Self::DrawConstants => 'D',
        }
    }

    /// The `PSV0` resource type and register a shader that binds this parameter records, always in
    /// [`REGISTER_SPACE`].
    #[must_use]
    pub const fn psv(self) -> (u32, u32) {
        match self {
            Self::Params => (PSV_RESOURCE_CBV, register::PARAMS_CBV),
            Self::Probe => (PSV_RESOURCE_UAV_RAW, register::PROBE_UAV),
            Self::Mask => (PSV_RESOURCE_UAV_RAW, register::MASK_UAV),
            Self::DrawConstants => (PSV_RESOURCE_CBV, register::DRAW_CONSTANTS),
        }
    }

    /// Whether `bind` is this parameter: its type, in space 42, at exactly its one register.
    #[must_use]
    pub fn is(self, bind: &PsvBind) -> bool {
        let (resource_type, register) = self.psv();
        bind.resource_type == resource_type
            && bind.space == REGISTER_SPACE
            && bind.lower_bound == register
            && bind.upper_bound == register
    }
}

/// The space42 parameters one root signature carries: what the `look_binding` ladder appended to
/// it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct Space42Set(u8);

impl Space42Set {
    /// A root signature that carries none of them.
    pub const NONE: Self = Self(0);
    /// A, B, C and D: a root signature at [`Level::Full`], which every port's is.
    pub const FULL: Self = Self::carried_by(Level::Full);

    /// What a root signature at `level` carries. The ladder appends A, B, C and D in that order
    /// and stops where the room does, so a level carries the first [`Level::parameters`] of them.
    #[must_use]
    pub const fn carried_by(level: Level) -> Self {
        Self((1_u8 << level.parameters()) - 1)
    }

    #[must_use]
    pub const fn contains(self, param: Space42Param) -> bool {
        self.0 & param.bit() != 0
    }

    /// Whether `bind` is one of the parameters this root signature carries.
    #[must_use]
    pub fn admits(self, bind: &PsvBind) -> bool {
        Space42Param::ALL
            .into_iter()
            .any(|param| self.contains(param) && param.is(bind))
    }
}

impl fmt::Display for Space42Set {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut carried = Space42Param::ALL
            .into_iter()
            .filter(|param| self.contains(*param));
        let Some(first) = carried.next() else {
            return f.write_str("none");
        };
        write!(f, "{}", first.letter())?;
        carried.try_for_each(|param| write!(f, ", {}", param.letter()))
    }
}

/// What a pixel shader shows the pipeline around it: the parts of its container the rules compare
/// a candidate with. Read once from the game's pixel shader and kept with the port record.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PsInterface {
    pub inputs: Vec<SigElement>,
    pub outputs: Vec<SigElement>,
    pub binds: Vec<PsvBind>,
    pub interpolation: PsvInterpolation,
    /// The features a candidate may ask for: the original's `SFI0`.
    pub sfi0: u64,
    /// The `ILDN` debug name, for a report; `None` when the shader carries none.
    pub debug_name: Option<String>,
}

impl PsInterface {
    /// The interface of the pixel shader in `blob`.
    ///
    /// # Errors
    ///
    /// The bytes are not a container, or a signature, `PSV0` or `SFI0` cannot be read, or `PSV0`
    /// is runtime info version 0 and records no interpolation modes to compare with.
    pub fn read(blob: &[u8]) -> Result<Self, DxbcError> {
        let container = Container::parse(blob)?;
        Ok(Self {
            inputs: container.signature(INPUT_SIGNATURE_PART)?,
            outputs: container.signature(OUTPUT_SIGNATURE_PART)?,
            binds: container.psv0_binds()?,
            interpolation: container.psv0_interp()?,
            sfi0: container.sfi0()?,
            debug_name: container.ildn().ok().map(str::to_owned),
        })
    }
}

/// Rules 1 to 6 and the blob's half of rule 7, for `candidate` standing in for the pixel shader
/// whose interface is `original`, in a pipeline whose root signature carries `carried`.
///
/// # Errors
///
/// Every failure, ordered by rule. Bytes that are not a readable container give exactly one, an
/// [`Offender::Unreadable`] under [`Rule::Container`].
pub fn verify(
    candidate: &[u8],
    original: &PsInterface,
    carried: Space42Set,
) -> Result<(), Vec<RuleFailure>> {
    let container = match Container::parse(candidate) {
        Ok(container) => container,
        Err(error) => {
            return Err(vec![RuleFailure::new(
                Rule::Container,
                Offender::Unreadable,
                error.to_string(),
            )]);
        }
    };
    let mut failures = Vec::new();
    check_container(candidate.len(), &container, &mut failures);
    check_signed(&container, &mut failures);
    check_signature(
        &container,
        INPUT_SIGNATURE_PART,
        &original.inputs,
        Rule::Linkage,
        &mut failures,
    );
    check_signature(
        &container,
        OUTPUT_SIGNATURE_PART,
        &original.outputs,
        Rule::Outputs,
        &mut failures,
    );
    match container.psv0() {
        Ok(psv0) => {
            check_interpolation(
                psv0.interpolation.as_ref(),
                &original.interpolation,
                &mut failures,
            );
            check_resources(&psv0.binds, &original.binds, carried, &mut failures);
        }
        Err(error) => {
            for rule in [Rule::Linkage, Rule::Resources] {
                failures.push(RuleFailure::new(
                    rule,
                    Offender::Part(*PSV0_PART),
                    error.to_string(),
                ));
            }
        }
    }
    check_features(&container, original.sfi0, &mut failures);
    if container.part(ROOT_SIGNATURE_PART).is_some() {
        failures.push(RuleFailure::new(
            Rule::RootSignature,
            Offender::Part(*ROOT_SIGNATURE_PART),
            "embeds a root signature, where a variant takes the game pipeline's own",
        ));
    }
    // Stable, so failures within a rule keep the order they were found in.
    failures.sort_by_key(|failure| failure.rule);
    if failures.is_empty() {
        Ok(())
    } else {
        Err(failures)
    }
}

/// The caller's half of rule 7: a variant is created with root signature `used`, which must be
/// the one the port record was copied with, `recorded`. Both are the pointers the caller holds.
///
/// # Errors
///
/// The record names no root signature, or `used` is another one.
pub fn check_root_signature(recorded: usize, used: usize) -> Result<(), RuleFailure> {
    if recorded == 0 {
        return Err(RuleFailure::new(
            Rule::RootSignature,
            Offender::RootSignature,
            "the record names no root signature",
        ));
    }
    if used != recorded {
        return Err(RuleFailure::new(
            Rule::RootSignature,
            Offender::RootSignature,
            format!("{used:#x}, the record's {recorded:#x}"),
        ));
    }
    Ok(())
}

/// Rule 1: the size, repeated parts, and the program's kind and model. `Container::parse` has
/// already proved the magic and that the part table lies inside the declared size.
fn check_container(len: usize, container: &Container<'_>, failures: &mut Vec<RuleFailure>) {
    let mut fail = |offender, detail: String| {
        failures.push(RuleFailure::new(Rule::Container, offender, detail));
    };
    let declared = container.total_size();
    if declared != len {
        fail(
            Offender::Container,
            format!(
                "{} bytes follow the {declared} the header declares",
                len - declared
            ),
        );
    }
    if len > MAX_BLOB_BYTES {
        fail(
            Offender::Container,
            format!("{len} bytes, more than the {MAX_BLOB_BYTES} a variant may be"),
        );
    }
    let mut counts: BTreeMap<[u8; 4], usize> = BTreeMap::new();
    for part in container.parts() {
        *counts.entry(part.code).or_default() += 1;
    }
    for (code, count) in counts {
        if count > 1 {
            // These rules read the first; D3D12 need not.
            fail(
                Offender::Part(code),
                format!("appears {count} times, and D3D12 need not read the one checked here"),
            );
        }
    }
    match container.program() {
        Ok(program) => {
            if program.kind != ShaderKind::Pixel {
                fail(
                    Offender::Program,
                    format!("a {} shader, not a pixel shader", kind_name(program.kind)),
                );
            }
            if (program.major, program.minor) != SHADER_MODEL {
                let (major, minor) = SHADER_MODEL;
                fail(
                    Offender::Program,
                    format!(
                        "shader model {}.{}, not {major}.{minor}",
                        program.major, program.minor
                    ),
                );
            }
        }
        Err(error) => fail(Offender::Part(*PROGRAM_PART), error.to_string()),
    }
}

/// Rule 2.
fn check_signed(container: &Container<'_>, failures: &mut Vec<RuleFailure>) {
    let digest = container.digest();
    if digest == [0; 16] {
        failures.push(RuleFailure::new(
            Rule::Signed,
            Offender::Container,
            "the digest is zero: no validator signed it",
        ));
        return;
    }
    let expected = container.retail_hash();
    if digest != expected {
        failures.push(RuleFailure::new(
            Rule::Signed,
            Offender::Container,
            format!(
                "the digest is {}, and the retail hash of the container is {}",
                hex(&digest),
                hex(&expected)
            ),
        ));
    }
}

/// Rules 3 and 4: the signature in part `code`, element for element against the original's.
fn check_signature(
    container: &Container<'_>,
    code: &[u8; 4],
    theirs: &[SigElement],
    rule: Rule,
    failures: &mut Vec<RuleFailure>,
) {
    let ours = match container.signature(code) {
        Ok(ours) => ours,
        Err(error) => {
            failures.push(RuleFailure::new(
                rule,
                Offender::Part(*code),
                error.to_string(),
            ));
            return;
        }
    };
    for index in 0..ours.len().max(theirs.len()) {
        let failure = match (ours.get(index), theirs.get(index)) {
            (Some(ours), Some(theirs)) if !ours.links_like(theirs) => RuleFailure::new(
                rule,
                Offender::element(code, index, ours),
                differences(ours, theirs),
            ),
            (Some(ours), None) => RuleFailure::new(
                rule,
                Offender::element(code, index, ours),
                format!(
                    "not in the original's signature, which has {} {}",
                    theirs.len(),
                    if theirs.len() == 1 {
                        "element"
                    } else {
                        "elements"
                    }
                ),
            ),
            (None, Some(theirs)) => RuleFailure::new(
                rule,
                Offender::element(code, index, theirs),
                "absent, and the original has it",
            ),
            _ => continue,
        };
        failures.push(failure);
    }
}

/// Every field [`SigElement::links_like`] compares that differs, with the original's value.
fn differences(ours: &SigElement, theirs: &SigElement) -> String {
    let mut found = Vec::new();
    let mut field = |name: &str, ours: String, theirs: String| {
        if ours != theirs {
            found.push(format!("{name} {ours}, the original's {theirs}"));
        }
    };
    field("stream", ours.stream.to_string(), theirs.stream.to_string());
    field("semantic", ours.name.clone(), theirs.name.clone());
    field(
        "semantic index",
        ours.semantic_index.to_string(),
        theirs.semantic_index.to_string(),
    );
    field(
        "system value",
        ours.system_value.to_string(),
        theirs.system_value.to_string(),
    );
    field(
        "component type",
        ours.component_type.to_string(),
        theirs.component_type.to_string(),
    );
    field(
        "register",
        ours.register.to_string(),
        theirs.register.to_string(),
    );
    field(
        "mask",
        format!("{:#x}", ours.mask),
        format!("{:#x}", theirs.mask),
    );
    field(
        "min precision",
        ours.min_precision.to_string(),
        theirs.min_precision.to_string(),
    );
    found.join("; ")
}

/// Rules 3 and 4, from `PSV0`: input and patch modes are linkage, output modes are outputs.
fn check_interpolation(
    ours: Option<&PsvInterpolation>,
    theirs: &PsvInterpolation,
    failures: &mut Vec<RuleFailure>,
) {
    let Some(ours) = ours else {
        failures.push(RuleFailure::new(
            Rule::Linkage,
            Offender::Part(*PSV0_PART),
            "runtime info version 0 records no interpolation modes",
        ));
        return;
    };
    let groups = [
        (
            INPUT_SIGNATURE_PART,
            Rule::Linkage,
            &ours.inputs,
            &theirs.inputs,
        ),
        (
            OUTPUT_SIGNATURE_PART,
            Rule::Outputs,
            &ours.outputs,
            &theirs.outputs,
        ),
        (
            PATCH_SIGNATURE_PART,
            Rule::Linkage,
            &ours.patch_constant_or_primitive,
            &theirs.patch_constant_or_primitive,
        ),
    ];
    for (part, rule, ours, theirs) in groups {
        for index in 0..ours.len().max(theirs.len()) {
            let (mode, original) = (ours.get(index), theirs.get(index));
            if mode != original {
                failures.push(RuleFailure::new(
                    rule,
                    Offender::Interpolation { part: *part, index },
                    format!(
                        "{}, the original's {}",
                        mode_name(mode),
                        mode_name(original)
                    ),
                ));
            }
        }
    }
}

/// Rule 5.
fn check_resources(
    ours: &[PsvBind],
    theirs: &[PsvBind],
    carried: Space42Set,
    failures: &mut Vec<RuleFailure>,
) {
    for bind in ours {
        if carried.admits(bind) || theirs.iter().any(|original| same_resource(bind, original)) {
            continue;
        }
        let detail = if bind.space == REGISTER_SPACE {
            match Space42Param::ALL.into_iter().find(|param| param.is(bind)) {
                Some(param) => format!(
                    "parameter {} is not carried by this root signature, which carries {carried}",
                    param.letter()
                ),
                None => "not one of the editor's space42 parameters".to_owned(),
            }
        } else {
            match theirs
                .iter()
                .find(|original| same_registers(bind, original))
            {
                Some(original) => bound_differently(bind, original),
                None => "the original binds nothing there".to_owned(),
            }
        };
        failures.push(RuleFailure::new(
            Rule::Resources,
            Offender::Bind(*bind),
            detail,
        ));
    }
}

/// The same type, space and register range.
fn same_registers(ours: &PsvBind, theirs: &PsvBind) -> bool {
    ours.resource_type == theirs.resource_type
        && ours.space == theirs.space
        && ours.lower_bound == theirs.lower_bound
        && ours.upper_bound == theirs.upper_bound
}

/// The same registers, read as the same kind of resource, with no flag the original lacks. Kind
/// and flags are compared only where both records carry them, which version 0 records do not.
fn same_resource(ours: &PsvBind, theirs: &PsvBind) -> bool {
    let kind = match (ours.kind, theirs.kind) {
        (Some(ours), Some(theirs)) => ours == theirs,
        _ => true,
    };
    let flags = match (ours.flags, theirs.flags) {
        (Some(ours), Some(theirs)) => ours & !theirs == 0,
        _ => true,
    };
    same_registers(ours, theirs) && kind && flags
}

/// Why `ours` is not `theirs` though both name the same registers.
fn bound_differently(ours: &PsvBind, theirs: &PsvBind) -> String {
    let mut found = Vec::new();
    if let (Some(kind), Some(original)) = (ours.kind, theirs.kind)
        && kind != original
    {
        found.push(format!("kind {kind}, the original's {original}"));
    }
    if let (Some(flags), Some(original)) = (ours.flags, theirs.flags)
        && flags & !original != 0
    {
        found.push(format!("flags {flags:#x}, the original's {original:#x}"));
    }
    found.join("; ")
}

/// Rule 6.
fn check_features(container: &Container<'_>, allowed: u64, failures: &mut Vec<RuleFailure>) {
    match container.sfi0() {
        Ok(ours) => {
            let extra = ours & !allowed;
            if extra != 0 {
                failures.push(RuleFailure::new(
                    Rule::Features,
                    Offender::Features(extra),
                    format!("SFI0 {ours:#x}, the original's {allowed:#x}"),
                ));
            }
        }
        Err(error) => failures.push(RuleFailure::new(
            Rule::Features,
            Offender::Part(*SFI0_PART),
            error.to_string(),
        )),
    }
}

/// How one rule came out.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Outcome {
    Passed,
    Refused,
    /// The bytes could not be read as a container, so the rule had nothing to check.
    NotChecked,
}

impl Outcome {
    #[must_use]
    pub const fn word(self) -> &'static str {
        match self {
            Self::Passed => "passed",
            Self::Refused => "refused",
            Self::NotChecked => "not checked",
        }
    }
}

/// Every rule's outcome, given the failures of [`verify`] together with the caller's
/// [`check_root_signature`]. Rule 7 counts as passed only when the caller's half was run and its
/// failure, if any, is among `failures`.
#[must_use]
pub fn outcomes(failures: &[RuleFailure]) -> [(Rule, Outcome); 7] {
    let unreadable = failures
        .iter()
        .any(|failure| failure.offender == Offender::Unreadable);
    Rule::ALL.map(|rule| {
        let outcome = if failures.iter().any(|failure| failure.rule == rule) {
            Outcome::Refused
        } else if unreadable {
            Outcome::NotChecked
        } else {
            Outcome::Passed
        };
        (rule, outcome)
    })
}

/// How many rules passed: the 7 in "verified 7 of 7".
#[must_use]
pub fn passed(failures: &[RuleFailure]) -> usize {
    outcomes(failures)
        .iter()
        .filter(|(_, outcome)| *outcome == Outcome::Passed)
        .count()
}

/// The rule-by-rule report: a count, then one line per rule, each failure indented under its
/// rule.
#[must_use]
pub fn report(failures: &[RuleFailure]) -> String {
    let mut out = format!("verified {} of {}\n", passed(failures), Rule::ALL.len());
    for (rule, outcome) in outcomes(failures) {
        let _ = writeln!(out, "{} {}: {}", rule.number(), rule.name(), outcome.word());
        for failure in failures.iter().filter(|failure| failure.rule == rule) {
            let _ = writeln!(out, "  {}: {}", failure.offender, failure.detail);
        }
    }
    out
}

/// `TEXCOORD` for index 0, `SV_Target1` for index 1: the semantic as HLSL spells it.
fn semantic_label(semantic: &str, index: u32) -> String {
    if index == 0 {
        semantic.to_owned()
    } else {
        format!("{semantic}{index}")
    }
}

/// A bind as a register range: `typed SRV t5, space0`.
fn bind_name(bind: &PsvBind) -> String {
    let (what, letter) = match bind.resource_type {
        PSV_RESOURCE_SAMPLER => ("sampler", 's'),
        PSV_RESOURCE_CBV => ("CBV", 'b'),
        PSV_RESOURCE_SRV_TYPED => ("typed SRV", 't'),
        PSV_RESOURCE_SRV_RAW => ("raw SRV", 't'),
        PSV_RESOURCE_SRV_STRUCTURED => ("structured SRV", 't'),
        PSV_RESOURCE_UAV_TYPED => ("typed UAV", 'u'),
        PSV_RESOURCE_UAV_RAW => ("raw UAV", 'u'),
        PSV_RESOURCE_UAV_STRUCTURED => ("structured UAV", 'u'),
        PSV_RESOURCE_UAV_STRUCTURED_WITH_COUNTER => ("structured UAV with a counter", 'u'),
        other => {
            return format!(
                "resource type {other} at {}..{}, space{}",
                bind.lower_bound, bind.upper_bound, bind.space
            );
        }
    };
    let registers = match (bind.lower_bound, bind.upper_bound) {
        (lower, upper) if lower == upper => format!("{letter}{lower}"),
        (lower, u32::MAX) => format!("{letter}{lower} onwards"),
        (lower, upper) => format!("{letter}{lower}..{letter}{upper}"),
    };
    format!("{what} {registers}, space{}", bind.space)
}

/// `DXIL::InterpolationMode`, by name; `none` where the element is absent.
fn mode_name(mode: Option<&u8>) -> String {
    let name = match mode.copied() {
        None => "none",
        Some(0) => "undefined",
        Some(1) => "constant",
        Some(2) => "linear",
        Some(3) => "linear centroid",
        Some(4) => "linear noperspective",
        Some(5) => "linear noperspective centroid",
        Some(6) => "linear sample",
        Some(7) => "linear noperspective sample",
        Some(other) => return format!("mode {other}"),
    };
    name.to_owned()
}

fn kind_name(kind: ShaderKind) -> String {
    let name = match kind {
        ShaderKind::Pixel => "pixel",
        ShaderKind::Vertex => "vertex",
        ShaderKind::Geometry => "geometry",
        ShaderKind::Hull => "hull",
        ShaderKind::Domain => "domain",
        ShaderKind::Compute => "compute",
        ShaderKind::Library => "library",
        ShaderKind::Other(raw) => return format!("kind {raw}"),
    };
    name.to_owned()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dxbc::{COMPONENT_FLOAT32, SYSTEM_VALUE_POSITION, SYSTEM_VALUE_TARGET, retail_hash};
    use crate::replace::{PortKind, REPLACEMENTS, fnv1_64};
    use std::path::{Path, PathBuf};

    /// `DXIL::InterpolationMode` values.
    const LINEAR: u8 = 2;
    const LINEAR_CENTROID: u8 = 3;
    const NO_PERSPECTIVE: u8 = 4;

    /// `PSVResourceKind` values, as the ports' and the game originals' `PSV0` records carry them.
    const KIND_TEXTURE_2D: u32 = 2;
    const KIND_TEXTURE_CUBE: u32 = 5;
    const KIND_RAW_BUFFER: u32 = 11;
    const KIND_CBUFFER: u32 = 13;
    const KIND_SAMPLER: u32 = 14;

    /// `(semantic, semantic index, register, system value, mask)`.
    type Sig = (&'static str, u32, u32, u32, u8);

    fn put(bytes: &mut Vec<u8>, word: u32) {
        bytes.extend_from_slice(&word.to_le_bytes());
    }

    fn set_u32(bytes: &mut [u8], at: usize, value: u32) {
        bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
    }

    /// A signature part: the element count, the table's offset, 32-byte elements, then the names.
    fn signature_part(elements: &[Sig]) -> Vec<u8> {
        let table = 8;
        let mut part = vec![0_u8; table + 32 * elements.len()];
        set_u32(&mut part, 0, elements.len() as u32);
        set_u32(&mut part, 4, table as u32);
        for (index, (name, semantic_index, register, system_value, mask)) in
            elements.iter().enumerate()
        {
            let at = table + 32 * index;
            let name_at = part.len() as u32;
            set_u32(&mut part, at + 4, name_at);
            set_u32(&mut part, at + 8, *semantic_index);
            set_u32(&mut part, at + 12, *system_value);
            set_u32(&mut part, at + 16, COMPONENT_FLOAT32);
            set_u32(&mut part, at + 20, *register);
            part[at + 24] = *mask;
            part.extend_from_slice(name.as_bytes());
            part.push(0);
        }
        part
    }

    /// A version 1 `PSV0`: a pixel shader's runtime info, 24-byte bind records, no strings, and a
    /// 16-byte record per signature element whose byte 13 is its interpolation mode.
    fn psv0_part(binds: &[PsvBind], inputs: &[u8], outputs: &[u8]) -> Vec<u8> {
        let mut runtime = [0_u8; 36];
        runtime[28] = inputs.len() as u8;
        runtime[29] = outputs.len() as u8;
        let mut part = Vec::new();
        put(&mut part, runtime.len() as u32);
        part.extend_from_slice(&runtime);
        put(&mut part, binds.len() as u32);
        if !binds.is_empty() {
            put(&mut part, 24);
            for bind in binds {
                for word in [
                    bind.resource_type,
                    bind.space,
                    bind.lower_bound,
                    bind.upper_bound,
                    bind.kind.unwrap_or_default(),
                    bind.flags.unwrap_or_default(),
                ] {
                    put(&mut part, word);
                }
            }
        }
        // The string table's size, then the semantic index table's count.
        put(&mut part, 0);
        put(&mut part, 0);
        if inputs.len() + outputs.len() > 0 {
            put(&mut part, 16);
            for mode in inputs.iter().chain(outputs) {
                let mut element = [0_u8; 16];
                element[13] = *mode;
                part.extend_from_slice(&element);
            }
        }
        part
    }

    /// A version 0 `PSV0`, which records binds and no signature elements.
    fn psv0_version_0() -> Vec<u8> {
        let mut part = Vec::new();
        put(&mut part, 24);
        part.extend_from_slice(&[0; 24]);
        put(&mut part, 0);
        part
    }

    /// A `DXIL` part: the program header, then four bytes standing in for the bitcode.
    fn program_part((kind, major, minor): (u32, u32, u32)) -> Vec<u8> {
        let mut part = vec![0_u8; 28];
        set_u32(&mut part, 0, (kind << 16) | (major << 4) | minor);
        let words = (part.len() / 4) as u32;
        set_u32(&mut part, 4, words);
        part[8..12].copy_from_slice(b"DXIL");
        set_u32(&mut part, 12, 0x100);
        set_u32(&mut part, 16, 16);
        set_u32(&mut part, 20, 4);
        part
    }

    /// An unsigned container with these parts.
    fn container(parts: &[([u8; 4], Vec<u8>)]) -> Vec<u8> {
        let header = 32 + 4 * parts.len();
        let total = header + parts.iter().map(|(_, data)| 8 + data.len()).sum::<usize>();
        let mut out = b"DXBC".to_vec();
        out.extend_from_slice(&[0; 16]);
        put(&mut out, 1);
        put(&mut out, total as u32);
        put(&mut out, parts.len() as u32);
        let mut at = header;
        for (_, data) in parts {
            put(&mut out, at as u32);
            at += 8 + data.len();
        }
        for (code, data) in parts {
            out.extend_from_slice(code);
            put(&mut out, data.len() as u32);
            out.extend_from_slice(data);
        }
        out
    }

    fn signed(mut blob: Vec<u8>) -> Vec<u8> {
        let digest = retail_hash(&blob);
        blob[4..20].copy_from_slice(&digest);
        blob
    }

    fn bind(resource_type: u32, space: u32, register: u32, kind: u32) -> PsvBind {
        PsvBind {
            resource_type,
            space,
            lower_bound: register,
            upper_bound: register,
            kind: Some(kind),
            flags: Some(0),
        }
    }

    /// A space42 parameter as a port's `PSV0` records it.
    fn editor_bind(param: Space42Param) -> PsvBind {
        let (resource_type, register) = param.psv();
        let kind = if resource_type == PSV_RESOURCE_CBV {
            KIND_CBUFFER
        } else {
            KIND_RAW_BUFFER
        };
        bind(resource_type, REGISTER_SPACE, register, kind)
    }

    /// A pixel shader container, part by part.
    #[derive(Clone)]
    struct Shader {
        /// Kind, major and minor of the program header.
        program: (u32, u32, u32),
        inputs: Vec<Sig>,
        outputs: Vec<Sig>,
        input_modes: Vec<u8>,
        output_modes: Vec<u8>,
        binds: Vec<PsvBind>,
        sfi0: u64,
        /// Parts after the usual ones.
        extra: Vec<([u8; 4], Vec<u8>)>,
        /// Leave the `DXIL` part out.
        no_program: bool,
    }

    impl Shader {
        /// A game pixel shader shaped like the tonemap pass: the position and one texture
        /// coordinate in, one target out, a constant buffer, a sampler and two textures, and its
        /// own root signature embedded, as every game shader has.
        fn game() -> Self {
            Self {
                program: (0, 6, 0),
                inputs: vec![
                    ("SV_Position", 0, 0, SYSTEM_VALUE_POSITION, 0xf),
                    ("TEXCOORD", 0, 1, 0, 0x7),
                ],
                outputs: vec![("SV_Target", 0, 0, SYSTEM_VALUE_TARGET, 0xf)],
                input_modes: vec![NO_PERSPECTIVE, LINEAR],
                output_modes: vec![0],
                binds: vec![
                    bind(PSV_RESOURCE_CBV, 0, 4, KIND_CBUFFER),
                    bind(PSV_RESOURCE_SAMPLER, 0, 1, KIND_SAMPLER),
                    bind(PSV_RESOURCE_SRV_TYPED, 0, 0, KIND_TEXTURE_2D),
                    bind(PSV_RESOURCE_SRV_TYPED, 0, 3, KIND_TEXTURE_2D),
                ],
                sfi0: 0,
                extra: vec![(*ROOT_SIGNATURE_PART, vec![0; 16])],
                no_program: false,
            }
        }

        /// A variant of [`Shader::game`]: the same interface, no root signature of its own, and
        /// the parameter block and the probe bound as every port binds them.
        fn variant() -> Self {
            let mut shader = Self::game();
            shader.extra.clear();
            shader.binds.push(editor_bind(Space42Param::Params));
            shader.binds.push(editor_bind(Space42Param::Probe));
            shader
        }

        fn unsigned(&self) -> Vec<u8> {
            let mut parts = vec![
                (*SFI0_PART, self.sfi0.to_le_bytes().to_vec()),
                (*INPUT_SIGNATURE_PART, signature_part(&self.inputs)),
                (*OUTPUT_SIGNATURE_PART, signature_part(&self.outputs)),
                (
                    *PSV0_PART,
                    psv0_part(&self.binds, &self.input_modes, &self.output_modes),
                ),
            ];
            if !self.no_program {
                parts.push((*PROGRAM_PART, program_part(self.program)));
            }
            parts.extend(self.extra.iter().cloned());
            container(&parts)
        }

        fn blob(&self) -> Vec<u8> {
            signed(self.unsigned())
        }
    }

    fn original() -> PsInterface {
        PsInterface::read(&Shader::game().blob()).expect("the game shader reads")
    }

    fn check(shader: &Shader) -> Result<(), Vec<RuleFailure>> {
        verify(&shader.blob(), &original(), Space42Set::FULL)
    }

    /// The failures, after asserting there are some and every one is `rule`'s.
    fn only(rule: Rule, result: Result<(), Vec<RuleFailure>>) -> Vec<RuleFailure> {
        let failures = result.expect_err("refused");
        assert!(
            !failures.is_empty() && failures.iter().all(|failure| failure.rule == rule),
            "{failures:#?}"
        );
        failures
    }

    fn offenders(failures: &[RuleFailure]) -> Vec<Offender> {
        failures
            .iter()
            .map(|failure| failure.offender.clone())
            .collect()
    }

    fn element(part: &[u8; 4], index: usize, semantic: &str, semantic_index: u32) -> Offender {
        Offender::Element {
            part: *part,
            index,
            semantic: semantic.to_owned(),
            semantic_index,
        }
    }

    #[test]
    fn a_variant_that_changes_nothing_but_its_code_passes_every_rule() {
        assert_eq!(check(&Shader::variant()), Ok(()));
        assert_eq!(passed(&[]), 7);
        assert!(outcomes(&[]).iter().all(|(_, o)| *o == Outcome::Passed));
        assert!(report(&[]).starts_with("verified 7 of 7\n1 container: passed\n"));
    }

    #[test]
    fn bytes_that_are_not_a_container_stop_at_rule_1() {
        let mut blob = Shader::variant().blob();
        blob[0] = b'X';
        let failures = only(
            Rule::Container,
            verify(&blob, &original(), Space42Set::FULL),
        );
        assert_eq!(offenders(&failures), [Offender::Unreadable]);
        let outcomes = outcomes(&failures);
        assert_eq!(outcomes[0], (Rule::Container, Outcome::Refused));
        assert!(
            outcomes[1..]
                .iter()
                .all(|(_, outcome)| *outcome == Outcome::NotChecked),
            "{outcomes:?}"
        );
        assert_eq!(passed(&failures), 0);
        assert!(report(&failures).contains("5 resources: not checked\n"));

        let whole = Shader::variant().blob();
        let failures = only(
            Rule::Container,
            verify(&whole[..whole.len() - 1], &original(), Space42Set::FULL),
        );
        assert_eq!(offenders(&failures), [Offender::Unreadable]);
    }

    #[test]
    fn bytes_after_the_declared_container_fail_rule_1() {
        let mut blob = Shader::variant().blob();
        blob.extend_from_slice(&[0; 4]);
        let failures = only(
            Rule::Container,
            verify(&blob, &original(), Space42Set::FULL),
        );
        assert_eq!(offenders(&failures), [Offender::Container]);
        assert!(
            failures[0].detail.starts_with("4 bytes follow"),
            "{}",
            failures[0]
        );
    }

    #[test]
    fn only_a_shader_model_6_0_pixel_shader_passes_rule_1() {
        for program in [(1, 6, 0), (0, 6, 5), (5, 6, 0)] {
            let shader = Shader {
                program,
                ..Shader::variant()
            };
            let failures = only(Rule::Container, check(&shader));
            assert_eq!(offenders(&failures), [Offender::Program], "{program:?}");
        }
        let failures = only(
            Rule::Container,
            check(&Shader {
                program: (1, 6, 5),
                ..Shader::variant()
            }),
        );
        assert_eq!(offenders(&failures), [Offender::Program, Offender::Program]);
        let failures = only(
            Rule::Container,
            check(&Shader {
                no_program: true,
                ..Shader::variant()
            }),
        );
        assert_eq!(offenders(&failures), [Offender::Part(*PROGRAM_PART)]);
    }

    #[test]
    fn a_repeated_part_fails_rule_1() {
        // The first signature is the right one, so only the repetition is wrong.
        let mut shader = Shader::variant();
        shader.extra.push((
            *INPUT_SIGNATURE_PART,
            signature_part(&[("TEXCOORD", 0, 3, 0, 0xf)]),
        ));
        let failures = only(Rule::Container, check(&shader));
        assert_eq!(
            offenders(&failures),
            [Offender::Part(*INPUT_SIGNATURE_PART)]
        );
    }

    #[test]
    fn a_blob_over_1_mib_fails_rule_1() {
        let mut shader = Shader::variant();
        shader.extra.push((*b"PADD", vec![0; MAX_BLOB_BYTES]));
        let failures = only(Rule::Container, check(&shader));
        assert_eq!(offenders(&failures), [Offender::Container]);
        assert!(failures[0].detail.contains("more than"), "{}", failures[0]);
    }

    #[test]
    fn an_unsigned_or_wrongly_signed_blob_fails_rule_2() {
        let unsigned = Shader::variant().unsigned();
        let failures = only(
            Rule::Signed,
            verify(&unsigned, &original(), Space42Set::FULL),
        );
        assert_eq!(offenders(&failures), [Offender::Container]);
        assert!(failures[0].detail.contains("zero"), "{}", failures[0]);

        let mut wrong = Shader::variant().blob();
        wrong[4] ^= 1;
        let failures = only(Rule::Signed, verify(&wrong, &original(), Space42Set::FULL));
        assert_eq!(offenders(&failures), [Offender::Container]);
        assert!(
            failures[0].detail.contains("retail hash"),
            "{}",
            failures[0]
        );
    }

    #[test]
    fn an_input_in_another_register_or_with_more_components_fails_rule_3() {
        let mut moved = Shader::variant();
        moved.inputs[1].2 = 2;
        let failures = only(Rule::Linkage, check(&moved));
        assert_eq!(
            offenders(&failures),
            [element(INPUT_SIGNATURE_PART, 1, "TEXCOORD", 0)]
        );
        assert_eq!(failures[0].detail, "register 2, the original's 1");

        let mut wider = Shader::variant();
        wider.inputs[1].4 = 0xf;
        let failures = only(Rule::Linkage, check(&wider));
        assert_eq!(failures[0].detail, "mask 0xf, the original's 0x7");
    }

    #[test]
    fn an_added_or_a_missing_input_fails_rule_3() {
        let mut added = Shader::variant();
        added.inputs.push(("TEXCOORD", 1, 2, 0, 0xf));
        added.input_modes.push(LINEAR);
        let failures = only(Rule::Linkage, check(&added));
        assert_eq!(
            offenders(&failures),
            [
                element(INPUT_SIGNATURE_PART, 2, "TEXCOORD", 1),
                Offender::Interpolation {
                    part: *INPUT_SIGNATURE_PART,
                    index: 2
                },
            ]
        );

        let mut missing = Shader::variant();
        missing.inputs.pop();
        missing.input_modes.pop();
        let failures = only(Rule::Linkage, check(&missing));
        assert_eq!(
            offenders(&failures),
            [
                element(INPUT_SIGNATURE_PART, 1, "TEXCOORD", 0),
                Offender::Interpolation {
                    part: *INPUT_SIGNATURE_PART,
                    index: 1
                },
            ]
        );
        assert_eq!(failures[0].detail, "absent, and the original has it");
        assert_eq!(failures[1].detail, "none, the original's linear");
    }

    #[test]
    fn another_interpolation_mode_fails_rule_3() {
        let mut shader = Shader::variant();
        shader.input_modes[1] = LINEAR_CENTROID;
        let failures = only(Rule::Linkage, check(&shader));
        assert_eq!(
            offenders(&failures),
            [Offender::Interpolation {
                part: *INPUT_SIGNATURE_PART,
                index: 1
            }]
        );
        assert_eq!(failures[0].detail, "linear centroid, the original's linear");
    }

    #[test]
    fn an_added_sv_target1_fails_rule_4() {
        let mut shader = Shader::variant();
        shader
            .outputs
            .push(("SV_Target", 1, 1, SYSTEM_VALUE_TARGET, 0xf));
        shader.output_modes.push(0);
        let failures = only(Rule::Outputs, check(&shader));
        assert_eq!(
            offenders(&failures),
            [
                element(OUTPUT_SIGNATURE_PART, 1, "SV_Target", 1),
                Offender::Interpolation {
                    part: *OUTPUT_SIGNATURE_PART,
                    index: 1
                },
            ]
        );
        assert_eq!(
            failures[0].to_string(),
            "rule 4 (outputs): OSG1 element 1 (SV_Target1): not in the original's signature, \
             which has 1 element"
        );
    }

    #[test]
    fn an_output_with_fewer_components_fails_rule_4() {
        let mut shader = Shader::variant();
        shader.outputs[0].4 = 0x7;
        let failures = only(Rule::Outputs, check(&shader));
        assert_eq!(
            offenders(&failures),
            [element(OUTPUT_SIGNATURE_PART, 0, "SV_Target", 0)]
        );
    }

    #[test]
    fn a_texture_the_original_does_not_bind_fails_rule_5() {
        // What a `Texture2D` declared behind a macro, past the lint, compiles to.
        let texture = bind(PSV_RESOURCE_SRV_TYPED, 0, 5, KIND_TEXTURE_2D);
        let mut shader = Shader::variant();
        shader.binds.push(texture);
        let failures = only(Rule::Resources, check(&shader));
        assert_eq!(offenders(&failures), [Offender::Bind(texture)]);
        assert_eq!(
            failures[0].to_string(),
            "rule 5 (resources): typed SRV t5, space0: the original binds nothing there"
        );
    }

    #[test]
    fn a_bind_made_differently_from_the_original_fails_rule_5() {
        let mut cube = Shader::variant();
        cube.binds[2].kind = Some(KIND_TEXTURE_CUBE);
        let failures = only(Rule::Resources, check(&cube));
        assert_eq!(failures[0].detail, "kind 5, the original's 2");

        let mut flagged = Shader::variant();
        flagged.binds[2].flags = Some(1);
        let failures = only(Rule::Resources, check(&flagged));
        assert_eq!(failures[0].detail, "flags 0x1, the original's 0x0");

        let mut wider = Shader::variant();
        wider.binds[3].upper_bound = 4;
        let failures = only(Rule::Resources, check(&wider));
        assert_eq!(failures[0].offender.to_string(), "typed SRV t3..t4, space0");

        // Fewer flags than the original's is no difference.
        let mut game = Shader::game();
        game.binds[2].flags = Some(1);
        let original = PsInterface::read(&game.blob()).expect("reads");
        assert_eq!(
            verify(&Shader::variant().blob(), &original, Space42Set::FULL),
            Ok(())
        );
    }

    #[test]
    fn a_version_0_record_matches_on_its_registers_alone() {
        // Every record in one `PSV0` has one size, so a container cannot mix versions; the
        // comparison is checked directly.
        let original = bind(PSV_RESOURCE_SRV_TYPED, 0, 0, KIND_TEXTURE_2D);
        let unversioned = PsvBind {
            kind: None,
            flags: None,
            ..original
        };
        assert!(same_resource(&unversioned, &original));
        assert!(same_resource(&original, &unversioned));
        let elsewhere = PsvBind {
            lower_bound: 1,
            upper_bound: 1,
            ..unversioned
        };
        assert!(!same_resource(&elsewhere, &original));
    }

    #[test]
    fn space42_binds_pass_only_where_the_root_signature_carries_them() {
        let mut shader = Shader::variant();
        shader.binds.push(editor_bind(Space42Param::Mask));
        shader.binds.push(editor_bind(Space42Param::DrawConstants));
        let blob = shader.blob();
        let original = original();
        assert_eq!(verify(&blob, &original, Space42Set::FULL), Ok(()));
        let expected: [(Level, &[Space42Param]); 3] = [
            (
                Level::CbvProbe,
                &[Space42Param::Mask, Space42Param::DrawConstants],
            ),
            (
                Level::Cbv,
                &[
                    Space42Param::Probe,
                    Space42Param::Mask,
                    Space42Param::DrawConstants,
                ],
            ),
            (Level::None, &Space42Param::ALL),
        ];
        for (level, refused) in expected {
            let carried = Space42Set::carried_by(level);
            let failures = only(Rule::Resources, verify(&blob, &original, carried));
            let mut found = offenders(&failures);
            found.sort_by_key(|offender| offender.to_string());
            let mut wanted: Vec<Offender> = refused
                .iter()
                .map(|param| Offender::Bind(editor_bind(*param)))
                .collect();
            wanted.sort_by_key(|offender| offender.to_string());
            assert_eq!(found, wanted, "{level:?}");
        }
        let failures = only(
            Rule::Resources,
            verify(&blob, &original, Space42Set::carried_by(Level::CbvProbe)),
        );
        assert!(
            failures[0]
                .detail
                .ends_with("not carried by this root signature, which carries A, B"),
            "{}",
            failures[0]
        );
    }

    #[test]
    fn a_space42_register_outside_the_abi_fails_rule_5_whatever_is_carried() {
        let stray = bind(PSV_RESOURCE_UAV_RAW, REGISTER_SPACE, 2, KIND_RAW_BUFFER);
        let mut shader = Shader::variant();
        shader.binds.push(stray);
        let failures = only(Rule::Resources, check(&shader));
        assert_eq!(offenders(&failures), [Offender::Bind(stray)]);
        assert_eq!(
            failures[0].detail,
            "not one of the editor's space42 parameters"
        );
    }

    #[test]
    fn a_feature_the_original_does_without_fails_rule_6() {
        let failures = only(
            Rule::Features,
            check(&Shader {
                sfi0: 0x11,
                ..Shader::variant()
            }),
        );
        assert_eq!(offenders(&failures), [Offender::Features(0x11)]);

        let mut game = Shader::game();
        game.sfi0 = 0x10;
        let original = PsInterface::read(&game.blob()).expect("reads");
        let more = Shader {
            sfi0: 0x11,
            ..Shader::variant()
        };
        let failures = only(
            Rule::Features,
            verify(&more.blob(), &original, Space42Set::FULL),
        );
        assert_eq!(offenders(&failures), [Offender::Features(0x1)]);
        for fewer in [0x10, 0] {
            let shader = Shader {
                sfi0: fewer,
                ..Shader::variant()
            };
            assert_eq!(verify(&shader.blob(), &original, Space42Set::FULL), Ok(()));
        }
    }

    #[test]
    fn an_embedded_root_signature_fails_rule_7() {
        let mut shader = Shader::variant();
        shader.extra.push((*ROOT_SIGNATURE_PART, vec![0; 16]));
        let failures = only(Rule::RootSignature, check(&shader));
        assert_eq!(offenders(&failures), [Offender::Part(*ROOT_SIGNATURE_PART)]);
    }

    #[test]
    fn the_callers_half_of_rule_7_wants_the_records_own_root_signature() {
        let recorded = 0x0000_02a0_1234_5670;
        assert_eq!(check_root_signature(recorded, recorded), Ok(()));
        let other = check_root_signature(recorded, recorded + 0x100).expect_err("another one");
        assert_eq!(
            (other.rule, other.offender.clone()),
            (Rule::RootSignature, Offender::RootSignature)
        );
        assert!(check_root_signature(0, 0).is_err());
        let outcomes = outcomes(&[other]);
        assert_eq!(outcomes[6], (Rule::RootSignature, Outcome::Refused));
        assert!(
            outcomes[..6]
                .iter()
                .all(|(_, outcome)| *outcome == Outcome::Passed)
        );
    }

    #[test]
    fn every_failure_is_reported_not_only_the_first() {
        let mut shader = Shader {
            sfi0: 0x1,
            ..Shader::variant()
        };
        shader.inputs[1].2 = 2;
        shader
            .binds
            .push(bind(PSV_RESOURCE_SRV_TYPED, 0, 5, KIND_TEXTURE_2D));
        let failures = check(&shader).expect_err("refused");
        let rules: Vec<Rule> = failures.iter().map(|failure| failure.rule).collect();
        assert_eq!(rules, [Rule::Linkage, Rule::Resources, Rule::Features]);
        let report = report(&failures);
        assert_eq!(
            report,
            "verified 4 of 7\n\
             1 container: passed\n\
             2 signed: passed\n\
             3 linkage: refused\n  \
             ISG1 element 1 (TEXCOORD): register 2, the original's 1\n\
             4 outputs: passed\n\
             5 resources: refused\n  \
             typed SRV t5, space0: the original binds nothing there\n\
             6 features: refused\n  \
             SFI0 bits 0x1: SFI0 0x1, the original's 0x0\n\
             7 root signature: passed\n"
        );
    }

    #[test]
    fn rules_are_numbered_as_the_design_numbers_them() {
        let numbered: Vec<(u8, &str)> = Rule::ALL
            .iter()
            .map(|rule| (rule.number(), rule.name()))
            .collect();
        assert_eq!(
            numbered,
            [
                (1, "container"),
                (2, "signed"),
                (3, "linkage"),
                (4, "outputs"),
                (5, "resources"),
                (6, "features"),
                (7, "root signature"),
            ]
        );
    }

    #[test]
    fn each_level_carries_the_first_of_a_to_d() {
        assert_eq!(Space42Set::carried_by(Level::None), Space42Set::NONE);
        for (level, count) in [
            (Level::None, 0),
            (Level::Cbv, 1),
            (Level::CbvProbe, 2),
            (Level::Full, 4),
        ] {
            let set = Space42Set::carried_by(level);
            assert_eq!(count, level.parameters() as usize);
            for (index, param) in Space42Param::ALL.into_iter().enumerate() {
                assert_eq!(set.contains(param), index < count, "{level:?} {param:?}");
                assert_eq!(set.admits(&editor_bind(param)), index < count);
            }
        }
        assert_eq!(Space42Set::FULL.to_string(), "A, B, C, D");
        assert_eq!(Space42Set::carried_by(Level::CbvProbe).to_string(), "A, B");
        assert_eq!(Space42Set::NONE.to_string(), "none");
        // A parameter at another register, or as another type, is not that parameter.
        let mut moved = editor_bind(Space42Param::Probe);
        moved.lower_bound = 2;
        moved.upper_bound = 2;
        assert!(!Space42Set::FULL.admits(&moved));
        let mut retyped = editor_bind(Space42Param::Params);
        retyped.resource_type = PSV_RESOURCE_SRV_TYPED;
        assert!(!Space42Set::FULL.admits(&retyped));
    }

    #[test]
    fn the_interface_is_read_from_the_original_and_needs_its_interpolation() {
        let interface = original();
        assert_eq!(interface.inputs.len(), 2);
        assert_eq!(interface.outputs.len(), 1);
        assert_eq!(interface.binds, Shader::game().binds);
        assert_eq!(interface.interpolation.inputs, [NO_PERSPECTIVE, LINEAR]);
        assert_eq!(interface.sfi0, 0);
        assert_eq!(interface.debug_name, None);

        let game = Shader::game();
        let no_modes = signed(container(&[
            (*SFI0_PART, vec![0; 8]),
            (*INPUT_SIGNATURE_PART, signature_part(&game.inputs)),
            (*OUTPUT_SIGNATURE_PART, signature_part(&game.outputs)),
            (*PSV0_PART, psv0_version_0()),
        ]));
        assert!(PsInterface::read(&no_modes).is_err());
        assert!(PsInterface::read(b"DXBC").is_err());
    }

    /// A port's own interface less its space42 binds: what its game original shows, as far as can
    /// be told without the dump, since each port binds exactly its original's resources and A and
    /// B (`dxbc::tests::every_port_writes_the_probe_and_binds_nothing_else_in_space42`).
    fn port_interface(kind: PortKind) -> PsInterface {
        let mut interface = PsInterface::read(kind.replacement().dxil).expect("a port reads");
        interface.binds.retain(|bind| bind.space != REGISTER_SPACE);
        interface
    }

    #[test]
    fn every_port_passes_on_a_root_signature_that_carries_a_and_b() {
        for kind in PortKind::ALL {
            let port = kind.replacement().dxil;
            let interface = port_interface(kind);
            assert_eq!(
                verify(port, &interface, Space42Set::FULL),
                Ok(()),
                "{kind:?}"
            );
            assert_eq!(
                verify(port, &interface, Space42Set::carried_by(Level::CbvProbe)),
                Ok(()),
                "{kind:?}"
            );
            let failures = only(
                Rule::Resources,
                verify(port, &interface, Space42Set::carried_by(Level::Cbv)),
            );
            assert_eq!(
                offenders(&failures),
                [Offender::Bind(editor_bind(Space42Param::Probe))],
                "{kind:?}"
            );
            let failures = only(Rule::Resources, verify(port, &interface, Space42Set::NONE));
            assert_eq!(failures.len(), 2, "{kind:?}");
        }
    }

    #[test]
    fn a_port_never_stands_in_for_another_pass() {
        // Measured from their signatures: AO writes SV_Target0 and SV_Target1, the albedo pass
        // SV_Target0 alone; the tonemap reads two inputs where AO reads three.
        let failures = verify(
            PortKind::Ao.replacement().dxil,
            &port_interface(PortKind::CmbAlbedo),
            Space42Set::FULL,
        )
        .expect_err("refused");
        assert!(
            failures.iter().any(|failure| failure.rule == Rule::Outputs
                && failure.offender == element(OUTPUT_SIGNATURE_PART, 1, "SV_Target", 1)),
            "{failures:#?}"
        );
        let failures = verify(
            PortKind::Tonemap.replacement().dxil,
            &port_interface(PortKind::Ao),
            Space42Set::FULL,
        )
        .expect_err("refused");
        assert!(
            failures.iter().any(|failure| failure.rule == Rule::Linkage),
            "{failures:#?}"
        );
    }

    /// The vkd3d-proton shader dump, a test input only: `$ER_VKD3D_DUMP_ROOT` when set, else
    /// `.worktrees/er-shaders/target/vkd3d-dump/run1/spv` under the nearest checkout that has it,
    /// as `dxbc`'s corpus test finds it.
    fn vkd3d_dump_root() -> Option<PathBuf> {
        if let Ok(root) = std::env::var("ER_VKD3D_DUMP_ROOT")
            && !root.trim().is_empty()
        {
            return Some(PathBuf::from(root)).filter(|root| root.is_dir());
        }
        let relative = Path::new(".worktrees/er-shaders/target/vkd3d-dump/run1/spv");
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .map(|dir| dir.join(relative))
            .find(|candidate| candidate.is_dir())
    }

    #[test]
    fn each_port_passes_against_its_game_original() {
        let Some(root) = vkd3d_dump_root() else {
            eprintln!(
                "SKIP: no vkd3d shader dump (set ER_VKD3D_DUMP_ROOT); originals not compared"
            );
            return;
        };
        let mut compared = 0;
        for replacement in REPLACEMENTS {
            let path = root.join(format!("{:016x}.dxil", replacement.original_fnv1));
            let Ok(original) = std::fs::read(&path) else {
                eprintln!("SKIP: {} absent from {}", replacement.name, root.display());
                continue;
            };
            assert_eq!(
                (original.len(), fnv1_64(&original)),
                (replacement.original_len, replacement.original_fnv1),
                "{}",
                path.display()
            );
            let interface = PsInterface::read(&original).expect("the original reads");
            assert!(
                interface
                    .debug_name
                    .as_deref()
                    .is_some_and(|name| name.ends_with(".ppo.pdb")),
                "{:?}",
                interface.debug_name
            );
            assert_eq!(
                verify(replacement.dxil, &interface, Space42Set::FULL),
                Ok(()),
                "{}",
                replacement.name
            );
            compared += 1;
        }
        assert!(
            compared > 0,
            "{} holds none of the originals",
            root.display()
        );
    }
}
