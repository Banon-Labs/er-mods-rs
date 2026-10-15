//! Reading the parts of a DXBC container that decide whether one pixel shader can stand in for
//! another, and the hash that signs it. Nothing here touches D3D12, so all of it is tested on the
//! host.
//!
//! # The container
//!
//! Shader bytecode and serialized root signatures are both DXBC containers: `DXBC`, a 16-byte
//! digest, a version, the total size, a part count at offset 28 and that many part offsets after
//! it. Each part is its four-character code, its size, then its bytes. The parts read here:
//!
//! - `ISG1` and `OSG1`, the input and output signatures: what a pixel shader takes from the stage
//!   before it, and what it writes to the render targets ([`Container::signature`]);
//! - `PSV0`, pipeline state validation: the registers the shader binds and how each signature
//!   element is interpolated ([`Container::psv0`]);
//! - `SFI0`, the optional features the shader needs ([`Container::sfi0`]);
//! - `DXIL`, whose header names the shader kind and model ([`Container::program`]);
//! - `ILDN`, the debug name FromSoft's build left in almost every game shader
//!   ([`Container::ildn`]).
//!
//! # The digest
//!
//! A native D3D12 runtime refuses a container whose digest is not DXC's retail hash of everything
//! after the digest, while vkd3d-proton does not look at it, so a blob that is wrong here passes
//! every Proton run and fails on Windows. [`retail_hash`] recomputes it.
//!
//! Every read is bounds-checked. A malformed container is an `Err` and never a panic, because these
//! bytes come from the game and, later, from a compiler fed with user source.

use core::fmt;
use core::ops::Range;

/// The root signature part's code.
pub const ROOT_SIGNATURE_PART: &[u8; 4] = b"RTS0";
/// The input signature part's code.
pub const INPUT_SIGNATURE_PART: &[u8; 4] = b"ISG1";
/// The output signature part's code.
pub const OUTPUT_SIGNATURE_PART: &[u8; 4] = b"OSG1";
/// The pipeline state validation part's code.
pub const PSV0_PART: &[u8; 4] = b"PSV0";
/// The shader feature flags part's code.
pub const SFI0_PART: &[u8; 4] = b"SFI0";
/// The program part's code: the DXIL header and its LLVM bitcode.
pub const PROGRAM_PART: &[u8; 4] = b"DXIL";
/// The debug name part's code.
pub const DEBUG_NAME_PART: &[u8; 4] = b"ILDN";

/// The container header: magic, digest, version, total size and part count. The part offsets
/// follow it.
const HEADER_BYTES: usize = 32;
/// Where the digest sits.
const DIGEST: Range<usize> = 4..20;
/// The digest covers every byte from here to the end of the container.
const HASHED_FROM: usize = 20;
const TOTAL_SIZE_AT: usize = 24;
const PART_COUNT_AT: usize = 28;
/// A part's code and size, before its bytes.
const PART_HEADER_BYTES: usize = 8;

/// Why a container, or one of its parts, could not be read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DxbcError {
    /// The bytes do not start with `DXBC`.
    NotAContainer,
    /// The structure named runs past the end of the bytes it was read from.
    Truncated(&'static str),
    /// The header declares no parts.
    NoParts,
    /// The part at this index of the part table lies outside the container or over its header.
    PartOutOfRange(usize),
    /// The container has no part with this code.
    MissingPart([u8; 4]),
    /// A field holds a value the format does not allow.
    Malformed(&'static str),
}

impl fmt::Display for DxbcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NotAContainer => f.write_str("not a DXBC container"),
            Self::Truncated(what) => write!(f, "{what} runs past the end of its bytes"),
            Self::NoParts => f.write_str("the container declares no parts"),
            Self::PartOutOfRange(index) => write!(f, "part {index} lies outside the container"),
            Self::MissingPart(code) => write!(f, "no {} part", code.escape_ascii()),
            Self::Malformed(why) => f.write_str(why),
        }
    }
}

impl std::error::Error for DxbcError {}

/// `start..start + len`, or `None` where that would overflow.
fn span(start: usize, len: usize) -> Option<Range<usize>> {
    Some(start..start.checked_add(len)?)
}

fn read_u32(bytes: &[u8], at: usize) -> Option<u32> {
    let word: [u8; 4] = bytes.get(span(at, 4)?)?.try_into().ok()?;
    Some(u32::from_le_bytes(word))
}

fn read_u16(bytes: &[u8], at: usize) -> Option<u16> {
    let half: [u8; 2] = bytes.get(span(at, 2)?)?.try_into().ok()?;
    Some(u16::from_le_bytes(half))
}

/// A little-endian `u32` as a size or offset, or [`DxbcError::Truncated`] naming `what`.
fn size_at(bytes: &[u8], at: usize, what: &'static str) -> Result<usize, DxbcError> {
    read_u32(bytes, at)
        .map(|word| word as usize)
        .ok_or(DxbcError::Truncated(what))
}

fn u32_at(bytes: &[u8], at: usize, what: &'static str) -> Result<u32, DxbcError> {
    read_u32(bytes, at).ok_or(DxbcError::Truncated(what))
}

/// A forward reader over one part, for the formats that are a run of variable-length tables.
struct Cursor<'a> {
    bytes: &'a [u8],
    at: usize,
    what: &'static str,
}

impl<'a> Cursor<'a> {
    fn new(bytes: &'a [u8], what: &'static str) -> Self {
        Self { bytes, at: 0, what }
    }

    fn u32(&mut self) -> Result<u32, DxbcError> {
        let word = u32_at(self.bytes, self.at, self.what)?;
        self.at += 4;
        Ok(word)
    }

    fn size(&mut self) -> Result<usize, DxbcError> {
        self.u32().map(|word| word as usize)
    }

    fn take(&mut self, len: usize) -> Result<&'a [u8], DxbcError> {
        let range = span(self.at, len).ok_or(DxbcError::Truncated(self.what))?;
        let taken = self
            .bytes
            .get(range.clone())
            .ok_or(DxbcError::Truncated(self.what))?;
        self.at = range.end;
        Ok(taken)
    }

    /// `count` records of `size` bytes each.
    fn records(&mut self, count: usize, size: usize) -> Result<&'a [u8], DxbcError> {
        let len = count
            .checked_mul(size)
            .ok_or(DxbcError::Truncated(self.what))?;
        self.take(len)
    }
}

/// One part of a container.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Part<'a> {
    pub code: [u8; 4],
    /// Byte offset of the part's header from the start of the container.
    pub offset: usize,
    /// The part's bytes, after its code and size.
    pub data: &'a [u8],
}

/// A container whose header and part table have been checked: every part lies inside it.
#[derive(Clone, Copy, Debug)]
pub struct Container<'a> {
    /// Exactly the size the header declares.
    bytes: &'a [u8],
    part_count: usize,
}

