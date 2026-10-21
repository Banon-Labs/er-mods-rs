//! The vertex splice measured over a vkd3d-proton shader dump: the pass/fail numbers of V0
//! (`docs/plans/er-shader-editor-vertex.md`, section 11), items 1 to 10.
//!
//! For every vertex shader in the dump it disassembles the container, reassembles the text
//! unchanged (item 1), then splices each carrier into it, assembles, validates and signs the
//! result, copies the original's debug name in and signs it again, and checks the variant against
//! the original by the nine rules of section 7.4 (rules 1 to 8 here; rule 9 is the pipeline
//! stream, which needs the DLL's record). It runs `dxv`, dxil-spirv and `spirv-val` on each
//! variant, `check_previous.py` on each velocity pass, `pos_slice.py` on every original and variant,
//! and reads the pipeline cache through `scripts/er-shader-vertex-census.py --pipelines-tsv`.
//!
//! # Usage
//!
//! ```text
//! vsplice_sweep --out <dir> [--dump <dir>] [--carriers a,b,...] [--jobs N] [--limit N]
//!               [--timing] [--research-refused <tsv>] [--dxa <dxa_lite>] [--dxc <dxc>]
//!               [--dxil-spirv <path>] [--spirv-val <path>]
//! ```
//!
//! The dump defaults to `ER_VKD3D_DUMP_ROOT`, then to `.worktrees/er-shaders/target/vkd3d-dump/
//! run1/spv` under the main checkout. A full sweep takes minutes, so run it in the background.
//! `--timing` runs one thread, two carriers (`wobble`, `branchy`) and the in-process steps only,
//! which is how item 10 is measured.
//!
//! # Tools
//!
//! - `dxc` and `dxv`: `--dxc`, else `$DXC`, else `~/tools/dxc/bin/dxc`; `dxv` beside it.
//! - `dxa_lite`: `--dxa`, else `$DXA_LITE`. Built from `scripts/vs-patch/dxa_lite.cpp`; the line
//!   this prototype was measured with:
//!   `g++ -std=c++17 -O2 -I ~/tools/dxc/include scripts/vs-patch/dxa_lite.cpp -L ~/tools/dxc/lib
//!   -ldxcompiler -Wl,-rpath,$HOME/tools/dxc/lib -o dxa_lite`. Its `--serve` mode keeps
//!   libdxcompiler loaded and times each call inside the process.
//! - dxil-spirv: `--dxil-spirv`, else `$DXIL_SPIRV`; run with `--invariant-position --ssbo-srv`,
//!   the flags the lift research used. Without it item 8 is reported unmeasured.
//! - `spirv-val`: `--spirv-val`, else `/usr/sbin/spirv-val`, else `spirv-val` on `PATH`.
//!
//! Missing `dxc`, `dxv`, `dxa_lite` or the dump is a skip with a message, not a failure.
//!
//! # Output
//!
//! - `<out>/results.jsonl`: one line per (shader, carrier); carrier `none` is the round trip.
//! - `<out>/summary.json`: the numbers of items 1 to 10, and `verdict`.
//! - `<out>/blobs/<hash>.<carrier>.dxil`: every variant container.
//! - `<out>/work/<hash>/`: the disassemblies the checks read.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fmt::Write as _;
use std::fs;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, ExitCode, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use er_cel_shade::dxbc::{self, Container, PSV_RESOURCE_CBV, ShaderKind};
use er_cel_shade::editor::annotations;
use vsplice_proto::vsplice::{
    self, Previous, PreviousOutcome, Refusal, SpliceOptions, Spliced, ViewIdOutcome, check_carrier,
};

/// The repository this binary was built in: `scripts/vsplice/../..`.
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

// ---- JSON ---------------------------------------------------------------------------------------

/// Just enough JSON to write the results: no reader is needed.
#[derive(Clone, Debug)]
enum Json {
    Null,
    Bool(bool),
    Num(f64),
    Int(i64),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    fn obj() -> Self {
        Self::Obj(Vec::new())
    }

    fn set(&mut self, key: &str, value: impl Into<Json>) -> &mut Self {
        if let Self::Obj(fields) = self {
            fields.push((key.to_owned(), value.into()));
        }
        self
    }

    fn write(&self, out: &mut String) {
        match self {
            Self::Null => out.push_str("null"),
            Self::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Self::Num(n) if n.is_finite() => {
                let _ = write!(out, "{}", (n * 10000.0).round() / 10000.0);
            }
            Self::Num(_) => out.push_str("null"),
            Self::Int(n) => {
                let _ = write!(out, "{n}");
            }
            Self::Str(s) => {
                out.push('"');
                for c in s.chars() {
                    match c {
                        '"' => out.push_str("\\\""),
                        '\\' => out.push_str("\\\\"),
                        '\n' => out.push_str("\\n"),
                        '\r' => out.push_str("\\r"),
                        '\t' => out.push_str("\\t"),
                        c if (c as u32) < 0x20 => {
                            let _ = write!(out, "\\u{:04x}", c as u32);
                        }
                        c => out.push(c),
                    }
                }
                out.push('"');
            }
            Self::Arr(items) => {
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    item.write(out);
                }
                out.push(']');
            }
            Self::Obj(fields) => {
                out.push('{');
                for (index, (key, value)) in fields.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    Self::Str(key.clone()).write(out);
                    out.push(':');
                    value.write(out);
                }
                out.push('}');
            }
        }
    }

    fn text(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }
}

impl From<bool> for Json {
    fn from(value: bool) -> Self {
        Self::Bool(value)
    }
}
impl From<f64> for Json {
    fn from(value: f64) -> Self {
        Self::Num(value)
    }
}
impl From<usize> for Json {
    fn from(value: usize) -> Self {
        Self::Int(i64::try_from(value).unwrap_or(i64::MAX))
    }
}
impl From<i64> for Json {
    fn from(value: i64) -> Self {
        Self::Int(value)
    }
}
impl From<u32> for Json {
    fn from(value: u32) -> Self {
        Self::Int(i64::from(value))
    }
}
impl From<&str> for Json {
    fn from(value: &str) -> Self {
        Self::Str(value.to_owned())
    }
}
impl From<String> for Json {
    fn from(value: String) -> Self {
        Self::Str(value)
    }
}
impl<T: Into<Json>> From<Option<T>> for Json {
    fn from(value: Option<T>) -> Self {
        value.map_or(Self::Null, Into::into)
    }
}
impl<T: Into<Json>> From<Vec<T>> for Json {
    fn from(value: Vec<T>) -> Self {
        Self::Arr(value.into_iter().map(Into::into).collect())
    }
}

// ---- tools --------------------------------------------------------------------------------------

#[derive(Clone, Debug)]
struct Tools {
    dxc: PathBuf,
    dxv: PathBuf,
    dxa: PathBuf,
    dxil_spirv: Option<PathBuf>,
    spirv_val: Option<PathBuf>,
    shaders: PathBuf,
    carriers: PathBuf,
    pos_slice: PathBuf,
    check_previous: PathBuf,
    census: PathBuf,
}

/// `dxa_lite --serve`, one per worker: libdxcompiler loaded once, each step timed inside it.
struct Server {
    child: Child,
    input: Option<ChildStdin>,
    output: BufReader<ChildStdout>,
}

impl Server {
    fn start(dxa: &Path) -> Result<Self, String> {
        let mut child = Command::new(dxa)
            .arg("--serve")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("{} did not start: {error}", dxa.display()))?;
        let input = child.stdin.take();
        let output = child.stdout.take().map(BufReader::new).ok_or("no stdout")?;
        Ok(Self {
            child,
            input,
            output,
        })
    }

    /// One request; `Ok` carries the milliseconds the server measured.
    fn ask(&mut self, op: &str, from: &Path, to: &Path) -> Result<Vec<f64>, String> {
        let input = self.input.as_mut().ok_or("the server's input is closed")?;
        writeln!(input, "{op}\t{}\t{}", from.display(), to.display())
            .and_then(|()| input.flush())
            .map_err(|error| format!("dxa_lite: {error}"))?;
        let mut line = String::new();
        self.output
            .read_line(&mut line)
            .map_err(|error| format!("dxa_lite: {error}"))?;
        let fields: Vec<&str> = line.trim_end().split('\t').collect();
        match fields.as_slice() {
            ["ok", times @ ..] => Ok(times.iter().filter_map(|t| t.parse().ok()).collect()),
            ["err", stage, rest @ ..] => Err(format!("{stage}: {}", rest.join(" "))),
            _ => Err(format!("dxa_lite answered {line:?}")),
        }
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.input.take();
        let _ = self.child.wait();
    }
}

/// Run a tool; `Ok` with its output when it exits 0.
fn run(program: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new(program)
        .args(args)
        .output()
        .map_err(|error| format!("{} did not run: {error}", program.display()))?;
    // UTF-8 Lossy: tool diagnostics are shown to a person, and a stray byte must not hide them.
    let mut text = String::from_utf8_lossy(&output.stdout).into_owned();
    // UTF-8 Lossy: as above, for the tool's error stream.
    text.push_str(&String::from_utf8_lossy(&output.stderr));
    if output.status.success() {
        Ok(text)
    } else {
        Err(first_line(&text))
    }
}

fn first_line(text: &str) -> String {
    text.lines()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("")
        .chars()
        .take(300)
        .collect()
}

// ---- carriers -----------------------------------------------------------------------------------

