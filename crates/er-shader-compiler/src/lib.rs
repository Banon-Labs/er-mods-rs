//! The shader editor's HLSL compiler, behind one boundary (design `docs/plans/er-shader-editor.md`,
//! section 7.5).
//!
//! # Two ways in
//!
//! er-cel-shade compiles a layer stack through `er-shader-compiler.exe`, a helper process it
//! spawns in a job object and talks to over pipes in the frames of [`protocol`]. LLVM inside
//! dxcompiler.dll can crash on a user's source, and a crash there must end the helper, not the
//! game; the DLL counts the broken pipe and starts another. Only when the helper cannot start at
//! all does the DLL load the same compiler itself, through the Windows-only `dxc` module on its
//! worker thread, and then a compiler crash does end the game, which its UI says.
//!
//! Both ways share everything in this file: the [`arguments`] dxc is given, the [`DxcVersion`]
//! rule, and the [`serve`] loop the helper runs, which is tested here against a stand-in
//! [`Backend`] because the real one needs Windows and dxcompiler.dll.
//!
//! # What the helper is sure of before the DLL trusts it
//!
//! Its [`protocol::Handshake`] carries the protocol version, the compiler it loaded and its
//! [`build_id`], a hash of this crate's sources. [`check_handshake`] refuses a helper that differs
//! in either, so a helper left over from an older build is never mistaken for the current one, and
//! one that loaded no compiler is not used for compiling.
//!
//! # Where the compiler comes from
//!
//! The DLL finds `er-shader-compiler.exe` and dxcompiler.dll each on its own, in whichever search
//! directory has it, and names the compiler on the helper's command line
//! ([`helper_command_line`], read back by [`dxcompiler_argument`]). The two need not share a
//! directory: cargo puts the helper beside the DLL, and `scripts/fetch-dxcompiler.py` may put the
//! compiler under the game. The handshake reports the path the helper loaded.
//!
//! dxcompiler.dll must be 1.8.2505 or later ([`DxcVersion::meets_minimum`]): that release is the
//! first to sign containers with its internal validator, so it needs no dxil.dll beside it, and
//! a native D3D12 runtime refuses an unsigned container that vkd3d-proton would accept.

pub mod protocol;

#[cfg(windows)]
pub mod dxc;

use core::fmt;
use std::ffi::OsString;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use er_game_base::fnv1a::{FNV1A64_OFFSET_BASIS, fnv1a64_extend};

use protocol::{Handshake, Op, PROTOCOL_VERSION, ProtocolError, Request, Response};

/// The compiler's file, looked for in the DLL's search directories, and by a helper started with
/// no [`DXCOMPILER_OPTION`] beside itself.
pub const DXCOMPILER_DLL: &str = "dxcompiler.dll";

/// The helper's one option: `--dxcompiler <full path>`, the compiler to load.
pub const DXCOMPILER_OPTION: &str = "--dxcompiler";

/// The helper's exit status after a `SelftestCrash` request: a plain exit, so neither Windows
/// Error Reporting nor a crash debugger gets involved in the proof that the DLL restarts it.
pub const SELFTEST_EXIT: u8 = 3;

/// The entry point every port and every generated variant has.
pub const ENTRY_POINT: &str = "ps_main";

/// The profile every variant is compiled to: the model the game's own passes use (design 13).
pub const PROFILE: &str = "ps_6_0";

/// HLSL 2021, which `scripts/build-cel-shaders.py` also passes, so an embedded port and its
/// runtime-compiled twin parse the same source alike.
pub const HLSL_VERSION: &str = "2021";

/// The validator version stamped into each container: the game's own passes carry 1.6, and with
/// no Agility SDK on Windows the system runtime is the one reading the blob.
pub const VALIDATOR_VERSION: &str = "1.6";

/// The oldest dxcompiler accepted, as major, minor and release: 1.8.2505.
pub const MIN_DXC_VERSION: (u32, u32, u32) = (1, 8, 2505);

/// The longest diagnostics text an answer carries; dxc can print far more on a bad source.
pub const DIAGNOSTICS_LIMIT: usize = 1 << 20;

