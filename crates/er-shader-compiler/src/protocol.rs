//! The frames the game DLL and `er-shader-compiler.exe` exchange over the helper's stdin and
//! stdout. Nothing here touches Windows, so all of it is tested on the host.
//!
//! # Framing
//!
//! Every frame is a little-endian `u32` payload length followed by the payload, which is at most
//! [`MAX_FRAME_BYTES`]. A reader refuses a longer length before it reads a byte of the payload, so
//! a corrupted length cannot make either side allocate gigabytes, and a stream that ends inside a
//! frame is an error rather than a short message. A stream that ends between frames is the clean
//! end: [`read_frame`] returns `None`, and the helper exits on it, which is how it dies with the
//! game even where Wine's job objects fall short.
//!
//! # Payloads
//!
//! A payload opens with a four-byte tag naming its message. Its fields follow in a fixed order:
//! integers little-endian, booleans as one byte that is 0 or 1, and strings and byte strings as a
//! `u32` length and the bytes, strings in UTF-8. Five messages exist:
//!
//! - [`Request`] (`REQ1`), from the DLL: compile a source, answer the handshake, or abort;
//! - [`Response`] (`RSP1`), from the helper: the answer to a compile;
//! - [`Handshake`] (`HSK1`), from the helper: the answer to a handshake request;
//! - [`PatchRequest`] (`PVQ1`), from the DLL: splice a vertex carrier into game vertex shaders
//!   (design `docs/plans/er-shader-editor-vertex.md`, section 7.3);
//! - [`PatchResponse`] (`PVA1`), from the helper: a patched container, or why not, per shader.
//!
//! The helper reads both requests from one stream; [`Incoming::decode`] picks one by its tag.
//!
//! Decoding is strict. An unknown tag or op, a string that is not UTF-8, a length that runs past
//! the payload and bytes left over after the last field are all an `Err`, because a payload that
//! decodes loosely is how two builds that disagree about the format go on talking.

use core::fmt;
use std::io::{self, Read, Write};

/// The largest payload either side sends or accepts: 4 MiB. A port's source is tens of kilobytes
/// and a compiled pixel shader at most 1 MiB (design 7.6), so a frame this size is already wrong.
pub const MAX_FRAME_BYTES: usize = 4 << 20;

/// The version of this format. The helper reports it in its [`Handshake`]; the DLL refuses a
/// helper that reports another ([`crate::check_handshake`]). 2 added [`Handshake::dxcompiler`], 3
/// the vertex patch messages.
pub const PROTOCOL_VERSION: u32 = 3;

/// The bytes of a frame's length prefix.
const LENGTH_BYTES: usize = 4;

/// What a request asks the helper to do.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub enum Op {
    /// Compile `source` and answer with a [`Response`].
    Compile = 1,
    /// Answer with a [`Handshake`]; the other fields are ignored.
    Handshake = 2,
    /// Exit at once with [`crate::SELFTEST_EXIT`], without answering. It exists only so a proof
    /// run can watch the DLL respawn a helper that died.
    SelftestCrash = 3,
}

impl Op {
    fn from_u32(value: u32) -> Result<Self, ProtocolError> {
        match value {
            1 => Ok(Self::Compile),
            2 => Ok(Self::Handshake),
            3 => Ok(Self::SelftestCrash),
            other => Err(ProtocolError::UnknownOp(other)),
        }
    }
}

/// A request from the DLL.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Request {
    /// Chosen by the DLL; the answer carries it back.
    pub id: u64,
    pub op: Op,
    /// The name dxc gives the main file in its diagnostics. The source arrives with every
    /// `#include` already expanded, so no file is ever opened by this name.
    pub file_name: String,
    /// The HLSL, UTF-8.
    pub source: String,
    /// The entry point, normally [`crate::ENTRY_POINT`].
    pub entry: String,
    /// The target profile, normally [`crate::PROFILE`].
    pub profile: String,
    /// `-D` defines as name and value; an empty value defines the name alone.
    pub defines: Vec<(String, String)>,
}

impl Request {
    /// A request to compile `source` as a shader model 6.0 pixel shader from `ps_main`.
    #[must_use]
    pub fn compile(
        id: u64,
        file_name: impl Into<String>,
        source: impl Into<String>,
        defines: Vec<(String, String)>,
    ) -> Self {
        Self {
            id,
            op: Op::Compile,
            file_name: file_name.into(),
            source: source.into(),
            entry: crate::ENTRY_POINT.to_owned(),
            profile: crate::PROFILE.to_owned(),
            defines,
        }
    }

    /// A request that asks only for the helper's [`Handshake`].
    #[must_use]
    pub fn handshake(id: u64) -> Self {
        Self::bare(id, Op::Handshake)
    }

    /// A request that makes the helper exit unanswered.
    #[must_use]
    pub fn selftest_crash(id: u64) -> Self {
        Self::bare(id, Op::SelftestCrash)
    }

    fn bare(id: u64, op: Op) -> Self {
        Self {
            id,
            op,
            file_name: String::new(),
            source: String::new(),
            entry: String::new(),
            profile: String::new(),
            defines: Vec::new(),
        }
    }
}

