//! The rules a patched vertex shader passes before any pipeline is created from it
//! (`docs/plans/er-shader-editor-vertex.md`, section 7.4). Pure, so all of it is tested on the
//! host.
//!
//! | Rule | Name | The variant passes when |
//! |---|---|---|
//! | 1 | container | a `DXBC` container of exactly its own length, at most [`MAX_BLOB_BYTES`], each part once, a vertex program of the original's shader model |
//! | 2 | signed | its digest is set and is the retail hash |
//! | 3 | input linkage | `ISG1` equals the original's element for element, and `PSV0`'s input records equal it matched by the slot each starts in |
//! | 4 | output linkage | `OSG1` likewise, `PSV0`'s output records likewise, and the position-output flag |
//! | 5 | resources | every `PSV0` bind is one the original has, or `CBV b0, space42` on a root signature whose A the vertex shader can read |
//! | 6 | features | `SFI0` asks for nothing the original's does not |
//! | 7 | root signature | it embeds none, and the variant is created with the record's own |
//! | 8 | residual | the helper's: the variant's text with the splice taken out is the original's ([`er_shader_compiler::patch`]) |
//! | 9 | stream | the variant's stream is the record's in every byte but the vertex shader's pointer and length, and the record has no stream output and no view instancing |
//!
//! `PSV0` records follow the order the signature metadata declares elements in, which in 481 of
//! 2303 game vertex shaders is not register order (design section 15), so rules 3 and 4 match them
//! by slot and never by index.

use core::fmt;
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::ops::Range;

use crate::dxbc::{
    Container, DxbcError, INPUT_SIGNATURE_PART, OUTPUT_SIGNATURE_PART, PSV_RESOURCE_CBV, PsvBind,
    PsvElement, ROOT_SIGNATURE_PART, ShaderKind, SigElement,
};
use crate::look_binding::{REGISTER_SPACE, register};

/// The largest variant the rules accept: a game vertex shader is tens of kilobytes.
pub const MAX_BLOB_BYTES: usize = 1 << 20;

/// One of the nine checks, numbered as design 7.4 numbers them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(u8)]
pub enum VertexRule {
    Container = 1,
    Signed = 2,
    InputLinkage = 3,
    OutputLinkage = 4,
    Resources = 5,
    Features = 6,
    RootSignature = 7,
    Residual = 8,
    Stream = 9,
}

impl VertexRule {
    pub const ALL: [Self; 9] = [
        Self::Container,
        Self::Signed,
        Self::InputLinkage,
        Self::OutputLinkage,
        Self::Resources,
        Self::Features,
        Self::RootSignature,
        Self::Residual,
        Self::Stream,
    ];

    #[must_use]
    pub const fn number(self) -> u8 {
        self as u8
    }

    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Container => "container",
            Self::Signed => "signed",
            Self::InputLinkage => "input linkage",
            Self::OutputLinkage => "output linkage",
            Self::Resources => "resources",
            Self::Features => "features",
            Self::RootSignature => "root signature",
            Self::Residual => "residual",
            Self::Stream => "stream",
        }
    }
}

/// A rule a variant failed, and why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VertexFailure {
    pub rule: VertexRule,
    pub detail: String,
}

impl VertexFailure {
    fn new(rule: VertexRule, detail: impl Into<String>) -> Self {
        Self {
            rule,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for VertexFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "rule {} ({}): {}",
            self.rule.number(),
            self.rule.name(),
            self.detail
        )
    }
}

/// What a game vertex shader shows the pipeline around it: the parts of its container the rules
/// compare a variant with. Read once per distinct shader.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct VsInterface {
    pub major: u32,
    pub minor: u32,
    pub inputs: Vec<SigElement>,
    pub outputs: Vec<SigElement>,
    pub psv_inputs: Vec<PsvElement>,
    pub psv_outputs: Vec<PsvElement>,
    pub position_out: u8,
    pub binds: Vec<PsvBind>,
    pub sfi0: u64,
    /// The `ILDN` part as the original carries it, copied into each variant.
    pub debug_name: Option<Vec<u8>>,
}