/// Where a carrier comes from, and the refusal rule a refusal carrier must be refused by.
#[derive(Clone, Copy, Debug)]
enum Source {
    /// A vertex layer template in `crates/er-cel-shade/shaders/templates/vertex/`, generated into a
    /// carrier as section 7.3's codegen does.
    Template,
    /// A carrier in `crates/er-shader-compiler/tests/carriers/`.
    Test,
    /// A carrier there that must be refused, and the rule that must refuse it.
    Refused(&'static str),
}

const CARRIERS: [(&str, Source); 12] = [
    ("wobble", Source::Template),
    ("sway", Source::Template),
    ("breathe", Source::Template),
    ("proof_lift", Source::Template),
    ("proof_lift_parity", Source::Template),
    ("branchy", Source::Test),
    ("loop", Source::Test),
    ("texture", Source::Refused("resource")),
    ("uav", Source::Refused("resource")),
    ("wave", Source::Refused("operation")),
    ("half", Source::Refused("type")),
    ("sv", Source::Refused("signature")),
];

/// The slot the sweep's one-layer carriers sit in.
const LAYER_SLOT: usize = 1;

/// A template layer as the carrier section 7.3's codegen writes: the layer in its namespace with
/// its parameters defined onto its slot, the stage function mixing it in by the slot header, and
/// `main` feeding it.
fn template_source(name: &str, text: &str) -> Result<String, String> {
    let annotated = annotations::parse(text, LAYER_SLOT);
    if annotated.has_errors() {
        return Err(format!("{name}: {:?}", annotated.problems));
    }
    let mut out = String::from("#include \"vertex_ctx.hlsli\"\n");
    let _ = writeln!(out, "namespace L{LAYER_SLOT}\n{{");
    for param in &annotated.params {
        out.push_str(&param.define());
        out.push('\n');
    }
    let _ = writeln!(out, "#line 1 \"layers/{LAYER_SLOT}-{name}.hlsl\"");
    out.push_str(&annotated.text);
    out.push('\n');
    for param in &annotated.params {
        let _ = writeln!(out, "#undef {}", param.name);
    }
    out.push_str("}\n");
    let _ = write!(
        out,
        "float3 er_vertex_stage(float3 p, VertexCtx c)\n{{\n    float4 h{LAYER_SLOT} = \
         er_p[ER_P_LAYERS + {LAYER_SLOT} * ER_P_LAYER_REGISTERS];\n    p = lerp(p, \
         L{LAYER_SLOT}::apply(p, c), h{LAYER_SLOT}.x * h{LAYER_SLOT}.y);\n    return p;\n}}\n"
    );
    out.push_str(
        "void main(float3 p : ER_POS, float t : ER_TIME, uint f : ER_FRAME, out float3 o : \
         ER_OUT)\n{\n    VertexCtx c;\n    c.time = t;\n    c.frame = f;\n    o = \
         er_vertex_stage(p, c);\n}\n",
    );
    Ok(out)
}

/// A compiled carrier: its disassembly, or why it has none.
struct CarrierBuild {
    name: &'static str,
    source: Source,
    text: Option<String>,
    compile_ms: f64,
    error: Option<String>,
    check: Result<(), Refusal>,
}

fn build_carrier(
    name: &'static str,
    source: Source,
    tools: &Tools,
    dir: &Path,
    server: &mut Server,
) -> CarrierBuild {
    let mut build = CarrierBuild {
        name,
        source,
        text: None,
        compile_ms: 0.0,
        error: None,
        check: Ok(()),
    };
    let hlsl = match source {
        Source::Template => {
            let file = tools
                .shaders
                .join("templates/vertex")
                .join(format!("{name}.hlsl"));
            let generated = fs::read_to_string(&file)
                .map_err(|e| format!("{}: {e}", file.display()))
                .and_then(|text| template_source(name, &text));
            match generated {
                Ok(text) => {
                    let path = dir.join(format!("{name}.hlsl"));
                    if let Err(error) = fs::write(&path, text) {
                        build.error = Some(error.to_string());
                        return build;
                    }
                    path
                }
                Err(error) => {
                    build.error = Some(error);
                    return build;
                }
            }
        }
        Source::Test | Source::Refused(_) => tools.carriers.join(format!("{name}.hlsl")),
    };
    let object = dir.join(format!("{name}.dxil"));
    let include = tools.shaders.display().to_string();
    let started = Instant::now();
    let compiled = run(
        &tools.dxc,
        &[
            "-T",
            "vs_6_0",
            "-E",
            "main",
            "-O3",
            "-Qstrip_debug",
            "-HV",
            "2021",
            "-validator-version",
            "1.6",
            "-I",
            &include,
            &hlsl.display().to_string(),
            "-Fo",
            &object.display().to_string(),
        ],
    );
    build.compile_ms = started.elapsed().as_secs_f64() * 1000.0;
    if let Err(error) = compiled {
        build.error = Some(format!("dxc: {error}"));
        return build;
    }
    let listing = dir.join(format!("{name}.ll"));
    if let Err(error) = server.ask("dis", &object, &listing) {
        build.error = Some(error);
        return build;
    }
    match fs::read_to_string(&listing) {
        Ok(text) => {
            build.check = check_carrier(&text);
            build.text = Some(text);
        }
        Err(error) => build.error = Some(error.to_string()),
    }
    build
}

// ---- names --------------------------------------------------------------------------------------

/// The pass tokens of a vertex shader's debug name (design 7.5).
const VS_PASSES: [&str; 10] = [
    "Gbuf", "GBuf", "Dpt", "DptA", "Velo", "Fwd", "Pnt", "PntCol", "FurDir", "Spec",
];

/// The file name of a debug name's path.
fn base_name(ildn: &str) -> &str {
    ildn.rsplit(['\\', '/']).next().unwrap_or(ildn)
}

/// `_`-separated tokens outside brackets.
fn tokens(stem: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut depth = 0_i32;
    let mut start = 0;
    for (at, c) in stem.char_indices() {
        match c {
            '[' => depth += 1,
            ']' => depth -= 1,
            '_' if depth == 0 => {
                out.push(&stem[start..at]);
                start = at + 1;
            }
            _ => {}
        }
    }
    out.push(&stem[start..]);
    out
}

/// (group, pass) of section 7.5: the name's stem with the `@[...]` suffix and the pass token
/// removed. A name with no pass token is its own group, with no pass.
fn group_of(ildn: &str) -> (String, String) {
    let base = base_name(ildn);
    let stem = base
        .strip_suffix(".pdb")
        .and_then(|s| s.rsplit_once('.').map(|(stem, _)| stem))
        .unwrap_or(base);
    let stem = stem.split('@').next().unwrap_or(stem);
    let parts = tokens(stem);
    match parts.iter().rposition(|t| VS_PASSES.contains(t)) {
        Some(at) if at > 0 => {
            let group: Vec<&str> = parts
                .iter()
                .enumerate()
                .filter(|(index, _)| *index != at)
                .map(|(_, t)| *t)
                .collect();
            (group.join("_"), parts[at].to_owned())
        }
        _ => (stem.to_owned(), String::new()),
    }
}

/// The research summary's key for the `pos_slice.py` agreement figure, the regular expression
/// `PASS` in `scripts/vs-patch/summarize.py`: `<base>_<pass>[_Skin][_FT][@[cl]].vpo.pdb` gives
/// ((base, skinned), pass).
fn summary_key(ildn: &str) -> Option<((String, bool), String)> {
    let mut rest = base_name(ildn).strip_suffix(".vpo.pdb")?;
    rest = rest.strip_suffix("@[cl]").unwrap_or(rest);
    rest = rest.strip_suffix("_FT").unwrap_or(rest);
    let skinned = rest.ends_with("_Skin");
    rest = rest.strip_suffix("_Skin").unwrap_or(rest);
    VS_PASSES.iter().find_map(|pass| {
        let base = rest.strip_suffix(pass)?.strip_suffix('_')?;
        (!base.is_empty()).then(|| ((base.to_owned(), skinned), (*pass).to_owned()))
    })
}

// ---- containers ---------------------------------------------------------------------------------

/// `container` with part `code` holding `data` inserted before its `HASH` part (or at the end),
/// signed with DXC's retail hash.
fn with_part(container: &[u8], code: &[u8; 4], data: &[u8]) -> Result<Vec<u8>, String> {
    let parsed = Container::parse(container).map_err(|e| e.to_string())?;
    let mut parts: Vec<([u8; 4], &[u8])> =
        parsed.parts().iter().map(|p| (p.code, p.data)).collect();
    let at = parts
        .iter()
        .position(|(c, _)| c == b"HASH")
        .unwrap_or(parts.len());
    parts.insert(at, (*code, data));
    let header = 32 + 4 * parts.len();
    let mut out = vec![0_u8; header];
    out[..4].copy_from_slice(b"DXBC");
    out[20..24].copy_from_slice(&container[20..24]);
    let count = u32::try_from(parts.len()).map_err(|e| e.to_string())?;
    out[28..32].copy_from_slice(&count.to_le_bytes());
    for (index, (code, data)) in parts.iter().enumerate() {
        let offset = u32::try_from(out.len()).map_err(|e| e.to_string())?;
        out[32 + 4 * index..36 + 4 * index].copy_from_slice(&offset.to_le_bytes());
        out.extend_from_slice(code);
        let size = u32::try_from(data.len()).map_err(|e| e.to_string())?;
        out.extend_from_slice(&size.to_le_bytes());
        out.extend_from_slice(data);
    }
    let total = u32::try_from(out.len()).map_err(|e| e.to_string())?;
    out[24..28].copy_from_slice(&total.to_le_bytes());
    let digest = dxbc::retail_hash(&out);
    out[4..20].copy_from_slice(&digest);
    Ok(out)
}

/// One `PSVSignatureElement0`, with its name and semantic indexes resolved.
#[derive(Clone, Debug, PartialEq, Eq)]
struct PsvElement {
    name: String,
    indexes: Vec<u32>,
    rows: u8,
    start_row: u8,
    columns: u8,
    start_column: u8,
    allocated: bool,
    kind: u8,
    component: u8,
    interpolation: u8,
    dynamic_mask_and_stream: u8,
}

/// The parts of `PSV0` the vertex rules compare.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Psv {
    runtime: Vec<u8>,
    inputs: Vec<PsvElement>,
    outputs: Vec<PsvElement>,
    patch: Vec<PsvElement>,
    /// Everything after the signature elements: output masks and input-to-output dependencies.
    tail: Vec<u8>,
}

