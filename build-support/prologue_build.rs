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
use iced_x86::{Code, Encoder, IcedError, Instruction, MemoryOperand, Register};

/// Mask byte meaning "this position must match exactly".
pub const PROLOGUE_BYTE_COMPARED: u8 = 0xff;
/// Mask byte meaning "ignore this position".
///
/// The ONLY positions that ever get this value are the displacement bytes of a RIP-relative
/// memory operand. See [`rip_relative_mask`] for why, and for what is deliberately NOT masked.
pub const PROLOGUE_BYTE_IGNORED: u8 = 0x00;

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
    /// `eldenring.exe` 1.17 -- the build the game has actually been since 2026-08-27. Ground
    /// truth is `eldenring-deobf-1.17.bin`, flat in the same way.
    ///
    /// # Why the version has to be part of the spec
    ///
    /// A prologue constant is compared against the bytes of the RUNNING game, and 1.17 moved
    /// things these bytes encode. The two save-request retractions are the clearest case: their
    /// whole body is `mov rax,[rip+disp]; mov byte [rax+0xb72],0; ret`, and `disp` reaches the
    /// GameMan singleton, which 1.17 moved by `+0x4070`. The instructions did not change and the
    /// field offset did not change, but four bytes of the encoding did -- so a signature
    /// generated against 1.16.2 fails its own byte check on 1.17, `call_verified_retract` fails
    /// closed, and the retraction silently never fires.
    ///
    /// Mapping the RVA does not fix that. The address translation gets the call to the right
    /// place; the check that decides whether to make the call is comparing against bytes from
    /// the previous build. Both halves have to move, which is why the version is named here
    /// rather than assumed.
    EldenRing1170,
    /// Seamless Co-op's `ersc.dll`, preferred base `0x180000000`. Ground truth is the installed
    /// DLL, an ordinary PE whose section table has to be walked to turn an RVA into an offset.
    Ersc,
}

impl Image {
    pub fn base(self) -> u64 {
        match self {
            Self::EldenRing | Self::EldenRing1170 => 0x1_4000_0000,
            Self::Ersc => 0x1_8000_0000,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::EldenRing => "eldenring-deobf.bin",
            Self::EldenRing1170 => "eldenring-deobf-1.17.bin",
            Self::Ersc => "ersc.dll",
        }
    }