impl VsInterface {
    /// The interface of the vertex shader in `blob`.
    ///
    /// # Errors
    ///
    /// The bytes are not a vertex shader container whose signatures, `PSV0` (version 1 or later)
    /// and `SFI0` read.
    pub fn read(blob: &[u8]) -> Result<Self, DxbcError> {
        let container = Container::parse(blob)?;
        let program = container.program()?;
        if program.kind != ShaderKind::Vertex {
            return Err(DxbcError::Malformed("not a vertex shader"));
        }
        let psv0 = container.psv0()?;
        let elements = psv0.elements.ok_or(DxbcError::Malformed(
            "PSV0 runtime info version 0 carries no signature elements",
        ))?;
        Ok(Self {
            major: program.major,
            minor: program.minor,
            inputs: container.signature(INPUT_SIGNATURE_PART)?,
            outputs: container.signature(OUTPUT_SIGNATURE_PART)?,
            psv_inputs: elements.inputs,
            psv_outputs: elements.outputs,
            position_out: psv0.stage_info,
            binds: psv0.binds,
            sfi0: container.sfi0()?,
            debug_name: container
                .part(crate::dxbc::DEBUG_NAME_PART)
                .map(<[u8]>::to_vec),
        })
    }
}

/// Is `bind` the parameter block, `CBV b0, space42`?
fn is_params(bind: &PsvBind) -> bool {
    bind.resource_type == PSV_RESOURCE_CBV
        && bind.space == REGISTER_SPACE
        && bind.lower_bound == register::PARAMS_CBV
        && bind.upper_bound == register::PARAMS_CBV
}

/// The same registers, read as the same kind of resource, with no flag the original lacks.
fn same_resource(ours: &PsvBind, theirs: &PsvBind) -> bool {
    let kind = match (ours.kind, theirs.kind) {
        (Some(ours), Some(theirs)) => ours == theirs,
        _ => true,
    };
    let flags = match (ours.flags, theirs.flags) {
        (Some(ours), Some(theirs)) => ours & !theirs == 0,
        _ => true,
    };
    ours.resource_type == theirs.resource_type
        && ours.space == theirs.space
        && ours.lower_bound == theirs.lower_bound
        && ours.upper_bound == theirs.upper_bound
        && kind
        && flags
}

/// `PSV0` records keyed by the slot each starts in and its semantic kind, which tells apart two
/// unallocated system values that both report slot 0.
fn by_slot(elements: &[PsvElement]) -> BTreeMap<(u8, u8, u8), PsvElement> {
    elements
        .iter()
        .map(|element| {
            let (row, column) = element.slot();
            ((row, column, element.semantic_kind), *element)
        })
        .collect()
}

fn check_records(
    rule: VertexRule,
    what: &str,
    ours: &[PsvElement],
    theirs: &[PsvElement],
    failures: &mut Vec<VertexFailure>,
) {
    let (ours, theirs) = (by_slot(ours), by_slot(theirs));
    if ours != theirs {
        let differing: Vec<String> = ours
            .keys()
            .chain(theirs.keys())
            .filter(|key| ours.get(key) != theirs.get(key))
            .map(|(row, column, kind)| format!("row {row} column {column} kind {kind}"))
            .collect();
        failures.push(VertexFailure::new(
            rule,
            format!("PSV0 {what} records differ at {}", differing.join(", ")),
        ));
    }
}

