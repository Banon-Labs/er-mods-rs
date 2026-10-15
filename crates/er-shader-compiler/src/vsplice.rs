//! Splicing a vertex-layer carrier into a game vertex shader, as DXIL text (design
//! `docs/plans/er-shader-editor-vertex.md`, section 7.3). Pure text in and text out, so all of it
//! is tested on the host.
//!
//! # The two inputs
//!
//! Both are the LLVM IR text DXC's disassembler prints (`dxc -dumpbin`, or
//! `IDxcCompiler::Disassemble` in the helper):
//!
//! - the game's vertex shader, which this module never needs to understand beyond its position
//!   loads, its resources and, for a velocity pass, which outputs read which view-projection;
//! - a carrier: the user's vertex layers compiled once by DXC as a `vs_6_0` shader whose only
//!   inputs are `ER_POS` (the stored position, `float3`), `ER_TIME` (`float`) and `ER_FRAME`
//!   (`uint`), whose only output is `ER_OUT` (the moved position, `float3`), and whose only
//!   resource is the editor's parameter block at `b0, space42`. [`check_carrier`] holds it to
//!   that, and to pure arithmetic.
//!
//! # The edit
//!
//! 1. Find the game's `POSITION0` input element through `!dx.entryPoints` (element ids are
//!    declaration order, not register order), and its three `loadInput.f32` columns, which must
//!    all sit in the entry block. The insertion point is after the last of them.
//! 2. Add `CBV b0, space42` to `!dx.resources` as the next constant-buffer range, and create one
//!    handle to it at the insertion point (the prelude), with `er_p[3].x` as the time and
//!    `er_p[3].y` as the frame.
//! 3. Copy the carrier's code there as instance 0. Its values are renamed `%er_c0_<n>` and its
//!    blocks `er_c0_b<n>`; its loads of `ER_POS` become the game's loaded x, y and z, its time and
//!    frame the prelude's, and its handle the prelude's. What it stores to `ER_OUT` becomes
//!    `%er_c0_x`, `%er_c0_y` and `%er_c0_z`, except a column it stores unchanged.
//! 4. A carrier with more than one block splits the game's entry block: the game's remaining
//!    instructions move to `er_c<k>_exit`, and every game phi that names the entry block names that
//!    block instead.
//! 5. Every later use of the game's x, y and z reads the carrier's output instead. Nothing before
//!    the insertion point uses them; a shader where something does is refused.
//! 6. For a velocity pass -- a shader with exactly one output whose slice reads the scene buffer's
//!    previous view-projection (`b8` registers 29-32) and not the current one (24-27) -- a second
//!    instance runs at the previous time, `er_p[32]`, on the input that slice reads: `POSITION0`,
//!    or `POSITION1` where cloth and hair carry a previous-position stream. Every instruction of the
//!    slice that depends on a moved input is cloned right after itself as `%er_pv_<n>`, iterating
//!    to a fixed point for loop phis, and that output's stores take the clones.
//! 7. Declarations the game lacks are added with an attribute group of the same content (matched by
//!    content, never by number), and so are the types.
//!
//! Every name this module adds starts with `er_`, and nothing else it does changes a game line
//! beyond the substitutions [`SpliceMap`] records, which is what [`residual`] undoes (rule 8).
//!
//! # Refusals
//!
//! A shader this cannot patch is refused with a [`Refusal`] and stays vanilla: no `POSITION0`,
//! position loads outside the entry block or of only some columns, no `!dx.resources`, or a
//! carrier [`check_carrier`] refuses. A velocity pass whose previous position cannot be given the
//! previous time is still patched; [`PreviousOutcome::Skipped`] says why its motion vectors carry
//! only the camera's motion.

use core::fmt;
use core::fmt::Write as _;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use er_game_base::fnv1a::{FNV1A64_OFFSET_BASIS, fnv1a64_extend};

/// The register space of the editor's parameter block.
pub const PARAMS_SPACE: i64 = 42;
/// Its register, `b0`.
pub const PARAMS_REGISTER: i64 = 0;
/// Its size: 1024 registers of 16 bytes, all of which the DLL writes every present.
pub const PARAMS_BYTES: i64 = 16384;
/// `er_p[3]`: (time in seconds, asfloat(frame), display width, display height).
pub const TIME_REGISTER: i64 = 3;
/// `er_p[32]`: (the previous present's time, asfloat(previous frame), proof mode, 0).
pub const PREVIOUS_TIME_REGISTER: i64 = 32;

/// The carrier's position input.
pub const CARRIER_POSITION: &str = "ER_POS";
/// The carrier's time input.
pub const CARRIER_TIME: &str = "ER_TIME";
/// The carrier's frame input.
pub const CARRIER_FRAME: &str = "ER_FRAME";
/// The carrier's one output.
pub const CARRIER_OUTPUT: &str = "ER_OUT";

/// The type the added constant buffer is declared with in the game shader. The carrier's own name
/// for it is not copied, so it can never collide with a game type.
const PARAMS_TYPE: &str = "%er.EditorParams";
const PARAMS_TYPE_DEFINITION: &str = "%er.EditorParams = type { [1024 x <4 x float>] }";

/// DXIL opcodes this module reads or writes.
const OP_LOAD_INPUT: i64 = 4;
const OP_STORE_OUTPUT: i64 = 5;
const OP_CREATE_HANDLE: i64 = 57;
const OP_CBUFFER_LOAD_LEGACY: i64 = 59;
/// `dx.op.fma`, which exists for doubles only.
const OP_FMA: i64 = 47;
const OP_LEGACY_F32_TO_F16: i64 = 130;
const OP_LEGACY_F16_TO_F32: i64 = 131;

/// `DXIL::ComponentType`: 32-bit float, and the two 32-bit integers.
const COMPONENT_F32: i64 = 9;
const COMPONENT_I32: i64 = 4;
const COMPONENT_U32: i64 = 5;

/// The resource class of a constant buffer in `createHandle`.
const CLASS_CBV: i64 = 2;

/// The LLVM instructions a carrier may use: arithmetic, comparisons, conversions, control flow and
/// calls, which [`check_carrier`] narrows to the pure `dx.op` functions. No memory instruction.
const CARRIER_INSTRUCTIONS: [&str; 37] = [
    "fadd",
    "fsub",
    "fmul",
    "fdiv",
    "frem",
    "add",
    "sub",
    "mul",
    "udiv",
    "sdiv",
    "urem",
    "srem",
    "shl",
    "lshr",
    "ashr",
    "and",
    "or",
    "xor",
    "fcmp",
    "icmp",
    "select",
    "phi",
    "extractvalue",
    "insertvalue",
    "sitofp",
    "uitofp",
    "fptosi",
    "fptoui",
    "bitcast",
    "zext",
    "sext",
    "trunc",
    "fpext",
    "fptrunc",
    "call",
    "br",
    "ret",
];

/// Types a carrier may not use: doubles, minimum precision, and the 16- and 64-bit integers,
/// which each need a shader feature the game's vertex shaders do not ask for.
const CARRIER_TYPES_REFUSED: [&str; 4] = ["double", "half", "i16", "i64"];

/// Is `opcode` a `dx.op` a carrier may call: pure arithmetic, bit operations and conversions, plus
/// the input, output and parameter reads it is spliced through?
fn carrier_opcode_allowed(opcode: i64) -> bool {
    matches!(
        opcode,
        OP_LOAD_INPUT
            | OP_STORE_OUTPUT
            | OP_CREATE_HANDLE
            | OP_CBUFFER_LOAD_LEGACY
            | OP_LEGACY_F32_TO_F16
            | OP_LEGACY_F16_TO_F32
    ) || ((6..=56).contains(&opcode) && opcode != OP_FMA)
}

/// The scene buffer a velocity pass reads both view-projections from, and where they sit in it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SceneBuffer {
    pub space: i64,
    pub register: i64,
    /// `VC_MatrixViewProj`'s registers.
    pub current: [i64; 4],
    /// `VC_PrevMatrixViewProj`'s registers.
    pub previous: [i64; 4],
}

impl Default for SceneBuffer {
    /// `cbSceneParam` at `b8`, as every game mesh shader's reflection declares it.
    fn default() -> Self {
        Self {
            space: 0,
            register: 8,
            current: [24, 25, 26, 27],
            previous: [29, 30, 31, 32],
        }
    }
}

/// Whether a velocity pass gets the layers a second time at the previous time.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Previous {
    /// When the shader has exactly one previous-only output (design section 5).
    #[default]
    Auto,
    /// Never: the previous output then reads the moved position at the current time.
    Off,
}

/// How [`splice`] is to patch.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SpliceOptions {
    pub previous: Previous,
    pub scene: SceneBuffer,
}

/// Why a carrier was refused, by the rule it broke.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum CarrierRule {
    /// Not a shader model 6.0 vertex shader.
    Model,
    /// An input or output other than `ER_POS`, `ER_TIME`, `ER_FRAME` and `ER_OUT`.
    Signature,
    /// A resource other than the parameter block.
    Resource,
    /// A type that needs a shader feature: doubles, minimum precision, 16- or 64-bit integers.
    Type,
    /// An instruction or `dx.op` that is not pure arithmetic: memory, waves, derivatives,
    /// resource reads.
    Operation,
    /// An instruction with a metadata attachment.
    Metadata,
    /// Shader flags other than none.
    Features,
    /// A shape the splice cannot copy: several returns, an output not stored exactly once per
    /// column in the returning block, a `switch`.
    Shape,
}

impl CarrierRule {
    /// The rule's name, as a refusal class prints it.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Model => "model",
            Self::Signature => "signature",
            Self::Resource => "resource",
            Self::Type => "type",
            Self::Operation => "operation",
            Self::Metadata => "metadata",
            Self::Features => "features",
            Self::Shape => "shape",
        }
    }
}

/// Why a shader was not patched. It stays vanilla.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// The text does not have the shape DXC's disassembler prints: what was missing.
    Unreadable(String),
    /// No input element is `POSITION0`.
    NoPosition,
    /// `POSITION0` is not a 32-bit float element.
    PositionNotFloat,
    /// Not all of x, y and z of `POSITION0` are loaded: the columns that are.
    PartialPosition(Vec<u8>),
    /// A load of `POSITION0` sits outside the entry block, or reads a row that is not constant.
    PositionOutsideEntry,
    /// A column of `POSITION0` is loaded more than once.
    PositionLoadedTwice(u8),
    /// A loaded position value is used before the insertion point: the line.
    UsedBeforeInsertion(String),
    /// No `!dx.resources`. Adding one also means editing the entry point.
    NoResources,
    /// The shader already binds something in space 42.
    Space42Taken,
    /// The shader's constant-buffer range ids are not 0 to n - 1, so the next id is unknown.
    ResourceIds,
    /// The shader already has a value or block whose name starts with `er_`.
    NameCollision(String),
    /// The carrier broke a rule.
    Carrier { rule: CarrierRule, detail: String },
}

impl Refusal {
    /// The refusal's class: one word per kind, which the sweep counts by.
    #[must_use]
    pub fn class(&self) -> String {
        match self {
            Self::Unreadable(_) => "unreadable".to_owned(),
            Self::NoPosition => "no_position".to_owned(),
            Self::PositionNotFloat => "position_not_float".to_owned(),
            Self::PartialPosition(_) => "partial_position".to_owned(),
            Self::PositionOutsideEntry => "position_outside_entry".to_owned(),
            Self::PositionLoadedTwice(_) => "position_loaded_twice".to_owned(),
            Self::UsedBeforeInsertion(_) => "used_before_insertion".to_owned(),
            Self::NoResources => "no_resources".to_owned(),
            Self::Space42Taken => "space42_taken".to_owned(),
            Self::ResourceIds => "resource_ids".to_owned(),
            Self::NameCollision(_) => "name_collision".to_owned(),
            Self::Carrier { rule, .. } => format!("carrier_{}", rule.name()),
        }
    }