impl<'a> Container<'a> {
    /// Check the header and the part table.
    ///
    /// Bytes past the size the header declares are not part of the container and are ignored, so
    /// [`Container::total_size`] is what a caller compares with its own length to refuse them.
    ///
    /// # Errors
    ///
    /// Anything other than `DXBC`, a header or part table that runs past the end of `bytes`, no
    /// parts, or a part that lies outside the declared size or over the header.
    pub fn parse(bytes: &'a [u8]) -> Result<Self, DxbcError> {
        if bytes.get(..4) != Some(b"DXBC".as_slice()) {
            return Err(DxbcError::NotAContainer);
        }
        let total = size_at(bytes, TOTAL_SIZE_AT, "the container header")?;
        let bytes = bytes
            .get(..total)
            .ok_or(DxbcError::Truncated("the container"))?;
        let part_count = size_at(bytes, PART_COUNT_AT, "the container header")?;
        if part_count == 0 {
            return Err(DxbcError::NoParts);
        }
        let table_end = part_count
            .checked_mul(4)
            .and_then(|len| len.checked_add(HEADER_BYTES))
            .filter(|&end| end <= total)
            .ok_or(DxbcError::Truncated("the part table"))?;
        for index in 0..part_count {
            let offset = size_at(bytes, HEADER_BYTES + 4 * index, "the part table")?;
            let size = offset
                .checked_add(4)
                .and_then(|at| read_u32(bytes, at))
                .ok_or(DxbcError::PartOutOfRange(index))?;
            let fits = offset
                .checked_add(PART_HEADER_BYTES)
                .and_then(|start| span(start, size as usize))
                .is_some_and(|data| data.end <= total);
            if offset < table_end || !fits {
                return Err(DxbcError::PartOutOfRange(index));
            }
        }
        Ok(Self { bytes, part_count })
    }

    /// The container's own bytes, `DXBC` to the declared end.
    #[must_use]
    pub fn bytes(&self) -> &'a [u8] {
        self.bytes
    }

    /// The size the header declares.
    #[must_use]
    pub fn total_size(&self) -> usize {
        self.bytes.len()
    }

    /// The 16 bytes after `DXBC`: all zero until a validator signs the container.
    #[must_use]
    pub fn digest(&self) -> [u8; 16] {
        self.bytes
            .get(DIGEST)
            .and_then(|digest| digest.try_into().ok())
            .unwrap_or_default()
    }

    /// The part at `index` of the part table. `parse` proved every one is in range.
    fn part_at(&self, index: usize) -> Option<Part<'a>> {
        let offset = read_u32(self.bytes, HEADER_BYTES + 4 * index)? as usize;
        let code: [u8; 4] = self.bytes.get(span(offset, 4)?)?.try_into().ok()?;
        let size = read_u32(self.bytes, offset.checked_add(4)?)? as usize;
        let data = self
            .bytes
            .get(span(offset.checked_add(PART_HEADER_BYTES)?, size)?)?;
        Some(Part { code, offset, data })
    }

    /// Every part, in part-table order.
    #[must_use]
    pub fn parts(&self) -> Vec<Part<'a>> {
        (0..self.part_count)
            .filter_map(|index| self.part_at(index))
            .collect()
    }

    /// The bytes of the first part with this code.
    #[must_use]
    pub fn part(&self, code: &[u8; 4]) -> Option<&'a [u8]> {
        (0..self.part_count)
            .filter_map(|index| self.part_at(index))
            .find(|part| &part.code == code)
            .map(|part| part.data)
    }

    fn required(&self, code: &[u8; 4]) -> Result<&'a [u8], DxbcError> {
        self.part(code).ok_or(DxbcError::MissingPart(*code))
    }

    /// The elements of a signature part: [`INPUT_SIGNATURE_PART`], [`OUTPUT_SIGNATURE_PART`], or
    /// `PSG1` for patch constants.
    ///
    /// # Errors
    ///
    /// The part is absent, its element table runs past its end, or an element's name does not
    /// resolve to a terminated UTF-8 string inside the part.
    pub fn signature(&self, code: &[u8; 4]) -> Result<Vec<SigElement>, DxbcError> {
        parse_signature(self.required(code)?)
    }

    /// The `PSV0` part: bound registers and, from runtime info version 1, interpolation modes.
    ///
    /// # Errors
    ///
    /// The part is absent, a table runs past its end, or a record is smaller than version 0 of
    /// its structure.
    pub fn psv0(&self) -> Result<Psv0, DxbcError> {
        parse_psv0(self.required(PSV0_PART)?)
    }

    /// The registers the shader binds, from `PSV0`.
    ///
    /// # Errors
    ///
    /// As [`Container::psv0`].
    pub fn psv0_binds(&self) -> Result<Vec<PsvBind>, DxbcError> {
        self.psv0().map(|psv0| psv0.binds)
    }

    /// The interpolation mode of every signature element, from `PSV0`.
    ///
    /// # Errors
    ///
    /// As [`Container::psv0`], and [`DxbcError::Malformed`] when the runtime info is version 0,
    /// which carries no signature elements. An empty answer there would make two shaders' modes
    /// compare equal without either having been read.
    pub fn psv0_interp(&self) -> Result<PsvInterpolation, DxbcError> {
        self.psv0()?.interpolation.ok_or(DxbcError::Malformed(
            "PSV0 runtime info version 0 carries no signature elements",
        ))
    }

    /// The optional features the shader needs, as `SFI0` records them.
    ///
    /// DXC writes the part for every DXIL program, a zero one included (all five ports and their
    /// five game originals carry it), so an absent part is an error rather than a zero.
    ///
    /// # Errors
    ///
    /// The part is absent, or is not exactly 8 bytes.
    pub fn sfi0(&self) -> Result<u64, DxbcError> {
        let flags: [u8; 8] = self
            .required(SFI0_PART)?
            .try_into()
            .map_err(|_| DxbcError::Malformed("SFI0 is not 8 bytes"))?;
        Ok(u64::from_le_bytes(flags))
    }

    /// The header of the `DXIL` part.
    ///
    /// # Errors
    ///
    /// The part is absent, shorter than the header, without the `DXIL` magic, or declares a size
    /// or a bitcode range that does not fit inside it.
    pub fn program(&self) -> Result<ProgramHeader, DxbcError> {
        parse_program(self.required(PROGRAM_PART)?)
    }

    /// The `ILDN` debug name: for a game shader the path of its PDB, which ends in the pass name.
    ///
    /// # Errors
    ///
    /// The part is absent, the name runs past its end or is not followed by its terminator, or it
    /// is not UTF-8.
    pub fn ildn(&self) -> Result<&'a str, DxbcError> {
        parse_debug_name(self.required(DEBUG_NAME_PART)?)
    }

    /// DXC's retail hash of this container.
    #[must_use]
    pub fn retail_hash(&self) -> [u8; 16] {
        retail_hash(self.bytes)
    }

    /// Whether the digest is set and is the retail hash of the bytes after it, which is what a
    /// native D3D12 runtime checks before it accepts the container.
    #[must_use]
    pub fn is_signed_correctly(&self) -> bool {
        let digest = self.digest();
        digest != [0; 16] && digest == self.retail_hash()
    }
}