/// FNV-1a 64 over this crate's sources, which is what [`build_id`] names. The DLL links this
/// library and the helper is built from it, so the two agree exactly when they were built from
/// the same sources. A function rather than a `u64` constant, because `scripts/rva_symbols.py`
/// cannot evaluate a hash and an unread 64-bit constant blocks its every "nothing claims this
/// address" proof.
#[must_use]
pub const fn build_hash() -> u64 {
    let mut hash = FNV1A64_OFFSET_BASIS;
    hash = fnv1a64_extend(hash, include_bytes!("lib.rs"));
    hash = fnv1a64_extend(hash, include_bytes!("protocol.rs"));
    hash = fnv1a64_extend(hash, include_bytes!("dxc.rs"));
    fnv1a64_extend(hash, include_bytes!("main.rs"))
}

/// The build id a [`Handshake`] carries: the crate version and [`build_hash`].
#[must_use]
pub fn build_id() -> String {
    format!(
        "er-shader-compiler {} {:016x}",
        env!("CARGO_PKG_VERSION"),
        build_hash()
    )
}

/// The arguments dxc is given for `request`, in the order `scripts/build-cel-shaders.py` passes
/// them, the file name last: `-T <profile> -E <entry> -O3 -Qstrip_debug -HV 2021
/// -validator-version 1.6`, then one `-D` per define. `-Vd`, which would skip validation and so
/// leave the container unsigned, is never among them.
///
/// Each argument reaches dxc as its own string, never through a command line, so nothing in a
/// field can split into a second option. What is refused is what dxc would read as an option or
/// what cannot cross as a wide string: a file name starting with `-`, an entry point, profile or
/// define name that is not an identifier, and a NUL anywhere.
///
/// # Errors
///
/// A sentence naming the field that was refused.
pub fn arguments(request: &Request) -> Result<Vec<String>, String> {
    if request.file_name.is_empty() || request.file_name.starts_with('-') {
        return Err(format!(
            "the file name {:?} is empty or starts with '-'",
            request.file_name
        ));
    }
    if request.file_name.contains('\0') {
        return Err("the file name contains a NUL".to_owned());
    }
    for (field, value) in [
        ("entry point", &request.entry),
        ("profile", &request.profile),
    ] {
        if !is_identifier(value) {
            return Err(format!("the {field} {value:?} is not an identifier"));
        }
    }
    let mut arguments: Vec<String> = [
        "-T",
        &request.profile,
        "-E",
        &request.entry,
        "-O3",
        "-Qstrip_debug",
        "-HV",
        HLSL_VERSION,
        "-validator-version",
        VALIDATOR_VERSION,
    ]
    .into_iter()
    .map(str::to_owned)
    .collect();
    for (name, value) in &request.defines {
        if !is_identifier(name) {
            return Err(format!("the define name {name:?} is not an identifier"));
        }
        if value.contains('\0') {
            return Err(format!("the value of define {name} contains a NUL"));
        }
        arguments.push("-D".to_owned());
        arguments.push(if value.is_empty() {
            name.clone()
        } else {
            format!("{name}={value}")
        });
    }
    arguments.push(request.file_name.clone());
    Ok(arguments)
}

/// The command line the DLL starts the helper with: the helper's path, then
/// [`DXCOMPILER_OPTION`] and the compiler's path, each quoted. A Windows path holds no `"` and
/// these two end in a file name, never a backslash, so the quotes are all the escaping the C
/// runtime's parser needs to hand both back whole.
#[must_use]
pub fn helper_command_line(exe: &Path, dxcompiler: &Path) -> String {
    format!(
        "\"{}\" {DXCOMPILER_OPTION} \"{}\"",
        exe.display(),
        dxcompiler.display()
    )
}

/// The compiler the helper's arguments (its own path left out) name: `None` with none, the path
/// after [`DXCOMPILER_OPTION`] otherwise.
///
/// # Errors
///
/// Any other arguments, which only a caller that is not the DLL would pass.
pub fn dxcompiler_argument(args: &[OsString]) -> Result<Option<PathBuf>, String> {
    match args {
        [] => Ok(None),
        [option, path] if option == DXCOMPILER_OPTION && !path.is_empty() => {
            Ok(Some(PathBuf::from(path)))
        }
        _ => Err(format!(
            "the helper takes no arguments or `{DXCOMPILER_OPTION} <path>`, and was given {args:?}"
        )),
    }
}