    fn carrier(rule: CarrierRule, detail: impl Into<String>) -> Self {
        Self::Carrier {
            rule,
            detail: detail.into(),
        }
    }
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unreadable(what) => write!(f, "not DXC disassembly: {what}"),
            Self::NoPosition => write!(f, "no POSITION0 input"),
            Self::PositionNotFloat => write!(f, "POSITION0 is not a 32-bit float"),
            Self::PartialPosition(columns) => {
                write!(f, "POSITION0 loads columns {columns:?} only")
            }
            Self::PositionOutsideEntry => {
                write!(f, "POSITION0 is loaded outside the entry block")
            }
            Self::PositionLoadedTwice(column) => {
                write!(f, "POSITION0 column {column} is loaded twice")
            }
            Self::UsedBeforeInsertion(line) => {
                write!(f, "a position value is used before the last load: {line}")
            }
            Self::NoResources => write!(f, "no !dx.resources"),
            Self::Space42Taken => write!(f, "the shader already binds space 42"),
            Self::ResourceIds => write!(f, "constant-buffer range ids are not 0 to n - 1"),
            Self::NameCollision(name) => write!(f, "the shader already names {name}"),
            Self::Carrier { rule, detail } => {
                write!(f, "carrier refused ({}): {detail}", rule.name())
            }
        }
    }
}

impl std::error::Error for Refusal {}

/// What happened to the previous-position output of a velocity pass.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PreviousOutcome {
    /// [`Previous::Off`].
    NotAsked,
    /// No output reads only the previous view-projection: not a velocity pass.
    None,
    /// A second instance runs on each of `sources` (`POSITION0`, `POSITION1`) for output
    /// `element`, and `cloned` instructions of its slice were cloned.
    Applied {
        element: u32,
        sources: Vec<String>,
        cloned: usize,
    },
    /// The output was found and could not be given the previous time: why. The current position
    /// is still patched, and the previous output reads it at the current time.
    Skipped(String),
}

/// What [`splice`] did, for the helper's report.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SpliceReport {
    /// The DXIL element id of `POSITION0`.
    pub position_element: u32,
    /// Instructions added: the prelude, each instance, and the branch a split needs. Clones are
    /// counted in `previous`.
    pub inserted: usize,
    /// Blocks added.
    pub blocks_added: usize,
    pub previous: PreviousOutcome,
    /// FNV-1a 64 of instance 0's code with the game's names replaced by placeholders: equal in
    /// every shader the same carrier is spliced into, whatever its numbering.
    pub instance0_hash: u64,
    /// The constant-buffer range id the parameter block was given.
    pub range_id: i64,
    /// What happened to the input-to-output dependency table.
    pub view_id: ViewIdOutcome,
}

impl SpliceReport {
    /// Instructions cloned for the previous position.
    #[must_use]
    pub fn cloned(&self) -> usize {
        match &self.previous {
            PreviousOutcome::Applied { cloned, .. } => *cloned,
            _ => 0,
        }
    }
}

/// The substitutions a splice made in game lines, which [`residual`] undoes.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SpliceMap {
    /// Each added name a game line may now read, and the game value it stands for.
    pub back: Vec<(String, String)>,
    /// The block the game's entry block continues in after a split.
    pub exit_block: Option<String>,
    /// The game's entry block, as phis name it (`%0`).
    pub entry_block: String,
    /// Types the splice declared.
    pub added_types: Vec<String>,
    /// Functions the splice declared.
    pub added_declarations: Vec<String>,
    /// Whether the parameter block was added to `!dx.resources`.
    pub added_resource: bool,
    /// The `!dx.viewIdState` node as the splice wrote it, and as the game had it.
    pub view_id_state: Option<(String, String)>,
}

/// A patched shader.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Spliced {
    pub text: String,
    pub map: SpliceMap,
    pub report: SpliceReport,
}

// ---- text ---------------------------------------------------------------------------------------

/// A line's code and its comment, split at the first `;` outside a string.
fn split_comment(line: &str) -> (&str, &str) {
    let mut quoted = false;
    for (at, c) in line.char_indices() {
        match c {
            '"' => quoted = !quoted,
            ';' if !quoted => return (&line[..at], &line[at..]),
            _ => {}
        }
    }
    (line, "")
}

/// Characters of an LLVM local name after its `%`.
const fn name_char(c: u8) -> bool {
    c.is_ascii_alphanumeric() || c == b'.' || c == b'_' || c == b'$'
}

/// The `%name` tokens of `code`, as byte ranges.
fn local_tokens(code: &str) -> Vec<(usize, usize)> {
    let bytes = code.as_bytes();
    let mut out = Vec::new();
    let mut at = 0;
    while at < bytes.len() {
        if bytes[at] == b'%' {
            let mut end = at + 1;
            while end < bytes.len() && name_char(bytes[end]) {
                end += 1;
            }
            if end > at + 1 {
                out.push((at, end));
            }
            at = end;
        } else {
            at += 1;
        }
    }
    out
}

/// `code` with every `%name` token that `map` names replaced.
fn substitute(code: &str, map: &HashMap<String, String>) -> String {
    if map.is_empty() {
        return code.to_owned();
    }
    let mut out = String::with_capacity(code.len() + 16);
    let mut last = 0;
    for (start, end) in local_tokens(code) {
        if let Some(to) = map.get(&code[start..end]) {
            out.push_str(&code[last..start]);
            out.push_str(to);
            last = end;
        }
    }
    out.push_str(&code[last..]);
    out
}

/// The value a line defines, `%name` in `%name = ...`.
fn defined(code: &str) -> Option<&str> {
    let code = code.trim_start();
    if !code.starts_with('%') {
        return None;
    }
    let (name, rest) = code.split_once(' ')?;
    rest.starts_with("= ").then_some(name)
}

/// What a line computes: the part after `=`, or the whole of a line that defines nothing.
fn computation(code: &str) -> &str {
    if defined(code).is_some()
        && let Some((_, right)) = code.split_once(" = ")
    {
        return right.trim();
    }
    code.trim()
}

/// The `%name` operands of a line, its definition left out.
fn operands(code: &str) -> Vec<&str> {
    let rhs = computation(code);
    local_tokens(rhs)
        .into_iter()
        .map(|(start, end)| &rhs[start..end])
        .collect()
}

/// Top-level comma-separated items of `text`, brackets and strings respected.
fn split_top(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut depth = 0_i32;
    let mut quoted = false;
    let mut start = 0;
    for (at, c) in text.char_indices() {
        match c {
            '"' => quoted = !quoted,
            '{' | '[' | '(' | '<' if !quoted => depth += 1,
            '}' | ']' | ')' | '>' if !quoted => depth -= 1,
            ',' if !quoted && depth == 0 => {
                out.push(text[start..at].trim());
                start = at + 1;
            }
            _ => {}
        }
    }
    let last = text[start..].trim();
    if !last.is_empty() {
        out.push(last);
    }
    out
}

/// A `dx.op` call: its function's name after `@dx.op.`, and its arguments.
struct DxCall<'a> {
    name: &'a str,
    args: Vec<&'a str>,
}

impl<'a> DxCall<'a> {
    fn opcode(&self) -> Option<i64> {
        self.int(0)
    }

    fn int(&self, index: usize) -> Option<i64> {
        self.args.get(index).and_then(|arg| int_arg(arg))
    }

    fn value(&self, index: usize) -> Option<&'a str> {
        self.args.get(index).map(|arg| arg_value(arg))
    }
}

fn dx_call(code: &str) -> Option<DxCall<'_>> {
    let at = code.find("@dx.op.")?;
    let rest = &code[at + "@dx.op.".len()..];
    let open = rest.find('(')?;
    let close = rest.rfind(')')?;
    (open < close).then(|| DxCall {
        name: &rest[..open],
        args: split_top(&rest[open + 1..close]),
    })
}

/// The integer of an argument such as `i32 4` or `i8 2`.
fn int_arg(arg: &str) -> Option<i64> {
    arg.rsplit(' ').next()?.parse().ok()
}

/// The value of an argument without its type: `%5` from `float %5`.
fn arg_value(arg: &str) -> &str {
    arg.rsplit(' ').next().unwrap_or(arg)
}

/// The integer of a metadata item such as `i32 5`.
fn md_int(item: &str) -> Option<i64> {
    let (_, value) = item.split_once(' ')?;
    value.trim().parse().ok()
}

/// The node a metadata item names, `!12`.
fn md_ref(item: &str) -> Option<u32> {
    item.strip_prefix('!')?.parse().ok()
}

/// The string of a metadata item, `!"POSITION"`.
fn md_str(item: &str) -> Option<&str> {
    item.strip_prefix("!\"")?.strip_suffix('"')
}

/// The items of a tuple node, `!{a, b}` or `distinct !{a, b}`.
fn md_tuple(text: &str) -> Option<Vec<&str>> {
    let text = text.trim();
    let text = text.strip_prefix("distinct ").unwrap_or(text);
    let inner = text.strip_prefix("!{")?.strip_suffix('}')?;
    Some(split_top(inner))
}

// ---- modules ------------------------------------------------------------------------------------

/// One parsed disassembly: its lines, where its function body is, and its metadata.
struct Module<'a> {
    lines: Vec<&'a str>,
    /// The `define` line.
    define: usize,
    /// The function's closing `}`.
    close: usize,
    /// Numbered metadata, by number: the text after `!N = `.
    nodes: BTreeMap<u32, &'a str>,
    /// Named metadata, by name: the text after `= `.
    named: BTreeMap<&'a str, &'a str>,
}

impl<'a> Module<'a> {
    fn read(text: &'a str) -> Result<Self, Refusal> {
        let lines: Vec<&str> = text.lines().collect();
        let define = lines
            .iter()
            .position(|line| line.starts_with("define "))
            .ok_or_else(|| Refusal::Unreadable("no function".to_owned()))?;
        if lines[define + 1..]
            .iter()
            .any(|line| line.starts_with("define "))
        {
            return Err(Refusal::Unreadable("more than one function".to_owned()));
        }
        let close = lines[define..]
            .iter()
            .position(|line| *line == "}")
            .map(|at| at + define)
            .ok_or_else(|| Refusal::Unreadable("no end to the function".to_owned()))?;
        let mut nodes = BTreeMap::new();
        let mut named = BTreeMap::new();
        for line in &lines[close..] {
            let Some(rest) = line.strip_prefix('!') else {
                continue;
            };
            let Some((name, value)) = rest.split_once(" = ") else {
                continue;
            };
            if let Ok(number) = name.parse::<u32>() {
                nodes.insert(number, value);
            } else {
                named.insert(name, value);
            }
        }
        Ok(Self {
            lines,
            define,
            close,
            nodes,
            named,
        })
    }

    fn node(&self, item: &str) -> Option<&'a str> {
        self.nodes.get(&md_ref(item)?).copied()
    }

    fn tuple(&self, item: &str) -> Option<Vec<&'a str>> {
        md_tuple(self.node(item)?)
    }

    /// The single node a named metadata list holds, `!dx.entryPoints = !{!9}`.
    fn named_single(&self, name: &str) -> Option<&'a str> {
        let items = md_tuple(self.named.get(name)?)?;
        match items.as_slice() {
            [only] => Some(*only),
            _ => None,
        }
    }

    /// The entry point tuple: function, name, signatures, resources, properties.
    fn entry_point(&self) -> Result<Vec<&'a str>, Refusal> {
        self.named_single("dx.entryPoints")
            .and_then(|item| self.tuple(item))
            .filter(|items| items.len() == 5)
            .ok_or_else(|| Refusal::Unreadable("no single !dx.entryPoints".to_owned()))
    }

    /// The input (0), output (1) or patch-constant (2) signature.
    fn signature(&self, which: usize) -> Result<Vec<Element>, Refusal> {
        let entry = self.entry_point()?;
        if entry[2] == "null" {
            return Ok(Vec::new());
        }
        let signatures = self
            .tuple(entry[2])
            .filter(|items| items.len() == 3)
            .ok_or_else(|| Refusal::Unreadable("no signature tuple".to_owned()))?;
        if signatures[which] == "null" {
            return Ok(Vec::new());
        }
        let list = self
            .tuple(signatures[which])
            .ok_or_else(|| Refusal::Unreadable("no signature list".to_owned()))?;
        list.iter()
            .map(|item| {
                self.tuple(item)
                    .and_then(|fields| Element::read(self, &fields))
                    .ok_or_else(|| Refusal::Unreadable(format!("signature element {item}")))
            })
            .collect()
    }

    /// The resource tuple, `!{srv, uav, cbv, sampler}`, and its node number.
    fn resources(&self) -> Option<(u32, Vec<&'a str>)> {
        let item = self.named_single("dx.resources")?;
        let tuple = self.tuple(item).filter(|items| items.len() == 4)?;
        Some((md_ref(item)?, tuple))
    }

    /// Every range of one resource class: (range id, space, lower bound).
    fn ranges(&self, list: &str) -> Vec<(i64, i64, i64)> {
        if list == "null" {
            return Vec::new();
        }
        self.tuple(list)
            .unwrap_or_default()
            .iter()
            .filter_map(|item| {
                let fields = self.tuple(item)?;
                Some((
                    md_int(fields.first()?)?,
                    md_int(fields.get(3)?)?,
                    md_int(fields.get(4)?)?,
                ))
            })
            .collect()
    }

    /// The body's lines: between `define` and `}`.
    fn body(&self) -> &[&'a str] {
        &self.lines[self.define + 1..self.close]
    }

    fn next_node(&self) -> u32 {
        self.nodes.keys().next_back().map_or(0, |last| last + 1)
    }
}