/// The bytes of one part of a DXBC container, by four-character code, or `None` when the bytes
/// are not a well-formed container or have no such part.
#[must_use]
pub fn container_part<'a>(container: &'a [u8], code: &[u8; 4]) -> Option<&'a [u8]> {
    Container::parse(container).ok()?.part(code)
}

/// `D3D_NAME_POSITION`: an element's system value when it is `SV_Position`.
pub const SYSTEM_VALUE_POSITION: u32 = 1;
/// `D3D_NAME_TARGET`: an element's system value when it is `SV_Target`.
pub const SYSTEM_VALUE_TARGET: u32 = 64;
/// `DxilProgramSigCompType::Float32`.
pub const COMPONENT_FLOAT32: u32 = 3;

/// One element of an `ISG1`, `OSG1` or `PSG1` signature: DXC's `DxilProgramSignatureElement`, 32
/// bytes, with its name resolved.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SigElement {
    /// The geometry-shader output stream; 0 everywhere else.
    pub stream: u32,
    /// The semantic, such as `SV_Position` or `TEXCOORD`, without its index.
    pub name: String,
    pub semantic_index: u32,
    /// `D3D_NAME`: 0 for a semantic of the shader's own, [`SYSTEM_VALUE_POSITION`],
    /// [`SYSTEM_VALUE_TARGET`] and so on.
    pub system_value: u32,
    /// `DxilProgramSigCompType`, such as [`COMPONENT_FLOAT32`].
    pub component_type: u32,
    pub register: u32,
    /// The components the element occupies, `x` in bit 0.
    pub mask: u8,
    /// For an input, the components the shader always reads; for an output, the components it
    /// never writes.
    pub rw_mask: u8,
    /// `DxilProgramSigMinPrecision`.
    pub min_precision: u32,
}

impl SigElement {
    /// Whether `other` sits in the same slot in the same way, which is what linking a pixel shader
    /// to the stage before it compares.
    ///
    /// The read and write mask is not compared. It records what this shader's code does with the
    /// element, not where the element is, and a port that also reads `SV_Position` (the probe
    /// does) must still link where the original did.
    #[must_use]
    pub fn links_like(&self, other: &Self) -> bool {
        self.stream == other.stream
            && self.name == other.name
            && self.semantic_index == other.semantic_index
            && self.system_value == other.system_value
            && self.component_type == other.component_type
            && self.register == other.register
            && self.mask == other.mask
            && self.min_precision == other.min_precision
    }
}

/// Size of one `DxilProgramSignatureElement`.
const SIGNATURE_ELEMENT_BYTES: usize = 32;

fn parse_signature(part: &[u8]) -> Result<Vec<SigElement>, DxbcError> {
    const WHAT: &str = "a signature element table";
    let count = size_at(part, 0, WHAT)?;
    let first = size_at(part, 4, WHAT)?;
    let table = count
        .checked_mul(SIGNATURE_ELEMENT_BYTES)
        .and_then(|len| span(first, len))
        .and_then(|range| part.get(range))
        .ok_or(DxbcError::Truncated(WHAT))?;
    table
        .as_chunks::<SIGNATURE_ELEMENT_BYTES>()
        .0
        .iter()
        .map(|element| {
            Ok(SigElement {
                stream: u32_at(element, 0, WHAT)?,
                name: name_at(part, size_at(element, 4, WHAT)?)?.to_owned(),
                semantic_index: u32_at(element, 8, WHAT)?,
                system_value: u32_at(element, 12, WHAT)?,
                component_type: u32_at(element, 16, WHAT)?,
                register: u32_at(element, 20, WHAT)?,
                // Indexes into a 32-byte array, checked when this compiles.
                mask: element[24],
                rw_mask: element[25],
                min_precision: u32_at(element, 28, WHAT)?,
            })
        })
        .collect()
}

/// The terminated string at `at`, an offset from the start of `part`.
fn name_at(part: &[u8], at: usize) -> Result<&str, DxbcError> {
    let tail = part
        .get(at..)
        .ok_or(DxbcError::Truncated("a signature element name"))?;
    let end = tail
        .iter()
        .position(|&byte| byte == 0)
        .ok_or(DxbcError::Malformed(
            "a signature element name has no terminator",
        ))?;
    core::str::from_utf8(tail.get(..end).unwrap_or_default())
        .map_err(|_| DxbcError::Malformed("a signature element name is not UTF-8"))
}

/// `PSVResourceType::Sampler`.
pub const PSV_RESOURCE_SAMPLER: u32 = 1;
/// `PSVResourceType::CBV`.
pub const PSV_RESOURCE_CBV: u32 = 2;
/// `PSVResourceType::SRVTyped`: a texture.
pub const PSV_RESOURCE_SRV_TYPED: u32 = 3;
/// `PSVResourceType::SRVRaw`.
pub const PSV_RESOURCE_SRV_RAW: u32 = 4;
/// `PSVResourceType::SRVStructured`.
pub const PSV_RESOURCE_SRV_STRUCTURED: u32 = 5;
/// `PSVResourceType::UAVTyped`.
pub const PSV_RESOURCE_UAV_TYPED: u32 = 6;
/// `PSVResourceType::UAVRaw`: a `RWByteAddressBuffer`.
pub const PSV_RESOURCE_UAV_RAW: u32 = 7;
/// `PSVResourceType::UAVStructured`.
pub const PSV_RESOURCE_UAV_STRUCTURED: u32 = 8;
/// `PSVResourceType::UAVStructuredWithCounter`.
pub const PSV_RESOURCE_UAV_STRUCTURED_WITH_COUNTER: u32 = 9;

/// One register range a shader binds: DXC's `PSVResourceBindInfo0`, or `PSVResourceBindInfo1`
/// when the records are 24 bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct PsvBind {
    /// `PSVResourceType`, one of the `PSV_RESOURCE_*` constants.
    pub resource_type: u32,
    pub space: u32,
    pub lower_bound: u32,
    /// Equal to `lower_bound` for one register; `u32::MAX` for an unbounded array.
    pub upper_bound: u32,
    /// `PSVResourceKind`, from version 1 records.
    pub kind: Option<u32>,
    /// `PSVResourceFlags`, from version 1 records.
    pub flags: Option<u32>,
}