fn u32_at(bytes: &[u8], at: usize) -> Result<u32, String> {
    bytes
        .get(at..at + 4)
        .and_then(|b| b.try_into().ok())
        .map(u32::from_le_bytes)
        .ok_or_else(|| format!("PSV0 ends at {at}"))
}

fn parse_psv(part: &[u8]) -> Result<Psv, String> {
    let mut at = 0;
    let mut take = |len: usize| -> Result<&[u8], String> {
        let bytes = part
            .get(at..at + len)
            .ok_or_else(|| format!("PSV0 ends inside {len} bytes at {at}"))?;
        at += len;
        Ok(bytes)
    };
    let runtime_size = u32_at(take(4)?, 0)? as usize;
    let runtime = take(runtime_size)?.to_vec();
    let resources = u32_at(take(4)?, 0)? as usize;
    if resources > 0 {
        let record = u32_at(take(4)?, 0)? as usize;
        take(record * resources)?;
    }
    if runtime_size < 36 {
        return Err("PSV0 runtime info version 0".to_owned());
    }
    let strings_size = u32_at(take(4)?, 0)? as usize;
    let strings = take(strings_size)?.to_vec();
    let index_count = u32_at(take(4)?, 0)? as usize;
    let index_bytes = take(4 * index_count)?;
    let indexes: Vec<u32> = index_bytes
        .as_chunks::<4>()
        .0
        .iter()
        .map(|b| u32::from_le_bytes(*b))
        .collect();
    let counts = [
        usize::from(runtime[28]),
        usize::from(runtime[29]),
        usize::from(runtime[30]),
    ];
    let mut lists: [Vec<PsvElement>; 3] = Default::default();
    if counts.iter().sum::<usize>() > 0 {
        let record = u32_at(take(4)?, 0)? as usize;
        for (list, count) in lists.iter_mut().zip(counts) {
            for _ in 0..count {
                let e = take(record)?;
                let name_at = u32_at(e, 0)? as usize;
                let index_at = u32_at(e, 4)? as usize;
                let name = strings
                    .get(name_at..)
                    .and_then(|s| s.split(|b| *b == 0).next())
                    .map(|s| s.iter().map(|b| char::from(*b)).collect())
                    .unwrap_or_default();
                let rows = e[8];
                list.push(PsvElement {
                    name,
                    indexes: indexes
                        .get(index_at..index_at + usize::from(rows))
                        .map(<[u32]>::to_vec)
                        .unwrap_or_default(),
                    rows,
                    start_row: e[9],
                    columns: e[10] & 0xf,
                    start_column: (e[10] >> 4) & 0x3,
                    allocated: e[10] & 0x40 != 0,
                    kind: e[11],
                    component: e[12],
                    interpolation: e[13],
                    dynamic_mask_and_stream: e[14],
                });
            }
        }
    }
    let tail = part.get(at..).unwrap_or_default().to_vec();
    let [inputs, outputs, patch] = lists;
    Ok(Psv {
        runtime,
        inputs,
        outputs,
        patch,
        tail,
    })
}

/// Elements by (start row, start column), which is how the vertex rules pair them: element order
/// differs from register order in many game shaders.
fn by_start(list: &[PsvElement]) -> BTreeMap<(u8, u8), &PsvElement> {
    list.iter()
        .map(|e| ((e.start_row, e.start_column), e))
        .collect()
}

/// One rule's verdict, and why when it failed.
type Verdict = Result<(), String>;

/// Rules 1 to 7 of section 7.4 for a variant against its original.
fn container_rules(original: &[u8], variant: &[u8]) -> [Verdict; 7] {
    let mut out: [Verdict; 7] = std::array::from_fn(|_| Ok(()));
    let (orig, var) = match (Container::parse(original), Container::parse(variant)) {
        (Ok(o), Ok(v)) => (o, v),
        (_, Err(error)) => {
            let why = Err(format!("the variant does not parse: {error}"));
            return [
                why.clone(),
                why.clone(),
                why.clone(),
                why.clone(),
                why.clone(),
                why.clone(),
                why,
            ];
        }
        (Err(error), _) => {
            let why = Err(format!("the original does not parse: {error}"));
            return [
                why.clone(),
                why.clone(),
                why.clone(),
                why.clone(),
                why.clone(),
                why.clone(),
                why,
            ];
        }
    };
    // Rule 1: container.
    out[0] = (|| {
        if var.total_size() != variant.len() {
            return Err(format!(
                "declares {} bytes of {}",
                var.total_size(),
                variant.len()
            ));
        }
        if variant.len() > 1 << 20 {
            return Err(format!("{} bytes", variant.len()));
        }
        let codes: Vec<[u8; 4]> = var.parts().iter().map(|p| p.code).collect();
        let unique: HashSet<&[u8; 4]> = codes.iter().collect();
        if unique.len() != codes.len() {
            return Err("a part appears twice".to_owned());
        }
        let (o, v) = (orig.program(), var.program());
        match (o, v) {
            (Ok(o), Ok(v))
                if v.kind == ShaderKind::Vertex && (v.major, v.minor) == (o.major, o.minor) =>
            {
                Ok(())
            }
            (o, v) => Err(format!("program {v:?}, original {o:?}")),
        }
    })();
    // Rule 2: signed.
    out[1] = if var.is_signed_correctly() {
        Ok(())
    } else {
        Err("the digest is not the retail hash".to_owned())
    };
    let psv = |c: &Container<'_>| -> Result<Psv, String> {
        c.part(dxbc::PSV0_PART)
            .ok_or_else(|| "no PSV0".to_owned())
            .and_then(parse_psv)
    };
    let psvs = psv(&orig).and_then(|o| psv(&var).map(|v| (o, v)));
    // Rule 3: input linkage.
    out[2] = (|| {
        let (o, v) = psvs.as_ref().map_err(Clone::clone)?;
        let a = orig.signature(dxbc::INPUT_SIGNATURE_PART);
        let b = var.signature(dxbc::INPUT_SIGNATURE_PART);
        if a != b {
            return Err("ISG1 differs".to_owned());
        }
        if by_start(&o.inputs) != by_start(&v.inputs) {
            return Err("PSV0 input records differ".to_owned());
        }
        Ok(())
    })();
    // Rule 4: output linkage.
    out[3] = (|| {
        let (o, v) = psvs.as_ref().map_err(Clone::clone)?;
        let a = orig.signature(dxbc::OUTPUT_SIGNATURE_PART);
        let b = var.signature(dxbc::OUTPUT_SIGNATURE_PART);
        if a != b {
            return Err("OSG1 differs".to_owned());
        }
        if by_start(&o.outputs) != by_start(&v.outputs) || o.patch != v.patch {
            return Err("PSV0 output records or interpolation differ".to_owned());
        }
        if o.runtime.first() != v.runtime.first() {
            return Err("the position-output flag differs".to_owned());
        }
        Ok(())
    })();
    // Rule 5: resources.
    out[4] = (|| {
        let a: HashSet<_> = orig
            .psv0_binds()
            .map_err(|e| e.to_string())?
            .into_iter()
            .collect();
        for bind in var.psv0_binds().map_err(|e| e.to_string())? {
            let params = bind.resource_type == PSV_RESOURCE_CBV
                && bind.space == 42
                && bind.lower_bound == 0
                && bind.upper_bound == 0;
            if !params && !a.contains(&bind) {
                return Err(format!("{bind:?} is not the original's"));
            }
        }
        Ok(())
    })();
    // Rule 6: features.
    out[5] = match (orig.sfi0(), var.sfi0()) {
        (Ok(o), Ok(v)) if v & !o == 0 => Ok(()),
        (o, v) => Err(format!("SFI0 {v:?}, original {o:?}")),
    };
    // Rule 7: no root signature of its own.
    out[6] = if var.part(dxbc::ROOT_SIGNATURE_PART).is_none() {
        Ok(())
    } else {
        Err("an RTS0 part".to_owned())
    };
    out
}

/// What reassembly changed in PSV0 beyond the signature records: informative, not a rule.
fn psv_changes(original: &[u8], variant: &[u8]) -> Json {
    let psv = |bytes: &[u8]| {
        Container::parse(bytes)
            .ok()
            .and_then(|c| c.part(dxbc::PSV0_PART).map(<[u8]>::to_vec))
            .and_then(|part| parse_psv(&part).ok())
    };
    let mut out = Json::obj();
    if let (Some(o), Some(v)) = (psv(original), psv(variant)) {
        out.set("runtime_same", o.runtime == v.runtime);
        out.set("dependencies_same", o.tail == v.tail);
    }
    out
}

// ---- the sweep ----------------------------------------------------------------------------------

struct Config {
    dump: PathBuf,
    out: PathBuf,
    carriers: Vec<&'static str>,
    jobs: usize,
    limit: Option<usize>,
    timing: bool,
    research_refused: Option<PathBuf>,
}

/// One (shader, carrier) result.
struct Row {
    json: Json,
    hash: String,
    carrier: String,
    status: String,
    group: String,
    pass: String,
    name: String,
    /// Rules 1 to 8, each `Some(true)` when it passed; `None` when not run.
    rules: [Option<bool>; 8],
    instance0_hash: Option<u64>,
    previous: Option<PreviousOutcome>,
    previous_check: Option<String>,
    dxv: Option<bool>,
    spirv: Option<bool>,
    spirv_val: Option<bool>,
    spirv_baseline: Option<bool>,
    times: BTreeMap<&'static str, f64>,
    variant_listing: Option<PathBuf>,
    refusal: Option<String>,
    asm_error: Option<String>,
    round_trip: Option<RoundTrip>,
    /// Whether rule 8 failed with one substitution left undone, as it must.
    rule8_sensitive: Option<bool>,
    /// What the splice did with the dependency table: `rewritten`, `unchanged`, or `kept: why`.
    view_id: Option<String>,
}

