// Shared build-script support for GENERATING detour-prologue byte constants with `iced-x86`.
//
// This file is `include!`d by the build script of every crate that byte-checks a game function's
// prologue before detouring or calling it. It exists so that no crate has to hand-type machine
// code: the expected bytes are produced by naming the instructions and letting the assembler
// encode them.
//
// # Why hand-typed prologues are a trap
//
// `mov rax, rsp` has two legal encodings. `CodeAssembler::mov(rax, rsp)` emits `48 89 e0`; the
// game ships `48 8b c4`. A prologue that differs by one byte fails its own install-time byte
// check, the hook disarms itself on every launch, and the feature looks built while doing
// nothing. That happened in this repo. So the rule is: name the instruction, and where the
// assembler has a choice, name the exact `Code` (see [`mov_r64_rm64`] and friends below).
//
// # What pins the result
//
// Two independent checks, both applied by [`generate`]:
//
// 1. **The pin.** Every spec carries the byte sequence the constant had before it was generated.
//    It is machine-independent and always runs, so a wrong encoding choice breaks the build
//    everywhere -- including a machine with no copy of the game.
// 2. **Ground truth.** When a readable copy of the image is found, the assembled bytes are
//    compared against the bytes actually living at that VA in the real binary. This is the
//    stronger check, but it can only run where the image exists, so it SKIPS (with a
//    `cargo:warning` naming what went unverified) rather than failing when it is absent -- the
//    same shape as the corpus-gated tests in `crates/er-gfx/tests/common/mod.rs`.
//
// The pin is therefore not redundant with ground truth: it is the half that survives on a
// machine, or in CI, that has no game files.

use std::{
    env,
    fmt::Write as _,
    fs,
    path::{Path, PathBuf},
};

use iced_x86::code_asm::*;
use iced_x86::{Code, IcedError, Instruction, MemoryOperand, Register};

/// MSVC emits a REX prefix with every bit clear ahead of some single-byte pushes (`40 55` for
/// `push rbp`). iced computes REX from the operands and exposes no way to request a redundant
/// one, so this prefix byte -- the only raw byte in the whole generator -- is named here and
/// emitted with `db`. [`rex_push`] is the only user.
pub const REDUNDANT_REX_PREFIX: u8 = 0x40;

/// Which module a prologue lives in, and therefore which base its VA is relative to and which
/// file can ground-truth it.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Image {
    /// `eldenring.exe` 1.16.2. Ground truth is `eldenring-deobf.bin`, a FLAT image at base
    /// `0x140000000` in which file offset == RVA for every section.
    EldenRing,
    /// Seamless Co-op's `ersc.dll`, preferred base `0x180000000`. Ground truth is the installed
    /// DLL, an ordinary PE whose section table has to be walked to turn an RVA into an offset.
    Ersc,
}

impl Image {
    pub fn base(self) -> u64 {
        match self {
            Self::EldenRing => 0x1_4000_0000,
            Self::Ersc => 0x1_8000_0000,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::EldenRing => "eldenring-deobf.bin",
            Self::Ersc => "ersc.dll",
        }
    }

    fn env_override(self) -> &'static str {
        match self {
            Self::EldenRing => "ER_DEOBF_BIN",
            Self::Ersc => "ER_ERSC_DLL",
        }
    }

    /// Candidate locations, in order. Every one is env-overridable and none is required; a miss
    /// downgrades ground truth to a warning rather than breaking the build.
    fn locate(self, manifest_dir: &Path) -> Option<PathBuf> {
        if let Some(explicit) = env::var_os(self.env_override()) {
            let path = PathBuf::from(explicit);
            return path.is_file().then_some(path);
        }
        match self {
            Self::EldenRing => manifest_dir
                .ancestors()
                .map(|ancestor| ancestor.join(self.label()))
                .find(|candidate| candidate.is_file()),
            Self::Ersc => steam_roots()
                .into_iter()
                .map(|root| root.join("steamapps/common/ELDEN RING/Game/SeamlessCoop/ersc.dll"))
                .find(|candidate| candidate.is_file()),
        }
    }

    /// The bytes the real module has at `va`, or `None` when the file cannot answer.
    fn bytes_at(self, path: &Path, va: u64, len: usize) -> Option<Vec<u8>> {
        let image = fs::read(path).ok()?;
        let rva = va.checked_sub(self.base())?;
        let offset = match self {
            Self::EldenRing => usize::try_from(rva).ok()?,
            Self::Ersc => pe_rva_to_offset(&image, u32::try_from(rva).ok()?)?,
        };
        image
            .get(offset..offset.checked_add(len)?)
            .map(<[u8]>::to_vec)
    }
}