/// The interpolation mode of each signature element, in signature order: DXC's
/// `DXIL::InterpolationMode`, for example 2 for linear and 4 for linear without perspective.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PsvInterpolation {
    pub inputs: Vec<u8>,
    pub outputs: Vec<u8>,
    pub patch_constant_or_primitive: Vec<u8>,
}

/// What `PSV0` says about a shader.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Psv0 {
    /// `PSVShaderKind`, from runtime info version 1.
    pub shader_stage: Option<u8>,
    pub binds: Vec<PsvBind>,
    /// `None` for runtime info version 0, which has no signature elements.
    pub interpolation: Option<PsvInterpolation>,
}

/// `sizeof(PSVRuntimeInfo0)`.
const PSV_RUNTIME_INFO0_BYTES: usize = 24;
/// `sizeof(PSVRuntimeInfo1)`, the first version with signature elements.
const PSV_RUNTIME_INFO1_BYTES: usize = 36;
/// Where `PSVRuntimeInfo1` keeps `ShaderStage` and the three signature element counts.
const PSV_SHADER_STAGE_AT: usize = 24;
const PSV_INPUT_ELEMENTS_AT: usize = 28;
const PSV_OUTPUT_ELEMENTS_AT: usize = 29;
const PSV_PATCH_ELEMENTS_AT: usize = 30;
/// `sizeof(PSVResourceBindInfo0)` and `sizeof(PSVResourceBindInfo1)`.
const PSV_BIND0_BYTES: usize = 16;
const PSV_BIND1_BYTES: usize = 24;
/// `sizeof(PSVSignatureElement0)`, and where it keeps `InterpolationMode`.
const PSV_ELEMENT0_BYTES: usize = 16;
const PSV_INTERPOLATION_AT: usize = 13;

/// `PSV0` is the runtime info's size and bytes, the resource count and, when there are any, the
/// bind record size and records. From version 1 there follow a string table, a semantic index
/// table, and, when there are signature elements, their record size and records: inputs, then
/// outputs, then patch constants or primitives.
fn parse_psv0(part: &[u8]) -> Result<Psv0, DxbcError> {
    let mut at = Cursor::new(part, "the PSV0 part");
    let runtime_size = at.size()?;
    if runtime_size < PSV_RUNTIME_INFO0_BYTES {
        return Err(DxbcError::Malformed(
            "PSV0 runtime info is smaller than version 0",
        ));
    }
    let runtime = at.take(runtime_size)?;
    let resource_count = at.size()?;
    let mut binds = Vec::new();
    if resource_count > 0 {
        let record = at.size()?;
        if record < PSV_BIND0_BYTES {
            return Err(DxbcError::Malformed(
                "PSV0 bind records are smaller than version 0",
            ));
        }
        const WHAT: &str = "a PSV0 bind record";
        binds = at
            .records(resource_count, record)?
            .chunks_exact(record)
            .map(|bind| {
                let version1 = record >= PSV_BIND1_BYTES;
                Ok(PsvBind {
                    resource_type: u32_at(bind, 0, WHAT)?,
                    space: u32_at(bind, 4, WHAT)?,
                    lower_bound: u32_at(bind, 8, WHAT)?,
                    upper_bound: u32_at(bind, 12, WHAT)?,
                    kind: if version1 {
                        Some(u32_at(bind, 16, WHAT)?)
                    } else {
                        None
                    },
                    flags: if version1 {
                        Some(u32_at(bind, 20, WHAT)?)
                    } else {
                        None
                    },
                })
            })
            .collect::<Result<_, DxbcError>>()?;
    }
    if runtime_size < PSV_RUNTIME_INFO1_BYTES {
        return Ok(Psv0 {
            shader_stage: None,
            binds,
            interpolation: None,
        });
    }
    let count_at = |at: usize| usize::from(runtime.get(at).copied().unwrap_or_default());
    let inputs = count_at(PSV_INPUT_ELEMENTS_AT);
    let outputs = count_at(PSV_OUTPUT_ELEMENTS_AT);
    let patch = count_at(PSV_PATCH_ELEMENTS_AT);
    let strings = at.size()?;
    at.take(strings)?;
    let indexes = at.size()?;
    at.records(indexes, 4)?;
    let mut modes = Vec::new();
    if inputs + outputs + patch > 0 {
        let record = at.size()?;
        if record < PSV_ELEMENT0_BYTES {
            return Err(DxbcError::Malformed(
                "PSV0 signature elements are smaller than version 0",
            ));
        }
        modes = at
            .records(inputs + outputs + patch, record)?
            .chunks_exact(record)
            .map(|element| {
                element
                    .get(PSV_INTERPOLATION_AT)
                    .copied()
                    .ok_or(DxbcError::Truncated("a PSV0 signature element"))
            })
            .collect::<Result<_, DxbcError>>()?;
    }
    Ok(Psv0 {
        shader_stage: runtime.get(PSV_SHADER_STAGE_AT).copied(),
        binds,
        interpolation: Some(PsvInterpolation {
            inputs: modes.iter().copied().take(inputs).collect(),
            outputs: modes.iter().copied().skip(inputs).take(outputs).collect(),
            patch_constant_or_primitive: modes.iter().copied().skip(inputs + outputs).collect(),
        }),
    })
}

/// `DXIL::ShaderKind`, from the program header.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ShaderKind {
    Pixel,
    Vertex,
    Geometry,
    Hull,
    Domain,
    Compute,
    Library,
    /// Ray tracing, mesh, amplification and node shaders, which nothing here reads.
    Other(u32),
}

impl ShaderKind {
    fn from_raw(raw: u32) -> Self {
        match raw {
            0 => Self::Pixel,
            1 => Self::Vertex,
            2 => Self::Geometry,
            3 => Self::Hull,
            4 => Self::Domain,
            5 => Self::Compute,
            6 => Self::Library,
            other => Self::Other(other),
        }
    }
}

/// DXC's `DxilProgramHeader`, with its `DxilBitcodeHeader`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ProgramHeader {
    pub kind: ShaderKind,
    /// The shader model: `ps_6_0` is major 6, minor 0.
    pub major: u32,
    pub minor: u32,
    /// The part's size in 32-bit words, this header included.
    pub size_in_words: u32,
    /// The DXIL version, major in bits 8 and up.
    pub dxil_version: u32,
    /// Where the LLVM bitcode starts, counted from the bitcode header 8 bytes into the part.
    pub bitcode_offset: u32,
    pub bitcode_size: u32,
}

/// `sizeof(DxilProgramHeader)`.
const PROGRAM_HEADER_BYTES: usize = 24;
/// Where the bitcode header starts, which `BitcodeOffset` counts from.
const BITCODE_HEADER_AT: usize = 8;