#[derive(Clone, Copy, Debug, Default)]
struct RoundTrip {
    assembled: bool,
    ir_same: bool,
    dxv: bool,
    parts_same: bool,
}

impl Row {
    fn new(hash: &str, carrier: &str, name: &str, group: &str, pass: &str) -> Self {
        Self {
            json: Json::obj(),
            hash: hash.to_owned(),
            carrier: carrier.to_owned(),
            status: String::new(),
            group: group.to_owned(),
            pass: pass.to_owned(),
            name: name.to_owned(),
            rules: [None; 8],
            instance0_hash: None,
            previous: None,
            previous_check: None,
            dxv: None,
            spirv: None,
            spirv_val: None,
            spirv_baseline: None,
            times: BTreeMap::new(),
            variant_listing: None,
            refusal: None,
            asm_error: None,
            round_trip: None,
            rule8_sensitive: None,
            view_id: None,
        }
    }

    fn finish(&mut self, pos_slice: Option<&String>) -> Json {
        let mut json = Json::obj();
        json.set("hash", self.hash.as_str())
            .set("name", self.name.as_str())
            .set("group", self.group.as_str())
            .set("pass", self.pass.as_str())
            .set("carrier", self.carrier.as_str())
            .set("status", self.status.as_str());
        if let Json::Obj(fields) = std::mem::replace(&mut self.json, Json::Null)
            && let Json::Obj(out) = &mut json
        {
            out.extend(fields);
        }
        let mut times = Json::obj();
        for (step, ms) in &self.times {
            times.set(step, *ms);
        }
        json.set("ms", times);
        let mut rules = Json::obj();
        for (index, rule) in self.rules.iter().enumerate() {
            rules.set(&format!("r{}", index + 1), *rule);
        }
        json.set("rules", rules)
            .set("dxv", self.dxv)
            .set("dxil_spirv", self.spirv)
            .set("spirv_val", self.spirv_val)
            .set("dxil_spirv_original", self.spirv_baseline)
            .set("check_previous", self.previous_check.clone())
            .set("pos_slice", pos_slice.cloned());
        json
    }
}

/// What every worker shares.
struct Shared<'a> {
    config: &'a Config,
    tools: &'a Tools,
    carriers: &'a [CarrierBuild],
}

fn ms(started: Instant) -> f64 {
    started.elapsed().as_secs_f64() * 1000.0
}

/// dxil-spirv then spirv-val on `container`: (dxil-spirv accepted, spirv-val passed).
fn translate(tools: &Tools, container: &Path, spv: &Path) -> (Option<bool>, Option<bool>) {
    let Some(dxil_spirv) = &tools.dxil_spirv else {
        return (None, None);
    };
    let spv_text = spv.display().to_string();
    let accepted = run(
        dxil_spirv,
        &[
            &container.display().to_string(),
            "--output",
            &spv_text,
            "--invariant-position",
            "--ssbo-srv",
        ],
    )
    .is_ok()
        && spv.is_file();
    let valid = if accepted {
        tools
            .spirv_val
            .as_ref()
            .map(|val| run(val, &[&spv_text]).is_ok())
    } else {
        Some(false)
    };
    (Some(accepted), valid)
}

/// Everything for one vertex shader: the round trip, then each carrier.
#[allow(clippy::too_many_lines)]
fn process(hash: &str, shared: &Shared<'_>, server: &mut Server) -> Vec<Row> {
    let config = shared.config;
    let tools = shared.tools;
    let source = config.dump.join(format!("{hash}.dxil"));
    let work = config.out.join("work").join(hash);
    let blobs = config.out.join("blobs");
    let _ = fs::create_dir_all(&work);
    let original = fs::read(&source).unwrap_or_default();
    let ildn = Container::parse(&original)
        .ok()
        .and_then(|c| c.ildn().ok().map(str::to_owned))
        .unwrap_or_default();
    let name = base_name(&ildn).to_owned();
    let (group, pass) = group_of(&ildn);
    let new_row = |carrier: &str| Row::new(hash, carrier, &name, &group, &pass);
    let mut rows = Vec::new();

    let orig_ll = work.join("orig.ll");
    let mut base = new_row("none");
    let dis = server.ask("dis", &source, &orig_ll);
    let original_text = match dis.and_then(|t| {
        fs::read_to_string(&orig_ll)
            .map(|text| (t, text))
            .map_err(|e| e.to_string())
    }) {
        Ok((times, text)) => {
            base.times
                .insert("dis", times.first().copied().unwrap_or(0.0));
            text
        }
        Err(error) => {
            base.status = "error".to_owned();
            base.json.set("error", error);
            rows.push(base);
            return rows;
        }
    };
    let started = Instant::now();
    let original_canonical = vsplice::canonical(&original_text);
    let canonical_ms = ms(started);

    // Item 1: the round trip with no carrier.
    if !config.timing {
        let rt = work.join("rt.dxil");
        let rt_ll = work.join("rt.ll");
        let mut trip = RoundTrip::default();
        match server.ask("asm", &orig_ll, &rt) {
            Ok(times) => {
                trip.assembled = true;
                base.times
                    .insert("asm", times.first().copied().unwrap_or(0.0));
                base.times
                    .insert("validate", times.get(1).copied().unwrap_or(0.0));
                if server.ask("dis", &rt, &rt_ll).is_ok() {
                    let ir = |text: &str| {
                        text.find("\ntarget datalayout")
                            .map(|at| text[at..].to_owned())
                    };
                    let again = fs::read_to_string(&rt_ll).unwrap_or_default();
                    trip.ir_same = ir(&original_text).is_some() && ir(&original_text) == ir(&again);
                }
                trip.dxv = run(&tools.dxv, &[&rt.display().to_string()]).is_ok();
                let rebuilt = fs::read(&rt).unwrap_or_default();
                trip.parts_same = [
                    dxbc::INPUT_SIGNATURE_PART,
                    dxbc::OUTPUT_SIGNATURE_PART,
                    dxbc::PSV0_PART,
                    dxbc::SFI0_PART,
                ]
                .iter()
                .all(|code| {
                    let a = dxbc::container_part(&original, code);
                    a.is_some() && a == dxbc::container_part(&rebuilt, code)
                });
            }
            Err(error) => {
                base.json.set("asm_error", error);
            }
        }
        base.status = if trip.assembled && trip.ir_same && trip.dxv && trip.parts_same {
            "round_trip_ok".to_owned()
        } else {
            "round_trip_failed".to_owned()
        };
        base.json
            .set("assembled", trip.assembled)
            .set("ir_same", trip.ir_same)
            .set("dxv_rt", trip.dxv)
            .set("parts_same", trip.parts_same);
        base.round_trip = Some(trip);
        if tools.dxil_spirv.is_some() {
            base.spirv_baseline = translate(tools, &source, &work.join("orig.spv")).0;
        }
    } else {
        base.status = "timing".to_owned();
    }
    let spirv_baseline = base.spirv_baseline;
    rows.push(base);

    for carrier in shared.carriers {
        let mut row = new_row(carrier.name);
        row.spirv_baseline = spirv_baseline;
        let Some(carrier_text) = &carrier.text else {
            row.status = "carrier_unbuilt".to_owned();
            rows.push(row);
            continue;
        };
        row.times
            .insert("dis", rows[0].times.get("dis").copied().unwrap_or(0.0));
        let started = Instant::now();
        let spliced = vsplice::splice(&original_text, carrier_text, &SpliceOptions::default());
        row.times.insert("splice", ms(started));
        let spliced: Spliced = match spliced {
            Ok(spliced) => spliced,
            Err(refusal) => {
                row.status = "refused".to_owned();
                row.json
                    .set("refusal_class", refusal.class())
                    .set("refusal", refusal.to_string());
                row.refusal = Some(refusal.class());
                rows.push(row);
                continue;
            }
        };
        row.instance0_hash = Some(spliced.report.instance0_hash);
        row.previous = Some(spliced.report.previous.clone());
        let previous = match &spliced.report.previous {
            PreviousOutcome::NotAsked => Json::from("not_asked"),
            PreviousOutcome::None => Json::from("none"),
            PreviousOutcome::Applied {
                element,
                sources,
                cloned,
            } => {
                let mut p = Json::obj();
                p.set("element", *element)
                    .set("sources", sources.clone())
                    .set("cloned", *cloned);
                p
            }
            PreviousOutcome::Skipped(why) => {
                let mut p = Json::obj();
                p.set("skipped", why.as_str());
                p
            }
        };
        row.json
            .set("position_element", spliced.report.position_element)
            .set("inserted", spliced.report.inserted)
            .set("cloned", spliced.report.cloned())
            .set("blocks_added", spliced.report.blocks_added)
            .set("range_id", spliced.report.range_id)
            .set(
                "instance0_hash",
                format!("{:016x}", spliced.report.instance0_hash),
            )
            .set("previous", previous)
            .set("view_id", {
                let label = match &spliced.report.view_id {
                    ViewIdOutcome::Rewritten { changed: true } => "rewritten".to_owned(),
                    ViewIdOutcome::Rewritten { changed: false } => "unchanged".to_owned(),
                    ViewIdOutcome::Kept(why) => format!("kept: {why}"),
                };
                row.view_id = Some(label.clone());
                label
            })
            .set(
                "text_growth",
                spliced.text.len() as i64 - original_text.len() as i64,
            );
        let spliced_ll = work.join(format!("{}.spliced.ll", carrier.name));
        let assembled = work.join(format!("{}.asm.dxil", carrier.name));
        if let Err(error) = fs::write(&spliced_ll, &spliced.text) {
            row.status = "error".to_owned();
            row.json.set("error", error.to_string());
            rows.push(row);
            continue;
        }
        match server.ask("asm", &spliced_ll, &assembled) {
            Ok(times) => {
                row.times
                    .insert("asm", times.first().copied().unwrap_or(0.0));
                row.times
                    .insert("validate", times.get(1).copied().unwrap_or(0.0));
            }
            Err(error) => {
                row.status = "asm_failed".to_owned();
                row.json.set("asm_error", error.clone());
                row.asm_error = Some(error);
                rows.push(row);
                continue;
            }
        }
        let _ = fs::remove_file(&spliced_ll);
        // The original's debug name copied in, and the container signed again.
        let started = Instant::now();
        let container = fs::read(&assembled).unwrap_or_default();
        let ildn_part = dxbc::container_part(&original, dxbc::DEBUG_NAME_PART);
        let finished = match ildn_part {
            Some(part) => with_part(&container, dxbc::DEBUG_NAME_PART, part),
            None => Ok(container),
        };
        row.times.insert("sign", ms(started));
        let finished = match finished {
            Ok(bytes) => bytes,
            Err(error) => {
                row.status = "error".to_owned();
                row.json.set("error", error);
                rows.push(row);
                continue;
            }
        };
        let blob = blobs.join(format!("{hash}.{}.dxil", carrier.name));
        let _ = fs::write(&blob, &finished);
        let _ = fs::remove_file(&assembled);
        // Rule 8: residual identity, on the variant as DXC prints it back.
        let listing = work.join(format!("{}.ll", carrier.name));
        let rule8 = match server.ask("dis", &blob, &listing) {
            Ok(times) => {
                row.times
                    .insert("dis_variant", times.first().copied().unwrap_or(0.0));
                let variant_text = fs::read_to_string(&listing).unwrap_or_default();
                let started = Instant::now();
                let residual = vsplice::residual(&variant_text, &spliced.map);
                let same = residual == original_canonical;
                row.times.insert("residual", ms(started) + canonical_ms);
                // The same check with one substitution left undone must fail, or rule 8 is
                // looking at nothing.
                if !config.timing && !spliced.map.back.is_empty() {
                    let mut short = spliced.map.clone();
                    short.back.remove(0);
                    let blind = vsplice::residual(&variant_text, &short) == original_canonical;
                    row.json.set("rule8_sensitive", !blind);
                    row.rule8_sensitive = Some(!blind);
                }
                if !same
                    && let Some((line, ours, theirs)) =
                        vsplice::first_difference(&residual, &original_canonical)
                {
                    row.json.set(
                        "residual_difference",
                        format!("line {line}: variant `{ours}` original `{theirs}`"),
                    );
                }
                row.variant_listing = Some(listing.clone());
                same
            }
            Err(error) => {
                row.json.set("dis_variant_error", error);
                false
            }
        };
        let rules = container_rules(&original, &finished);
        let mut details = Json::obj();
        for (index, verdict) in rules.iter().enumerate() {
            row.rules[index] = Some(verdict.is_ok());
            if let Err(why) = verdict {
                details.set(&format!("r{}", index + 1), why.as_str());
            }
        }
        row.rules[7] = Some(rule8);
        row.json.set("rule_failures", details);
        if !config.timing {
            row.json.set("psv0", psv_changes(&original, &finished));
            row.dxv = Some(run(&tools.dxv, &[&blob.display().to_string()]).is_ok());
            let (spirv, valid) =
                translate(tools, &blob, &work.join(format!("{}.spv", carrier.name)));
            row.spirv = spirv;
            row.spirv_val = valid;
            if carrier.name == "wobble"
                && matches!(spliced.report.previous, PreviousOutcome::Applied { .. })
            {
                row.previous_check = Some(previous_check(
                    &original_text,
                    carrier_text,
                    &listing,
                    &work,
                    tools,
                    server,
                ));
            }
        }
        let total: f64 = [
            "dis",
            "splice",
            "asm",
            "validate",
            "dis_variant",
            "residual",
        ]
        .iter()
        .filter_map(|step| row.times.get(step))
        .sum();
        row.times.insert("total", total);
        let all = row.rules.iter().all(|r| *r == Some(true));
        row.status = if all { "applied" } else { "rule_failed" }.to_owned();
        rows.push(row);
    }
    rows
}