fn check_signature(
    rule: VertexRule,
    code: &[u8; 4],
    container: &Container<'_>,
    theirs: &[SigElement],
    failures: &mut Vec<VertexFailure>,
) {
    match container.signature(code) {
        Ok(ours) if ours == theirs => {}
        Ok(ours) => {
            let first = ours
                .iter()
                .zip(theirs)
                .position(|(a, b)| a != b)
                .unwrap_or(ours.len().min(theirs.len()));
            failures.push(VertexFailure::new(
                rule,
                format!(
                    "{} differs from element {first} on ({} elements, the original's {})",
                    code.escape_ascii(),
                    ours.len(),
                    theirs.len()
                ),
            ));
        }
        Err(error) => failures.push(VertexFailure::new(
            rule,
            format!("{}: {error}", code.escape_ascii()),
        )),
    }
}

/// Rules 1 to 6 and the blob's half of rule 7 for `candidate` standing in for the vertex shader
/// whose interface is `original`, in a pipeline whose root signature lets the vertex shader read
/// A when `a_vertex`. Every failure, ordered by rule; none when it passes.
#[must_use]
pub fn verify_vertex(
    candidate: &[u8],
    original: &VsInterface,
    a_vertex: bool,
) -> Vec<VertexFailure> {
    let container = match Container::parse(candidate) {
        Ok(container) => container,
        Err(error) => {
            return vec![VertexFailure::new(
                VertexRule::Container,
                format!("not a container: {error}"),
            )];
        }
    };
    let mut failures = Vec::new();
    let mut fail = |rule, detail: String| failures.push(VertexFailure::new(rule, detail));
    if container.total_size() != candidate.len() {
        fail(
            VertexRule::Container,
            format!(
                "{} bytes, the header declares {}",
                candidate.len(),
                container.total_size()
            ),
        );
    }
    if candidate.len() > MAX_BLOB_BYTES {
        fail(
            VertexRule::Container,
            format!("{} bytes, more than {MAX_BLOB_BYTES}", candidate.len()),
        );
    }
    let mut seen: Vec<[u8; 4]> = Vec::new();
    for part in container.parts() {
        if seen.contains(&part.code) {
            fail(
                VertexRule::Container,
                format!("the {} part appears twice", part.code.escape_ascii()),
            );
        }
        seen.push(part.code);
    }
    match container.program() {
        Ok(program) if program.kind != ShaderKind::Vertex => fail(
            VertexRule::Container,
            format!("a {:?} program, not a vertex shader", program.kind),
        ),
        Ok(program) if (program.major, program.minor) != (original.major, original.minor) => fail(
            VertexRule::Container,
            format!(
                "shader model {}.{}, the original's {}.{}",
                program.major, program.minor, original.major, original.minor
            ),
        ),
        Ok(_) => {}
        Err(error) => fail(VertexRule::Container, error.to_string()),
    }
    let digest = container.digest();
    if digest == [0; 16] {
        fail(VertexRule::Signed, "the digest is zero".to_owned());
    } else if !container.is_signed_correctly() {
        fail(
            VertexRule::Signed,
            "the digest is not the container's retail hash".to_owned(),
        );
    }
    check_signature(
        VertexRule::InputLinkage,
        INPUT_SIGNATURE_PART,
        &container,
        &original.inputs,
        &mut failures,
    );
    check_signature(
        VertexRule::OutputLinkage,
        OUTPUT_SIGNATURE_PART,
        &container,
        &original.outputs,
        &mut failures,
    );
    match container.psv0() {
        Ok(psv0) => {
            match &psv0.elements {
                Some(elements) => {
                    check_records(
                        VertexRule::InputLinkage,
                        "input",
                        &elements.inputs,
                        &original.psv_inputs,
                        &mut failures,
                    );
                    check_records(
                        VertexRule::OutputLinkage,
                        "output",
                        &elements.outputs,
                        &original.psv_outputs,
                        &mut failures,
                    );
                }
                None => failures.push(VertexFailure::new(
                    VertexRule::InputLinkage,
                    "PSV0 runtime info version 0 records no signature elements",
                )),
            }
            if psv0.stage_info != original.position_out {
                failures.push(VertexFailure::new(
                    VertexRule::OutputLinkage,
                    format!(
                        "the position-output flag is {}, the original's {}",
                        psv0.stage_info, original.position_out
                    ),
                ));
            }
            for bind in &psv0.binds {
                if (is_params(bind) && a_vertex)
                    || original
                        .binds
                        .iter()
                        .any(|theirs| same_resource(bind, theirs))
                {
                    continue;
                }
                let why = if is_params(bind) {
                    "the parameter block, on a root signature whose A the vertex shader cannot read"
                } else {
                    "the original binds nothing there"
                };
                failures.push(VertexFailure::new(
                    VertexRule::Resources,
                    format!(
                        "type {} space {} registers {}..{}: {why}",
                        bind.resource_type, bind.space, bind.lower_bound, bind.upper_bound
                    ),
                ));
            }
        }
        Err(error) => failures.push(VertexFailure::new(
            VertexRule::Resources,
            format!("PSV0: {error}"),
        )),
    }
    match container.sfi0() {
        Ok(ours) if ours & !original.sfi0 != 0 => failures.push(VertexFailure::new(
            VertexRule::Features,
            format!("SFI0 {ours:#x}, the original's {:#x}", original.sfi0),
        )),
        Ok(_) => {}
        Err(error) => failures.push(VertexFailure::new(VertexRule::Features, error.to_string())),
    }
    if container.part(ROOT_SIGNATURE_PART).is_some() {
        failures.push(VertexFailure::new(
            VertexRule::RootSignature,
            "embeds a root signature, where a variant takes the record's own",
        ));
    }
    failures.sort_by_key(|failure| failure.rule);
    failures
}