fn parse_program(part: &[u8]) -> Result<ProgramHeader, DxbcError> {
    const WHAT: &str = "the DXIL program header";
    if part.len() < PROGRAM_HEADER_BYTES {
        return Err(DxbcError::Truncated(WHAT));
    }
    if part.get(BITCODE_HEADER_AT..BITCODE_HEADER_AT + 4) != Some(b"DXIL".as_slice()) {
        return Err(DxbcError::Malformed(
            "the DXIL program header has no DXIL magic",
        ));
    }
    // `EncodeVersion`: the kind in bits 16 and up, the major model in bits 4 to 7, the minor in 0
    // to 3.
    let version = u32_at(part, 0, WHAT)?;
    let header = ProgramHeader {
        kind: ShaderKind::from_raw(version >> 16),
        major: (version >> 4) & 0xf,
        minor: version & 0xf,
        size_in_words: u32_at(part, 4, WHAT)?,
        dxil_version: u32_at(part, 12, WHAT)?,
        bitcode_offset: u32_at(part, 16, WHAT)?,
        bitcode_size: u32_at(part, 20, WHAT)?,
    };
    let declared_fits = (header.size_in_words as usize)
        .checked_mul(4)
        .is_some_and(|len| len <= part.len());
    let bitcode_fits = BITCODE_HEADER_AT
        .checked_add(header.bitcode_offset as usize)
        .and_then(|start| span(start, header.bitcode_size as usize))
        .is_some_and(|bitcode| bitcode.end <= part.len());
    if !declared_fits || !bitcode_fits {
        return Err(DxbcError::Truncated("the DXIL program"));
    }
    Ok(header)
}

/// `DxilShaderDebugName`: flags, the name's length, the name, its terminator, then padding to four
/// bytes.
fn parse_debug_name(part: &[u8]) -> Result<&str, DxbcError> {
    let length = read_u16(part, 2).ok_or(DxbcError::Truncated("the ILDN header"))?;
    let name = part
        .get(4..4 + usize::from(length) + 1)
        .ok_or(DxbcError::Truncated("the ILDN name"))?;
    let Some((&0, name)) = name.split_last() else {
        return Err(DxbcError::Malformed("the ILDN name has no terminator"));
    };
    core::str::from_utf8(name).map_err(|_| DxbcError::Malformed("the ILDN name is not UTF-8"))
}

/// MD5's starting state.
const MD5_INIT: [u32; 4] = [0x6745_2301, 0xefcd_ab89, 0x98ba_dcfe, 0x1032_5476];

/// MD5's per-step additive constants, `floor(abs(sin(i + 1)) * 2^32)`.
const MD5_K: [u32; 64] = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
];

/// MD5's per-step rotations.
const MD5_S: [u32; 64] = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9,
    14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15,
    21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

/// MD5's compression function: one 64-byte block into the state.
fn md5_block(state: &mut [u32; 4], block: &[u8; 64]) {
    let (words, _) = block.as_chunks::<4>();
    let mut m = [0_u32; 16];
    for (word, bytes) in m.iter_mut().zip(words) {
        *word = u32::from_le_bytes(*bytes);
    }
    let [mut a, mut b, mut c, mut d] = *state;
    for (step, (&k, &s)) in MD5_K.iter().zip(&MD5_S).enumerate() {
        let (f, g) = match step / 16 {
            0 => ((b & c) | (!b & d), step),
            1 => ((d & b) | (!d & c), (5 * step + 1) % 16),
            2 => (b ^ c ^ d, (3 * step + 5) % 16),
            _ => (c ^ (b | !d), (7 * step) % 16),
        };
        let mixed = f
            .wrapping_add(a)
            .wrapping_add(k)
            .wrapping_add(m.get(g).copied().unwrap_or_default())
            .rotate_left(s);
        a = d;
        d = c;
        c = b;
        b = b.wrapping_add(mixed);
    }
    for (word, step) in state.iter_mut().zip([a, b, c, d]) {
        *word = word.wrapping_add(step);
    }
}