/// A C identifier: what dxc accepts as an entry point or a macro name, and what no option begins
/// with.
fn is_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    chars
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// The version a dxcompiler reports about itself.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DxcVersion {
    /// From `IDxcVersionInfo::GetVersion`.
    pub major: u32,
    pub minor: u32,
    /// `IDxcVersionInfo3::GetCustomVersionString`, which a release build fills with its file
    /// version, `1.9.2609.5` for the pinned one; `None` where the build lacks the interface.
    pub custom: Option<String>,
    /// `IDxcVersionInfo2::GetCommitInfo`, where the build has it.
    pub commit_count: Option<u32>,
    pub commit_hash: Option<String>,
}

impl DxcVersion {
    /// The release number, the third field of [`Self::custom`] when its first two are this
    /// version's major and minor: 2505 in `1.8.2505.1`.
    #[must_use]
    pub fn release(&self) -> Option<u32> {
        let mut fields = self.custom.as_deref()?.split('.');
        let major = fields.next()?.parse::<u32>().ok()?;
        let minor = fields.next()?.parse::<u32>().ok()?;
        let release = fields.next()?.parse::<u32>().ok()?;
        (major == self.major && minor == self.minor).then_some(release)
    }

    /// Is this [`MIN_DXC_VERSION`] or later? A 1.8 that does not say which release it is fails,
    /// because 1.8 releases before 2505 return unsigned containers without dxil.dll.
    #[must_use]
    pub fn meets_minimum(&self) -> bool {
        let (major, minor, release) = MIN_DXC_VERSION;
        match (self.major, self.minor).cmp(&(major, minor)) {
            core::cmp::Ordering::Greater => true,
            core::cmp::Ordering::Equal => self.release().is_some_and(|found| found >= release),
            core::cmp::Ordering::Less => false,
        }
    }
}

impl fmt::Display for DxcVersion {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}.{}", self.major, self.minor)?;
        if let Some(custom) = &self.custom {
            write!(f, " ({custom})")?;
        }
        match (self.commit_count, &self.commit_hash) {
            (Some(count), Some(hash)) => write!(f, " commit {count}-{hash}"),
            (Some(count), None) => write!(f, " commit {count}"),
            (None, Some(hash)) => write!(f, " commit {hash}"),
            (None, None) => Ok(()),
        }
    }
}

/// Text from a C string that should be UTF-8, with any byte that is not shown as `\xNN` rather
/// than replaced, so a diagnostic loses nothing on the way to the log.
#[must_use]
pub fn escape_utf8(bytes: &[u8]) -> String {
    use core::fmt::Write as _;
    let mut text = String::with_capacity(bytes.len());
    for chunk in bytes.utf8_chunks() {
        text.push_str(chunk.valid());
        for byte in chunk.invalid() {
            // Writing to a `String` cannot fail.
            let _ = write!(text, "\\x{byte:02x}");
        }
    }
    text
}

/// What answers the requests the helper reads.
pub trait Backend {
    /// The compiler, as [`DxcVersion`] prints it, or empty with none.
    fn dxc_version(&self) -> String;

    /// Compile one request; every failure is an answer with `ok` false, never a panic.
    fn compile(&mut self, request: &Request) -> Response;
}

/// The backend when no compiler loaded: every compile is answered with why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NoCompiler {
    pub reason: String,
}

impl NoCompiler {
    #[must_use]
    pub fn new(reason: impl Into<String>) -> Self {
        Self {
            reason: reason.into(),
        }
    }
}

impl Backend for NoCompiler {
    fn dxc_version(&self) -> String {
        String::new()
    }

    fn compile(&mut self, request: &Request) -> Response {
        Response::failed(request.id, format!("no compiler: {}", self.reason), "")
    }
}

/// How [`serve`] ended without a protocol error.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Served {
    /// The input closed between frames: the DLL is gone, or let the helper go.
    Eof,
    /// A [`Op::SelftestCrash`] arrived; the caller exits with [`SELFTEST_EXIT`], unanswered.
    CrashRequested,
}

/// Answer requests from `input` on `output` until the input closes between frames or a
/// [`Op::SelftestCrash`] arrives. Each answer is flushed before the next request is read, and a
/// compile's answer always carries its request's id. `dxcompiler` is what the handshake reports
/// as [`Handshake::dxcompiler`].
///
/// # Errors
///
/// The first [`ProtocolError`] reading a request or writing an answer. The stream is out of step
/// after one, so the helper exits on it rather than guess where the next frame begins.
pub fn serve<B: Backend + ?Sized>(
    input: &mut impl Read,
    output: &mut impl Write,
    backend: &mut B,
    dxcompiler: &str,
) -> Result<Served, ProtocolError> {
    loop {
        let Some(request) = protocol::receive::<Request>(input)? else {
            return Ok(Served::Eof);
        };
        match request.op {
            Op::Handshake => protocol::send(
                output,
                &Handshake {
                    protocol_version: PROTOCOL_VERSION,
                    dxc_version: backend.dxc_version(),
                    build_id: build_id(),
                    dxcompiler: dxcompiler.to_owned(),
                },
            )?,
            Op::Compile => {
                let mut response = backend.compile(&request);
                response.id = request.id;
                protocol::send(output, &fit_frame(response))?;
            }
            Op::SelftestCrash => return Ok(Served::CrashRequested),
        }
    }
}