/// `check_previous.py` on the variant without the previous-time instance against the one with
/// it: `cur=same prev=ok others=same` is a pass.
fn previous_check(
    original_text: &str,
    carrier_text: &str,
    with_previous: &Path,
    work: &Path,
    tools: &Tools,
    server: &mut Server,
) -> String {
    let options = SpliceOptions {
        previous: Previous::Off,
        ..SpliceOptions::default()
    };
    let spliced = match vsplice::splice(original_text, carrier_text, &options) {
        Ok(spliced) => spliced,
        Err(refusal) => return format!("no plain variant: {refusal}"),
    };
    let text = work.join("wobble.noprev.spliced.ll");
    let blob = work.join("wobble.noprev.dxil");
    let listing = work.join("wobble.noprev.ll");
    if let Err(error) = fs::write(&text, &spliced.text) {
        return error.to_string();
    }
    if let Err(error) = server.ask("asm", &text, &blob) {
        return format!("the plain variant did not assemble: {error}");
    }
    if let Err(error) = server.ask("dis", &blob, &listing) {
        return error.to_string();
    }
    let _ = fs::remove_file(&text);
    match run(
        Path::new("python3"),
        &[
            &tools.check_previous.display().to_string(),
            &listing.display().to_string(),
            &with_previous.display().to_string(),
        ],
    ) {
        Ok(out) => first_line(&out),
        Err(error) => format!("check_previous.py failed: {error}"),
    }
}

/// `pos_slice.py`'s two hashes for each file, in batches across `jobs` processes.
fn pos_slices(files: &[PathBuf], tools: &Tools, jobs: usize) -> HashMap<PathBuf, String> {
    let chunks: Vec<&[PathBuf]> = files.chunks(200).collect();
    let next = AtomicUsize::new(0);
    let found = Mutex::new(HashMap::new());
    std::thread::scope(|scope| {
        for _ in 0..jobs.max(1) {
            scope.spawn(|| {
                loop {
                    let index = next.fetch_add(1, Ordering::Relaxed);
                    let Some(chunk) = chunks.get(index) else {
                        break;
                    };
                    let mut args = vec![tools.pos_slice.display().to_string()];
                    args.extend(chunk.iter().map(|p| p.display().to_string()));
                    let Ok(output) = Command::new("python3").args(&args).output() else {
                        continue;
                    };
                    let mut local = Vec::new();
                    // A path that is not UTF-8 fails to match back and shows as a missing hash.
                    // UTF-8 Lossy: the batch prints only paths and hex digests.
                    for line in String::from_utf8_lossy(&output.stdout).lines() {
                        let mut fields = line.splitn(2, '\t');
                        if let (Some(path), Some(hashes)) = (fields.next(), fields.next()) {
                            local.push((PathBuf::from(path), hashes.to_owned()));
                        }
                    }
                    if let Ok(mut map) = found.lock() {
                        map.extend(local);
                    }
                }
            });
        }
    });
    found.into_inner().unwrap_or_default()
}

// ---- summary ------------------------------------------------------------------------------------

fn percentile(sorted: &[f64], p: f64) -> f64 {
    if sorted.is_empty() {
        return f64::NAN;
    }
    let rank = (p * (sorted.len() - 1) as f64).round() as usize;
    sorted[rank.min(sorted.len() - 1)]
}

fn distribution(mut values: Vec<f64>) -> Json {
    values.sort_by(f64::total_cmp);
    let mut out = Json::obj();
    out.set("n", values.len())
        .set("median", percentile(&values, 0.5))
        .set("p95", percentile(&values, 0.95))
        .set("max", values.last().copied());
    out
}

fn read_research_refused(path: &Path) -> BTreeMap<String, String> {
    fs::read_to_string(path)
        .unwrap_or_default()
        .lines()
        .filter_map(|line| {
            let (hash, why) = line.split_once('\t')?;
            Some((hash.to_owned(), why.to_owned()))
        })
        .collect()
}

/// The research's words for a refusal, as this module's class.
fn research_class(why: &str) -> &'static str {
    if why.contains("no dx.resources") {
        "no_resources"
    } else if why.contains("no POSITION0") {
        "no_position"
    } else if why.contains("columns") {
        "partial_position"
    } else {
        "other"
    }
}