fn steam_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(explicit) = env::var_os("ME3_STEAM_DIR") {
        roots.push(PathBuf::from(explicit));
    }
    if let Some(home) = env::var_os("HOME") {
        let home = PathBuf::from(home);
        roots.push(home.join(".local/share/Steam"));
        roots.push(home.join(".steam/steam"));
    }
    roots
}

/// Minimal PE section walk: RVA -> file offset. Returns `None` for anything that does not parse
/// as a PE32+ image or for an RVA that falls outside every section.
fn pe_rva_to_offset(image: &[u8], rva: u32) -> Option<usize> {
    let read_u16 = |at: usize| -> Option<u16> {
        Some(u16::from_le_bytes(image.get(at..at + 2)?.try_into().ok()?))
    };
    let read_u32 = |at: usize| -> Option<u32> {
        Some(u32::from_le_bytes(image.get(at..at + 4)?.try_into().ok()?))
    };
    let pe = usize::try_from(read_u32(0x3c)?).ok()?;
    if image.get(pe..pe + 4)? != b"PE\0\0" {
        return None;
    }
    let sections = usize::from(read_u16(pe + 6)?);
    let optional_size = usize::from(read_u16(pe + 20)?);
    let table = pe + 24 + optional_size;
    for index in 0..sections {
        let entry = table + 40 * index;
        let virtual_size = read_u32(entry + 8)?;
        let virtual_address = read_u32(entry + 12)?;
        let raw_size = read_u32(entry + 16)?;
        let raw_pointer = read_u32(entry + 20)?;
        let span = virtual_size.max(raw_size);
        if rva >= virtual_address && rva - virtual_address < span {
            return usize::try_from(raw_pointer + (rva - virtual_address)).ok();
        }
    }
    None
}

/// How the generated constant is declared, chosen to match whatever the consuming module already
/// expects so that migrating a hand-written constant touches no call site.
#[derive(Clone, Copy)]
pub enum Shape {
    /// `const NAME: &[u8] = &[..];`
    Slice,
    /// `const NAME: [u8; N] = [..];`
    Array,
}

/// One generated prologue constant.
pub struct PrologueSpec {
    /// Constant name, exactly as the consuming module already spells it.
    pub name: &'static str,
    /// Doc comment for the generated constant, without the leading `///`. Empty for none.
    pub doc: &'static str,
    /// Visibility prefix: `""`, `"pub"` or `"pub(crate)"`.
    pub visibility: &'static str,
    pub shape: Shape,
    pub image: Image,
    /// Virtual address of the function entry, i.e. `image.base() + rva`.
    pub va: u64,
    /// How many of the assembled bytes the constant keeps. `0` keeps all of them. A shorter value
    /// is how a prologue that deliberately stops mid-instruction is expressed; the instructions
    /// are still named in full and only the tail is dropped.
    pub take: usize,
    /// The bytes this constant had before it was generated. See the module docs: this is the
    /// machine-independent half of the verification and must never be relaxed to make a change
    /// build.
    pub pin: &'static [u8],
}

/// The named-instruction body of a prologue.
pub type Assemble = fn(&mut CodeAssembler) -> Result<(), IcedError>;

// ---------------------------------------------------------------------------------------------
// Encoding helpers for the forms where the assembler would otherwise pick the other encoding.
// ---------------------------------------------------------------------------------------------

/// `push <reg>` behind MSVC's redundant REX prefix, e.g. `40 55`.
pub fn rex_push(asm: &mut CodeAssembler, register: AsmRegister64) -> Result<(), IcedError> {
    asm.db(&[REDUNDANT_REX_PREFIX])?;
    asm.push(register)
}