/// Bytes a [`Response`] spends on its tag, id, flag and three length prefixes.
const RESPONSE_OVERHEAD: usize = 4 + 8 + 1 + 3 * 4;

/// `response`, cut down until it fits one frame: diagnostics past [`DIAGNOSTICS_LIMIT`] are cut at
/// a character boundary and say how much was cut, and an object too large for any frame is
/// dropped for a failure that names its size.
#[must_use]
pub fn fit_frame(mut response: Response) -> Response {
    if response.diagnostics.len() > DIAGNOSTICS_LIMIT {
        let mut cut = DIAGNOSTICS_LIMIT;
        while !response.diagnostics.is_char_boundary(cut) {
            cut -= 1;
        }
        let dropped = response.diagnostics.len() - cut;
        response.diagnostics.truncate(cut);
        response
            .diagnostics
            .push_str(&format!("\n[{dropped} more bytes of diagnostics cut]"));
    }
    let size = RESPONSE_OVERHEAD
        + response.blob.len()
        + response.diagnostics.len()
        + response.dxc_version.len();
    if size > protocol::MAX_FRAME_BYTES {
        let object = response.blob.len();
        response.ok = false;
        response.blob = Vec::new();
        response.diagnostics = format!(
            "the compiled object is {object} bytes, too large for one {}-byte frame",
            protocol::MAX_FRAME_BYTES
        );
    }
    response
}

/// Why the DLL will not use a helper that answered its handshake.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HandshakeRefusal {
    /// It speaks another version of the frame format.
    Protocol { helper: u32, ours: u32 },
    /// It was built from other sources than the DLL: a helper left over from an older build.
    Build { helper: String, ours: String },
    /// It runs, but loaded no compiler: `why` is its [`Handshake::dxcompiler`].
    NoCompiler { why: String },
}

impl fmt::Display for HandshakeRefusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Protocol { helper, ours } => {
                write!(f, "the helper speaks protocol {helper}, the DLL {ours}")
            }
            Self::Build { helper, ours } => write!(
                f,
                "the helper is build {helper:?}, the DLL {ours:?}: rebuild both with \
                 scripts/er-build-dlls.sh er-cel-shade"
            ),
            Self::NoCompiler { why } => write!(f, "the helper loaded no compiler: {why}"),
        }
    }
}

impl std::error::Error for HandshakeRefusal {}