#[allow(clippy::too_many_lines)]
fn summarize(
    rows: &[Row],
    config: &Config,
    carriers: &[CarrierBuild],
    pipelines: &[Vec<String>],
    pos: &HashMap<PathBuf, String>,
) -> (Json, Vec<String>) {
    let mut summary = Json::obj();
    let mut failures: Vec<String> = Vec::new();
    let shaders: BTreeSet<&str> = rows.iter().map(|r| r.hash.as_str()).collect();
    summary
        .set("dump", config.dump.display().to_string())
        .set("vertex_shaders", shaders.len());
    let by_carrier =
        |name: &str| -> Vec<&Row> { rows.iter().filter(|r| r.carrier == name).collect() };

    // Item 1.
    let trips: Vec<RoundTrip> = rows.iter().filter_map(|r| r.round_trip).collect();
    if !trips.is_empty() {
        let mut item = Json::obj();
        let count = |f: fn(&RoundTrip) -> bool| trips.iter().filter(|t| f(t)).count();
        let all = count(|t| t.assembled && t.ir_same && t.dxv && t.parts_same);
        item.set("shaders", trips.len())
            .set("assembled", count(|t| t.assembled))
            .set("ir_identical", count(|t| t.ir_same))
            .set("dxv", count(|t| t.dxv))
            .set("isg1_osg1_psv0_sfi0_identical", count(|t| t.parts_same))
            .set("all", all)
            .set("pass", all == trips.len());
        if all != trips.len() {
            failures.push(format!("item 1: {all}/{} round trips", trips.len()));
        }
        summary.set("item1_round_trip", item);
    }

    // Items 2 to 4 and 8, per carrier.
    let research = config
        .research_refused
        .as_ref()
        .map(|p| read_research_refused(p));
    let wobble_applied: BTreeSet<&str> = by_carrier("wobble")
        .iter()
        .filter(|r| r.status == "applied" || r.status == "rule_failed")
        .map(|r| r.hash.as_str())
        .collect();
    let mut per_carrier = Json::obj();
    for carrier in carriers {
        let list = by_carrier(carrier.name);
        let mut item = Json::obj();
        item.set("compile_ms", carrier.compile_ms)
            .set("build_error", carrier.error.clone())
            .set(
                "check_carrier",
                match &carrier.check {
                    Ok(()) => "ok".to_owned(),
                    Err(refusal) => refusal.to_string(),
                },
            );
        if let Source::Refused(rule) = carrier.source {
            // Item 7.
            let expected = format!("carrier_{rule}");
            let refused_by = carrier.check.as_ref().err().map(Refusal::class);
            let spliced = list.iter().filter(|r| r.refusal.is_none()).count();
            let pass = refused_by.as_deref() == Some(expected.as_str()) && spliced == 0;
            item.set("expected", expected.as_str())
                .set("refused_as", refused_by.clone())
                .set("spliced", spliced)
                .set("pass", pass);
            if !pass {
                failures.push(format!(
                    "item 7: {} refused as {refused_by:?}, {spliced} spliced",
                    carrier.name
                ));
            }
            per_carrier.set(carrier.name, item);
            continue;
        }
        let tried = list.len();
        let applied: Vec<&&Row> = list
            .iter()
            .filter(|r| r.status == "applied" || r.status == "rule_failed")
            .collect();
        let mut classes: BTreeMap<String, usize> = BTreeMap::new();
        for row in &list {
            if let Some(class) = &row.refusal {
                *classes.entry(class.clone()).or_default() += 1;
            }
        }
        let asm_failed = list.iter().filter(|r| r.status == "asm_failed").count();
        let rule_ok = |n: usize| applied.iter().filter(|r| r.rules[n] == Some(true)).count();
        let all_rules = applied
            .iter()
            .filter(|r| r.rules.iter().all(|v| *v == Some(true)))
            .count();
        let dxv = applied.iter().filter(|r| r.dxv == Some(true)).count();
        let mut view_ids: BTreeMap<&str, usize> = BTreeMap::new();
        for row in &applied {
            *view_ids
                .entry(row.view_id.as_deref().unwrap_or("none"))
                .or_default() += 1;
        }
        item.set("view_id_state", {
            let mut v = Json::obj();
            for (label, n) in &view_ids {
                v.set(label, *n);
            }
            v
        });
        if view_ids.keys().any(|label| label.starts_with("kept")) {
            failures.push(format!(
                "{}: a dependency table was left stale: {view_ids:?}",
                carrier.name
            ));
        }
        let mut rules = Json::obj();
        for n in 0..8 {
            rules.set(&format!("r{}", n + 1), rule_ok(n));
        }
        item.set("vertex_shaders", tried)
            .set("applied", applied.len())
            .set("refused", classes.values().sum::<usize>())
            .set("refusal_classes", {
                let mut c = Json::obj();
                for (class, n) in &classes {
                    c.set(class, *n);
                }
                c
            })
            .set("asm_or_validate_failed", asm_failed)
            .set("rules_passed", rules)
            .set("all_rules", all_rules)
            .set("dxv", dxv);
        if !config.timing {
            let probed = applied
                .iter()
                .filter(|r| r.rule8_sensitive.is_some())
                .count();
            let sensitive = applied
                .iter()
                .filter(|r| r.rule8_sensitive == Some(true))
                .count();
            item.set("rule8_probed_with_one_substitution_undone", probed)
                .set("rule8_caught_it", sensitive);
            if sensitive != probed {
                failures.push(format!(
                    "rule 8 is blind on {} of {probed} {} variants",
                    probed - sensitive,
                    carrier.name
                ));
            }
        }
        let mut failing: Vec<String> = list
            .iter()
            .filter(|r| r.status == "asm_failed" || r.status == "rule_failed")
            .map(|r| format!("{} {}", r.hash, r.name))
            .collect();
        failing.truncate(20);
        item.set("failing_examples", failing);
        // Item 8.
        if config.timing {
            item.set("dxil_spirv", "not run in --timing");
        } else if applied.iter().any(|r| r.spirv.is_some()) {
            let accepted = applied.iter().filter(|r| r.spirv == Some(true)).count();
            let valid = applied.iter().filter(|r| r.spirv_val == Some(true)).count();
            let baseline = applied
                .iter()
                .filter(|r| r.spirv_baseline == Some(true))
                .count();
            item.set("dxil_spirv_accepted", accepted)
                .set("spirv_val_passed", valid)
                .set("dxil_spirv_accepts_original", baseline);
            if matches!(carrier.name, "wobble" | "branchy")
                && (accepted != applied.len() || valid != applied.len())
            {
                failures.push(format!(
                    "item 8: {}: dxil-spirv {accepted}/{}, spirv-val {valid}/{}",
                    carrier.name,
                    applied.len(),
                    applied.len()
                ));
            }
        } else {
            item.set("dxil_spirv", "unmeasured: dxil-spirv absent");
        }
        // Item 2/3: the same applied set as the wobble, and against the research.
        let set: BTreeSet<&str> = applied.iter().map(|r| r.hash.as_str()).collect();
        if carrier.name != "wobble" {
            item.set("same_set_as_wobble", set == wobble_applied);
        } else if let Some(research) = &research {
            let refused: BTreeSet<&str> = list
                .iter()
                .filter(|r| r.refusal.is_some())
                .map(|r| r.hash.as_str())
                .collect();
            let theirs: BTreeSet<&str> = research.keys().map(String::as_str).collect();
            let same = refused == theirs;
            let classes_agree = list.iter().filter(|r| r.refusal.is_some()).all(|r| {
                // A class the research has no word for can only disagree with it.
                let ours = r.refusal.as_deref().unwrap_or("other");
                research.get(&r.hash).map(|why| research_class(why)) == Some(ours)
            });
            item.set("refused_set_equals_research", same)
                .set("refusal_classes_agree_with_research", classes_agree)
                .set(
                    "only_ours",
                    refused
                        .difference(&theirs)
                        .map(|s| (*s).to_owned())
                        .collect::<Vec<_>>(),
                )
                .set(
                    "only_research",
                    theirs
                        .difference(&refused)
                        .map(|s| (*s).to_owned())
                        .collect::<Vec<_>>(),
                );
            if !same || !classes_agree {
                failures.push("item 2: the refused set differs from patch_vs_ll.py's".to_owned());
            }
        }
        if all_rules != applied.len() || asm_failed > 0 {
            failures.push(format!(
                "items 2-4: {}: {all_rules}/{} applied pass rules 1-8, {asm_failed} did not \
                 assemble",
                carrier.name,
                applied.len()
            ));
        }
        if !config.timing && dxv != applied.len() {
            failures.push(format!("{}: dxv {dxv}/{}", carrier.name, applied.len()));
        }
        // Item 5: the inserted code is the same in every shader of a group.
        let mut groups: BTreeMap<&str, BTreeSet<u64>> = BTreeMap::new();
        for row in &applied {
            if let Some(h) = row.instance0_hash {
                groups.entry(row.group.as_str()).or_default().insert(h);
            }
        }
        let consistent = groups.values().filter(|h| h.len() == 1).count();
        item.set("groups", groups.len())
            .set("groups_with_one_instance0_hash", consistent);
        if consistent != groups.len() {
            failures.push(format!(
                "item 5: {}: {consistent}/{} groups share instance 0's code",
                carrier.name,
                groups.len()
            ));
        }
        // Item 5: pos_slice agreement, as the research summary computed it.
        if !pos.is_empty() {
            item.set(
                "pos_slice",
                pos_agreement(rows, carrier.name, pos, &mut failures),
            );
        }
        // Item 9: pipeline linkage.
        let spliced_vs: HashMap<&str, bool> = applied
            .iter()
            .map(|r| {
                (
                    r.hash.as_str(),
                    r.rules[3] == Some(true) && r.rules[2] == Some(true),
                )
            })
            .collect();
        let with = pipelines
            .iter()
            .filter(|p| {
                p.get(1)
                    .is_some_and(|vs| spliced_vs.contains_key(vs.as_str()))
            })
            .count();
        let linked = pipelines
            .iter()
            .filter(|p| p.get(1).and_then(|vs| spliced_vs.get(vs.as_str())) == Some(&true))
            .count();
        item.set("pipelines_with_spliced_vs", with)
            .set("pipelines_linkage_equal", linked);
        if with != linked {
            failures.push(format!(
                "item 9: {}: {linked}/{with} pipelines",
                carrier.name
            ));
        }
        // Item 10.
        let mut times = Json::obj();
        for step in [
            "splice",
            "asm",
            "validate",
            "dis",
            "dis_variant",
            "residual",
            "sign",
            "total",
        ] {
            let values: Vec<f64> = applied
                .iter()
                .filter_map(|r| r.times.get(step).copied())
                .collect();
            times.set(step, distribution(values));
        }
        item.set("ms", times);
        // Size growth.
        per_carrier.set(carrier.name, item);
    }
    summary.set("carriers", per_carrier);

    // Item 6: the previous time. `--timing` runs no `check_previous.py`, so it has nothing to say.
    let wobble = by_carrier("wobble");
    if !wobble.is_empty() && !config.timing {
        let mut item = Json::obj();
        let velocity: Vec<&&Row> = wobble
            .iter()
            .filter(|r| matches!(r.pass.as_str(), "Velo" | "FurDir"))
            .collect();
        let applied: Vec<&&Row> = wobble
            .iter()
            .filter(|r| matches!(r.previous, Some(PreviousOutcome::Applied { .. })))
            .collect();
        let ok = |r: &&&Row| {
            r.status == "applied"
                && r.dxv != Some(false)
                && r.previous_check.as_deref() == Some("cur=same prev=ok others=same")
        };
        let passed = applied.iter().filter(|r| ok(r)).count();
        let velo = applied.iter().filter(|r| r.pass == "Velo").count();
        let fur = applied.iter().filter(|r| r.pass == "FurDir").count();
        let cloned: Vec<f64> = applied
            .iter()
            .filter_map(|r| match &r.previous {
                Some(PreviousOutcome::Applied { cloned, .. }) => Some(*cloned as f64),
                _ => None,
            })
            .collect();
        let misses: Vec<String> = velocity
            .iter()
            .filter(|r| !matches!(r.previous, Some(PreviousOutcome::Applied { .. })) || !ok(r))
            .map(|r| {
                format!(
                    "{} {} {}: {:?} check={:?} status={}",
                    r.hash, r.pass, r.name, r.previous, r.previous_check, r.status
                )
            })
            .collect();
        let others: Vec<String> = applied
            .iter()
            .filter(|r| !matches!(r.pass.as_str(), "Velo" | "FurDir"))
            .map(|r| format!("{} {} {}", r.hash, r.pass, r.name))
            .collect();
        let mut sources: BTreeMap<String, usize> = BTreeMap::new();
        for row in &applied {
            if let Some(PreviousOutcome::Applied { sources: s, .. }) = &row.previous {
                *sources.entry(s.join("+")).or_default() += 1;
            }
        }
        item.set("velo_and_furdir_vs", velocity.len())
            .set("applied", applied.len())
            .set("applied_velo", velo)
            .set("applied_furdir", fur)
            .set("applied_other_passes", others)
            .set("sources", {
                let mut s = Json::obj();
                for (k, v) in &sources {
                    s.set(k, *v);
                }
                s
            })
            .set("passed_asm_dxv_rules_check_previous", passed)
            .set("cloned", distribution(cloned))
            .set("misses", misses.clone());
        if passed != applied.len() || !misses.is_empty() {
            failures.push(format!(
                "item 6: {passed}/{} applied pass, {} misses",
                applied.len(),
                misses.len()
            ));
        }
        summary.set("item6_previous", item);
    }
    let mut verdict = Json::obj();
    verdict
        .set("pass", failures.is_empty())
        .set("failures", failures.clone());
    summary.set("verdict", verdict);
    (summary, failures)
}