/// The helper's answer to a compile.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Response {
    /// The request's id.
    pub id: u64,
    /// The compile succeeded and `blob` holds the container.
    pub ok: bool,
    /// The DXBC container dxc wrote, or nothing when `ok` is false.
    pub blob: Vec<u8>,
    /// dxc's errors and warnings as it printed them, or why there was no compile at all.
    pub diagnostics: String,
    /// The compiler that answered, as [`crate::DxcVersion`] prints it; empty with no compiler.
    pub dxc_version: String,
}

impl Response {
    /// An answer with no container.
    #[must_use]
    pub fn failed(id: u64, diagnostics: impl Into<String>, dxc_version: impl Into<String>) -> Self {
        Self {
            id,
            ok: false,
            blob: Vec::new(),
            diagnostics: diagnostics.into(),
            dxc_version: dxc_version.into(),
        }
    }
}

/// The helper's answer to a handshake request.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Handshake {
    /// The helper's [`PROTOCOL_VERSION`].
    pub protocol_version: u32,
    /// The compiler the helper loaded, as [`crate::DxcVersion`] prints it, or empty when it loaded
    /// none; the helper then answers every compile with why.
    pub dxc_version: String,
    /// The helper's [`crate::build_id`].
    pub build_id: String,
    /// The dxcompiler.dll it loaded, by full path; or, when it loaded none, the path it tried and
    /// why that failed.
    pub dxcompiler: String,
}