/// One signature element, from its metadata tuple.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Element {
    id: u32,
    name: String,
    component: i64,
    system_value: i64,
    indices: Vec<i64>,
    rows: i64,
    columns: i64,
    /// Where the element is packed: its first register and its first component in it.
    start_row: i64,
    start_column: i64,
}

impl Element {
    fn read(module: &Module<'_>, fields: &[&str]) -> Option<Self> {
        if fields.len() < 10 {
            return None;
        }
        let indices = module
            .tuple(fields[4])?
            .iter()
            .map(|item| md_int(item))
            .collect::<Option<Vec<i64>>>()?;
        Some(Self {
            id: u32::try_from(md_int(fields[0])?).ok()?,
            name: md_str(fields[1])?.to_owned(),
            component: md_int(fields[2])?,
            system_value: md_int(fields[3])?,
            indices,
            rows: md_int(fields[6])?,
            columns: md_int(fields[7])?,
            start_row: md_int(fields[8])?,
            start_column: md_int(fields[9])?,
        })
    }

    /// The scalar index of row `row`, column `column` of this element, as `!dx.viewIdState`
    /// numbers inputs and outputs: four per register.
    fn scalar(&self, row: i64, column: i64) -> Option<usize> {
        usize::try_from((self.start_row + row) * 4 + self.start_column + column).ok()
    }

    /// The row semantic index `index` occupies in this element, if it is `semantic`.
    fn row_of(&self, semantic: &str, index: i64) -> Option<i64> {
        if !self.name.eq_ignore_ascii_case(semantic) {
            return None;
        }
        self.indices
            .iter()
            .position(|&i| i == index)
            .and_then(|row| i64::try_from(row).ok())
    }
}

/// One block of a function body, its lines borrowed from the text.
#[derive(Clone, Debug, Default)]
struct Block<'a> {
    /// How phis and branches name it: `%0` for the entry block of a function with no arguments.
    label: String,
    /// The line that starts it, as printed; none for the entry block.
    label_line: Option<&'a str>,
    lines: Vec<&'a str>,
}

/// The blocks of a body, in order.
fn blocks<'a>(body: &[&'a str]) -> Result<Vec<Block<'a>>, Refusal> {
    let mut out = vec![Block {
        label: "%0".to_owned(),
        label_line: None,
        lines: Vec::new(),
    }];
    for &line in body {
        if line.trim().is_empty() {
            continue;
        }
        if let Some(rest) = line.strip_prefix("; <label>:") {
            let number: String = rest.chars().take_while(char::is_ascii_digit).collect();
            if number.is_empty() {
                return Err(Refusal::Unreadable(format!("label line {line}")));
            }
            out.push(Block {
                label: format!("%{number}"),
                label_line: Some(line),
                lines: Vec::new(),
            });
        } else if line.starts_with(' ') {
            if let Some(block) = out.last_mut() {
                block.lines.push(line);
            }
        } else if let Some((name, _)) = split_comment(line).0.trim_end().split_once(':')
            && !name.is_empty()
            && name.bytes().all(name_char)
        {
            out.push(Block {
                label: format!("%{name}"),
                label_line: Some(line),
                lines: Vec::new(),
            });
        } else {
            return Err(Refusal::Unreadable(format!("body line {line}")));
        }
    }
    Ok(out)
}

/// A `declare` line split into its function name, the line without its attribute group, and the
/// group's number.
fn declaration(line: &str) -> Option<(&str, &str, Option<u32>)> {
    if !line.starts_with("declare ") {
        return None;
    }
    let at = line.find('@')?;
    let name_end = line[at..].find('(')? + at;
    let name = &line[at..name_end];
    let (head, group) = match line.rfind(" #") {
        Some(hash) if line[hash + 2..].bytes().all(|b| b.is_ascii_digit()) => {
            (&line[..hash], line[hash + 2..].parse().ok())
        }
        _ => (line, None),
    };
    Some((name, head, group))
}

/// `attributes #N = { ... }`: the number and the content between the braces, trimmed.
fn attribute_group(line: &str) -> Option<(u32, &str)> {
    let rest = line.strip_prefix("attributes #")?;
    let (number, content) = rest.split_once(" = ")?;
    let content = content.trim().strip_prefix('{')?.strip_suffix('}')?.trim();
    Some((number.parse().ok()?, content))
}

/// A named type definition's name.
fn type_definition(line: &str) -> Option<&str> {
    if !line.starts_with('%') || !line.contains(" = type ") {
        return None;
    }
    line.split_once(" = ").map(|(name, _)| name)
}

// ---- the carrier --------------------------------------------------------------------------------

/// What a carrier stores to one column of `ER_OUT`.
#[derive(Clone, Debug, PartialEq, Eq)]
enum Stored {
    /// Its own `ER_POS` load of the same column: the column does not move.
    Same,
    /// A value the carrier computes, stored to this column only.
    Value(String),
    /// Anything else -- a constant, another column's input, a value stored to two columns -- which
    /// gets a named copy.
    Other(String),
}

/// One block of a carrier, its lines trimmed of comments.
#[derive(Clone, Debug)]
struct CarrierBlock {
    label: String,
    lines: Vec<String>,
}

/// A carrier, checked and taken apart for copying.
#[derive(Clone, Debug)]
struct Carrier {
    /// Its blocks, with the loads, the handles, the stores and the return taken out.
    blocks: Vec<CarrierBlock>,
    /// The block that returns.
    returns: usize,
    /// `ER_POS` loads: value and column.
    position: Vec<(String, u8)>,
    time: Vec<String>,
    frame: Vec<String>,
    handles: Vec<String>,
    /// What each column of `ER_OUT` receives.
    stored: [Stored; 3],
    /// Every value it defines, for renaming.
    values: Vec<String>,
    /// Declarations by function name: the line without its group, and the group's content.
    declarations: BTreeMap<String, (String, String)>,
    /// Type definitions by name.
    types: BTreeMap<String, String>,
    /// For each output column, the `ER_POS` columns it depends on, bit `c` for column `c`.
    depends: [u8; 3],
}

/// Check `carrier_ir` against the carrier contract: a `vs_6_0` shader with inputs `ER_POS`,
/// `ER_TIME` and `ER_FRAME` and output `ER_OUT` and nothing else, no resource but `b0, space42`, no
/// shader flags, no type that needs a feature, only pure instructions and `dx.op`s, no metadata on
/// its instructions, and one return with each output column stored once in its block.
///
/// # Errors
///
/// [`Refusal::Carrier`] naming the rule broken, or [`Refusal::Unreadable`].
pub fn check_carrier(carrier_ir: &str) -> Result<(), Refusal> {
    Carrier::read(carrier_ir).map(|_| ())
}

/// The carrier's input element ids.
#[derive(Default)]
struct CarrierInputs {
    position: Option<u32>,
    time: Option<u32>,
    frame: Option<u32>,
}

impl Carrier {
    fn read(text: &str) -> Result<Self, Refusal> {
        let module = Module::read(text)?;
        check_model(&module)?;
        let (inputs, output) = check_signature(&module)?;
        let params_range = check_resources(&module)?;
        let raw = blocks(module.body())?;
        check_types(&raw)?;
        let mut position = Vec::new();
        let mut time = Vec::new();
        let mut frame = Vec::new();
        let mut handles = Vec::new();
        let mut stores: [Vec<(String, usize)>; 3] = Default::default();
        let mut values = Vec::new();
        let mut returns = Vec::new();
        let mut kept = Vec::with_capacity(raw.len());
        for (index, block) in raw.iter().enumerate() {
            let mut lines = Vec::new();
            for line in &block.lines {
                let code = split_comment(line).0.trim_end();
                let operation = computation(code)
                    .split_whitespace()
                    .next()
                    .unwrap_or_default();
                if !CARRIER_INSTRUCTIONS.contains(&operation) {
                    let rule = if operation == "switch" {
                        CarrierRule::Shape
                    } else {
                        CarrierRule::Operation
                    };
                    return Err(Refusal::carrier(rule, format!("`{}`", code.trim())));
                }
                if code.contains(", !") {
                    return Err(Refusal::carrier(
                        CarrierRule::Metadata,
                        format!("`{}`", code.trim()),
                    ));
                }
                if operation == "ret" {
                    returns.push(index);
                    continue;
                }
                let value = defined(code).map(str::to_owned);
                if operation == "call" {
                    let call = dx_call(code).ok_or_else(|| {
                        Refusal::carrier(CarrierRule::Operation, format!("`{}`", code.trim()))
                    })?;
                    let opcode = call.opcode().unwrap_or(-1);
                    if !carrier_opcode_allowed(opcode) {
                        return Err(Refusal::carrier(
                            CarrierRule::Operation,
                            format!("dx.op.{} ({opcode})", call.name),
                        ));
                    }
                    match opcode {
                        OP_LOAD_INPUT => {
                            let value = value.ok_or_else(|| {
                                Refusal::Unreadable(format!("a load with no value: {code}"))
                            })?;
                            let id = call.int(1).and_then(|id| u32::try_from(id).ok());
                            if call.int(2) != Some(0) {
                                return Err(Refusal::carrier(
                                    CarrierRule::Shape,
                                    format!("a load of row {:?}", call.int(2)),
                                ));
                            }
                            match (id, call.int(3)) {
                                (Some(id), Some(column @ 0..=2)) if Some(id) == inputs.position => {
                                    position.push((value, column as u8));
                                }
                                (Some(id), Some(0)) if Some(id) == inputs.time => time.push(value),
                                (Some(id), Some(0)) if Some(id) == inputs.frame => {
                                    frame.push(value);
                                }
                                (id, column) => {
                                    return Err(Refusal::carrier(
                                        CarrierRule::Signature,
                                        format!("a load of element {id:?} column {column:?}"),
                                    ));
                                }
                            }
                            continue;
                        }
                        OP_STORE_OUTPUT => {
                            let id = call.int(1).and_then(|id| u32::try_from(id).ok());
                            match (id, call.int(2), call.int(3)) {
                                (Some(id), Some(0), Some(column @ 0..=2)) if id == output => {
                                    let stored = call.value(4).unwrap_or_default().to_owned();
                                    stores[column as usize].push((stored, index));
                                }
                                _ => {
                                    return Err(Refusal::carrier(
                                        CarrierRule::Shape,
                                        format!("a store `{}`", code.trim()),
                                    ));
                                }
                            }
                            continue;
                        }
                        OP_CREATE_HANDLE => {
                            let ok = call.int(1) == Some(CLASS_CBV)
                                && params_range.is_some()
                                && call.int(2) == params_range
                                && call.int(3) == Some(PARAMS_REGISTER);
                            if !ok {
                                return Err(Refusal::carrier(
                                    CarrierRule::Resource,
                                    format!("a handle `{}`", code.trim()),
                                ));
                            }
                            handles.push(value.unwrap_or_default());
                            continue;
                        }
                        _ => {}
                    }
                }
                if let Some(value) = value {
                    values.push(value);
                }
                lines.push(code.to_owned());
            }
            kept.push(CarrierBlock {
                label: block.label.clone(),
                lines,
            });
        }
        let [returns] = returns.as_slice() else {
            return Err(Refusal::carrier(
                CarrierRule::Shape,
                format!("{} returns", returns.len()),
            ));
        };
        let returns = *returns;
        let stored = Self::classify_stores(&stores, &position, &values, returns)?;
        // Last, so a carrier that sets a flag through a wave op or a minimum-precision type is
        // refused by that more specific rule.
        check_features(&module)?;
        let (declarations, types) = Self::declarations(&module);
        let depends = Self::dependencies(&kept, &position, &stored);
        Ok(Self {
            blocks: kept,
            returns,
            position,
            time,
            frame,
            handles,
            stored,
            values,
            declarations,
            types,
            depends,
        })
    }