/// One member of a summary group: its pass, and the `pos_slice.py` hashes of the original and the
/// variant.
type PassHashes = (String, String, String);

/// The research summary's agreement figure: in each (base, skinned) group with more than one
/// main-view pass (Gbuf, GBuf, Dpt, DptA, Fwd, Velo), do all passes compute `SV_Position` with one
/// expression, before and after the splice; and how many groups the splice split.
fn pos_agreement(
    rows: &[Row],
    carrier: &str,
    pos: &HashMap<PathBuf, String>,
    failures: &mut Vec<String>,
) -> Json {
    let original = |row: &Row| {
        pos.get(&row_orig_ll(row))
            .cloned()
            .unwrap_or_else(|| "missing".to_owned())
    };
    let mut groups: BTreeMap<(String, bool), Vec<PassHashes>> = BTreeMap::new();
    for row in rows.iter().filter(|r| r.carrier == carrier) {
        let Some((key, pass)) = summary_key(&row.name) else {
            continue;
        };
        let variant = row
            .variant_listing
            .as_ref()
            .and_then(|p| pos.get(p).cloned())
            .unwrap_or_else(|| "missing".to_owned());
        groups
            .entry(key)
            .or_default()
            .push((pass, original(row), variant));
    }
    let main = ["Gbuf", "GBuf", "Dpt", "DptA", "Fwd", "Velo"];
    let (mut main_groups, mut agree_o, mut agree_w, mut split, mut multi) =
        (0_usize, 0_usize, 0_usize, 0_usize, 0_usize);
    for members in groups.values() {
        if members.len() > 1 {
            multi += 1;
            let o: HashSet<&String> = members.iter().map(|m| &m.1).collect();
            let w: HashSet<&String> = members.iter().map(|m| &m.2).collect();
            if o.len() == 1 && w.len() != 1 {
                split += 1;
            }
        }
        let in_main: Vec<_> = members
            .iter()
            .filter(|m| main.contains(&m.0.as_str()))
            .collect();
        if in_main.len() > 1 {
            main_groups += 1;
            let o: HashSet<&String> = in_main.iter().map(|m| &m.1).collect();
            let w: HashSet<&String> = in_main.iter().map(|m| &m.2).collect();
            agree_o += usize::from(o.len() == 1);
            agree_w += usize::from(w.len() == 1);
        }
    }
    if split > 0 || agree_w != agree_o {
        failures.push(format!(
            "item 5: {carrier}: pos_slice agreement {agree_o} -> {agree_w} of {main_groups}, \
             {split} groups split"
        ));
    }
    let mut out = Json::obj();
    out.set("groups_with_two_or_more_passes", multi)
        .set("main_view_groups", main_groups)
        .set("main_view_agree_original", agree_o)
        .set("main_view_agree_variant", agree_w)
        .set("groups_split", split);
    out
}

fn row_orig_ll(row: &Row) -> PathBuf {
    row.variant_listing
        .as_ref()
        .and_then(|p| p.parent().map(|d| d.join("orig.ll")))
        .unwrap_or_default()
}

// ---- item 11 ------------------------------------------------------------------------------------

/// The Windows probe's containers (`vsplice_windows`, under Wine) against the Linux sweep in
/// `against`: each must pass Linux `dxv` and rules 1 to 7 once the debug name is copied in and it
/// is signed again, and its disassembly must equal the Linux variant's.
fn check_windows(dir: &Path, against: &Path, dump: &Path, tools: &Tools) -> ExitCode {
    let ir = |text: &str| {
        text.find("\ntarget datalayout")
            .map(|at| text[at..].to_owned())
            .unwrap_or_default()
    };
    let mut hashes: Vec<String> = fs::read_dir(dir)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().into_owned();
                    name.strip_suffix(".win.dxil").map(str::to_owned)
                })
                .collect()
        })
        .unwrap_or_default();
    hashes.sort();
    let mut all = true;
    let mut out = Json::Arr(Vec::new());
    for hash in &hashes {
        let original = fs::read(dump.join(format!("{hash}.dxil"))).unwrap_or_default();
        let windows = fs::read(dir.join(format!("{hash}.win.dxil"))).unwrap_or_default();
        let finished = dxbc::container_part(&original, dxbc::DEBUG_NAME_PART).map_or_else(
            || Ok(windows.clone()),
            |part| with_part(&windows, dxbc::DEBUG_NAME_PART, part),
        );
        let Ok(finished) = finished else {
            println!("{hash}: the debug name could not be copied in");
            all = false;
            continue;
        };
        let staged = dir.join(format!("{hash}.win.signed.dxil"));
        let _ = fs::write(&staged, &finished);
        let rules = container_rules(&original, &finished);
        let rules_ok = rules.iter().all(Result::is_ok);
        let raw_signed = Container::parse(&windows).is_ok_and(|c| c.is_signed_correctly());
        let dxv_raw = run(
            &tools.dxv,
            &[&dir.join(format!("{hash}.win.dxil")).display().to_string()],
        )
        .is_ok();
        let dxv = run(&tools.dxv, &[&staged.display().to_string()]).is_ok();
        let read = |path: PathBuf| fs::read_to_string(path).unwrap_or_default();
        let variant_same = ir(&read(dir.join(format!("{hash}.win.ll"))))
            == ir(&read(against.join("work").join(hash).join("wobble.ll")));
        let original_same = ir(&read(dir.join(format!("{hash}.orig.win.ll"))))
            == ir(&read(against.join("work").join(hash).join("orig.ll")));
        let linux_blob =
            fs::read(against.join("blobs").join(format!("{hash}.wobble.dxil"))).unwrap_or_default();
        let bytes_same = linux_blob == finished;
        let pass = rules_ok && dxv && variant_same;
        all &= pass;
        let mut row = Json::obj();
        row.set("hash", hash.as_str())
            .set("validator_signed", raw_signed)
            .set("dxv_as_validated", dxv_raw)
            .set("dxv_with_debug_name", dxv)
            .set("rules_1_7", rules_ok)
            .set("disassembly_equals_linux_variant", variant_same)
            .set("original_disassembly_equals_linux", original_same)
            .set("container_bytes_equal_linux", bytes_same)
            .set("pass", pass);
        println!("{}", row.text());
        if let Json::Arr(items) = &mut out {
            items.push(row);
        }
    }
    let _ = fs::write(dir.join("windows-check.json"), out.text());
    println!(
        "check-windows: {} shaders, {}",
        hashes.len(),
        if all && !hashes.is_empty() {
            "all pass"
        } else {
            "FAILURES"
        }
    );
    if all && !hashes.is_empty() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}

// ---- main ---------------------------------------------------------------------------------------

fn default_dump() -> PathBuf {
    if let Some(root) = std::env::var_os("ER_VKD3D_DUMP_ROOT") {
        return PathBuf::from(root);
    }
    let repo = repo();
    let mut main = repo.clone();
    let parts: Vec<_> = repo.components().collect();
    if let Some(at) = parts.iter().position(|c| c.as_os_str() == ".worktrees") {
        main = parts[..at].iter().collect();
    }
    main.join(".worktrees/er-shaders/target/vkd3d-dump/run1/spv")
}

fn find_on_path(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths)
            .map(|dir| dir.join(name))
            .find(|p| p.is_file())
    })
}