/// Why a frame or a payload was refused.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProtocolError {
    /// The stream or the payload ended inside this field.
    Truncated(&'static str),
    /// A frame or a field longer than the format allows.
    Oversized { what: &'static str, len: usize },
    /// The payload's tag is not the message the reader expected.
    WrongMessage { expected: [u8; 4], found: [u8; 4] },
    /// A request op this build does not know.
    UnknownOp(u32),
    /// This string field is not UTF-8.
    NotUtf8(&'static str),
    /// This boolean field is neither 0 nor 1.
    NotABool(&'static str, u8),
    /// This field holds a value its type does not have.
    OutOfRange(&'static str, u32),
    /// This many bytes follow the last field.
    TrailingBytes(usize),
    /// The pipe itself failed.
    Io {
        kind: io::ErrorKind,
        message: String,
    },
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Truncated(what) => write!(f, "the stream ended inside {what}"),
            Self::Oversized { what, len } => write!(
                f,
                "{what} is {len} bytes, over the {MAX_FRAME_BYTES}-byte frame limit"
            ),
            Self::WrongMessage { expected, found } => write!(
                f,
                "expected a {} message and read {}",
                expected.escape_ascii(),
                found.escape_ascii()
            ),
            Self::UnknownOp(op) => write!(f, "unknown request op {op}"),
            Self::NotUtf8(what) => write!(f, "{what} is not UTF-8"),
            Self::NotABool(what, byte) => write!(f, "{what} is {byte}, neither 0 nor 1"),
            Self::OutOfRange(what, value) => write!(f, "{what} is {value}, which it cannot be"),
            Self::TrailingBytes(count) => write!(f, "{count} bytes follow the last field"),
            Self::Io { kind, message } => write!(f, "pipe error ({kind:?}): {message}"),
        }
    }
}

impl std::error::Error for ProtocolError {}

impl From<io::Error> for ProtocolError {
    fn from(error: io::Error) -> Self {
        Self::Io {
            kind: error.kind(),
            message: error.to_string(),
        }
    }
}

/// Write one frame: the payload's length, then the payload.
///
/// # Errors
///
/// [`ProtocolError::Oversized`] for a payload over [`MAX_FRAME_BYTES`], before anything is
/// written; otherwise whatever the writer returns.
pub fn write_frame(out: &mut impl Write, payload: &[u8]) -> Result<(), ProtocolError> {
    let len = frame_len(payload.len(), "the frame")?;
    out.write_all(&len.to_le_bytes())?;
    out.write_all(payload)?;
    Ok(())
}

/// Read one frame's payload, or `None` when the stream ended cleanly between frames.
///
/// # Errors
///
/// [`ProtocolError::Truncated`] when the stream ends inside a frame, and
/// [`ProtocolError::Oversized`] for a length over [`MAX_FRAME_BYTES`], refused before the payload
/// is read.
pub fn read_frame(input: &mut impl Read) -> Result<Option<Vec<u8>>, ProtocolError> {
    let mut prefix = [0u8; LENGTH_BYTES];
    let mut filled = 0;
    while filled < LENGTH_BYTES {
        match input.read(&mut prefix[filled..]) {
            Ok(0) if filled == 0 => return Ok(None),
            Ok(0) => return Err(ProtocolError::Truncated("the frame length")),
            Ok(read) => filled += read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error.into()),
        }
    }
    let len = u32::from_le_bytes(prefix) as usize;
    if len > MAX_FRAME_BYTES {
        return Err(ProtocolError::Oversized {
            what: "the frame",
            len,
        });
    }
    let mut payload = vec![0u8; len];
    input.read_exact(&mut payload).map_err(|error| {
        if error.kind() == io::ErrorKind::UnexpectedEof {
            ProtocolError::Truncated("the frame payload")
        } else {
            error.into()
        }
    })?;
    Ok(Some(payload))
}

/// One of the three messages, with its tag and its fields in wire order.
pub trait Message: Sized {
    /// The four bytes a payload of this message opens with.
    const TAG: [u8; 4];

    /// Append the fields after the tag.
    fn encode_fields(&self, out: &mut Encoder) -> Result<(), ProtocolError>;

    /// Read the fields after the tag.
    fn decode_fields(input: &mut Decoder<'_>) -> Result<Self, ProtocolError>;

    /// The whole payload: tag and fields.
    ///
    /// # Errors
    ///
    /// [`ProtocolError::Oversized`] when the payload would not fit a frame.
    fn encode(&self) -> Result<Vec<u8>, ProtocolError> {
        let mut out = Encoder(Self::TAG.to_vec());
        self.encode_fields(&mut out)?;
        frame_len(out.0.len(), "the payload")?;
        Ok(out.0)
    }

    /// A payload as this message, every byte of it accounted for.
    ///
    /// # Errors
    ///
    /// Any [`ProtocolError`] that names what was wrong with it.
    fn decode(payload: &[u8]) -> Result<Self, ProtocolError> {
        let mut input = Decoder { bytes: payload };
        let found = input.array("the message tag")?;
        if found != Self::TAG {
            return Err(ProtocolError::WrongMessage {
                expected: Self::TAG,
                found,
            });
        }
        let message = Self::decode_fields(&mut input)?;
        if !input.bytes.is_empty() {
            return Err(ProtocolError::TrailingBytes(input.bytes.len()));
        }
        Ok(message)
    }
}

/// Encode `message`, frame it, and flush, so the other side sees it now.
///
/// # Errors
///
/// As [`Message::encode`] and [`write_frame`].
pub fn send<M: Message>(out: &mut impl Write, message: &M) -> Result<(), ProtocolError> {
    write_frame(out, &message.encode()?)?;
    out.flush()?;
    Ok(())
}

/// Read one frame and decode it as `M`, or `None` when the stream ended cleanly between frames.
///
/// # Errors
///
/// As [`read_frame`] and [`Message::decode`].
pub fn receive<M: Message>(input: &mut impl Read) -> Result<Option<M>, ProtocolError> {
    read_frame(input)?
        .map(|payload| M::decode(&payload))
        .transpose()
}

/// `len` as a frame length, or [`ProtocolError::Oversized`] naming `what`.
fn frame_len(len: usize, what: &'static str) -> Result<u32, ProtocolError> {
    if len > MAX_FRAME_BYTES {
        return Err(ProtocolError::Oversized { what, len });
    }
    u32::try_from(len).map_err(|_| ProtocolError::Oversized { what, len })
}

/// The fields of a payload being written.
pub struct Encoder(Vec<u8>);

impl Encoder {
    fn u32(&mut self, value: u32) {
        self.0.extend_from_slice(&value.to_le_bytes());
    }

    fn u64(&mut self, value: u64) {
        self.0.extend_from_slice(&value.to_le_bytes());
    }

    fn bool(&mut self, value: bool) {
        self.0.push(u8::from(value));
    }

    fn byte(&mut self, value: u8) {
        self.0.push(value);
    }

    fn bytes(&mut self, value: &[u8], what: &'static str) -> Result<(), ProtocolError> {
        self.u32(frame_len(value.len(), what)?);
        self.0.extend_from_slice(value);
        Ok(())
    }

    fn str(&mut self, value: &str, what: &'static str) -> Result<(), ProtocolError> {
        self.bytes(value.as_bytes(), what)
    }
}

/// The unread rest of a payload.
pub struct Decoder<'a> {
    bytes: &'a [u8],
}

impl<'a> Decoder<'a> {
    fn take(&mut self, len: usize, what: &'static str) -> Result<&'a [u8], ProtocolError> {
        if len > self.bytes.len() {
            return Err(ProtocolError::Truncated(what));
        }
        let (taken, rest) = self.bytes.split_at(len);
        self.bytes = rest;
        Ok(taken)
    }

    fn array<const N: usize>(&mut self, what: &'static str) -> Result<[u8; N], ProtocolError> {
        let mut array = [0u8; N];
        array.copy_from_slice(self.take(N, what)?);
        Ok(array)
    }

    fn u32(&mut self, what: &'static str) -> Result<u32, ProtocolError> {
        self.array(what).map(u32::from_le_bytes)
    }

    fn u64(&mut self, what: &'static str) -> Result<u64, ProtocolError> {
        self.array(what).map(u64::from_le_bytes)
    }

    fn bool(&mut self, what: &'static str) -> Result<bool, ProtocolError> {
        match self.array::<1>(what)? {
            [0] => Ok(false),
            [1] => Ok(true),
            [other] => Err(ProtocolError::NotABool(what, other)),
        }
    }

    fn bytes(&mut self, what: &'static str) -> Result<&'a [u8], ProtocolError> {
        let len = self.u32(what)? as usize;
        self.take(len, what)
    }

    fn string(&mut self, what: &'static str) -> Result<String, ProtocolError> {
        let bytes = self.bytes(what)?;
        core::str::from_utf8(bytes)
            .map(str::to_owned)
            .map_err(|_| ProtocolError::NotUtf8(what))
    }
}

impl Message for Request {
    const TAG: [u8; 4] = *b"REQ1";

    fn encode_fields(&self, out: &mut Encoder) -> Result<(), ProtocolError> {
        out.u64(self.id);
        out.u32(self.op as u32);
        out.str(&self.file_name, "the file name")?;
        out.str(&self.source, "the source")?;
        out.str(&self.entry, "the entry point")?;
        out.str(&self.profile, "the profile")?;
        out.u32(frame_len(self.defines.len(), "the define list")?);
        for (name, value) in &self.defines {
            out.str(name, "a define name")?;
            out.str(value, "a define value")?;
        }
        Ok(())
    }

    fn decode_fields(input: &mut Decoder<'_>) -> Result<Self, ProtocolError> {
        let id = input.u64("the request id")?;
        let op = Op::from_u32(input.u32("the request op")?)?;
        let file_name = input.string("the file name")?;
        let source = input.string("the source")?;
        let entry = input.string("the entry point")?;
        let profile = input.string("the profile")?;
        let count = input.u32("the define count")?;
        // Grown one pair at a time, so a corrupt count costs a `Truncated`, not an allocation.
        let mut defines = Vec::new();
        for _ in 0..count {
            let name = input.string("a define name")?;
            let value = input.string("a define value")?;
            defines.push((name, value));
        }
        Ok(Self {
            id,
            op,
            file_name,
            source,
            entry,
            profile,
            defines,
        })
    }
}

impl Message for Response {
    const TAG: [u8; 4] = *b"RSP1";

    fn encode_fields(&self, out: &mut Encoder) -> Result<(), ProtocolError> {
        out.u64(self.id);
        out.bool(self.ok);
        out.bytes(&self.blob, "the compiled object")?;
        out.str(&self.diagnostics, "the diagnostics")?;
        out.str(&self.dxc_version, "the compiler version")
    }

    fn decode_fields(input: &mut Decoder<'_>) -> Result<Self, ProtocolError> {
        Ok(Self {
            id: input.u64("the response id")?,
            ok: input.bool("the ok flag")?,
            blob: input.bytes("the compiled object")?.to_vec(),
            diagnostics: input.string("the diagnostics")?,
            dxc_version: input.string("the compiler version")?,
        })
    }
}

impl Message for Handshake {
    const TAG: [u8; 4] = *b"HSK1";

    fn encode_fields(&self, out: &mut Encoder) -> Result<(), ProtocolError> {
        out.u32(self.protocol_version);
        out.str(&self.dxc_version, "the compiler version")?;
        out.str(&self.build_id, "the build id")?;
        out.str(&self.dxcompiler, "the compiler's path")
    }

    fn decode_fields(input: &mut Decoder<'_>) -> Result<Self, ProtocolError> {
        Ok(Self {
            protocol_version: input.u32("the protocol version")?,
            dxc_version: input.string("the compiler version")?,
            build_id: input.string("the build id")?,
            dxcompiler: input.string("the compiler's path")?,
        })
    }
}

/// A request from the DLL to splice a vertex carrier into game vertex shaders
/// ([`crate::patch::patch_vertex`]).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PatchRequest {
    /// Chosen by the DLL; the answer carries it back.
    pub id: u64,
    /// The carrier: the vertex layers compiled as a `vs_6_0` container.
    pub carrier: Vec<u8>,
    /// Give a velocity pass's previous position the layers at the previous time.
    pub previous: bool,
    /// The game's vertex shader containers, each patched on its own.
    pub items: Vec<Vec<u8>>,
}

/// How the second, previous-time instance went for one shader ([`crate::vsplice::PreviousOutcome`]).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum PreviousKind {
    /// Not a velocity pass, or the request did not ask.
    None = 0,
    /// The previous position is moved at the previous time.
    Applied = 1,
    /// A velocity pass whose previous position could not be given the previous time: its motion
    /// vectors carry the camera's motion only.
    Skipped = 2,
}

impl PreviousKind {
    fn from_u8(value: u8) -> Result<Self, ProtocolError> {
        match value {
            0 => Ok(Self::None),
            1 => Ok(Self::Applied),
            2 => Ok(Self::Skipped),
            other => Err(ProtocolError::OutOfRange(
                "the previous outcome",
                u32::from(other),
            )),
        }
    }
}

/// What became of one shader of a [`PatchRequest`].
#[derive(Clone, Debug, PartialEq)]
pub struct PatchItem {
    /// The container was patched, assembled, validated and passed residual identity.
    pub ok: bool,
    /// The patched container, signed by the validator; empty when `ok` is false.
    pub blob: Vec<u8>,
    /// Empty when `ok`; else one word: a [`crate::vsplice::Refusal::class`], or `disassemble`,
    /// `assemble`, `validate`, `residual`, `no_patcher`.
    pub class: String,
    /// Why, in a sentence, when `ok` is false; the splice's previous-time note otherwise.
    pub detail: String,
    /// The DXIL element id of `POSITION0`.
    pub position_element: u32,
    /// Instructions added, and cloned for the previous position.
    pub inserted: u32,
    pub cloned: u32,
    pub blocks_added: u32,
    pub previous: PreviousKind,
    /// The output element the previous position goes to, when [`PreviousKind::Applied`].
    pub previous_element: u32,
    /// FNV-1a 64 of instance 0's code with the game's names replaced: equal in every shader the
    /// same carrier went into.
    pub instance0_hash: u64,
    /// Milliseconds of each step: disassemble, splice, assemble, validate, residual.
    pub ms: [f32; 5],
}

impl PatchItem {
    /// A shader that was not patched, for `class` and `detail`.
    #[must_use]
    pub fn refused(class: impl Into<String>, detail: impl Into<String>) -> Self {
        Self {
            ok: false,
            blob: Vec::new(),
            class: class.into(),
            detail: detail.into(),
            position_element: 0,
            inserted: 0,
            cloned: 0,
            blocks_added: 0,
            previous: PreviousKind::None,
            previous_element: 0,
            instance0_hash: 0,
            ms: [0.0; 5],
        }
    }
}

/// The helper's answer to a [`PatchRequest`].
#[derive(Clone, Debug, PartialEq)]
pub struct PatchResponse {
    pub id: u64,
    /// The carrier passed `vsplice::check_carrier`. When it did not, every item is refused.
    pub carrier_ok: bool,
    /// Why the carrier was refused; empty when it was not.
    pub carrier_detail: String,
    /// The compiler that answered, as [`crate::DxcVersion`] prints it; empty with none.
    pub dxc_version: String,
    /// One per item of the request, in its order.
    pub items: Vec<PatchItem>,
}

impl PatchResponse {
    /// An answer refusing every item of `request` for one reason.
    #[must_use]
    pub fn refused_all(
        request: &PatchRequest,
        carrier_ok: bool,
        class: &str,
        detail: &str,
        dxc_version: impl Into<String>,
    ) -> Self {
        Self {
            id: request.id,
            carrier_ok,
            carrier_detail: if carrier_ok {
                String::new()
            } else {
                detail.to_owned()
            },
            dxc_version: dxc_version.into(),
            items: request
                .items
                .iter()
                .map(|_| PatchItem::refused(class, detail))
                .collect(),
        }
    }
}

impl Message for PatchRequest {
    const TAG: [u8; 4] = *b"PVQ1";

    fn encode_fields(&self, out: &mut Encoder) -> Result<(), ProtocolError> {
        out.u64(self.id);
        out.bytes(&self.carrier, "the carrier")?;
        out.bool(self.previous);
        out.u32(frame_len(self.items.len(), "the item list")?);
        for item in &self.items {
            out.bytes(item, "a vertex shader")?;
        }
        Ok(())
    }

    fn decode_fields(input: &mut Decoder<'_>) -> Result<Self, ProtocolError> {
        let id = input.u64("the request id")?;
        let carrier = input.bytes("the carrier")?.to_vec();
        let previous = input.bool("the previous flag")?;
        let count = input.u32("the item count")?;
        // Grown one at a time, so a corrupt count costs a `Truncated`, not an allocation.
        let mut items = Vec::new();
        for _ in 0..count {
            items.push(input.bytes("a vertex shader")?.to_vec());
        }
        Ok(Self {
            id,
            carrier,
            previous,
            items,
        })
    }
}

impl Message for PatchResponse {
    const TAG: [u8; 4] = *b"PVA1";

    fn encode_fields(&self, out: &mut Encoder) -> Result<(), ProtocolError> {
        out.u64(self.id);
        out.bool(self.carrier_ok);
        out.str(&self.carrier_detail, "the carrier's refusal")?;
        out.str(&self.dxc_version, "the compiler version")?;
        out.u32(frame_len(self.items.len(), "the item list")?);
        for item in &self.items {
            out.bool(item.ok);
            out.bytes(&item.blob, "a patched shader")?;
            out.str(&item.class, "a refusal class")?;
            out.str(&item.detail, "a refusal")?;
            out.u32(item.position_element);
            out.u32(item.inserted);
            out.u32(item.cloned);
            out.u32(item.blocks_added);
            out.byte(item.previous as u8);
            out.u32(item.previous_element);
            out.u64(item.instance0_hash);
            for ms in item.ms {
                out.u32(ms.to_bits());
            }
        }
        Ok(())
    }

    fn decode_fields(input: &mut Decoder<'_>) -> Result<Self, ProtocolError> {
        let id = input.u64("the response id")?;
        let carrier_ok = input.bool("the carrier flag")?;
        let carrier_detail = input.string("the carrier's refusal")?;
        let dxc_version = input.string("the compiler version")?;
        let count = input.u32("the item count")?;
        let mut items = Vec::new();
        for _ in 0..count {
            let ok = input.bool("an item's ok flag")?;
            let blob = input.bytes("a patched shader")?.to_vec();
            let class = input.string("a refusal class")?;
            let detail = input.string("a refusal")?;
            let position_element = input.u32("the position element")?;
            let inserted = input.u32("the instructions inserted")?;
            let cloned = input.u32("the instructions cloned")?;
            let blocks_added = input.u32("the blocks added")?;
            let previous = PreviousKind::from_u8(input.array::<1>("the previous outcome")?[0])?;
            let previous_element = input.u32("the previous element")?;
            let instance0_hash = input.u64("the instance hash")?;
            let mut ms = [0.0_f32; 5];
            for slot in &mut ms {
                *slot = f32::from_bits(input.u32("a step's milliseconds")?);
            }
            items.push(PatchItem {
                ok,
                blob,
                class,
                detail,
                position_element,
                inserted,
                cloned,
                blocks_added,
                previous,
                previous_element,
                instance0_hash,
                ms,
            });
        }
        Ok(Self {
            id,
            carrier_ok,
            carrier_detail,
            dxc_version,
            items,
        })
    }
}

/// A request the helper read: either message the DLL sends.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Incoming {
    Request(Request),
    Patch(PatchRequest),
}