    /// Which `ER_POS` columns each output column depends on: through its operands, repeated to a
    /// fixed point for loop phis, and, in a carrier with branches, through every branch
    /// condition too, since a phi's choice depends on the branch that chose it. The second part
    /// can only add columns, which is the safe direction for a dependency table.
    fn dependencies(
        blocks: &[CarrierBlock],
        position: &[(String, u8)],
        stored: &[Stored; 3],
    ) -> [u8; 3] {
        let mut marks: HashMap<&str, u8> = position
            .iter()
            .map(|(value, column)| (value.as_str(), 1_u8 << column))
            .collect();
        let lines: Vec<&str> = blocks
            .iter()
            .flat_map(|b| b.lines.iter().map(String::as_str))
            .collect();
        let mut changed = true;
        while changed {
            changed = false;
            for line in &lines {
                let Some(value) = defined(line) else { continue };
                let mark = operands(line)
                    .iter()
                    .fold(0, |m, v| m | marks.get(v).copied().unwrap_or(0));
                if mark != marks.get(value).copied().unwrap_or(0) {
                    marks.insert(value, mark);
                    changed = true;
                }
            }
        }
        let branches = if blocks.len() > 1 {
            lines
                .iter()
                .filter(|line| computation(line).starts_with("br i1 "))
                .flat_map(|line| operands(line))
                .fold(0, |m, v| m | marks.get(v).copied().unwrap_or(0))
        } else {
            0
        };
        let mut out = [0_u8; 3];
        for (column, what) in stored.iter().enumerate() {
            out[column] = match what {
                Stored::Same => 1 << column,
                Stored::Value(value) | Stored::Other(value) => {
                    marks.get(value.as_str()).copied().unwrap_or(0) | branches
                }
            };
        }
        out
    }

    /// Whether output column `column` depends on input column `input`, as the game sees it after
    /// the splice: through the carrier for a moving column, and directly for one left alone.
    fn feeds(&self, column: usize, input: usize) -> bool {
        if self.column_moves(column) {
            self.depends[column] & (1 << input) != 0
        } else {
            column == input
        }
    }

    /// What each output column receives, every store being in the returning block, once.
    fn classify_stores(
        stores: &[Vec<(String, usize)>; 3],
        position: &[(String, u8)],
        values: &[String],
        returns: usize,
    ) -> Result<[Stored; 3], Refusal> {
        let mut stored = [Stored::Same, Stored::Same, Stored::Same];
        for (column, list) in stores.iter().enumerate() {
            let [(value, block)] = list.as_slice() else {
                return Err(Refusal::carrier(
                    CarrierRule::Shape,
                    format!("ER_OUT column {column} is stored {} times", list.len()),
                ));
            };
            if *block != returns {
                return Err(Refusal::carrier(
                    CarrierRule::Shape,
                    format!("ER_OUT column {column} is stored outside the returning block"),
                ));
            }
            let shared = stores
                .iter()
                .enumerate()
                .any(|(other, list)| other != column && list.iter().any(|(v, _)| v == value));
            let own_input = position
                .iter()
                .any(|(load, load_column)| load == value && usize::from(*load_column) == column);
            stored[column] = if own_input {
                Stored::Same
            } else if values.contains(value) && !shared {
                Stored::Value(value.clone())
            } else {
                Stored::Other(value.clone())
            };
        }
        Ok(stored)
    }

    /// The carrier's declarations, with their attribute groups by content, and its types.
    #[allow(clippy::type_complexity)]
    fn declarations(
        module: &Module<'_>,
    ) -> (BTreeMap<String, (String, String)>, BTreeMap<String, String>) {
        let mut groups = HashMap::new();
        for line in &module.lines[module.close..] {
            if let Some((number, content)) = attribute_group(line) {
                groups.insert(number, content.to_owned());
            }
        }
        let mut declarations = BTreeMap::new();
        let mut types = BTreeMap::new();
        for line in &module.lines {
            if let Some((name, head, group)) = declaration(line) {
                let content = group
                    .and_then(|number| groups.get(&number).cloned())
                    .unwrap_or_default();
                declarations.insert(name.to_owned(), (head.to_owned(), content));
            } else if let Some(name) = type_definition(line) {
                types.insert(name.to_owned(), (*line).to_owned());
            }
        }
        (declarations, types)
    }

    fn reads_time(&self) -> bool {
        !self.time.is_empty()
    }

    fn reads_frame(&self) -> bool {
        !self.frame.is_empty()
    }

    /// Whether an output column is the input unchanged.
    fn column_moves(&self, column: usize) -> bool {
        self.stored[column] != Stored::Same
    }
}

fn check_model(module: &Module<'_>) -> Result<(), Refusal> {
    let model = module
        .named_single("dx.shaderModel")
        .and_then(|item| module.tuple(item))
        .unwrap_or_default();
    let ok = model.len() == 3
        && md_str(model[0]) == Some("vs")
        && md_int(model[1]) == Some(6)
        && md_int(model[2]) == Some(0);
    if ok {
        Ok(())
    } else {
        Err(Refusal::carrier(
            CarrierRule::Model,
            format!("shader model {model:?}, not vs 6.0"),
        ))
    }
}

/// The carrier's inputs and its output's element id.
fn check_signature(module: &Module<'_>) -> Result<(CarrierInputs, u32), Refusal> {
    let mut inputs = CarrierInputs::default();
    for element in module.signature(0)? {
        let fits = |component: &[i64], columns: i64| {
            component.contains(&element.component)
                && element.columns == columns
                && element.rows == 1
                && element.system_value == 0
                && element.indices == [0]
        };
        let slot = match element.name.as_str() {
            CARRIER_POSITION if fits(&[COMPONENT_F32], 3) => &mut inputs.position,
            CARRIER_TIME if fits(&[COMPONENT_F32], 1) => &mut inputs.time,
            CARRIER_FRAME if fits(&[COMPONENT_I32, COMPONENT_U32], 1) => &mut inputs.frame,
            _ => {
                return Err(Refusal::carrier(
                    CarrierRule::Signature,
                    format!(
                        "input {}{:?} (system value {})",
                        element.name, element.indices, element.system_value
                    ),
                ));
            }
        };
        *slot = Some(element.id);
    }
    let outputs = module.signature(1)?;
    let patch = module.signature(2)?;
    match outputs.as_slice() {
        [only]
            if only.name == CARRIER_OUTPUT
                && only.component == COMPONENT_F32
                && only.columns == 3
                && only.rows == 1
                && only.system_value == 0
                && patch.is_empty() =>
        {
            Ok((inputs, only.id))
        }
        _ => {
            let names: Vec<&str> = outputs.iter().map(|e| e.name.as_str()).collect();
            Err(Refusal::carrier(
                CarrierRule::Signature,
                format!("outputs {names:?}, not ER_OUT alone"),
            ))
        }
    }
}

/// The parameter block's range id, when the carrier reads it; nothing else may be bound.
fn check_resources(module: &Module<'_>) -> Result<Option<i64>, Refusal> {
    let Some((_, tuple)) = module.resources() else {
        return Ok(None);
    };
    let mut params_range = None;
    for (class, list) in tuple.iter().enumerate() {
        if *list == "null" {
            continue;
        }
        if class != 2 {
            let kind = ["an SRV", "a UAV", "", "a sampler"][class];
            return Err(Refusal::carrier(
                CarrierRule::Resource,
                format!("{kind} is declared"),
            ));
        }
        for (id, space, lower) in module.ranges(list) {
            if space != PARAMS_SPACE || lower != PARAMS_REGISTER || params_range.is_some() {
                return Err(Refusal::carrier(
                    CarrierRule::Resource,
                    format!("a constant buffer at b{lower}, space{space}"),
                ));
            }
            params_range = Some(id);
        }
    }
    Ok(params_range)
}

/// No shader flags: anything the carrier needs beyond plain arithmetic shows here.
fn check_features(module: &Module<'_>) -> Result<(), Refusal> {
    let entry = module.entry_point()?;
    if entry[4] == "null" {
        return Ok(());
    }
    let properties = module.tuple(entry[4]).unwrap_or_default();
    let nonzero: Vec<String> = properties
        .chunks(2)
        .filter_map(|pair| match pair {
            [tag, value] if md_int(value) != Some(0) => Some(format!("{tag}={value}")),
            _ => None,
        })
        .collect();
    if nonzero.is_empty() {
        Ok(())
    } else {
        Err(Refusal::carrier(
            CarrierRule::Features,
            format!("entry properties {}", nonzero.join(", ")),
        ))
    }
}

fn check_types(blocks: &[Block<'_>]) -> Result<(), Refusal> {
    for line in blocks.iter().flat_map(|block| &block.lines) {
        let code = split_comment(line).0;
        let found = code
            .split(|c: char| !c.is_ascii_alphanumeric())
            .find(|word| CARRIER_TYPES_REFUSED.contains(word));
        if let Some(word) = found {
            return Err(Refusal::carrier(
                CarrierRule::Type,
                format!("`{word}` in `{}`", code.trim()),
            ));
        }
    }
    Ok(())
}

/// The column names an instance's outputs are given.
const AXES: [&str; 3] = ["x", "y", "z"];

/// What one copy of the carrier is fed.
struct Feed<'a> {
    /// The game's value for each column of `ER_POS`.
    position: [&'a str; 3],
    time: &'a str,
    frame: &'a str,
}

/// One copy of the carrier, renamed for instance `k`.
struct Instance {
    /// Lines for the block the copy starts in, its terminator included when it has more blocks.
    entry: Vec<String>,
    /// Its other blocks: label and lines. The returning block is last, without a terminator.
    blocks: Vec<(String, Vec<String>)>,
    /// What the game reads for each column after this copy: a new name, or the input unchanged.
    outputs: [String; 3],
    /// The label of the block the copy ends in, when it is not the block it started in.
    ends_in: Option<String>,
    /// Instructions added.
    instructions: usize,
}

impl Carrier {
    /// The carrier renamed as instance `k`, starting in block `start`, fed `feed`.
    fn instance(&self, k: usize, start: &str, feed: &Feed<'_>, handle: &str) -> Instance {
        let mut rename: HashMap<String, String> = HashMap::new();
        for (value, column) in &self.position {
            rename.insert(
                value.clone(),
                feed.position[usize::from(*column)].to_owned(),
            );
        }
        for value in &self.time {
            rename.insert(value.clone(), feed.time.to_owned());
        }
        for value in &self.frame {
            rename.insert(value.clone(), feed.frame.to_owned());
        }
        for value in &self.handles {
            rename.insert(value.clone(), handle.to_owned());
        }
        for value in &self.values {
            rename.insert(value.clone(), format!("%er_c{k}_{}", &value[1..]));
        }
        for (column, stored) in self.stored.iter().enumerate() {
            if let Stored::Value(value) = stored {
                rename.insert(value.clone(), format!("%er_c{k}_{}", AXES[column]));
            }
        }
        for block in &self.blocks[1..] {
            rename.insert(
                block.label.clone(),
                format!("%er_c{k}_b{}", &block.label[1..]),
            );
        }
        rename.insert(self.blocks[0].label.clone(), start.to_owned());
        let mut outputs: [String; 3] = Default::default();
        let mut copies = Vec::new();
        for (column, stored) in self.stored.iter().enumerate() {
            outputs[column] = match stored {
                Stored::Same => feed.position[column].to_owned(),
                Stored::Value(value) => rename[value].clone(),
                Stored::Other(value) => {
                    let source = rename.get(value).cloned().unwrap_or_else(|| value.clone());
                    let name = format!("%er_c{k}_{}", AXES[column]);
                    copies.push(format!("  {name} = fadd float {source}, -0.000000e+00"));
                    name
                }
            };
        }
        let mut instructions = 0;
        let mut entry = Vec::new();
        let mut blocks = Vec::new();
        let mut ends_in = None;
        for (index, block) in self.blocks.iter().enumerate() {
            let mut lines: Vec<String> = block
                .lines
                .iter()
                .map(|line| substitute(line, &rename))
                .collect();
            if index == self.returns {
                lines.extend(copies.iter().cloned());
            }
            instructions += lines.len();
            if index == 0 {
                entry = lines;
            } else {
                let label = rename[&block.label].clone();
                if index == self.returns {
                    ends_in = Some(label.clone());
                }
                blocks.push((label, lines));
            }
        }
        // The returning block goes last, so the next instance or the game continues in it.
        if let Some(at) = blocks
            .iter()
            .position(|(label, _)| Some(label) == ends_in.as_ref())
        {
            let returning = blocks.remove(at);
            blocks.push(returning);
        }
        Instance {
            entry,
            blocks,
            outputs,
            ends_in,
            instructions,
        }
    }
}

/// Instance 0's code with the game's names replaced by placeholders, hashed with the range id it
/// reads through.
fn canonical_hash(instance: &Instance, feed: &Feed<'_>, prelude: &[String]) -> u64 {
    let mut placeholders = HashMap::new();
    for (column, value) in feed.position.iter().enumerate() {
        placeholders.insert((*value).to_owned(), format!("$p{column}"));
    }
    let mut hash = FNV1A64_OFFSET_BASIS;
    let blocks = instance
        .blocks
        .iter()
        .flat_map(|(label, lines)| core::iter::once(label).chain(lines));
    for line in prelude.iter().chain(&instance.entry).chain(blocks) {
        hash = fnv1a64_extend(hash, substitute(line, &placeholders).as_bytes());
        hash = fnv1a64_extend(hash, b"\n");
    }
    hash
}

// ---- the game shader ----------------------------------------------------------------------------

/// One body line, read once: its code, what it defines and reads, and its `dx.op` call.
struct Info<'a> {
    /// The code before the comment, without its trailing spaces.
    code: &'a str,
    /// The spaces between the code and the comment, kept so a rewritten line prints the same.
    pad: &'a str,
    comment: &'a str,
    def: Option<&'a str>,
    operands: Vec<&'a str>,
    call: Option<DxCall<'a>>,
}