/// DXC's `ComputeHashRetail` over bytes `[20..]` of `container`: the digest a validator writes at
/// bytes `4..20`.
///
/// It is MD5's compression function with DXC's own padding, and without MD5's finalisation, so
/// no MD5 library produces it. The last block carries the length twice: the bit count as its first
/// word, where MD5 puts a 64-bit bit count at the end, and `1 | bytes << 1` as its last word. When
/// the data's last partial row holds 56 bytes or more, the two words no longer fit beside it: the
/// `0x80` terminator and zeros close that row, and a row of their own carries the two words. The
/// length is DXC's 32-bit `UINT`.
#[must_use]
pub fn retail_hash(container: &[u8]) -> [u8; 16] {
    let hashed = container.get(HASHED_FROM..).unwrap_or_default();
    let length = hashed.len() as u32;
    let (blocks, rest) = hashed.as_chunks::<64>();
    let mut state = MD5_INIT;
    for block in blocks {
        md5_block(&mut state, block);
    }
    // `rest` is shorter than 64 bytes, so every index below is in range.
    let mut tail = [0_u8; 64];
    if rest.len() < 56 {
        tail[..4].copy_from_slice(&(length << 3).to_le_bytes());
        tail[4..4 + rest.len()].copy_from_slice(rest);
        tail[4 + rest.len()] = 0x80;
    } else {
        tail[..rest.len()].copy_from_slice(rest);
        tail[rest.len()] = 0x80;
        md5_block(&mut state, &tail);
        tail = [0; 64];
        tail[..4].copy_from_slice(&(length << 3).to_le_bytes());
    }
    tail[60..].copy_from_slice(&(1 | (length << 1)).to_le_bytes());
    md5_block(&mut state, &tail);
    let mut digest = [0_u8; 16];
    for (bytes, word) in digest.as_chunks_mut::<4>().0.iter_mut().zip(state) {
        *bytes = word.to_le_bytes();
    }
    digest
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replace::{REPLACEMENTS, Replacement, fnv1_64};
    use std::path::{Path, PathBuf};

    /// `DXIL::InterpolationMode::Linear` and `LinearNoperspective`.
    const LINEAR: u8 = 2;
    const NO_PERSPECTIVE: u8 = 4;

    /// A container with the given parts and a correct total size.
    fn container(parts: &[(&[u8; 4], &[u8])]) -> Vec<u8> {
        let header = HEADER_BYTES + 4 * parts.len();
        let total = header
            + parts
                .iter()
                .map(|(_, data)| PART_HEADER_BYTES + data.len())
                .sum::<usize>();
        let mut out = b"DXBC".to_vec();
        out.extend_from_slice(&[0; 16]);
        out.extend_from_slice(&1_u16.to_le_bytes());
        out.extend_from_slice(&0_u16.to_le_bytes());
        out.extend_from_slice(&(total as u32).to_le_bytes());
        out.extend_from_slice(&(parts.len() as u32).to_le_bytes());
        let mut at = header;
        for (_, data) in parts {
            out.extend_from_slice(&(at as u32).to_le_bytes());
            at += PART_HEADER_BYTES + data.len();
        }
        for (code, data) in parts {
            out.extend_from_slice(*code);
            out.extend_from_slice(&(data.len() as u32).to_le_bytes());
            out.extend_from_slice(data);
        }
        out
    }

    fn set_u32(bytes: &mut [u8], at: usize, value: u32) {
        bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
    }

    /// A signature part with these `(name, register, system value, mask)` elements.
    fn signature_part(elements: &[(&str, u32, u32, u8)]) -> Vec<u8> {
        let table = 8;
        let names = table + elements.len() * SIGNATURE_ELEMENT_BYTES;
        let mut part = vec![0_u8; names];
        set_u32(&mut part, 0, elements.len() as u32);
        set_u32(&mut part, 4, table as u32);
        for (index, (name, register, system_value, mask)) in elements.iter().enumerate() {
            let at = table + index * SIGNATURE_ELEMENT_BYTES;
            let name_at = part.len() as u32;
            set_u32(&mut part, at + 4, name_at);
            set_u32(&mut part, at + 12, *system_value);
            set_u32(&mut part, at + 16, COMPONENT_FLOAT32);
            set_u32(&mut part, at + 20, *register);
            part[at + 24] = *mask;
            part.extend_from_slice(name.as_bytes());
            part.push(0);
        }
        part
    }

    fn parse(blob: &[u8]) -> Container<'_> {
        Container::parse(blob).expect("a well-formed container")
    }

    fn element(name: &str, register: u32, system_value: u32, mask: u8, rw: u8) -> SigElement {
        SigElement {
            stream: 0,
            name: name.to_owned(),
            semantic_index: 0,
            system_value,
            component_type: COMPONENT_FLOAT32,
            register,
            mask,
            rw_mask: rw,
            min_precision: 0,
        }
    }

    fn replacement(name: &str) -> &'static Replacement {
        REPLACEMENTS
            .iter()
            .find(|r| r.name == name)
            .expect("a replacement by that name")
    }

    /// Standard MD5 built from the same compression function, so the function is pinned by the
    /// RFC 1321 test vectors independently of DXC's padding.
    fn md5(message: &[u8]) -> [u8; 16] {
        let mut padded = message.to_vec();
        padded.push(0x80);
        while padded.len() % 64 != 56 {
            padded.push(0);
        }
        padded.extend_from_slice(&((message.len() as u64) * 8).to_le_bytes());
        let mut state = MD5_INIT;
        for block in padded.as_chunks::<64>().0 {
            md5_block(&mut state, block);
        }
        let mut digest = [0_u8; 16];
        for (bytes, word) in digest.as_chunks_mut::<4>().0.iter_mut().zip(state) {
            *bytes = word.to_le_bytes();
        }
        digest
    }

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    #[test]
    fn the_compression_function_is_md5() {
        assert_eq!(hex(&md5(b"")), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(hex(&md5(b"abc")), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(
            hex(&md5(
                b"12345678901234567890123456789012345678901234567890123456789012345678901234567890"
            )),
            "57edf4a22be3c955ac49da2e2107b67a"
        );
    }

    #[test]
    fn retail_hash_is_the_digest_of_every_committed_blob() {
        // Their hashed lengths end 28, 48, 28, 23 and 63 bytes into a row, so both the one-row
        // and the two-row padding are checked against DXC's own output.
        for replacement in REPLACEMENTS {
            let blob = replacement.dxil;
            assert_eq!(
                retail_hash(blob).as_slice(),
                &blob[DIGEST],
                "{}",
                replacement.name
            );
            assert!(parse(blob).is_signed_correctly(), "{}", replacement.name);
        }
    }

    #[test]
    fn a_changed_byte_or_a_blank_digest_is_not_signed() {
        let mut changed = replacement("ToneMap_TabledToneMapPS").dxil.to_vec();
        let last = changed.len() - 1;
        changed[last] ^= 1;
        assert!(!parse(&changed).is_signed_correctly());
        let mut blank = replacement("ToneMap_TabledToneMapPS").dxil.to_vec();
        blank[DIGEST].fill(0);
        assert!(!parse(&blank).is_signed_correctly());
    }

    #[test]
    fn retail_hash_takes_every_length_without_panicking() {
        let bytes: Vec<u8> = (0..=255).collect();
        let mut seen = std::collections::HashSet::new();
        for len in 0..bytes.len() {
            seen.insert(retail_hash(&bytes[..len]));
        }
        // Bytes before 20 are not hashed, so lengths 0 to 20 share one digest.
        assert_eq!(seen.len(), bytes.len() - HASHED_FROM);
    }

    #[test]
    fn the_tonemap_port_takes_sv_position_and_writes_sv_target() {
        let tonemap = parse(replacement("ToneMap_TabledToneMapPS").dxil);
        let inputs = tonemap.signature(INPUT_SIGNATURE_PART).unwrap();
        let expected_inputs = [
            element("SV_Position", 0, SYSTEM_VALUE_POSITION, 0xf, 0),
            element("TEXCOORD", 1, 0, 0x7, 0x3),
        ];
        assert_eq!(inputs.len(), expected_inputs.len());
        for (found, expected) in inputs.iter().zip(&expected_inputs) {
            assert!(found.links_like(expected), "{found:?} vs {expected:?}");
        }
        assert_eq!(
            tonemap.signature(OUTPUT_SIGNATURE_PART).unwrap(),
            [element("SV_Target", 0, SYSTEM_VALUE_TARGET, 0xf, 0)]
        );
        assert_eq!(
            tonemap.psv0_interp().unwrap(),
            PsvInterpolation {
                inputs: vec![NO_PERSPECTIVE, LINEAR],
                outputs: vec![0],
                patch_constant_or_primitive: vec![],
            }
        );
    }

    #[test]
    fn every_port_binds_its_parameters_at_b0_space42() {
        for replacement in REPLACEMENTS {
            let binds = parse(replacement.dxil).psv0_binds().unwrap();
            assert!(
                binds
                    .iter()
                    .any(|bind| bind.resource_type == PSV_RESOURCE_CBV
                        && bind.space == 42
                        && bind.lower_bound == 0
                        && bind.upper_bound == 0),
                "{}: {binds:?}",
                replacement.name
            );
        }
    }

    #[test]
    fn every_port_writes_the_probe_and_binds_nothing_else_in_space42() {
        // A port that never calls `er_probe_record` has the probe stripped by the compiler, and
        // then nothing can tell its variant from the game's own pass.
        for replacement in REPLACEMENTS {
            let mut editor: Vec<(u32, u32, u32)> = parse(replacement.dxil)
                .psv0_binds()
                .unwrap()
                .iter()
                .filter(|bind| bind.space == 42)
                .map(|bind| (bind.resource_type, bind.lower_bound, bind.upper_bound))
                .collect();
            editor.sort_unstable();
            assert_eq!(
                editor,
                [(PSV_RESOURCE_CBV, 0, 0), (PSV_RESOURCE_UAV_RAW, 1, 1)],
                "{}",
                replacement.name
            );
        }
    }

    #[test]
    fn every_port_is_a_shader_model_6_0_pixel_shader_needing_no_features() {
        for replacement in REPLACEMENTS {
            let blob = parse(replacement.dxil);
            let program = blob.program().unwrap();
            assert_eq!(
                (program.kind, program.major, program.minor),
                (ShaderKind::Pixel, 6, 0),
                "{}",
                replacement.name
            );
            assert_eq!(blob.psv0().unwrap().shader_stage, Some(0));
            // The originals need none, and a port may need no feature its original lacks.
            assert_eq!(blob.sfi0(), Ok(0), "{}", replacement.name);
            assert_eq!(blob.total_size(), replacement.dxil.len());
        }
    }

    #[test]
    fn container_part_finds_the_root_signature_among_others() {
        let blob = container(&[(b"DXIL", &[1, 2, 3]), (b"RTS0", &[9, 8, 7, 6])]);
        assert_eq!(
            container_part(&blob, ROOT_SIGNATURE_PART),
            Some(&[9_u8, 8, 7, 6][..])
        );
        assert_eq!(container_part(&blob, PSV0_PART), None);
        let codes: Vec<[u8; 4]> = parse(&blob).parts().iter().map(|p| p.code).collect();
        assert_eq!(codes, [*b"DXIL", *b"RTS0"]);
    }

    #[test]
    fn container_part_refuses_what_is_not_a_container() {
        assert_eq!(container_part(b"nope", ROOT_SIGNATURE_PART), None);
        let mut blob = container(&[(b"RTS0", &[1, 2, 3, 4])]);
        blob.truncate(blob.len() - 2);
        assert_eq!(container_part(&blob, ROOT_SIGNATURE_PART), None);
    }

    #[test]
    fn bytes_past_the_declared_size_are_not_the_container() {
        let mut blob = container(&[(b"SFI0", &[0; 8])]);
        let declared = blob.len();
        blob.extend_from_slice(&[0xee; 5]);
        let parsed = parse(&blob);
        assert_eq!(parsed.total_size(), declared);
        assert_eq!(parsed.sfi0(), Ok(0));
    }

    #[test]
    fn a_truncated_container_is_refused_at_every_length() {
        for replacement in REPLACEMENTS {
            for len in 0..replacement.dxil.len() {
                assert!(
                    Container::parse(&replacement.dxil[..len]).is_err(),
                    "{} cut to {len}",
                    replacement.name
                );
            }
        }
    }

    #[test]
    fn a_container_with_no_parts_is_refused() {
        assert_eq!(
            Container::parse(&container(&[])).err(),
            Some(DxbcError::NoParts)
        );
    }

    #[test]
    fn other_magic_is_refused() {
        let mut blob = container(&[(b"SFI0", &[0; 8])]);
        blob[0] = b'X';
        assert_eq!(
            Container::parse(&blob).err(),
            Some(DxbcError::NotAContainer)
        );
    }

    #[test]
    fn part_offsets_outside_the_container_are_refused() {
        let good = container(&[(b"SFI0", &[0; 8]), (b"ILDN", &[0; 8])]);
        let second_offset = HEADER_BYTES + 4;
        let cases: [(&str, usize, u32); 4] = [
            ("past the end", second_offset, good.len() as u32),
            ("inside the header", second_offset, 8),
            (
                "into the part table",
                HEADER_BYTES,
                (HEADER_BYTES + 4) as u32,
            ),
            ("at u32::MAX", second_offset, u32::MAX),
        ];
        for (why, at, value) in cases {
            let mut bad = good.clone();
            set_u32(&mut bad, at, value);
            assert!(
                matches!(Container::parse(&bad), Err(DxbcError::PartOutOfRange(_))),
                "{why}"
            );
        }
        // The second part's size, one byte more than the container has left.
        let second = parse(&good).parts()[1].offset;
        let mut oversized = good.clone();
        set_u32(&mut oversized, second + 4, 9);
        assert_eq!(
            Container::parse(&oversized).err(),
            Some(DxbcError::PartOutOfRange(1))
        );
        let mut huge_count = good;
        set_u32(&mut huge_count, PART_COUNT_AT, u32::MAX);
        assert_eq!(
            Container::parse(&huge_count).err(),
            Some(DxbcError::Truncated("the part table"))
        );
    }

    #[test]
    fn signature_names_are_resolved_and_bounds_checked() {
        let part = signature_part(&[("SV_Position", 0, 1, 0xf), ("TEXCOORD", 1, 0, 0x3)]);
        let blob = container(&[(b"ISG1", &part)]);
        let elements = parse(&blob).signature(INPUT_SIGNATURE_PART).unwrap();
        assert_eq!(elements[0].name, "SV_Position");
        assert_eq!(elements[1].name, "TEXCOORD");
        assert_eq!(elements[1].register, 1);
        assert_eq!(
            parse(&blob).signature(OUTPUT_SIGNATURE_PART),
            Err(DxbcError::MissingPart(*OUTPUT_SIGNATURE_PART))
        );

        let mut name_outside = part.clone();
        set_u32(&mut name_outside, 8 + 4, part.len() as u32 + 1);
        let blob = container(&[(b"ISG1", &name_outside)]);
        assert!(parse(&blob).signature(INPUT_SIGNATURE_PART).is_err());

        let mut unterminated = part.clone();
        unterminated.pop();
        let blob = container(&[(b"ISG1", &unterminated)]);
        assert_eq!(
            parse(&blob).signature(INPUT_SIGNATURE_PART),
            Err(DxbcError::Malformed(
                "a signature element name has no terminator"
            ))
        );

        let mut too_many = part;
        set_u32(&mut too_many, 0, u32::MAX);
        let blob = container(&[(b"ISG1", &too_many)]);
        assert!(parse(&blob).signature(INPUT_SIGNATURE_PART).is_err());
    }

    #[test]
    fn psv0_version_0_has_binds_and_no_interpolation() {
        let mut part = Vec::new();
        part.extend_from_slice(&(PSV_RUNTIME_INFO0_BYTES as u32).to_le_bytes());
        part.extend_from_slice(&[0; PSV_RUNTIME_INFO0_BYTES]);
        part.extend_from_slice(&1_u32.to_le_bytes());
        part.extend_from_slice(&(PSV_BIND0_BYTES as u32).to_le_bytes());
        for word in [PSV_RESOURCE_UAV_RAW, 42, 1, 1] {
            part.extend_from_slice(&word.to_le_bytes());
        }
        let blob = container(&[(b"PSV0", &part)]);
        let psv0 = parse(&blob).psv0().unwrap();
        assert_eq!(
            psv0.binds,
            [PsvBind {
                resource_type: PSV_RESOURCE_UAV_RAW,
                space: 42,
                lower_bound: 1,
                upper_bound: 1,
                kind: None,
                flags: None,
            }]
        );
        assert_eq!(psv0.shader_stage, None);
        assert!(parse(&blob).psv0_interp().is_err());

        let mut short_record = part.clone();
        set_u32(&mut short_record, 4 + PSV_RUNTIME_INFO0_BYTES + 4, 8);
        let blob = container(&[(b"PSV0", &short_record)]);
        assert!(parse(&blob).psv0().is_err());

        let mut cut = part;
        cut.truncate(cut.len() - 1);
        let blob = container(&[(b"PSV0", &cut)]);
        assert!(parse(&blob).psv0().is_err());
    }

    #[test]
    fn debug_names_need_their_terminator() {
        let name = b"GXLightDeferredAO.ppo.pdb";
        let mut part = vec![0, 0];
        part.extend_from_slice(&(name.len() as u16).to_le_bytes());
        part.extend_from_slice(name);
        part.push(0);
        while part.len() % 4 != 0 {
            part.push(0);
        }
        let blob = container(&[(b"ILDN", &part)]);
        assert_eq!(parse(&blob).ildn(), Ok("GXLightDeferredAO.ppo.pdb"));

        let mut unterminated = part.clone();
        unterminated[4 + name.len()] = b'x';
        let blob = container(&[(b"ILDN", &unterminated)]);
        assert!(parse(&blob).ildn().is_err());

        let mut long = part;
        long[2..4].copy_from_slice(&u16::MAX.to_le_bytes());
        let blob = container(&[(b"ILDN", &long)]);
        assert!(parse(&blob).ildn().is_err());
    }

    #[test]
    fn a_program_header_must_fit_and_say_dxil() {
        let mut part = vec![0_u8; PROGRAM_HEADER_BYTES + 4];
        set_u32(&mut part, 0, 0x0001_0065);
        let words = (part.len() / 4) as u32;
        set_u32(&mut part, 4, words);
        part[8..12].copy_from_slice(b"DXIL");
        set_u32(&mut part, 12, 0x105);
        set_u32(&mut part, 16, 16);
        set_u32(&mut part, 20, 4);
        let blob = container(&[(b"DXIL", &part)]);
        let program = parse(&blob).program().unwrap();
        assert_eq!(
            (program.kind, program.major, program.minor),
            (ShaderKind::Vertex, 6, 5)
        );

        let mut no_magic = part.clone();
        no_magic[8] = b'X';
        let blob = container(&[(b"DXIL", &no_magic)]);
        assert!(parse(&blob).program().is_err());

        let mut bitcode_outside = part;
        set_u32(&mut bitcode_outside, 20, 5);
        let blob = container(&[(b"DXIL", &bitcode_outside)]);
        assert!(parse(&blob).program().is_err());
    }

    #[test]
    fn sfi0_must_be_eight_bytes() {
        let blob = container(&[(b"SFI0", &[1, 0, 0, 0, 0, 0, 0, 0x80])]);
        assert_eq!(parse(&blob).sfi0(), Ok(0x8000_0000_0000_0001));
        let blob = container(&[(b"SFI0", &[1, 0, 0, 0])]);
        assert!(parse(&blob).sfi0().is_err());
    }

    /// Every accessor on a container, for the corruption test: the answers do not matter, only
    /// that each one returns.
    fn read_everything(blob: &[u8]) {
        let Ok(parsed) = Container::parse(blob) else {
            return;
        };
        let _ = parsed.parts();
        let _ = parsed.signature(INPUT_SIGNATURE_PART);
        let _ = parsed.signature(OUTPUT_SIGNATURE_PART);
        let _ = parsed.psv0();
        let _ = parsed.sfi0();
        let _ = parsed.program();
        let _ = parsed.ildn();
        let _ = parsed.digest();
    }

    #[test]
    fn corrupted_headers_and_parts_are_errors_not_panics() {
        for replacement in REPLACEMENTS {
            let blob = replacement.dxil;
            let program_at = parse(blob)
                .parts()
                .iter()
                .find(|part| &part.code == PROGRAM_PART)
                .map(|part| part.offset)
                .unwrap();
            // The header, the part table, every small part, and the program header.
            let offsets = (0..1024.min(blob.len())).chain(program_at..program_at + 32);
            for at in offsets {
                for value in [0x00, 0x01, 0x7f, 0xff] {
                    let mut corrupt = blob.to_vec();
                    corrupt[at] = value;
                    read_everything(&corrupt);
                }
            }
        }
    }

    /// The vkd3d-proton shader dump, a test input only: `$ER_VKD3D_DUMP_ROOT` when set, else
    /// `.worktrees/er-shaders/target/vkd3d-dump/run1/spv` under the nearest checkout that has it,
    /// which finds the main tree's dump from inside a worktree too.
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
    fn each_port_links_exactly_like_its_game_original() {
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
                original.len(),
                replacement.original_len,
                "{}",
                path.display()
            );
            assert_eq!(
                fnv1_64(&original),
                replacement.original_fnv1,
                "{}",
                path.display()
            );
            let original = parse(&original);
            let port = parse(replacement.dxil);
            for code in [INPUT_SIGNATURE_PART, OUTPUT_SIGNATURE_PART] {
                let ours = port.signature(code).unwrap();
                let theirs = original.signature(code).unwrap();
                assert_eq!(ours.len(), theirs.len(), "{} {code:?}", replacement.name);
                for (ours, theirs) in ours.iter().zip(&theirs) {
                    assert!(
                        ours.links_like(theirs),
                        "{}: {ours:?} vs {theirs:?}",
                        replacement.name
                    );
                }
            }
            assert_eq!(
                port.psv0_interp().unwrap(),
                original.psv0_interp().unwrap(),
                "{}",
                replacement.name
            );
            // The game's own shaders are signed the same way, by an older validator.
            assert!(original.is_signed_correctly(), "{}", replacement.name);
            let debug_name = original.ildn().unwrap();
            assert!(
                debug_name.ends_with(&format!("\\{}.ppo.pdb", replacement.name)),
                "{debug_name}"
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