/// Rule 9 and the caller's half of rule 7: `variant` is the stream a variant is created from,
/// `record` the stream the game's pipeline was, `vs` the byte range of the vertex shader's
/// payload in both. `so_entries` and `views` are the record's stream-output declarations and
/// instanced views; `root_recorded` and `root_used` its root signature and the one in `variant`.
#[must_use]
pub fn check_stream(
    record: &[u8],
    variant: &[u8],
    vs: Range<usize>,
    so_entries: u32,
    views: u32,
    root_recorded: usize,
    root_used: usize,
) -> Vec<VertexFailure> {
    let mut failures = Vec::new();
    if record.len() != variant.len() {
        failures.push(VertexFailure::new(
            VertexRule::Stream,
            format!("{} bytes, the record's {}", variant.len(), record.len()),
        ));
    } else if vs.end > record.len() || vs.start > vs.end {
        failures.push(VertexFailure::new(
            VertexRule::Stream,
            "the vertex shader's payload lies outside the stream",
        ));
    } else {
        let outside = (0..record.len()).filter(|at| !vs.contains(at));
        if let Some(at) = outside.into_iter().find(|&at| record[at] != variant[at]) {
            failures.push(VertexFailure::new(
                VertexRule::Stream,
                format!("byte {at} differs from the record's"),
            ));
        }
    }
    if so_entries != 0 {
        failures.push(VertexFailure::new(
            VertexRule::Stream,
            format!("the record streams out {so_entries} declarations"),
        ));
    }
    if views != 0 {
        failures.push(VertexFailure::new(
            VertexRule::Stream,
            format!("the record instances {views} views"),
        ));
    }
    if root_recorded == 0 {
        failures.push(VertexFailure::new(
            VertexRule::RootSignature,
            "the record names no root signature",
        ));
    } else if root_used != root_recorded {
        failures.push(VertexFailure::new(
            VertexRule::RootSignature,
            format!("{root_used:#x}, the record's {root_recorded:#x}"),
        ));
    }
    failures
}