#[allow(clippy::too_many_lines)]
fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |flag: &str| -> Option<String> {
        args.iter()
            .position(|a| a == flag)
            .and_then(|at| args.get(at + 1).cloned())
    };
    if args.iter().any(|a| a == "--help" || a == "-h") {
        println!(
            "vsplice_sweep --out <dir> [--dump <dir>] [--carriers a,b] [--jobs N] [--limit N] \
             [--timing] [--research-refused <tsv>] [--dxa <path>] [--dxc <path>] [--dxil-spirv \
             <path>] [--spirv-val <path>]"
        );
        return ExitCode::SUCCESS;
    }
    let check = value("--check-windows").map(PathBuf::from);
    let out = value("--out").map(PathBuf::from).unwrap_or_default();
    if out.as_os_str().is_empty() && check.is_none() {
        eprintln!("vsplice_sweep: --out <dir> is required");
        return ExitCode::from(2);
    }
    let timing = args.iter().any(|a| a == "--timing");
    let repo = repo();
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    let dxc = value("--dxc")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("DXC").map(PathBuf::from))
        .unwrap_or_else(|| home.join("tools/dxc/bin/dxc"));
    let dxv = dxc.with_file_name("dxv");
    let dxa = value("--dxa")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("DXA_LITE").map(PathBuf::from));
    let dump = value("--dump").map_or_else(default_dump, PathBuf::from);
    let mut missing = Vec::new();
    if !dump.is_dir() {
        missing.push(format!("the dump {}", dump.display()));
    }
    // `--check-windows` reads containers and runs `dxv`; it needs neither dxc nor dxa_lite.
    let needed: Vec<(&str, Option<&PathBuf>)> = if check.is_some() {
        vec![("dxv", Some(&dxv))]
    } else {
        vec![
            ("dxc", Some(&dxc)),
            ("dxv", Some(&dxv)),
            ("dxa_lite", dxa.as_ref()),
        ]
    };
    for (what, path) in needed {
        if !path.is_some_and(|p| p.is_file()) {
            missing.push(format!(
                "{what} ({})",
                path.map_or("unset".to_owned(), |p| p.display().to_string())
            ));
        }
    }
    if !missing.is_empty() {
        println!("vsplice_sweep: SKIP, missing {}", missing.join(", "));
        return ExitCode::SUCCESS;
    }
    let tools = Tools {
        dxc,
        dxv,
        dxa: dxa.unwrap_or_default(),
        dxil_spirv: value("--dxil-spirv")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("DXIL_SPIRV").map(PathBuf::from))
            .filter(|p| p.is_file()),
        spirv_val: value("--spirv-val")
            .map(PathBuf::from)
            .or_else(|| Some(PathBuf::from("/usr/sbin/spirv-val")).filter(|p| p.is_file()))
            .or_else(|| find_on_path("spirv-val")),
        shaders: repo.join("crates/er-cel-shade/shaders"),
        carriers: repo.join("crates/er-shader-compiler/tests/carriers"),
        pos_slice: repo.join("scripts/vs-patch/pos_slice.py"),
        check_previous: repo.join("scripts/vs-patch/check_previous.py"),
        census: repo.join("scripts/er-shader-vertex-census.py"),
    };
    if let Some(dir) = check {
        let Some(against) = value("--against").map(PathBuf::from) else {
            eprintln!("vsplice_sweep: --check-windows needs --against <the Linux sweep's --out>");
            return ExitCode::from(2);
        };
        return check_windows(&dir, &against, &dump, &tools);
    }
    let names: Vec<&'static str> = match value("--carriers") {
        Some(list) => CARRIERS
            .iter()
            .map(|(n, _)| *n)
            .filter(|n| list.split(',').any(|w| w == *n))
            .collect(),
        None if timing => vec!["wobble", "branchy"],
        None => CARRIERS.iter().map(|(n, _)| *n).collect(),
    };
    let config = Config {
        dump,
        out,
        carriers: names,
        jobs: if timing {
            1
        } else {
            value("--jobs").and_then(|j| j.parse().ok()).unwrap_or(6)
        },
        limit: value("--limit").and_then(|l| l.parse().ok()),
        timing,
        research_refused: value("--research-refused").map(PathBuf::from),
    };
    for dir in ["carriers", "blobs", "work"] {
        if let Err(error) = fs::create_dir_all(config.out.join(dir)) {
            eprintln!("vsplice_sweep: {}: {error}", config.out.display());
            return ExitCode::from(2);
        }
    }
    let started = Instant::now();

    // The vertex shaders of the dump.
    let mut hashes: Vec<String> = fs::read_dir(&config.dump)
        .map(|entries| {
            entries
                .filter_map(Result::ok)
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|e| e == "dxil"))
                .filter(|p| {
                    fs::read(p).ok().is_some_and(|bytes| {
                        Container::parse(&bytes)
                            .ok()
                            .and_then(|c| c.program().ok())
                            .is_some_and(|h| h.kind == ShaderKind::Vertex)
                    })
                })
                .filter_map(|p| p.file_stem().map(|s| s.to_string_lossy().into_owned()))
                .collect()
        })
        .unwrap_or_default();
    hashes.sort();
    if let Some(limit) = config.limit {
        hashes.truncate(limit);
    }
    eprintln!("vsplice_sweep: {} vertex shaders", hashes.len());

    // The carriers.
    let mut server = match Server::start(&tools.dxa) {
        Ok(server) => server,
        Err(error) => {
            eprintln!("vsplice_sweep: {error}");
            return ExitCode::from(2);
        }
    };
    let carriers: Vec<CarrierBuild> = config
        .carriers
        .iter()
        .filter_map(|name| CARRIERS.iter().find(|(n, _)| n == name))
        .map(|(name, source)| {
            build_carrier(
                name,
                *source,
                &tools,
                &config.out.join("carriers"),
                &mut server,
            )
        })
        .collect();
    drop(server);
    for carrier in &carriers {
        eprintln!(
            "vsplice_sweep: carrier {}: {:.1} ms, {}",
            carrier.name,
            carrier.compile_ms,
            carrier
                .error
                .clone()
                .unwrap_or_else(|| format!("{:?}", carrier.check))
        );
    }

    // The pipelines, through the census script.
    let pipelines_tsv = config.out.join("pipelines.tsv");
    let pipelines: Vec<Vec<String>> = if config.timing {
        Vec::new()
    } else {
        let _ = run(
            Path::new("python3"),
            &[
                &tools.census.display().to_string(),
                "--dump",
                &config.dump.display().to_string(),
                "--pipelines-tsv",
                &pipelines_tsv.display().to_string(),
            ],
        );
        fs::read_to_string(&pipelines_tsv)
            .unwrap_or_default()
            .lines()
            .map(|l| l.split('\t').map(str::to_owned).collect())
            .collect()
    };

    // The sweep.
    let shared = Shared {
        config: &config,
        tools: &tools,
        carriers: &carriers,
    };
    let next = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    let rows: Mutex<Vec<Row>> = Mutex::new(Vec::new());
    let partial = fs::File::create(config.out.join("partial.jsonl")).map(BufWriter::new);
    let partial = Mutex::new(partial.ok());
    std::thread::scope(|scope| {
        for _ in 0..config.jobs.max(1) {
            scope.spawn(|| {
                let Ok(mut server) = Server::start(&tools.dxa) else {
                    return;
                };
                loop {
                    let index = next.fetch_add(1, Ordering::Relaxed);
                    let Some(hash) = hashes.get(index) else { break };
                    let mut found = process(hash, &shared, &mut server);
                    if let Ok(mut file) = partial.lock()
                        && let Some(file) = file.as_mut()
                    {
                        for row in &mut found {
                            let mut probe = Json::obj();
                            probe
                                .set("hash", row.hash.as_str())
                                .set("carrier", row.carrier.as_str())
                                .set("status", row.status.as_str());
                            let _ = writeln!(file, "{}", probe.text());
                        }
                    }
                    if let Ok(mut all) = rows.lock() {
                        all.extend(found);
                    }
                    let count = done.fetch_add(1, Ordering::Relaxed) + 1;
                    if count.is_multiple_of(100) {
                        eprintln!(
                            "vsplice_sweep: {count}/{} shaders, {:.0} s",
                            hashes.len(),
                            started.elapsed().as_secs_f64()
                        );
                    }
                }
            });
        }
    });
    let mut rows = rows.into_inner().unwrap_or_default();
    rows.sort_by(|a, b| (&a.hash, &a.carrier).cmp(&(&b.hash, &b.carrier)));

    // `pos_slice.py` over every original and variant.
    let pos = if config.timing {
        HashMap::new()
    } else {
        let mut files: Vec<PathBuf> = hashes
            .iter()
            .map(|h| config.out.join("work").join(h).join("orig.ll"))
            .collect();
        files.extend(rows.iter().filter_map(|r| r.variant_listing.clone()));
        eprintln!("vsplice_sweep: pos_slice.py over {} files", files.len());
        pos_slices(&files, &tools, config.jobs)
    };

    let (summary, failures) = summarize(&rows, &config, &carriers, &pipelines, &pos);
    let mut results = String::new();
    for row in &mut rows {
        let slice = row.variant_listing.as_ref().and_then(|p| pos.get(p));
        let slice = slice.or_else(|| {
            (row.carrier == "none")
                .then(|| pos.get(&config.out.join("work").join(&row.hash).join("orig.ll")))
                .flatten()
        });
        results.push_str(&row.finish(slice).text());
        results.push('\n');
    }
    let name = if config.timing { "timing" } else { "results" };
    let mut summary = summary;
    summary.set("seconds", started.elapsed().as_secs_f64());
    let wrote = fs::write(config.out.join(format!("{name}.jsonl")), results).and_then(|()| {
        fs::write(
            config.out.join(if config.timing {
                "timing-summary.json"
            } else {
                "summary.json"
            }),
            summary.text(),
        )
    });
    if let Err(error) = wrote {
        eprintln!("vsplice_sweep: {error}");
        return ExitCode::from(2);
    }
    eprintln!(
        "vsplice_sweep: wrote {} in {:.0} s; {}",
        config.out.display(),
        started.elapsed().as_secs_f64(),
        if failures.is_empty() {
            "every checked number passed".to_owned()
        } else {
            format!("{} failures", failures.len())
        }
    );
    for failure in &failures {
        eprintln!("  {failure}");
    }
    if failures.is_empty() {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    }
}