impl Incoming {
    /// A payload as whichever request its tag names.
    ///
    /// # Errors
    ///
    /// A tag that is neither request's, or the decoding error of the one it names.
    pub fn decode(payload: &[u8]) -> Result<Self, ProtocolError> {
        match payload.get(..4) {
            Some(tag) if tag == PatchRequest::TAG => PatchRequest::decode(payload).map(Self::Patch),
            _ => Request::decode(payload).map(Self::Request),
        }
    }

    /// The request's id.
    #[must_use]
    pub fn id(&self) -> u64 {
        match self {
            Self::Request(request) => request.id,
            Self::Patch(patch) => patch.id,
        }
    }
}

/// Read one frame and decode it as either request, or `None` when the stream ended cleanly
/// between frames.
///
/// # Errors
///
/// As [`read_frame`] and [`Incoming::decode`].
pub fn receive_incoming(input: &mut impl Read) -> Result<Option<Incoming>, ProtocolError> {
    read_frame(input)?
        .map(|payload| Incoming::decode(&payload))
        .transpose()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn compile_request() -> Request {
        Request::compile(
            7,
            "tonemap.hlsl",
            "float4 ps_main() : SV_Target0 { return 1; } // \u{e9}\u{1f600}",
            vec![
                ("ER_VARIANT_ID".to_owned(), "50331649".to_owned()),
                ("CEL_LUT".to_owned(), String::new()),
            ],
        )
    }

    fn response() -> Response {
        Response {
            id: 7,
            ok: true,
            blob: b"DXBC\0\x01\x02\xff".to_vec(),
            diagnostics: "layers/warm_grade.hlsl:2:68: warning: unused".to_owned(),
            dxc_version: "1.9 (1.9.2609.5)".to_owned(),
        }
    }

    fn handshake() -> Handshake {
        Handshake {
            protocol_version: PROTOCOL_VERSION,
            dxc_version: "1.9 (1.9.2609.5) commit 5000-01b62ad47".to_owned(),
            build_id: "er-shader-compiler 0.1.0 0123456789abcdef".to_owned(),
            dxcompiler: "Z:\\game\\er-shader-editor\\compiler\\dxcompiler.dll".to_owned(),
        }
    }

    /// Frame `payload` the way [`write_frame`] does, without its checks.
    fn framed(payload: &[u8]) -> Vec<u8> {
        let mut out = (payload.len() as u32).to_le_bytes().to_vec();
        out.extend_from_slice(payload);
        out
    }

    #[test]
    fn every_message_round_trips_through_a_frame() {
        let mut stream = Vec::new();
        send(&mut stream, &compile_request()).unwrap();
        send(&mut stream, &Request::handshake(8)).unwrap();
        send(&mut stream, &Request::selftest_crash(9)).unwrap();
        send(&mut stream, &response()).unwrap();
        send(&mut stream, &handshake()).unwrap();
        let mut input = stream.as_slice();
        assert_eq!(receive(&mut input), Ok(Some(compile_request())));
        assert_eq!(receive(&mut input), Ok(Some(Request::handshake(8))));
        assert_eq!(receive(&mut input), Ok(Some(Request::selftest_crash(9))));
        assert_eq!(receive(&mut input), Ok(Some(response())));
        assert_eq!(receive(&mut input), Ok(Some(handshake())));
        // The stream ended between frames.
        assert_eq!(receive::<Request>(&mut input), Ok(None));
    }

    #[test]
    fn an_empty_stream_is_the_clean_end() {
        assert_eq!(read_frame(&mut &[][..]), Ok(None));
    }

    #[test]
    fn a_stream_cut_anywhere_inside_a_frame_is_refused() {
        let whole = framed(&compile_request().encode().unwrap());
        for cut in 1..whole.len() {
            let result = read_frame(&mut &whole[..cut]);
            let expected = if cut < LENGTH_BYTES {
                "the frame length"
            } else {
                "the frame payload"
            };
            assert_eq!(result, Err(ProtocolError::Truncated(expected)), "cut {cut}");
        }
    }

    #[test]
    fn a_payload_cut_anywhere_is_refused() {
        // A frame whose length is honest but whose payload stops early, field by field.
        let payload = compile_request().encode().unwrap();
        for cut in 0..payload.len() {
            let result = Request::decode(&payload[..cut]);
            assert!(
                matches!(result, Err(ProtocolError::Truncated(_))),
                "cut {cut}: {result:?}"
            );
        }
        let payload = response().encode().unwrap();
        for cut in 0..payload.len() {
            assert!(Response::decode(&payload[..cut]).is_err(), "cut {cut}");
        }
    }

    #[test]
    fn an_oversized_length_is_refused_before_the_payload_is_read() {
        // One byte over the limit, with no payload behind it: the length alone is enough.
        let len = (MAX_FRAME_BYTES as u32 + 1).to_le_bytes();
        assert_eq!(
            read_frame(&mut &len[..]),
            Err(ProtocolError::Oversized {
                what: "the frame",
                len: MAX_FRAME_BYTES + 1
            })
        );
        assert_eq!(
            read_frame(&mut &u32::MAX.to_le_bytes()[..]),
            Err(ProtocolError::Oversized {
                what: "the frame",
                len: u32::MAX as usize
            })
        );
    }

    #[test]
    fn a_frame_of_exactly_the_limit_is_accepted() {
        let payload = vec![0x5a; MAX_FRAME_BYTES];
        let mut stream = Vec::new();
        write_frame(&mut stream, &payload).unwrap();
        assert_eq!(read_frame(&mut stream.as_slice()), Ok(Some(payload)));
    }

    #[test]
    fn an_oversized_message_is_refused_before_anything_is_written() {
        let mut request = compile_request();
        request.source = "x".repeat(MAX_FRAME_BYTES);
        let mut stream = Vec::new();
        assert!(matches!(
            send(&mut stream, &request),
            Err(ProtocolError::Oversized {
                what: "the payload",
                ..
            })
        ));
        assert!(stream.is_empty());
        assert!(matches!(
            write_frame(&mut stream, &vec![0; MAX_FRAME_BYTES + 1]),
            Err(ProtocolError::Oversized {
                what: "the frame",
                ..
            })
        ));
        assert!(stream.is_empty());
    }

    #[test]
    fn an_unknown_op_is_refused() {
        let mut payload = Request::handshake(3).encode().unwrap();
        // The op follows the tag and the id.
        payload[12..16].copy_from_slice(&4u32.to_le_bytes());
        assert_eq!(Request::decode(&payload), Err(ProtocolError::UnknownOp(4)));
        payload[12..16].copy_from_slice(&0u32.to_le_bytes());
        assert_eq!(Request::decode(&payload), Err(ProtocolError::UnknownOp(0)));
        let mut stream = framed(&payload);
        stream.extend(framed(&Request::handshake(4).encode().unwrap()));
        assert_eq!(
            receive::<Request>(&mut stream.as_slice()),
            Err(ProtocolError::UnknownOp(0))
        );
    }

    #[test]
    fn a_message_read_as_another_is_refused() {
        let payload = response().encode().unwrap();
        assert_eq!(
            Request::decode(&payload),
            Err(ProtocolError::WrongMessage {
                expected: *b"REQ1",
                found: *b"RSP1"
            })
        );
        assert!(matches!(
            Handshake::decode(b"junk and more"),
            Err(ProtocolError::WrongMessage { .. })
        ));
    }

    #[test]
    fn bytes_after_the_last_field_are_refused() {
        let mut payload = handshake().encode().unwrap();
        payload.push(0);
        assert_eq!(
            Handshake::decode(&payload),
            Err(ProtocolError::TrailingBytes(1))
        );
    }

    #[test]
    fn a_string_that_is_not_utf8_is_refused() {
        let mut payload = Request::handshake(1).encode().unwrap();
        // The file name follows the tag, the id and the op: an empty string becomes 0xff.
        let at = 4 + 8 + 4;
        payload.splice(at..at + 4, [1, 0, 0, 0, 0xff]);
        assert_eq!(
            Request::decode(&payload),
            Err(ProtocolError::NotUtf8("the file name"))
        );
    }

    #[test]
    fn an_ok_flag_other_than_0_or_1_is_refused() {
        let mut payload = response().encode().unwrap();
        payload[12] = 2;
        assert_eq!(
            Response::decode(&payload),
            Err(ProtocolError::NotABool("the ok flag", 2))
        );
    }

    #[test]
    fn a_huge_define_count_costs_a_refusal_not_an_allocation() {
        let mut payload = Request::handshake(1).encode().unwrap();
        let count_at = payload.len() - 4;
        payload[count_at..].copy_from_slice(&u32::MAX.to_le_bytes());
        assert_eq!(
            Request::decode(&payload),
            Err(ProtocolError::Truncated("a define name"))
        );
    }

    fn patch_request() -> PatchRequest {
        PatchRequest {
            id: 11,
            carrier: b"DXBC carrier".to_vec(),
            previous: true,
            items: vec![
                b"DXBC game vs one".to_vec(),
                Vec::new(),
                b"DXBC two".to_vec(),
            ],
        }
    }

    fn patch_response() -> PatchResponse {
        let mut applied = PatchItem::refused("", "");
        applied.ok = true;
        applied.blob = b"DXBC patched".to_vec();
        applied.position_element = 2;
        applied.inserted = 31;
        applied.cloned = 14;
        applied.blocks_added = 2;
        applied.previous = PreviousKind::Applied;
        applied.previous_element = 5;
        applied.instance0_hash = 0x0123_4567_89ab_cdef;
        applied.ms = [0.5, 0.25, 1.0, 2.0, f32::INFINITY];
        PatchResponse {
            id: 11,
            carrier_ok: true,
            carrier_detail: String::new(),
            dxc_version: "1.9 (1.9.2609.5)".to_owned(),
            items: vec![
                applied,
                PatchItem::refused("no_position", "no POSITION0 input"),
                PatchItem::refused("residual", "line 9 differs"),
            ],
        }
    }

    #[test]
    fn the_patch_messages_round_trip_and_share_the_request_stream() {
        let mut stream = Vec::new();
        send(&mut stream, &Request::handshake(10)).unwrap();
        send(&mut stream, &patch_request()).unwrap();
        send(&mut stream, &patch_response()).unwrap();
        let mut input = stream.as_slice();
        assert_eq!(
            receive_incoming(&mut input),
            Ok(Some(Incoming::Request(Request::handshake(10))))
        );
        let read = receive_incoming(&mut input).unwrap().unwrap();
        assert_eq!(read.id(), 11);
        assert_eq!(read, Incoming::Patch(patch_request()));
        // A response is neither request.
        let response: PatchResponse = receive(&mut input).unwrap().unwrap();
        assert_eq!(response, patch_response());
        assert_eq!(receive_incoming(&mut input), Ok(None));
        let payload = patch_response().encode().unwrap();
        assert!(matches!(
            Incoming::decode(&payload),
            Err(ProtocolError::WrongMessage { .. })
        ));
    }

    #[test]
    fn a_patch_payload_cut_anywhere_is_refused() {
        for payload in [
            patch_request().encode().unwrap(),
            patch_response().encode().unwrap(),
        ] {
            for cut in 0..payload.len() {
                let request = PatchRequest::decode(&payload[..cut]);
                let response = PatchResponse::decode(&payload[..cut]);
                assert!(request.is_err() && response.is_err(), "cut {cut}");
            }
        }
    }

    #[test]
    fn an_unknown_previous_outcome_is_refused() {
        let mut payload = patch_response().encode().unwrap();
        // The first item's previous byte: after the tag, id, flag, two empty-or-short strings,
        // the count, the flag, the blob, the two strings and four u32s.
        let response = patch_response();
        let item = &response.items[0];
        let at = 4
            + 8
            + 1
            + 4
            + response.carrier_detail.len()
            + 4
            + response.dxc_version.len()
            + 4
            + 1
            + 4
            + item.blob.len()
            + 4
            + item.class.len()
            + 4
            + item.detail.len()
            + 16;
        assert_eq!(payload[at], PreviousKind::Applied as u8);
        payload[at] = 3;
        assert_eq!(
            PatchResponse::decode(&payload),
            Err(ProtocolError::OutOfRange("the previous outcome", 3))
        );
    }

    #[test]
    fn refusing_every_item_keeps_the_count_and_the_reason() {
        let request = patch_request();
        let refused = PatchResponse::refused_all(&request, false, "carrier_operation", "waves", "");
        assert_eq!(refused.id, request.id);
        assert!(!refused.carrier_ok);
        assert_eq!(refused.carrier_detail, "waves");
        assert_eq!(refused.items.len(), request.items.len());
        assert!(
            refused
                .items
                .iter()
                .all(|item| !item.ok && item.class == "carrier_operation" && item.blob.is_empty())
        );
        let accepted = PatchResponse::refused_all(&request, true, "too_large", "big", "1.9");
        assert!(accepted.carrier_ok && accepted.carrier_detail.is_empty());
        assert!(accepted.items.iter().all(|item| item.detail == "big"));
    }

    #[test]
    fn a_broken_pipe_is_an_io_error() {
        struct Broken;
        impl Write for Broken {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> {
                Err(io::ErrorKind::BrokenPipe.into())
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        assert!(matches!(
            send(&mut Broken, &handshake()),
            Err(ProtocolError::Io {
                kind: io::ErrorKind::BrokenPipe,
                ..
            })
        ));
    }
}