/// The rule-by-rule report for the log and the diagnostics: a count, then one line per rule, each
/// failure under its rule. `residual` is rule 8's outcome, which the helper decided.
#[must_use]
pub fn report(failures: &[VertexFailure], residual: bool) -> String {
    let refused = |rule: VertexRule| {
        failures.iter().any(|failure| failure.rule == rule)
            || (rule == VertexRule::Residual && !residual)
    };
    let passed = VertexRule::ALL
        .iter()
        .filter(|rule| !refused(**rule))
        .count();
    let mut out = format!("verified {passed} of {}\n", VertexRule::ALL.len());
    for rule in VertexRule::ALL {
        let word = if refused(rule) { "refused" } else { "passed" };
        let _ = writeln!(out, "{} {}: {word}", rule.number(), rule.name());
        for failure in failures.iter().filter(|failure| failure.rule == rule) {
            let _ = writeln!(out, "  {}", failure.detail);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dxbc::{DEBUG_NAME_PART, PSV0_PART, SFI0_PART, with_part};
    use crate::replace::REPLACEMENTS;

    /// A pixel port is no vertex shader; its interface stands in for the reading tests.
    fn port() -> &'static [u8] {
        REPLACEMENTS[0].dxil
    }

    fn interface_of(blob: &[u8]) -> VsInterface {
        let container = Container::parse(blob).expect("a container");
        let program = container.program().expect("a program");
        let psv0 = container.psv0().expect("PSV0");
        let elements = psv0.elements.clone().expect("version 1");
        VsInterface {
            major: program.major,
            minor: program.minor,
            inputs: container.signature(INPUT_SIGNATURE_PART).expect("ISG1"),
            outputs: container.signature(OUTPUT_SIGNATURE_PART).expect("OSG1"),
            psv_inputs: elements.inputs,
            psv_outputs: elements.outputs,
            position_out: psv0.stage_info,
            binds: psv0.binds,
            sfi0: container.sfi0().expect("SFI0"),
            debug_name: None,
        }
    }

    #[test]
    fn a_pixel_shader_is_not_read_as_a_vertex_shader() {
        assert!(VsInterface::read(port()).is_err());
        assert!(VsInterface::read(b"junk").is_err());
    }

    #[test]
    fn a_container_against_itself_fails_only_for_being_no_vertex_shader() {
        let blob = port();
        let failures = verify_vertex(blob, &interface_of(blob), true);
        assert_eq!(failures.len(), 1, "{failures:?}");
        assert_eq!(failures[0].rule, VertexRule::Container);
        assert!(failures[0].detail.contains("not a vertex shader"));
    }

    #[test]
    fn an_unsigned_or_padded_variant_is_refused() {
        let blob = port().to_vec();
        let interface = interface_of(&blob);
        let mut unsigned = blob.clone();
        unsigned[4..20].fill(0);
        let rules: Vec<VertexRule> = verify_vertex(&unsigned, &interface, true)
            .iter()
            .map(|f| f.rule)
            .collect();
        assert!(rules.contains(&VertexRule::Signed));
        let mut padded = blob;
        padded.push(0);
        assert!(
            verify_vertex(&padded, &interface, true)
                .iter()
                .any(|f| f.rule == VertexRule::Container && f.detail.contains("declares"))
        );
        assert_eq!(
            verify_vertex(b"junk", &interface, true)[0].rule,
            VertexRule::Container
        );
    }

    #[test]
    fn the_parameter_block_needs_a_vertex_readable_a_and_nothing_else_is_admitted() {
        let blob = port();
        let interface = interface_of(blob);
        // The port binds b0, space42; an original that does not has it admitted only as A.
        let mut without = interface.clone();
        without.binds.retain(|bind| !is_params(bind));
        let readable = verify_vertex(blob, &without, true);
        assert!(
            readable.iter().all(|f| f.rule != VertexRule::Resources),
            "{readable:?}"
        );
        let unreadable = verify_vertex(blob, &without, false);
        assert!(
            unreadable
                .iter()
                .any(|f| f.rule == VertexRule::Resources && f.detail.contains("cannot read"))
        );
        // Any other bind the original lacks is refused whatever A is.
        let mut bare = interface;
        bare.binds.clear();
        assert!(
            verify_vertex(blob, &bare, true)
                .iter()
                .any(|f| f.rule == VertexRule::Resources && f.detail.contains("binds nothing"))
        );
    }

    #[test]
    fn a_feature_or_a_root_signature_the_original_lacks_is_refused() {
        let blob = port();
        let mut interface = interface_of(blob);
        let with_rts = with_part(blob, ROOT_SIGNATURE_PART, &[1, 0, 0, 0]).expect("rebuilt");
        assert!(
            verify_vertex(&with_rts, &interface, true)
                .iter()
                .any(|f| f.rule == VertexRule::RootSignature)
        );
        let with_feature = with_part(blob, SFI0_PART, &1_u64.to_le_bytes()).expect("rebuilt");
        interface.sfi0 = 0;
        assert!(
            verify_vertex(&with_feature, &interface, true)
                .iter()
                .any(|f| f.rule == VertexRule::Features)
        );
        // The debug name copied in changes no rule: it is the part the worker adds.
        let named = with_part(blob, DEBUG_NAME_PART, &[0, 0, 1, 0, b'x', 0, 0, 0]).expect("named");
        let failures = verify_vertex(&named, &interface_of(blob), true);
        assert!(
            failures.iter().all(|f| f.rule == VertexRule::Container),
            "{failures:?}"
        );
    }

    #[test]
    fn psv0_records_are_matched_by_slot_not_by_order() {
        let interface = interface_of(port());
        let mut reordered = interface.psv_outputs.clone();
        reordered.reverse();
        let mut failures = Vec::new();
        check_records(
            VertexRule::OutputLinkage,
            "output",
            &reordered,
            &interface.psv_outputs,
            &mut failures,
        );
        assert!(failures.is_empty(), "{failures:?}");
        let mut moved = interface.psv_outputs.clone();
        moved[0].interpolation ^= 1;
        check_records(
            VertexRule::OutputLinkage,
            "output",
            &moved,
            &interface.psv_outputs,
            &mut failures,
        );
        assert_eq!(failures.len(), 1);
        assert!(failures[0].detail.contains("row"));
        let _ = PSV0_PART;
    }

    #[test]
    fn the_stream_may_differ_in_the_vertex_shader_alone() {
        let record: Vec<u8> = (0..64).collect();
        let mut variant = record.clone();
        variant[16..32].fill(0xee);
        assert!(check_stream(&record, &variant, 16..32, 0, 0, 7, 7).is_empty());
        variant[40] ^= 1;
        let failures = check_stream(&record, &variant, 16..32, 0, 0, 7, 7);
        assert_eq!(failures.len(), 1);
        assert!(failures[0].detail.contains("byte 40"));
        let failures = check_stream(&record, &record, 16..32, 2, 1, 0, 0);
        let rules: Vec<VertexRule> = failures.iter().map(|f| f.rule).collect();
        assert_eq!(
            rules,
            [
                VertexRule::Stream,
                VertexRule::Stream,
                VertexRule::RootSignature
            ]
        );
        assert_eq!(
            check_stream(&record, &record, 16..32, 0, 0, 7, 8)[0].rule,
            VertexRule::RootSignature
        );
        assert_eq!(
            check_stream(&record, &record[..63], 16..32, 0, 0, 7, 7)[0].rule,
            VertexRule::Stream
        );
    }

    #[test]
    fn the_report_counts_rule_8_from_the_helper() {
        let clean = report(&[], true);
        assert!(clean.starts_with("verified 9 of 9\n"), "{clean}");
        let residual = report(&[], false);
        assert!(residual.starts_with("verified 8 of 9\n"));
        assert!(residual.contains("8 residual: refused"));
        let failures = [VertexFailure::new(VertexRule::Stream, "byte 4")];
        let text = report(&failures, true);
        assert!(text.contains("9 stream: refused\n  byte 4"));
        assert_eq!(failures[0].to_string(), "rule 9 (stream): byte 4");
    }
}