impl<'a> Info<'a> {
    fn read(line: &'a str) -> Self {
        let (code, comment) = split_comment(line);
        let trimmed = code.trim_end();
        Self {
            code: trimmed,
            pad: &code[trimmed.len()..],
            comment,
            def: defined(trimmed),
            operands: operands(trimmed),
            call: if trimmed.contains("@dx.op.") {
                dx_call(trimmed)
            } else {
                None
            },
        }
    }

    fn opcode(&self) -> Option<i64> {
        self.call.as_ref().and_then(DxCall::opcode)
    }

    fn int(&self, index: usize) -> Option<i64> {
        self.call.as_ref().and_then(|call| call.int(index))
    }

    fn value(&self, index: usize) -> Option<&'a str> {
        self.call.as_ref().and_then(|call| call.value(index))
    }
}

/// A body read once: one list of [`Info`] per block.
type Body<'a> = Vec<Vec<Info<'a>>>;

fn read_body<'a>(blocks: &[Block<'a>]) -> Body<'a> {
    blocks
        .iter()
        .map(|block| block.lines.iter().map(|line| Info::read(line)).collect())
        .collect()
}

/// A position input's loads: one value per column, and the last entry-block line holding one.
#[derive(Clone, Debug)]
struct Loads {
    values: [String; 3],
    last: usize,
}

/// One load found by [`element_loads`]: value, column, block and line.
type Load<'a> = (&'a str, Option<i64>, usize, usize);

/// Every load of row `row` of element `id`, wherever it is. A load whose row is not a constant
/// could be any row, this one included, and has no column.
fn element_loads<'a>(body: &Body<'a>, id: u32, row: i64) -> Vec<Load<'a>> {
    let mut out = Vec::new();
    for (index, block) in body.iter().enumerate() {
        for (at, info) in block.iter().enumerate() {
            if info.opcode() != Some(OP_LOAD_INPUT)
                || info.int(1).and_then(|e| u32::try_from(e).ok()) != Some(id)
            {
                continue;
            }
            match info.int(2) {
                Some(load_row) if load_row != row => {}
                Some(_) => out.push((info.def.unwrap_or_default(), info.int(3), index, at)),
                None => out.push((info.def.unwrap_or_default(), None, index, at)),
            }
        }
    }
    out
}

/// The loads of x, y and z of row `row` of element `id`, all in the entry block, once each.
fn position_loads(body: &Body<'_>, id: u32, row: i64) -> Result<Loads, Refusal> {
    let mut found: [Option<String>; 3] = Default::default();
    let mut last = 0;
    for (value, column, index, at) in element_loads(body, id, row) {
        let Some(column) = column else {
            return Err(Refusal::PositionOutsideEntry);
        };
        if !(0..=2).contains(&column) {
            continue;
        }
        if index != 0 {
            return Err(Refusal::PositionOutsideEntry);
        }
        let slot = &mut found[column as usize];
        if slot.is_some() {
            return Err(Refusal::PositionLoadedTwice(column as u8));
        }
        *slot = Some(value.to_owned());
        last = last.max(at);
    }
    match found {
        [Some(x), Some(y), Some(z)] => Ok(Loads {
            values: [x, y, z],
            last,
        }),
        partial => Err(Refusal::PartialPosition(
            partial
                .iter()
                .enumerate()
                .filter(|(_, value)| value.is_some())
                .map(|(column, _)| column as u8)
                .collect(),
        )),
    }
}

/// Where a line sits: block, then line.
type At = (usize, usize);

/// A body's definitions, its stores by output element, and its scene-buffer reads.
struct Graph<'a> {
    defs: HashMap<&'a str, At>,
    stores: BTreeMap<u32, Vec<&'a str>>,
    /// Each value a `cbufferLoadLegacy` of the scene buffer defines, and the register it reads.
    scene_reads: HashMap<&'a str, i64>,
}

impl<'a> Graph<'a> {
    fn read(body: &Body<'a>, scene_range: Option<i64>) -> Self {
        let mut defs = HashMap::new();
        let mut stores: BTreeMap<u32, Vec<&'a str>> = BTreeMap::new();
        let mut handles = HashSet::new();
        let mut scene_reads = HashMap::new();
        for (index, block) in body.iter().enumerate() {
            for (at, info) in block.iter().enumerate() {
                if let Some(value) = info.def {
                    defs.insert(value, (index, at));
                }
                match info.opcode() {
                    Some(OP_STORE_OUTPUT) => {
                        if let (Some(element), Some(stored)) = (
                            info.int(1).and_then(|e| u32::try_from(e).ok()),
                            info.value(4),
                        ) {
                            stores.entry(element).or_default().push(stored);
                        }
                    }
                    Some(OP_CREATE_HANDLE)
                        if info.int(1) == Some(CLASS_CBV)
                            && scene_range.is_some()
                            && info.int(2) == scene_range =>
                    {
                        if let Some(value) = info.def {
                            handles.insert(value);
                        }
                    }
                    Some(OP_CBUFFER_LOAD_LEGACY) => {
                        if let (Some(value), Some(handle), Some(register)) =
                            (info.def, info.value(1), info.int(2))
                            && handles.contains(handle)
                        {
                            scene_reads.insert(value, register);
                        }
                    }
                    _ => {}
                }
            }
        }
        Self {
            defs,
            stores,
            scene_reads,
        }
    }

    /// The values `element`'s stores depend on.
    fn slice(&self, body: &Body<'a>, element: u32) -> HashSet<&'a str> {
        let mut seen = HashSet::new();
        let mut stack: Vec<&'a str> = self.stores.get(&element).cloned().unwrap_or_default();
        while let Some(value) = stack.pop() {
            let Some(&(block, line)) = self.defs.get(value) else {
                continue;
            };
            if seen.insert(value) {
                stack.extend(body[block][line].operands.iter().copied());
            }
        }
        seen
    }

    /// The outputs whose slice reads the previous view-projection and not the current one. One
    /// pass in program order marks each value with which of the two it depends on, repeated until
    /// nothing changes, because a loop phi names a value defined later in the text.
    fn previous_only(&self, body: &Body<'a>, scene: &SceneBuffer) -> Vec<u32> {
        const CURRENT: u8 = 1;
        const PREVIOUS: u8 = 2;
        let mut marks: HashMap<&'a str, u8> = HashMap::with_capacity(self.defs.len());
        let mut changed = true;
        while changed {
            changed = false;
            for info in body.iter().flatten() {
                let Some(value) = info.def else { continue };
                let mut mark = match self.scene_reads.get(value) {
                    Some(r) if scene.current.contains(r) => CURRENT,
                    Some(r) if scene.previous.contains(r) => PREVIOUS,
                    _ => 0,
                };
                for operand in &info.operands {
                    mark |= marks.get(operand).copied().unwrap_or(0);
                }
                if mark != marks.get(value).copied().unwrap_or(0) {
                    marks.insert(value, mark);
                    changed = true;
                }
            }
        }
        self.stores
            .iter()
            .filter(|(_, stored)| {
                stored.iter().fold(0, |mark, value| {
                    mark | marks.get(value).copied().unwrap_or(0)
                }) == PREVIOUS
            })
            .map(|(element, _)| *element)
            .collect()
    }
}

/// `!dx.viewIdState`: the input and output scalar counts, and for each input scalar the output
/// scalars it contributes to, one bit per output in words of 32. The assembler copies this from
/// the text into PSV0 without recomputing it (measured: a splice that makes x read y leaves the
/// table saying x reads x alone, and the validator accepts it), so the splice rewrites it.
#[derive(Clone, Debug)]
struct ViewIdState {
    node: u32,
    /// The node as printed, `!{[K x i32] [...]}`.
    original: String,
    /// `[K x i32]`.
    array_type: String,
    values: Vec<u32>,
    inputs: usize,
    words: usize,
    /// Where input 0's mask starts: after the two counts, and after the outputs that depend on
    /// `SV_ViewID` when the shader reads it.
    first_mask: usize,
}

impl ViewIdState {
    fn read(module: &Module<'_>) -> Result<Self, String> {
        let item = module
            .named_single("dx.viewIdState")
            .ok_or("no !dx.viewIdState")?;
        let node = md_ref(item).ok_or("!dx.viewIdState names no node")?;
        let original: &str = module.nodes.get(&node).ok_or("its node is missing")?;
        let inner = md_tuple(original)
            .and_then(|items| items.first().copied())
            .ok_or("its node is not a tuple")?;
        let (array_type, rest) = inner.split_once("] [").ok_or("its node holds no array")?;
        let rest = rest.strip_suffix(']').ok_or("its array is not closed")?;
        let values = split_top(rest)
            .iter()
            .map(|v| {
                md_int(v)
                    .and_then(|n| i32::try_from(n).ok())
                    .map(|n| n as u32)
            })
            .collect::<Option<Vec<u32>>>()
            .ok_or("its array is not 32-bit integers")?;
        let [inputs, outputs, ..] = values.as_slice() else {
            return Err("its array is shorter than its counts".to_owned());
        };
        let (inputs, outputs) = (*inputs as usize, *outputs as usize);
        let words = outputs.div_ceil(32);
        let masks = inputs * words;
        let first_mask = match values.len() - 2 {
            n if n == masks => 2,
            n if n == words + masks => 2 + words,
            _ => {
                return Err(format!(
                    "{} values do not fit {inputs} inputs and {outputs} outputs of one stream",
                    values.len()
                ));
            }
        };
        Ok(Self {
            node,
            original: original.to_owned(),
            array_type: format!("{array_type}]"),
            values,
            inputs,
            words,
            first_mask,
        })
    }

    fn mask(&self, input: usize) -> &[u32] {
        let at = self.first_mask + input * self.words;
        &self.values[at..at + self.words]
    }

    fn set(&mut self, input: usize, mask: &[u32]) {
        let at = self.first_mask + input * self.words;
        self.values[at..at + self.words].copy_from_slice(mask);
    }

    /// The node as DXC prints it.
    fn text(&self) -> String {
        let items: Vec<String> = self
            .values
            .iter()
            .map(|v| format!("i32 {}", *v as i32))
            .collect();
        format!("!{{{} [{}]}}", self.array_type, items.join(", "))
    }
}

/// What the splice did with `!dx.viewIdState`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ViewIdOutcome {
    /// Rewritten for the carrier's dependencies; `changed` when that altered a bit.
    Rewritten { changed: bool },
    /// Left as it was: why. The variant's dependency table is then the original's.
    Kept(String),
}