/// `mov <r64>, <r64>` in the `8b` (r64 <- rm64) direction. `asm.mov(rax, rsp)` picks `89`
/// instead, which is the one-byte difference that silently disarms a hook.
pub fn mov_r64_rm64(
    asm: &mut CodeAssembler,
    destination: Register,
    source: Register,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(Code::Mov_r64_rm64, destination, source)?)
}

/// `mov <r32>, <r32>` in the `8b` direction, for the same reason as [`mov_r64_rm64`].
pub fn mov_r32_rm32(
    asm: &mut CodeAssembler,
    destination: Register,
    source: Register,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(Code::Mov_r32_rm32, destination, source)?)
}

/// `cmp <r32>, <r32>` in the `3b` direction; `asm.cmp` picks `39`.
pub fn cmp_r32_rm32(
    asm: &mut CodeAssembler,
    destination: Register,
    source: Register,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(Code::Cmp_r32_rm32, destination, source)?)
}

/// `xor <r32>, <r32>` in the `33` direction; `asm.xor` picks `31`.
pub fn xor_r32_rm32(
    asm: &mut CodeAssembler,
    destination: Register,
    source: Register,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(Code::Xor_r32_rm32, destination, source)?)
}

/// `mov <r64>, [<base> + displacement]`.
pub fn mov_r64_mem(
    asm: &mut CodeAssembler,
    destination: Register,
    base: Register,
    displacement: i64,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(
        Code::Mov_r64_rm64,
        destination,
        MemoryOperand::with_base_displ(base, displacement),
    )?)
}

/// `mov <r64>, [<base>]` with NO displacement byte.
///
/// Distinct from `mov_r64_mem(.., 0)` on purpose: iced encodes an explicit zero displacement as
/// `mod=01, disp8=0` (`49 8b 40 00`), and the game ships the `mod=00` form (`49 8b 00`). The pin
/// catches the difference, but naming the two forms separately means it never has to.
pub fn mov_r64_mem_base(
    asm: &mut CodeAssembler,
    destination: Register,
    base: Register,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(
        Code::Mov_r64_rm64,
        destination,
        MemoryOperand::with_base(base),
    )?)
}

/// `mov <r32>, [<base> + displacement]`.
pub fn mov_r32_mem(
    asm: &mut CodeAssembler,
    destination: Register,
    base: Register,
    displacement: i64,
) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(
        Code::Mov_r32_rm32,
        destination,
        MemoryOperand::with_base_displ(base, displacement),
    )?)
}

/// `mov rax, [rip + ..]` written as the ABSOLUTE address it resolves to. iced turns a `RIP`-based
/// memory operand's displacement into the rip-relative delta for the VA being assembled at, so
/// the singleton's address is named rather than the encoded offset.
pub fn mov_rax_rip_absolute(asm: &mut CodeAssembler, target: u64) -> Result<(), IcedError> {
    asm.add_instruction(Instruction::with2(
        Code::Mov_r64_rm64,
        Register::RAX,
        MemoryOperand::with_base_displ(Register::RIP, target as i64),
    )?)
}

// ---------------------------------------------------------------------------------------------
// Generation
// ---------------------------------------------------------------------------------------------

fn assemble(spec: &PrologueSpec, body: Assemble) -> Vec<u8> {
    let mut asm = CodeAssembler::new(64).unwrap_or_else(|error| {
        panic!("{}: CodeAssembler::new failed: {error}", spec.name);
    });
    body(&mut asm).unwrap_or_else(|error| {
        panic!(
            "{}: assembling named instructions failed: {error}",
            spec.name
        );
    });
    let bytes = asm.assemble(spec.va).unwrap_or_else(|error| {
        panic!("{}: encoding at 0x{:x} failed: {error}", spec.name, spec.va);
    });
    let take = if spec.take == 0 {
        bytes.len()
    } else {
        spec.take
    };
    assert!(
        take <= bytes.len(),
        "{}: asked for {take} bytes but the named instructions encode to only {}",
        spec.name,
        bytes.len()
    );
    bytes[..take].to_vec()
}

fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

fn render(spec: &PrologueSpec, bytes: &[u8]) -> String {
    let mut out = String::new();
    for line in spec.doc.lines().filter(|line| !line.is_empty()) {
        writeln!(out, "/// {line}").expect("write to String");
    }
    writeln!(
        out,
        "/// Generated from named `iced-x86` instructions; see this crate's `build.rs`. Do not"
    )
    .expect("write to String");
    writeln!(out, "/// hand-edit -- edit the instructions instead.").expect("write to String");
    let space = if spec.visibility.is_empty() { "" } else { " " };
    match spec.shape {
        Shape::Slice => writeln!(
            out,
            "{}{space}const {}: &[u8] = &[",
            spec.visibility, spec.name
        ),
        Shape::Array => writeln!(
            out,
            "{}{space}const {}: [u8; {}] = [",
            spec.visibility,
            spec.name,
            bytes.len()
        ),
    }
    .expect("write to String");
    for chunk in bytes.chunks(12) {
        out.push_str("    ");
        for (index, byte) in chunk.iter().enumerate() {
            if index != 0 {
                out.push_str(", ");
            }
            write!(out, "0x{byte:02x}").expect("write to String");
        }
        out.push_str(",\n");
    }
    out.push_str("];\n");
    out
}

/// Assemble every spec, verify it, and write the constants to `OUT_DIR/<out_file>`.
///
/// Panics on any pin mismatch or any ground-truth mismatch. A missing image is not a mismatch:
/// it is reported as a `cargo:warning` and the build continues on the pin alone.
pub fn generate(specs: &[(PrologueSpec, Assemble)], out_file: &str) {
    let manifest_dir = PathBuf::from(
        env::var_os("CARGO_MANIFEST_DIR").expect("cargo sets CARGO_MANIFEST_DIR for build scripts"),
    );
    let mut generated = String::new();
    let mut unverified: Vec<&'static str> = Vec::new();

    for (spec, body) in specs {
        let bytes = assemble(spec, *body);
        assert_eq!(
            bytes.as_slice(),
            spec.pin,
            "{}: the named instructions encode to {} but the pinned bytes are {} -- the \
             assembler picked a different encoding than the one the game ships at 0x{:x}",
            spec.name,
            hex(&bytes),
            hex(spec.pin),
            spec.va
        );
        match spec.image.locate(&manifest_dir) {
            Some(path) => match spec.image.bytes_at(&path, spec.va, bytes.len()) {
                Some(actual) => assert_eq!(
                    bytes,
                    actual,
                    "{}: assembled {} but {} has {} at 0x{:x}",
                    spec.name,
                    hex(&bytes),
                    path.display(),
                    hex(&actual),
                    spec.va
                ),
                None => unverified.push(spec.name),
            },
            None => unverified.push(spec.name),
        }
        generated.push_str(&render(spec, &bytes));
        generated.push('\n');
    }

    if !unverified.is_empty() {
        let images: Vec<&str> = {
            let mut seen: Vec<&str> = specs
                .iter()
                .map(|(spec, _)| spec.image.env_override())
                .collect();
            seen.dedup();
            seen
        };
        println!(
            "cargo:warning=prologue ground truth skipped for {} ({}); set {} to verify against \
             the real module -- the pinned bytes still apply",
            out_file,
            unverified.join(", "),
            images.join(" / ")
        );
    }

    let out_dir =
        PathBuf::from(env::var_os("OUT_DIR").expect("cargo sets OUT_DIR for build scripts"));
    fs::write(out_dir.join(out_file), generated)
        .unwrap_or_else(|error| panic!("writing {out_file}: {error}"));
}

/// Tell cargo to re-run when the build script or this shared support file changes, and when an
/// image override moves.
pub fn declare_rerun(build_script_relative_support_path: &str) {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed={build_script_relative_support_path}");
    println!("cargo:rerun-if-env-changed=ER_DEOBF_BIN");
    println!("cargo:rerun-if-env-changed=ER_ERSC_DLL");
    println!("cargo:rerun-if-env-changed=ME3_STEAM_DIR");
}