    fn env_override(self) -> &'static str {
        match self {
            Self::EldenRing => "ER_DEOBF_BIN",
            Self::EldenRing1170 => "ER_DEOBF_BIN_1170",
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
            Self::EldenRing | Self::EldenRing1170 => manifest_dir
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
            Self::EldenRing | Self::EldenRing1170 => usize::try_from(rva).ok()?,
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

fn assemble(spec: &PrologueSpec, body: Assemble) -> (Vec<u8>, Vec<u8>) {
    let mut asm = CodeAssembler::new(64).unwrap_or_else(|error| {
        panic!("{}: CodeAssembler::new failed: {error}", spec.name);
    });
    body(&mut asm).unwrap_or_else(|error| {
        panic!(
            "{}: assembling named instructions failed: {error}",
            spec.name
        );
    });
    // Captured BEFORE `assemble` because that call needs `&mut asm`. These are the same named
    // instructions the pin is generated from, which is what makes the mask derivable rather than
    // hand-marked.
    let instructions: Vec<Instruction> = asm.instructions().to_vec();
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
    let kept = bytes[..take].to_vec();
    let mask = rip_relative_mask(spec, &instructions, &kept);
    (kept, mask)
}

/// The comparison mask for `bytes`: `PROLOGUE_BYTE_IGNORED` at every byte that belongs to the
/// displacement of a RIP-relative memory operand, `PROLOGUE_BYTE_COMPARED` everywhere else.
///
/// # What is masked, and why exactly that
///
/// `mov rax, [rip+disp32]` encodes the delta from the END of the instruction to the global it
/// names. Both ends move when the game is patched, so the four displacement bytes are GUARANTEED
/// to re-encode across builds even when the function is byte-for-byte the same code doing the
/// same job. A pin that includes them is pinning a value that cannot survive, and it disarms its
/// hook on a target that translated perfectly. Measured on 1.17: three prologues
/// (`SAVE_REQUEST_RETRACT_B72_SIG`, `..._B73_SIG`, `QUIT_PHASE_SETTLE_SIG`) differ from their
/// 1.16.2 pin ONLY inside that field.
///
/// # What is deliberately NOT masked
///
/// * **Opcode and ModRM bytes.** They are the instruction's identity; masking them would turn the
///   pin into "some instruction is here", which is not a pin at all.
/// * **Register-base memory displacements** (`[rax+0xb72]`). They are struct field offsets, and
///   they are the ONLY thing distinguishing `SAVE_REQUEST_RETRACT_B72_SIG` from `..._B73_SIG`:
///   masking them would let each of those two pins accept the other function. `map-rvas`'s
///   search matcher masks them because a search wants candidates; a gate wants identity.
/// * **Immediates** (`cmp [rax+0xbc4], 2`) and **relative branch targets** (`jne +0xa`). A branch
///   inside the checked window is relative to the function's OWN layout, so it does not move when
///   the function moves -- it survived 1.17 unchanged, and it is evidence worth keeping.
///
/// # Fail-closed
///
/// Every uncertainty answers "compare this byte". If an instruction will not re-encode, or the
/// re-encoded stream does not reproduce the assembled bytes exactly, the mask is all-COMPARED and
/// the pin behaves exactly as it did before this function existed.
fn rip_relative_mask(spec: &PrologueSpec, instructions: &[Instruction], bytes: &[u8]) -> Vec<u8> {
    let all_compared = vec![PROLOGUE_BYTE_COMPARED; bytes.len()];
    let mut mask = all_compared.clone();
    let mut encoder = Encoder::new(64);
    let mut stream: Vec<u8> = Vec::with_capacity(bytes.len());
    for instruction in instructions {
        let at = stream.len();
        let length = match encoder.encode(instruction, spec.va + at as u64) {
            Ok(length) => length,
            // A declared-data pseudo-instruction (`db`, used for MSVC's redundant REX prefix) or
            // anything else the encoder declines: give up on masking, keep every byte compared.
            Err(_) => return all_compared,
        };
        let encoded = encoder.take_buffer();
        if encoded.len() != length {
            return all_compared;
        }
        let offsets = encoder.get_constant_offsets();
        if instruction.is_ip_rel_memory_operand() && offsets.has_displacement() {
            let start = at + offsets.displacement_offset();
            for index in start..start + offsets.displacement_size() {
                if index < mask.len() {
                    mask[index] = PROLOGUE_BYTE_IGNORED;
                }
            }
        }
        stream.extend_from_slice(&encoded);
        if stream.len() >= bytes.len() {
            break;
        }
    }
    // Self-validation: the per-instruction re-encode must reproduce the block-assembled bytes.
    // If it does not, the offsets above describe a different byte stream than the pin, so the
    // mask would be aimed at the wrong positions.
    if stream.len() < bytes.len() || &stream[..bytes.len()] != bytes {
        return all_compared;
    }
    // Structural invariants. A mask that reaches byte 0, that masks everything, or that masks a
    // number of bytes no displacement field could account for, is a derivation bug, not a
    // relocation -- and it would silently weaken a gate. Break the build instead.
    let masked = mask.iter().filter(|&&m| m == PROLOGUE_BYTE_IGNORED).count();
    assert!(
        mask[0] == PROLOGUE_BYTE_COMPARED,
        "{}: derived mask would ignore the opening byte",
        spec.name
    );
    assert!(
        masked < mask.len() && masked % 4 == 0,
        "{}: derived mask ignores {masked} of {} bytes; a RIP disp32 field is 4 bytes",
        spec.name,
        mask.len()
    );
    mask
}

/// `[(start, len)]` for each masked run, for the generated doc comment.
fn masked_runs(mask: &[u8]) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut start = None;
    for (index, &byte) in mask.iter().enumerate() {
        match (byte == PROLOGUE_BYTE_IGNORED, start) {
            (true, None) => start = Some(index),
            (false, Some(from)) => {
                runs.push((from, index - from));
                start = None;
            }
            _ => {}
        }
    }
    if let Some(from) = start {
        runs.push((from, mask.len() - from));
    }
    runs
}

fn hex(bytes: &[u8]) -> String {
    bytes
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<Vec<_>>()
        .join(" ")
}

fn declaration(spec: &PrologueSpec, name: &str, bytes: &[u8], out: &mut String) {
    let space = if spec.visibility.is_empty() { "" } else { " " };
    match spec.shape {
        Shape::Slice => writeln!(out, "{}{space}const {}: &[u8] = &[", spec.visibility, name),
        Shape::Array => writeln!(
            out,
            "{}{space}const {}: [u8; {}] = [",
            spec.visibility,
            name,
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
}

fn render(spec: &PrologueSpec, bytes: &[u8], mask: &[u8]) -> String {
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
    declaration(spec, spec.name, bytes, &mut out);
    out.push('\n');

    let runs = masked_runs(mask);
    writeln!(
        out,
        "/// Comparison mask for [`{}`]: 0xff = compare, 0x00 = ignore.",
        spec.name
    )
    .expect("write to String");
    writeln!(out, "///").expect("write to String");
    if runs.is_empty() {
        writeln!(
            out,
            "/// Nothing is ignored -- this prologue has no RIP-relative operand, so every byte is"
        )
        .expect("write to String");
        writeln!(
            out,
            "/// an exact-match byte and the comparison is identical to a plain `==`."
        )
        .expect("write to String");
    } else {
        let listed: Vec<String> = runs
            .iter()
            .map(|(start, length)| format!("+{start}..+{}", start + length))
            .collect();
        writeln!(
            out,
            "/// IGNORED: {}. Each run is the displacement field of a RIP-relative memory operand,",
            listed.join(", ")
        )
        .expect("write to String");
        writeln!(
            out,
            "/// which re-encodes on every build because both the instruction and the global it"
        )
        .expect("write to String");
        writeln!(
            out,
            "/// names move. Opcode, ModRM, register-base displacements, immediates and relative"
        )
        .expect("write to String");
        writeln!(
            out,
            "/// branch targets are all still compared. See `build-support/prologue_build.rs`."
        )
        .expect("write to String");
    }
    writeln!(out, "#[allow(dead_code)]").expect("write to String");
    declaration(spec, &format!("{}_MASK", spec.name), mask, &mut out);
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
        let (bytes, mask) = assemble(spec, *body);
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
        generated.push_str(&render(spec, &bytes, &mask));
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
    println!("cargo:rerun-if-env-changed=ER_DEOBF_BIN_1170");
    println!("cargo:rerun-if-env-changed=ER_ERSC_DLL");
    println!("cargo:rerun-if-env-changed=ME3_STEAM_DIR");
}