/// `state` with the carrier's dependencies composed in: an output that read a moving column of
/// `POSITION0` now reads every column the carrier's output for it reads, and so does the previous
/// output for a `POSITION1` it reads through the second instance.
fn compose_view_id(
    state: &mut ViewIdState,
    carrier: &Carrier,
    position0: [usize; 3],
    previous: Option<([usize; 3], Vec<usize>)>,
) -> Result<(), String> {
    let inputs = state.inputs;
    let in_range = |scalars: &[usize; 3]| scalars.iter().all(|s| *s < inputs);
    if !in_range(&position0) {
        return Err("POSITION0 lies outside the table's inputs".to_owned());
    }
    let words = state.words;
    let before = state.clone();
    for input in 0..3 {
        let mut mask = vec![0_u32; words];
        for column in (0..3).filter(|c| carrier.feeds(*c, input)) {
            for (m, b) in mask.iter_mut().zip(before.mask(position0[column])) {
                *m |= b;
            }
        }
        state.set(position0[input], &mask);
    }
    if let Some((position1, outputs)) = previous {
        if !in_range(&position1) {
            return Err("POSITION1 lies outside the table's inputs".to_owned());
        }
        let mut previous_bits = vec![0_u32; words];
        for output in outputs {
            if let Some(word) = previous_bits.get_mut(output / 32) {
                *word |= 1 << (output % 32);
            }
        }
        for input in 0..3 {
            let mut mask: Vec<u32> = before
                .mask(position1[input])
                .iter()
                .zip(&previous_bits)
                .map(|(b, p)| b & !p)
                .collect();
            for column in (0..3).filter(|c| carrier.feeds(*c, input)) {
                for ((m, b), p) in mask
                    .iter_mut()
                    .zip(before.mask(position1[column]))
                    .zip(&previous_bits)
                {
                    *m |= b & p;
                }
            }
            state.set(position1[input], &mask);
        }
    }
    Ok(())
}

// ---- splicing -----------------------------------------------------------------------------------