/// Is the helper that sent `handshake` one the DLL may compile through?
///
/// # Errors
///
/// The first [`HandshakeRefusal`] that applies, protocol first.
pub fn check_handshake(handshake: &Handshake) -> Result<(), HandshakeRefusal> {
    if handshake.protocol_version != PROTOCOL_VERSION {
        return Err(HandshakeRefusal::Protocol {
            helper: handshake.protocol_version,
            ours: PROTOCOL_VERSION,
        });
    }
    let ours = build_id();
    if handshake.build_id != ours {
        return Err(HandshakeRefusal::Build {
            helper: handshake.build_id.clone(),
            ours,
        });
    }
    if handshake.dxc_version.is_empty() {
        return Err(HandshakeRefusal::NoCompiler {
            why: handshake.dxcompiler.clone(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tonemap_request() -> Request {
        Request::compile(
            1,
            "tonemap.hlsl",
            "float4 ps_main() : SV_Target0 { return 1; }",
            vec![
                ("ER_VARIANT_ID".to_owned(), "50331649".to_owned()),
                ("CEL_LUT".to_owned(), String::new()),
            ],
        )
    }

    #[test]
    fn the_arguments_are_build_cel_shaders_own() {
        assert_eq!(
            arguments(&tonemap_request()).unwrap(),
            [
                "-T",
                "ps_6_0",
                "-E",
                "ps_main",
                "-O3",
                "-Qstrip_debug",
                "-HV",
                "2021",
                "-validator-version",
                "1.6",
                "-D",
                "ER_VARIANT_ID=50331649",
                "-D",
                "CEL_LUT",
                "tonemap.hlsl",
            ]
        );
    }

    #[test]
    fn nothing_in_a_request_can_become_an_option() {
        let refused = |edit: fn(&mut Request)| {
            let mut request = tonemap_request();
            edit(&mut request);
            arguments(&request).unwrap_err()
        };
        assert!(refused(|r| r.file_name = "-Vd".to_owned()).contains("file name"));
        assert!(refused(|r| r.file_name.clear()).contains("file name"));
        assert!(refused(|r| r.file_name = "a\0b.hlsl".to_owned()).contains("NUL"));
        assert!(refused(|r| r.entry = "-Vd".to_owned()).contains("entry point"));
        assert!(refused(|r| r.entry.clear()).contains("entry point"));
        assert!(refused(|r| r.profile = "ps 6 0".to_owned()).contains("profile"));
        assert!(
            refused(|r| r.defines.push(("-Vd".to_owned(), String::new()))).contains("define name")
        );
        assert!(
            refused(|r| r.defines.push(("1X".to_owned(), String::new()))).contains("define name")
        );
        assert!(refused(|r| r.defines.push(("X".to_owned(), "\0".to_owned()))).contains("NUL"));
        // A define's value is the value of its own `-D`, so a dash there is only text.
        let mut request = tonemap_request();
        request.defines.push(("X".to_owned(), "-Vd".to_owned()));
        let arguments = arguments(&request).unwrap();
        assert!(arguments.contains(&"X=-Vd".to_owned()));
        assert!(!arguments.contains(&"-Vd".to_owned()));
    }

    fn version(major: u32, minor: u32, custom: Option<&str>) -> DxcVersion {
        DxcVersion {
            major,
            minor,
            custom: custom.map(str::to_owned),
            ..DxcVersion::default()
        }
    }

    #[test]
    fn the_oldest_accepted_compiler_is_1_8_2505() {
        assert!(version(1, 8, Some("1.8.2505.1")).meets_minimum());
        assert!(version(1, 8, Some("1.8.2505")).meets_minimum());
        assert!(version(1, 8, Some("1.8.2507.3")).meets_minimum());
        assert!(!version(1, 8, Some("1.8.2502.11")).meets_minimum());
        // A 1.8 development build numbers its release 0.
        assert!(!version(1, 8, Some("1.8.0.4775")).meets_minimum());
        // A 1.8 that does not say which release it is.
        assert!(!version(1, 8, None).meets_minimum());
        assert!(!version(1, 8, Some("custom")).meets_minimum());
        // A custom string about another version proves nothing about this one.
        assert!(!version(1, 8, Some("1.9.2609.5")).meets_minimum());
        assert!(version(1, 9, Some("1.9.2609.5")).meets_minimum());
        assert!(version(1, 9, None).meets_minimum());
        // The Linux dxc this repo builds the embedded ports with.
        assert!(version(1, 9, Some("1.9.0.5191")).meets_minimum());
        assert!(version(2, 0, None).meets_minimum());
        assert!(!version(1, 7, Some("1.7.2308.1")).meets_minimum());
        assert!(!version(0, 9, None).meets_minimum());
    }

    #[test]
    fn a_version_prints_every_part_it_has() {
        assert_eq!(version(1, 9, None).to_string(), "1.9");
        let full = DxcVersion {
            commit_count: Some(5191),
            commit_hash: Some("d355aa83".to_owned()),
            ..version(1, 9, Some("1.9.0.5191"))
        };
        assert_eq!(full.to_string(), "1.9 (1.9.0.5191) commit 5191-d355aa83");
        assert_eq!(full.release(), Some(0));
    }

    #[test]
    fn bytes_that_are_not_utf8_are_shown_not_replaced() {
        assert_eq!(escape_utf8(b"a.hlsl:2:68: error"), "a.hlsl:2:68: error");
        assert_eq!(escape_utf8("\u{e9}".as_bytes()), "\u{e9}");
        assert_eq!(escape_utf8(b"x\xffy\xc3"), "x\\xffy\\xc3");
    }

    /// Answers every compile with a blob naming the request, and records what it was asked.
    struct Echo {
        asked: Vec<u64>,
    }

    impl Backend for Echo {
        fn dxc_version(&self) -> String {
            "1.9 (1.9.2609.5)".to_owned()
        }

        fn compile(&mut self, request: &Request) -> Response {
            self.asked.push(request.id);
            Response {
                // A backend that gets the id wrong is corrected by `serve`.
                id: 0,
                ok: true,
                blob: request.source.as_bytes().to_vec(),
                diagnostics: String::new(),
                dxc_version: self.dxc_version(),
            }
        }
    }

    fn stream_of(requests: &[Request]) -> Vec<u8> {
        let mut stream = Vec::new();
        for request in requests {
            protocol::send(&mut stream, request).unwrap();
        }
        stream
    }

    const DXCOMPILER: &str = "Z:\\game\\er-shader-editor\\compiler\\dxcompiler.dll";

    #[test]
    fn the_helper_answers_in_order_and_ends_with_its_input() {
        let input = stream_of(&[
            Request::handshake(1),
            Request::compile(2, "a.hlsl", "first", Vec::new()),
            Request::compile(3, "b.hlsl", "second", Vec::new()),
        ]);
        let mut output = Vec::new();
        let mut backend = Echo { asked: Vec::new() };
        assert_eq!(
            serve(&mut input.as_slice(), &mut output, &mut backend, DXCOMPILER),
            Ok(Served::Eof)
        );
        assert_eq!(backend.asked, [2, 3]);
        let mut answers = output.as_slice();
        let handshake: Handshake = protocol::receive(&mut answers).unwrap().unwrap();
        assert_eq!(check_handshake(&handshake), Ok(()));
        assert_eq!(handshake.dxcompiler, DXCOMPILER);
        for (id, source) in [(2, "first"), (3, "second")] {
            let response: Response = protocol::receive(&mut answers).unwrap().unwrap();
            assert_eq!(
                (response.id, response.blob.as_slice()),
                (id, source.as_bytes())
            );
        }
        assert_eq!(protocol::receive::<Response>(&mut answers), Ok(None));
    }

    #[test]
    fn a_selftest_crash_stops_the_helper_unanswered() {
        let input = stream_of(&[
            Request::selftest_crash(1),
            Request::compile(2, "a.hlsl", "never read", Vec::new()),
        ]);
        let mut output = Vec::new();
        let mut backend = Echo { asked: Vec::new() };
        assert_eq!(
            serve(&mut input.as_slice(), &mut output, &mut backend, DXCOMPILER),
            Ok(Served::CrashRequested)
        );
        assert!(output.is_empty());
        assert!(backend.asked.is_empty());
        // Neither the protocol failure's status nor success.
        const { assert!(SELFTEST_EXIT != 0 && SELFTEST_EXIT != 2) };
    }

    #[test]
    fn a_frame_it_cannot_read_ends_the_helper_with_the_reason() {
        let mut input = stream_of(&[Request::handshake(1)]);
        input.extend_from_slice(&[9, 0]);
        let mut output = Vec::new();
        let tried = "Z:\\x\\dxcompiler.dll: did not load";
        assert_eq!(
            serve(
                &mut input.as_slice(),
                &mut output,
                &mut NoCompiler::new("test"),
                tried
            ),
            Err(ProtocolError::Truncated("the frame length"))
        );
        // The handshake before it was still answered, and says why there is no compiler.
        let handshake: Handshake = protocol::receive(&mut output.as_slice()).unwrap().unwrap();
        assert_eq!(
            check_handshake(&handshake),
            Err(HandshakeRefusal::NoCompiler {
                why: tried.to_owned()
            })
        );
    }

    #[test]
    fn the_command_line_names_the_compiler_and_the_helper_reads_it_back() {
        let exe = Path::new("Z:\\repo\\target\\release\\er-shader-compiler.exe");
        let dll =
            Path::new("Z:\\steam\\ELDEN RING\\Game\\er-shader-editor\\compiler\\dxcompiler.dll");
        let line = helper_command_line(exe, dll);
        assert_eq!(
            line,
            "\"Z:\\repo\\target\\release\\er-shader-compiler.exe\" --dxcompiler \
             \"Z:\\steam\\ELDEN RING\\Game\\er-shader-editor\\compiler\\dxcompiler.dll\""
        );
        // What the C runtime hands the helper, its own path left out: quoted spans are one
        // argument each, which is all a path with no `"` and no trailing backslash needs.
        let mut args: Vec<OsString> = Vec::new();
        let mut current = String::new();
        let mut quoted = false;
        for c in line.chars() {
            match c {
                '"' => quoted = !quoted,
                ' ' if !quoted => {
                    if !current.is_empty() {
                        args.push(OsString::from(std::mem::take(&mut current)));
                    }
                }
                other => current.push(other),
            }
        }
        args.push(OsString::from(current));
        assert_eq!(dxcompiler_argument(&args[1..]), Ok(Some(dll.to_path_buf())));
        assert_eq!(dxcompiler_argument(&[]), Ok(None));
        for wrong in [
            vec![OsString::from(DXCOMPILER_OPTION)],
            vec![OsString::from("--other"), OsString::from("x")],
            vec![OsString::from(DXCOMPILER_OPTION), OsString::new()],
        ] {
            assert!(dxcompiler_argument(&wrong).is_err(), "{wrong:?}");
        }
    }

    #[test]
    fn without_a_compiler_every_compile_says_why() {
        let input = stream_of(&[Request::compile(5, "a.hlsl", "x", Vec::new())]);
        let mut output = Vec::new();
        let mut backend = NoCompiler::new("dxcompiler.dll did not load: not found");
        serve(&mut input.as_slice(), &mut output, &mut backend, DXCOMPILER).unwrap();
        let response: Response = protocol::receive(&mut output.as_slice()).unwrap().unwrap();
        assert_eq!(response.id, 5);
        assert!(!response.ok);
        assert!(response.diagnostics.contains("did not load: not found"));
    }

    #[test]
    fn a_helper_from_another_build_or_protocol_is_refused() {
        let good = Handshake {
            protocol_version: PROTOCOL_VERSION,
            dxc_version: "1.9".to_owned(),
            build_id: build_id(),
            dxcompiler: DXCOMPILER.to_owned(),
        };
        assert_eq!(check_handshake(&good), Ok(()));
        let older = Handshake {
            protocol_version: PROTOCOL_VERSION + 1,
            ..good.clone()
        };
        assert!(matches!(
            check_handshake(&older),
            Err(HandshakeRefusal::Protocol { .. })
        ));
        let stale = Handshake {
            build_id: "er-shader-compiler 0.1.0 0000000000000000".to_owned(),
            ..good
        };
        assert!(matches!(
            check_handshake(&stale),
            Err(HandshakeRefusal::Build { .. })
        ));
        assert!(build_id().ends_with(&format!("{:016x}", build_hash())));
    }

    #[test]
    fn an_answer_too_large_for_a_frame_is_cut_to_fit() {
        let long = Response {
            id: 1,
            ok: false,
            blob: Vec::new(),
            diagnostics: format!(
                "{}\u{e9}{}",
                "e".repeat(DIAGNOSTICS_LIMIT - 1),
                "f".repeat(10)
            ),
            dxc_version: "1.9".to_owned(),
        };
        let cut = fit_frame(long);
        // The two-byte character straddled the limit, so the cut falls before it.
        assert!(
            cut.diagnostics
                .starts_with(&"e".repeat(DIAGNOSTICS_LIMIT - 1))
        );
        assert!(
            cut.diagnostics
                .ends_with("[12 more bytes of diagnostics cut]")
        );
        let huge = Response {
            id: 2,
            ok: true,
            blob: vec![0; protocol::MAX_FRAME_BYTES],
            diagnostics: String::new(),
            dxc_version: "1.9".to_owned(),
        };
        let refused = fit_frame(huge);
        assert!(!refused.ok);
        assert!(refused.blob.is_empty());
        assert!(refused.diagnostics.contains("too large"));
        // What was cut fits, and an answer that already fitted is untouched.
        let mut stream = Vec::new();
        protocol::send(&mut stream, &cut).unwrap();
        protocol::send(&mut stream, &refused).unwrap();
        let small = Response::failed(3, "x", "1.9");
        assert_eq!(fit_frame(small.clone()), small);
        // The overhead constant is exact: the largest answer that fits encodes to the limit.
        let edge = Response {
            id: 4,
            ok: true,
            blob: vec![0; protocol::MAX_FRAME_BYTES - RESPONSE_OVERHEAD - 3],
            diagnostics: String::new(),
            dxc_version: "1.9".to_owned(),
        };
        assert_eq!(fit_frame(edge.clone()), edge);
        assert_eq!(
            protocol::Message::encode(&edge).unwrap().len(),
            protocol::MAX_FRAME_BYTES
        );
    }
}