/// The plan for the previous position: which output, which moved inputs its slice reads, and the
/// slice's instructions that depend on them.
struct PreviousPlan {
    element: u32,
    /// Each moved input the slice reads (`POSITION0`, `POSITION1`), with its loads.
    sources: Vec<(&'static str, Loads)>,
    /// The slice's values that depend on a moving column of a source, and where each is defined.
    tainted: Vec<(String, At)>,
    /// The values the output's stores write.
    stored: Vec<String>,
}

/// Find the previous-position output and plan its second instance, or say why there is none.
fn plan_previous(
    body: &Body<'_>,
    inputs: &[Element],
    position_loads0: &Loads,
    graph: &Graph<'_>,
    carrier: &Carrier,
    options: &SpliceOptions,
) -> Result<PreviousPlan, PreviousOutcome> {
    let candidates = graph.previous_only(body, &options.scene);
    let element = match candidates.as_slice() {
        [] => return Err(PreviousOutcome::None),
        [only] => *only,
        many => {
            return Err(PreviousOutcome::Skipped(format!(
                "{} outputs read only the previous view-projection",
                many.len()
            )));
        }
    };
    let slice = graph.slice(body, element);
    let mut sources = Vec::new();
    if position_loads0
        .values
        .iter()
        .any(|v| slice.contains(v.as_str()))
    {
        sources.push(("POSITION0", position_loads0.clone()));
    }
    let second = inputs
        .iter()
        .find_map(|e| e.row_of("POSITION", 1).map(|row| (e, row)));
    if let Some((element1, row1)) = second {
        let read = element_loads(body, element1.id, row1)
            .iter()
            .any(|(value, ..)| slice.contains(value));
        if read {
            if element1.component != COMPONENT_F32 {
                return Err(PreviousOutcome::Skipped(
                    "POSITION1 is not a 32-bit float".to_owned(),
                ));
            }
            match position_loads(body, element1.id, row1) {
                Ok(found) => sources.push(("POSITION1", found)),
                Err(why) => return Err(PreviousOutcome::Skipped(format!("POSITION1: {why}"))),
            }
        }
    }
    if sources.is_empty() {
        return Err(PreviousOutcome::Skipped(
            "the previous output reads no position input".to_owned(),
        ));
    }
    // The forward closure of the moving source columns within the slice, to a fixed point: a loop
    // phi names a value defined later in the text.
    let mut seeds: HashSet<&str> = HashSet::new();
    for (_, loads) in &sources {
        for (column, value) in loads.values.iter().enumerate() {
            if carrier.column_moves(column) {
                seeds.insert(value.as_str());
            }
        }
    }
    let in_slice: Vec<(At, &Info<'_>)> = body
        .iter()
        .enumerate()
        .flat_map(|(index, block)| {
            block
                .iter()
                .enumerate()
                .map(move |(at, info)| ((index, at), info))
        })
        .filter(|(_, info)| info.def.is_some_and(|v| slice.contains(v)))
        .collect();
    let mut tainted: BTreeMap<&str, At> = BTreeMap::new();
    let mut grew = true;
    while grew {
        grew = false;
        for (at, info) in &in_slice {
            let Some(value) = info.def else { continue };
            if tainted.contains_key(value) {
                continue;
            }
            if info
                .operands
                .iter()
                .any(|v| seeds.contains(v) || tainted.contains_key(v))
            {
                tainted.insert(value, *at);
                grew = true;
            }
        }
    }
    let stored = graph
        .stores
        .get(&element)
        .map(|list| list.iter().map(|v| (*v).to_owned()).collect())
        .unwrap_or_default();
    Ok(PreviousPlan {
        element,
        sources,
        tainted: tainted
            .into_iter()
            .map(|(value, at)| (value.to_owned(), at))
            .collect(),
        stored,
    })
}

/// The prelude's handle to the parameter block, as range `range_id`.
fn handle_line(handle: &str, range_id: &str) -> String {
    format!(
        "  {handle} = call %dx.types.Handle @dx.op.createHandle(i32 {OP_CREATE_HANDLE}, i8 \
         {CLASS_CBV}, i32 {range_id}, i32 {PARAMS_REGISTER}, i1 false)"
    )
}

/// The prelude's reads of one row of the parameter block: time and frame, each only when the
/// carrier reads it.
fn read_row(
    carrier: &Carrier,
    handle: &str,
    register: i64,
    prefix: &str,
    prelude: &mut Vec<String>,
) -> (String, String) {
    let row = format!("%er_{prefix}r{register}");
    prelude.push(format!(
        "  {row} = call %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32 \
         {OP_CBUFFER_LOAD_LEGACY}, %dx.types.Handle {handle}, i32 {register})"
    ));
    let mut time = "undef".to_owned();
    let mut frame = "undef".to_owned();
    if carrier.reads_time() {
        time = format!("%er_{prefix}time");
        prelude.push(format!(
            "  {time} = extractvalue %dx.types.CBufRet.f32 {row}, 0"
        ));
    }
    if carrier.reads_frame() {
        frame = format!("%er_{prefix}frame");
        prelude.push(format!(
            "  %er_{prefix}framef = extractvalue %dx.types.CBufRet.f32 {row}, 1"
        ));
        prelude.push(format!(
            "  {frame} = bitcast float %er_{prefix}framef to i32"
        ));
    }
    (time, frame)
}

/// Splice `carrier_ir` into `game_ir` (the module documentation's steps 1 to 7).
///
/// # Errors
///
/// A [`Refusal`] when the game shader or the carrier is not one this patches.
pub fn splice(
    game_ir: &str,
    carrier_ir: &str,
    options: &SpliceOptions,
) -> Result<Spliced, Refusal> {
    let carrier = Carrier::read(carrier_ir)?;
    let game = Module::read(game_ir)?;
    if let Some(line) = game.body().iter().find(|line| {
        let code = split_comment(line).0;
        code.contains("%er_") || code.trim_start().starts_with("er_")
    }) {
        return Err(Refusal::NameCollision(line.trim().to_owned()));
    }

    // Step 1: the position element and its loads.
    let inputs = game.signature(0)?;
    let (position, row) = inputs
        .iter()
        .find_map(|e| e.row_of("POSITION", 0).map(|row| (e, row)))
        .ok_or(Refusal::NoPosition)?;
    if position.component != COMPONENT_F32 {
        return Err(Refusal::PositionNotFloat);
    }
    let blocks = blocks(game.body())?;
    if blocks.iter().skip(1).any(|block| block.label == "%0") {
        return Err(Refusal::Unreadable(
            "a block other than the entry is %0".to_owned(),
        ));
    }
    let body = read_body(&blocks);
    let loads = position_loads(&body, position.id, row)?;

    // Step 2: the resources.
    let (resource_node, resources) = game.resources().ok_or(Refusal::NoResources)?;
    if resources
        .iter()
        .any(|list| game.ranges(list).iter().any(|r| r.1 == PARAMS_SPACE))
    {
        return Err(Refusal::Space42Taken);
    }
    let constant_buffers = game.ranges(resources[2]);
    let mut scene_range = None;
    for (index, &(id, space, lower)) in constant_buffers.iter().enumerate() {
        if i64::try_from(index) != Ok(id) {
            return Err(Refusal::ResourceIds);
        }
        if space == options.scene.space && lower == options.scene.register {
            scene_range = Some(id);
        }
    }
    let range_id = i64::try_from(constant_buffers.len()).unwrap_or(i64::MAX);

    // Step 5's precondition: nothing up to the last position load reads a loaded column.
    let moved: HashSet<&str> = loads.values.iter().map(String::as_str).collect();
    if let Some(info) = body[0][..=loads.last]
        .iter()
        .find(|info| info.operands.iter().any(|value| moved.contains(value)))
    {
        return Err(Refusal::UsedBeforeInsertion(info.code.trim().to_owned()));
    }

    // Step 6's plan, on the original body.
    let mut previous_outcome = PreviousOutcome::NotAsked;
    let mut plan = None;
    if options.previous == Previous::Auto {
        let graph = Graph::read(&body, scene_range);
        match plan_previous(&body, &inputs, &loads, &graph, &carrier, options) {
            Ok(found) => {
                let last = found
                    .sources
                    .iter()
                    .map(|(_, l)| l.last)
                    .fold(loads.last, usize::max);
                let early = found
                    .tainted
                    .iter()
                    .find(|(_, (block, at))| *block == 0 && *at <= last);
                if let Some((value, _)) = early {
                    previous_outcome = PreviousOutcome::Skipped(format!(
                        "{value} of the previous slice is computed before the last position load"
                    ));
                } else {
                    plan = Some(found);
                }
            }
            Err(outcome) => previous_outcome = outcome,
        }
    }
    let last = match &plan {
        Some(plan) => plan
            .sources
            .iter()
            .map(|(_, l)| l.last)
            .fold(loads.last, usize::max),
        None => loads.last,
    };

    // The prelude and instance 0.
    let handle = "%er_h";
    let mut prelude = vec![handle_line(handle, &range_id.to_string())];
    let reads_parameters = carrier.reads_time() || carrier.reads_frame();
    let (time, frame) = if reads_parameters {
        read_row(&carrier, handle, TIME_REGISTER, "", &mut prelude)
    } else {
        ("undef".to_owned(), "undef".to_owned())
    };
    // What instance 0 reads, with the range id, which differs between a group's passes, left out.
    let mut prelude0 = prelude.clone();
    prelude0[0] = handle_line(handle, "$rid");
    let (previous_time, previous_frame) = if plan.is_some() && reads_parameters {
        read_row(&carrier, handle, PREVIOUS_TIME_REGISTER, "p", &mut prelude)
    } else {
        ("undef".to_owned(), "undef".to_owned())
    };
    let entry_label = blocks[0].label.clone();
    let feed0 = Feed {
        position: [
            loads.values[0].as_str(),
            loads.values[1].as_str(),
            loads.values[2].as_str(),
        ],
        time: &time,
        frame: &frame,
    };
    let mut current = entry_label.clone();
    let first = carrier.instance(0, &current, &feed0, handle);
    let instance0_hash = canonical_hash(&first, &feed0, &prelude0);
    if let Some(label) = &first.ends_in {
        current.clone_from(label);
    }
    let mut instances = vec![first];

    // Step 5: the game's later uses of x, y and z read instance 0's outputs.
    let mut redirect: HashMap<String, String> = HashMap::new();
    let mut back = Vec::new();
    for (column, output) in instances[0].outputs.iter().enumerate() {
        let original = &loads.values[column];
        if output != original {
            redirect.insert(original.clone(), output.clone());
            back.push((output.clone(), original.clone()));
        }
    }

    // Step 6: the previous position's instances, one per moved input its slice reads, and the
    // clones of its slice.
    let mut clones: HashMap<At, String> = HashMap::new();
    let mut store_values: HashMap<String, String> = HashMap::new();
    if let Some(plan) = &plan {
        let mut clone_map: HashMap<String, String> = HashMap::new();
        for (_, source) in &plan.sources {
            let feed = Feed {
                position: [
                    source.values[0].as_str(),
                    source.values[1].as_str(),
                    source.values[2].as_str(),
                ],
                time: &previous_time,
                frame: &previous_frame,
            };
            let instance = carrier.instance(instances.len(), &current, &feed, handle);
            if let Some(label) = &instance.ends_in {
                current.clone_from(label);
            }
            for (column, output) in instance.outputs.iter().enumerate() {
                if *output != source.values[column] {
                    clone_map.insert(source.values[column].clone(), output.clone());
                    back.push((output.clone(), source.values[column].clone()));
                }
            }
            instances.push(instance);
        }
        for (value, _) in &plan.tainted {
            let clone = format!("%er_pv_{}", &value[1..]);
            back.push((clone.clone(), value.clone()));
            clone_map.insert(value.clone(), clone);
        }
        for (_, (block, at)) in &plan.tainted {
            let info = &body[*block][*at];
            clones.insert((*block, *at), substitute(info.code, &clone_map));
        }
        for value in &plan.stored {
            if let Some(to) = clone_map.get(value) {
                store_values.insert(value.clone(), to.clone());
            }
        }
        previous_outcome = PreviousOutcome::Applied {
            element: plan.element,
            sources: plan.sources.iter().map(|(n, _)| (*n).to_owned()).collect(),
            cloned: plan.tainted.len(),
        };
    }
    let previous_element = plan.as_ref().map(|p| i64::from(p.element));

    // The dependency table, for the carrier's dependencies.
    let outputs = game.signature(1)?;
    let scalars = |element: &Element, row: i64| -> Option<[usize; 3]> {
        Some([
            element.scalar(row, 0)?,
            element.scalar(row, 1)?,
            element.scalar(row, 2)?,
        ])
    };
    let previous_scalars = plan.as_ref().and_then(|plan| {
        if !plan.sources.iter().any(|(name, _)| *name == "POSITION1") {
            return None;
        }
        let (element1, row1) = inputs
            .iter()
            .find_map(|e| e.row_of("POSITION", 1).map(|row| (e, row)))?;
        let output = outputs.iter().find(|e| e.id == plan.element)?;
        let written: Vec<usize> = (0..output.rows)
            .flat_map(|r| (0..output.columns).filter_map(move |c| output.scalar(r, c)))
            .collect();
        Some((scalars(element1, row1)?, written))
    });
    let mut view_id_line = None;
    let mut view_id_map = None;
    let view_id = match ViewIdState::read(&game).and_then(|mut state| {
        let position0 = scalars(position, row).ok_or("POSITION0 has no scalar index")?;
        compose_view_id(&mut state, &carrier, position0, previous_scalars)?;
        Ok(state)
    }) {
        Ok(state) => {
            let text = state.text();
            let changed = text != state.original;
            view_id_line = Some(format!("!{} = {text}", state.node));
            view_id_map = Some((text, state.original));
            ViewIdOutcome::Rewritten { changed }
        }
        Err(why) => ViewIdOutcome::Kept(why),
    };

    // Rewrite the game's lines after the insertion point.
    let last_instance = instances.len() - 1;
    let split = instances.iter().any(|instance| instance.ends_in.is_some());
    let exit_block = split.then(|| format!("%er_c{last_instance}_exit"));
    let mut relabel: HashMap<String, String> = HashMap::new();
    if let Some(exit) = &exit_block {
        relabel.insert(entry_label.clone(), exit.clone());
    }
    let mut rewritten: Vec<Vec<String>> = Vec::with_capacity(blocks.len());
    for (index, (block, infos)) in blocks.iter().zip(&body).enumerate() {
        let mut out = Vec::with_capacity(block.lines.len() + 1);
        for (at, (line, info)) in block.lines.iter().zip(infos).enumerate() {
            if index == 0 && at <= last {
                out.push((*line).to_owned());
                continue;
            }
            let mut code = std::borrow::Cow::Borrowed(info.code);
            if previous_element.is_some()
                && info.opcode() == Some(OP_STORE_OUTPUT)
                && info.int(1) == previous_element
                && let Some(value) = info.value(4)
                && let Some(to) = store_values.get(value)
            {
                let map = HashMap::from([(value.to_owned(), to.clone())]);
                code = std::borrow::Cow::Owned(substitute(info.code, &map));
            }
            let is_phi = computation(&code).starts_with("phi ");
            let mut text = match (info.def, code.split_once(" = ")) {
                // The definition itself keeps its name.
                (Some(_), Some((left, right))) => {
                    format!("{left} = {}", substitute(right, &redirect))
                }
                _ => substitute(&code, &redirect),
            };
            if is_phi && !relabel.is_empty() {
                text = relabel_phi(&text, &relabel);
            }
            out.push(format!("{text}{}{}", info.pad, info.comment));
            if let Some(clone) = clones.get(&(index, at)) {
                out.push(if is_phi && !relabel.is_empty() {
                    relabel_phi(clone, &relabel)
                } else {
                    clone.clone()
                });
            }
        }
        rewritten.push(out);
    }

    // Assemble the new body: the entry block up to the insertion point, the prelude and the
    // instances, then the rest of the entry block and every other block.
    let mut entry = rewritten.remove(0);
    let suffix = entry.split_off(last + 1);
    let mut added: Vec<String> = prelude.clone();
    for instance in &instances {
        added.extend(instance.entry.iter().cloned());
        for (label, lines) in &instance.blocks {
            added.push(String::new());
            added.push(format!("{}:", &label[1..]));
            added.extend(lines.iter().cloned());
        }
    }
    let mut inserted = prelude.len() + instances.iter().map(|i| i.instructions).sum::<usize>();
    let mut blocks_added = instances.iter().map(|i| i.blocks.len()).sum::<usize>();
    if let Some(exit) = &exit_block {
        added.push(format!("  br label {exit}"));
        added.push(String::new());
        added.push(format!("{}:", &exit[1..]));
        inserted += 1;
        blocks_added += 1;
    }
    let mut text_body: Vec<String> = entry;
    text_body.extend(added.iter().cloned());
    text_body.extend(suffix);
    for (block, lines) in blocks[1..].iter().zip(rewritten) {
        text_body.push(String::new());
        if let Some(label) = block.label_line {
            text_body.push(label.to_owned());
        }
        text_body.extend(lines);
    }

    // Step 7: types, declarations and attribute groups the new lines need.
    let merged = merge_declarations(&game, &carrier, &added)?;

    // Metadata: the parameter block, as the next constant-buffer range.
    let cb_node = game.next_node();
    let list_node = cb_node + 1;
    let mut cb_items: Vec<String> = if resources[2] == "null" {
        Vec::new()
    } else {
        game.tuple(resources[2])
            .unwrap_or_default()
            .iter()
            .map(|s| (*s).to_owned())
            .collect()
    };
    cb_items.push(format!("!{cb_node}"));
    let new_nodes = [
        format!(
            "!{cb_node} = !{{i32 {range_id}, {PARAMS_TYPE}* undef, !\"\", i32 {PARAMS_SPACE}, \
             i32 {PARAMS_REGISTER}, i32 1, i32 {PARAMS_BYTES}, null}}"
        ),
        format!("!{list_node} = !{{{}}}", cb_items.join(", ")),
    ];
    let resource_line = format!(
        "!{resource_node} = !{{{}, {}, !{list_node}, {}}}",
        resources[0], resources[1], resources[3]
    );

    let mut replaced = vec![resource_line];
    replaced.extend(view_id_line);
    let text = write_module(&game, &text_body, &merged, &replaced, &new_nodes);
    Ok(Spliced {
        text,
        map: SpliceMap {
            back,
            exit_block,
            entry_block: entry_label,
            added_types: merged.type_names,
            added_declarations: merged.declaration_names,
            added_resource: true,
            view_id_state: view_id_map,
        },
        report: SpliceReport {
            position_element: position.id,
            inserted,
            blocks_added,
            previous: previous_outcome,
            instance0_hash,
            range_id,
            view_id,
        },
    })
}

/// What [`merge_declarations`] adds to the game's module.
struct Merged {
    types: Vec<String>,
    type_names: Vec<String>,
    declarations: Vec<String>,
    declaration_names: Vec<String>,
    groups: Vec<String>,
}

/// The types, declarations and attribute groups the new body reads and the game lacks. A
/// declaration takes the game's attribute group of the same content, or a new one.
fn merge_declarations(
    game: &Module<'_>,
    carrier: &Carrier,
    text_body: &[String],
) -> Result<Merged, Refusal> {
    let mut merged = Merged {
        types: Vec::new(),
        type_names: Vec::new(),
        declarations: Vec::new(),
        declaration_names: Vec::new(),
        groups: Vec::new(),
    };
    let game_types: HashSet<&str> = game
        .lines
        .iter()
        .filter_map(|l| type_definition(l))
        .collect();
    let mut used: HashSet<&str> = HashSet::new();
    let mut called: BTreeSet<&str> = BTreeSet::new();
    for line in text_body {
        let code = split_comment(line).0;
        for (start, end) in local_tokens(code) {
            used.insert(&code[start..end]);
        }
        if let Some(at) = code.find("@dx.op.")
            && let Some(end) = code[at..].find('(')
        {
            called.insert(&code[at..at + end]);
        }
    }
    let mut add_type = |name: &str, definition: &str| {
        if !game_types.contains(name) && !merged.type_names.iter().any(|t| t == name) {
            merged.type_names.push(name.to_owned());
            merged.types.push(definition.to_owned());
        }
    };
    add_type(PARAMS_TYPE, PARAMS_TYPE_DEFINITION);
    let known = [
        ("%dx.types.Handle", "%dx.types.Handle = type { i8* }"),
        (
            "%dx.types.CBufRet.f32",
            "%dx.types.CBufRet.f32 = type { float, float, float, float }",
        ),
    ];
    for (name, definition) in known {
        if used.contains(name) {
            add_type(
                name,
                carrier.types.get(name).map_or(definition, String::as_str),
            );
        }
    }
    for (name, definition) in &carrier.types {
        if name.starts_with("%dx.types.") && used.contains(name.as_str()) {
            add_type(name, definition);
        }
    }
    let game_declared: HashSet<&str> = game
        .lines
        .iter()
        .filter_map(|line| declaration(line).map(|(name, _, _)| name))
        .collect();
    let mut groups: Vec<(u32, String)> = game.lines[game.close..]
        .iter()
        .filter_map(|line| attribute_group(line).map(|(n, c)| (n, c.to_owned())))
        .collect();
    let mut next_group = groups.iter().map(|(n, _)| n + 1).max().unwrap_or(0);
    for name in called {
        if game_declared.contains(name) {
            continue;
        }
        let (head, content) = match carrier.declarations.get(name) {
            Some((head, content)) => (head.clone(), content.clone()),
            None => match name {
                "@dx.op.createHandle" => (
                    "declare %dx.types.Handle @dx.op.createHandle(i32, i8, i32, i32, i1)"
                        .to_owned(),
                    "nounwind readonly".to_owned(),
                ),
                "@dx.op.cbufferLoadLegacy.f32" => (
                    "declare %dx.types.CBufRet.f32 @dx.op.cbufferLoadLegacy.f32(i32, \
                     %dx.types.Handle, i32)"
                        .to_owned(),
                    "nounwind readonly".to_owned(),
                ),
                other => {
                    return Err(Refusal::Unreadable(format!(
                        "{other} is called and declared nowhere"
                    )));
                }
            },
        };
        let group = if let Some((number, _)) = groups.iter().find(|(_, c)| *c == content) {
            *number
        } else {
            let number = next_group;
            next_group += 1;
            groups.push((number, content.clone()));
            merged
                .groups
                .push(format!("attributes #{number} = {{ {content} }}"));
            number
        };
        merged.declaration_names.push(name.to_owned());
        merged.declarations.push(String::new());
        merged
            .declarations
            .push(format!("; Function Attrs: {content}"));
        merged.declarations.push(format!("{head} #{group}"));
    }
    Ok(merged)
}

/// The game's module with the new body, the merged declarations, the rewritten metadata nodes in
/// `replaced` (each a whole `!N = ...` line) and the new nodes.
fn write_module(
    game: &Module<'_>,
    text_body: &[String],
    merged: &Merged,
    replaced: &[String],
    new_nodes: &[String],
) -> String {
    let last_type = game
        .lines
        .iter()
        .rposition(|line| type_definition(line).is_some());
    let last_declaration = game.lines[game.close..]
        .iter()
        .rposition(|line| declaration(line).is_some())
        .map_or(game.close, |at| at + game.close);
    let last_group = game.lines[game.close..]
        .iter()
        .rposition(|line| attribute_group(line).is_some())
        .map(|at| at + game.close);
    let replacement = |line: &str| {
        replaced.iter().find(|new| {
            new.split_once(" = ").is_some_and(|(name, _)| {
                line.strip_prefix(name)
                    .is_some_and(|r| r.starts_with(" = "))
            })
        })
    };
    let mut out = String::with_capacity(game.lines.len() * 64 + text_body.len() * 80);
    let mut push = |line: &str| {
        out.push_str(line);
        out.push('\n');
    };
    for (index, line) in game.lines.iter().enumerate() {
        if index > game.define && index < game.close {
            continue;
        }
        if index == game.define {
            if last_type.is_none() {
                merged.types.iter().for_each(|t| push(t));
                push("");
            }
            push(line);
            text_body.iter().for_each(|l| push(l));
            continue;
        }
        match replacement(line) {
            Some(new) if index > game.close => push(new),
            _ => push(line),
        }
        if Some(index) == last_type {
            merged.types.iter().for_each(|t| push(t));
        }
        if index == last_declaration {
            merged.declarations.iter().for_each(|d| push(d));
            if last_group.is_none() && !merged.groups.is_empty() {
                push("");
                merged.groups.iter().for_each(|g| push(g));
            }
        }
        if Some(index) == last_group {
            merged.groups.iter().for_each(|g| push(g));
        }
    }
    new_nodes.iter().for_each(|n| push(n));
    out
}

/// `code`, a phi, with the blocks `relabel` names replaced in its incoming pairs.
fn relabel_phi(code: &str, relabel: &HashMap<String, String>) -> String {
    let Some(open) = code.find('[') else {
        return code.to_owned();
    };
    let (head, pairs) = code.split_at(open);
    let mut out = head.to_owned();
    for (index, pair) in split_top(pairs).iter().enumerate() {
        if index > 0 {
            out.push_str(", ");
        }
        let inner = pair
            .trim()
            .strip_prefix('[')
            .and_then(|p| p.strip_suffix(']'))
            .unwrap_or(pair);
        match inner.rsplit_once(',') {
            Some((value, label)) => {
                let label = label.trim();
                let label = relabel.get(label).map_or(label, String::as_str);
                // Writing to a `String` cannot fail.
                let _ = write!(out, "[ {}, {label} ]", value.trim());
            }
            None => out.push_str(pair),
        }
    }
    out
}

// ---- rule 8 -------------------------------------------------------------------------------------

/// The variant `variant_ir` with every line the splice added taken out and every substitution
/// undone, in the canonical form [`canonical`] gives the original: rule 8 holds when the two are
/// equal.
///
/// Taken out: lines that define an `%er_` value, `er_` block labels, branches that name only `er_`
/// blocks, the declarations and types the splice added, and the parameter block's resource entry.
/// Undone: each name in [`SpliceMap::back`] back to the game value it stands for, the exit block
/// back to the entry block, and the rewritten `!dx.viewIdState` back to the game's, when it is
/// the table the splice wrote.
#[must_use]
pub fn residual(variant_ir: &str, map: &SpliceMap) -> String {
    let mut back: HashMap<String, String> = map.back.iter().cloned().collect();
    if let Some(exit) = &map.exit_block {
        back.insert(exit.clone(), map.entry_block.clone());
    }
    canonical_with(variant_ir, &back, map)
}

/// `ir` in canonical form: the header comments dropped; types and declarations as sorted sets
/// with attribute groups written out by content; the body as printed; named metadata with every
/// node written out in place, so two printings that numbered their nodes differently compare
/// equal.
#[must_use]
pub fn canonical(ir: &str) -> String {
    canonical_with(ir, &HashMap::new(), &SpliceMap::default())
}

fn canonical_with(ir: &str, back: &HashMap<String, String>, map: &SpliceMap) -> String {
    let lines: Vec<&str> = ir.lines().collect();
    let start = lines
        .iter()
        .position(|line| line.starts_with("target "))
        .unwrap_or(0);
    let mut groups: HashMap<u32, &str> = HashMap::new();
    let mut nodes: BTreeMap<u32, String> = BTreeMap::new();
    let mut named: Vec<(&str, &str)> = Vec::new();
    for line in &lines[start..] {
        if let Some((number, content)) = attribute_group(line) {
            groups.insert(number, content);
        } else if let Some(rest) = line.strip_prefix('!')
            && let Some((name, value)) = rest.split_once(" = ")
        {
            match name.parse::<u32>() {
                Ok(number) => {
                    nodes.insert(number, value.to_owned());
                }
                Err(_) => named.push((name, value)),
            }
        }
    }
    if map.added_resource {
        remove_params_resource(&mut nodes, &named);
    }
    let mut targets = Vec::new();
    let mut body = Vec::new();
    let mut types = BTreeSet::new();
    let mut declarations = BTreeSet::new();
    let mut in_body = false;
    for line in &lines[start..] {
        if line.starts_with("define ") {
            in_body = true;
            body.push((*line).to_owned());
            continue;
        }
        if in_body {
            if *line == "}" {
                in_body = false;
                body.push((*line).to_owned());
            } else if let Some(kept) = residual_body_line(line, back) {
                body.push(expand_references(kept, &nodes));
            }
            continue;
        }
        if line.starts_with("target ") {
            targets.push((*line).to_owned());
        } else if let Some(name) = type_definition(line) {
            if !map.added_types.iter().any(|t| t == name) {
                types.insert((*line).to_owned());
            }
        } else if let Some((name, head, group)) = declaration(line)
            && !map.added_declarations.iter().any(|d| d == name)
        {
            let content = group.and_then(|g| groups.get(&g)).copied().unwrap_or("");
            declarations.insert(format!("{head} #{{{content}}}"));
        }
    }
    let mut text = targets;
    text.push(String::new());
    text.extend(types);
    text.push(String::new());
    text.extend(body);
    text.push(String::new());
    text.extend(declarations);
    text.push(String::new());
    for (name, value) in named {
        let mut expanded = expand(value, &nodes, &mut Vec::new());
        // The dependency table the splice rewrote is undone only when it is exactly what the
        // splice wrote; anything else stays and shows as a difference.
        if name == "dx.viewIdState"
            && let Some((written, original)) = &map.view_id_state
            && expanded == format!("!{{{written}}}")
        {
            expanded = format!("!{{{original}}}");
        }
        text.push(format!("!{name} = {expanded}"));
    }
    let mut joined = text.join("\n");
    joined.push('\n');
    joined
}

/// One body line of a variant, or `None` when the splice added it.
fn residual_body_line(line: &str, back: &HashMap<String, String>) -> Option<String> {
    if line.trim().is_empty() {
        return None;
    }
    let code = split_comment(line).0;
    let added = if code.trim().is_empty() {
        // A label comment, `; <label>:N ; preds = ...`: a game block.
        false
    } else if !line.starts_with(' ') {
        // A named label: an added block when it is `er_...:`.
        code.trim().starts_with("er_")
    } else if defined(code).is_some_and(|value| value.starts_with("%er_")) {
        true
    } else if computation(code).starts_with("br ") {
        let mut labels = code
            .split("label ")
            .skip(1)
            .filter_map(|rest| rest.split([',', ' ']).next())
            .peekable();
        labels.peek().is_some() && labels.all(|label| label.starts_with("%er_"))
    } else {
        false
    };
    (!added).then(|| canonical_line(line, back))
}

/// `line` with the names `back` maps replaced and every run of spaces outside a string as one
/// space, trailing spaces dropped: the padding DXC puts before a comment depends on the line's
/// width, which says nothing about the instruction. One pass and one allocation, since this runs
/// on every line of two disassemblies per variant.
fn canonical_line(line: &str, back: &HashMap<String, String>) -> String {
    let bytes = line.as_bytes();
    let mut out = String::with_capacity(line.len());
    let mut at = 0;
    let mut quoted = false;
    let mut space = false;
    while at < bytes.len() {
        let c = bytes[at];
        if c == b'%' && !quoted {
            let mut end = at + 1;
            while end < bytes.len() && name_char(bytes[end]) {
                end += 1;
            }
            let token = &line[at..end];
            out.push_str(back.get(token).map_or(token, String::as_str));
            at = end;
            space = false;
            continue;
        }
        if c == b'"' {
            quoted = !quoted;
        }
        if c == b' ' && !quoted {
            if !space {
                out.push(' ');
            }
            space = true;
            at += 1;
            continue;
        }
        space = false;
        let width = line[at..].chars().next().map_or(1, char::len_utf8);
        out.push_str(&line[at..at + width]);
        at += width;
    }
    while out.ends_with(' ') {
        out.pop();
    }
    out
}

/// Take the parameter block's entry out of the constant-buffer list `!dx.resources` names.
fn remove_params_resource(nodes: &mut BTreeMap<u32, String>, named: &[(&str, &str)]) {
    let owned = |text: &str| -> Option<Vec<String>> {
        md_tuple(text).map(|items| items.into_iter().map(str::to_owned).collect())
    };
    let Some(resources) = named
        .iter()
        .find(|(name, _)| *name == "dx.resources")
        .and_then(|(_, value)| md_tuple(value))
        .and_then(|items| items.first().and_then(|item| md_ref(item)))
    else {
        return;
    };
    let Some(mut tuple) = nodes.get(&resources).and_then(|t| owned(t)) else {
        return;
    };
    if tuple.len() != 4 {
        return;
    }
    let Some(list) = md_ref(&tuple[2]) else {
        return;
    };
    let Some(items) = nodes.get(&list).and_then(|t| owned(t)) else {
        return;
    };
    let kept: Vec<String> = items
        .into_iter()
        .filter(|item| {
            let fields = md_ref(item)
                .and_then(|n| nodes.get(&n))
                .and_then(|t| owned(t))
                .unwrap_or_default();
            !(fields.len() >= 5
                && md_int(&fields[3]) == Some(PARAMS_SPACE)
                && md_int(&fields[4]) == Some(PARAMS_REGISTER))
        })
        .collect();
    if kept.is_empty() {
        tuple[2] = "null".to_owned();
    } else {
        nodes.insert(list, format!("!{{{}}}", kept.join(", ")));
    }
    nodes.insert(resources, format!("!{{{}}}", tuple.join(", ")));
}

/// `text` with every `!N` reference written out as the node it names, recursively.
fn expand(text: &str, nodes: &BTreeMap<u32, String>, open: &mut Vec<u32>) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut at = 0;
    let mut quoted = false;
    while at < bytes.len() {
        let c = bytes[at];
        if c == b'"' {
            quoted = !quoted;
        }
        if !quoted && c == b'!' && bytes.get(at + 1).is_some_and(u8::is_ascii_digit) {
            let mut end = at + 1;
            while end < bytes.len() && bytes[end].is_ascii_digit() {
                end += 1;
            }
            let number: u32 = text[at + 1..end].parse().unwrap_or(u32::MAX);
            match nodes.get(&number) {
                Some(node) if !open.contains(&number) => {
                    open.push(number);
                    out.push_str(&expand(node, nodes, open));
                    open.pop();
                }
                Some(_) => out.push_str("!<cycle>"),
                None => out.push_str("!<missing>"),
            }
            at = end;
            continue;
        }
        // Copy one whole character, so text that is not ASCII stays intact.
        let width = text[at..].chars().next().map_or(1, char::len_utf8);
        out.push_str(&text[at..at + width]);
        at += width;
    }
    out
}

/// A body line with its metadata attachments (`!dx.controlflow.hints !12`) written out.
fn expand_references(line: String, nodes: &BTreeMap<u32, String>) -> String {
    let (code, comment) = split_comment(&line);
    if !code.contains(" !") {
        return line;
    }
    format!("{}{comment}", expand(code, nodes, &mut Vec::new()))
}

/// The first line where `left` and `right` differ: (line number, left, right).
#[must_use]
pub fn first_difference(left: &str, right: &str) -> Option<(usize, String, String)> {
    let mut a = left.lines();
    let mut b = right.lines();
    let mut number = 1;
    loop {
        match (a.next(), b.next()) {
            (None, None) => return None,
            (x, y) if x == y => number += 1,
            (x, y) => {
                return Some((
                    number,
                    x.unwrap_or("<end>").to_owned(),
                    y.unwrap_or("<end>").to_owned(),
                ));
            }
        }
    }
}

#[cfg(test)]
mod tests;
