//! A deep copy of a game pipeline's description (design 6.2), kept so a variant of it can be created
//! later with only its pixel shader changed.
//!
//! # Why a copy
//!
//! The description the game hands a pipeline creator is the game's memory, valid for that call
//! only: its shader bytecode, its input layout and the semantic strings in it may be freed or
//! reused the moment the call returns, and whether the game keeps shader bytecode resident is
//! unmeasured. A cel variant made during the call (P0.1) could borrow them, but one made later -- a
//! recompile from the editor (P0.2a), a mask variant (P3) -- cannot. So the stream is copied at
//! creation, and every pointer in the copy aims at memory the record owns:
//!
//! * shader bytecode points at owned bytes, shared between records by length and FNV-1
//!   ([`ShaderStore`]). That covers `VS`, `HS`, `DS` and `GS`, and the pixel shader too, whose
//!   bytes are the game's and are kept in memory only, never written anywhere;
//! * `INPUT_LAYOUT` element arrays are copied, and so is each `SemanticName` string;
//! * `STREAM_OUTPUT` declarations (with their semantic strings) and buffer strides are copied, and
//!   so are `VIEW_INSTANCING` locations;
//! * `CACHED_PSO` is zeroed: a cached blob belongs to the game's pipeline, and a variant with
//!   another pixel shader would be refused for carrying it;
//! * the root signature pointer is kept as it is. The record does not own a reference on it; the
//!   Windows half's `PortRecord` does.
//!
//! A subobject type is copied only if this module knows whether its payload carries pointers
//! ([`crate::replace::POINTER_FREE_SUBOBJECTS`] or one of the types above). Any other type refuses
//! the copy, so a pointer this module has never heard of cannot be left aimed at the game's memory.
//!
//! The payloads are read through `repr(C)` mirrors of the D3D12 structs, so the copy is pure and
//! tested on the host; the Windows half checks at compile time that each mirror has the size,
//! alignment and field offsets of the `windows` crate's definition. The mirrors spell their padding
//! out as `_pad` fields, so a payload built from one has no uninitialized bytes.

use std::collections::HashMap;
use std::ffi::{CStr, CString, c_char};
use std::marker::PhantomData;
use std::sync::{Arc, Weak};

use crate::replace::{
    POINTER_FREE_SUBOBJECTS, Payload, SHADER_SUBOBJECTS, SUBOBJECT_CACHED_PSO,
    SUBOBJECT_INPUT_LAYOUT, SUBOBJECT_PS, SUBOBJECT_ROOT_SIGNATURE, SUBOBJECT_STREAM_OUTPUT,
    SUBOBJECT_VIEW_INSTANCING, Subobject, Walk, fnv1_64, subobjects,
};

/// `D3D12_SHADER_BYTECODE`.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ShaderBytecode {
    pub bytecode: *const u8,
    pub length: usize,
}

impl ShaderBytecode {
    /// No shader: what a description says for a stage it does not use.
    pub const NONE: Self = Self {
        bytecode: std::ptr::null(),
        length: 0,
    };
}

/// `D3D12_INPUT_LAYOUT_DESC`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct InputLayoutDesc {
    pub elements: *const InputElementDesc,
    pub count: u32,
    pub _pad: u32,
}

impl InputLayoutDesc {
    #[must_use]
    pub const fn new(elements: *const InputElementDesc, count: u32) -> Self {
        Self {
            elements,
            count,
            _pad: 0,
        }
    }
}

/// `D3D12_INPUT_ELEMENT_DESC`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct InputElementDesc {
    pub semantic_name: *const c_char,
    pub semantic_index: u32,
    pub format: i32,
    pub input_slot: u32,
    pub aligned_byte_offset: u32,
    pub input_slot_class: i32,
    pub instance_data_step_rate: u32,
}

/// `D3D12_STREAM_OUTPUT_DESC`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct StreamOutputDesc {
    pub declarations: *const SoDeclarationEntry,
    pub entries: u32,
    pub _pad: u32,
    pub strides: *const u32,
    pub stride_count: u32,
    pub rasterized_stream: u32,
}

impl StreamOutputDesc {
    #[must_use]
    pub const fn new(
        declarations: *const SoDeclarationEntry,
        entries: u32,
        strides: *const u32,
        stride_count: u32,
        rasterized_stream: u32,
    ) -> Self {
        Self {
            declarations,
            entries,
            _pad: 0,
            strides,
            stride_count,
            rasterized_stream,
        }
    }
}

/// `D3D12_SO_DECLARATION_ENTRY`. A null semantic name is a gap in the output.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct SoDeclarationEntry {
    pub stream: u32,
    pub _pad: u32,
    pub semantic_name: *const c_char,
    pub semantic_index: u32,
    pub start_component: u8,
    pub component_count: u8,
    pub output_slot: u8,
    pub _pad_end: u8,
}

impl SoDeclarationEntry {
    #[must_use]
    pub const fn new(
        stream: u32,
        semantic_name: *const c_char,
        semantic_index: u32,
        start_component: u8,
        component_count: u8,
        output_slot: u8,
    ) -> Self {
        Self {
            stream,
            _pad: 0,
            semantic_name,
            semantic_index,
            start_component,
            component_count,
            output_slot,
            _pad_end: 0,
        }
    }
}

/// `D3D12_VIEW_INSTANCING_DESC`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct ViewInstancingDesc {
    pub count: u32,
    pub _pad: u32,
    pub locations: *const ViewInstanceLocation,
    pub flags: i32,
    pub _pad_end: u32,
}

impl ViewInstancingDesc {
    #[must_use]
    pub const fn new(count: u32, locations: *const ViewInstanceLocation, flags: i32) -> Self {
        Self {
            count,
            _pad: 0,
            locations,
            flags,
            _pad_end: 0,
        }
    }
}

/// `D3D12_VIEW_INSTANCE_LOCATION`.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ViewInstanceLocation {
    pub viewport_array_index: u32,
    pub render_target_array_index: u32,
}

/// `D3D12_CACHED_PIPELINE_STATE`.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CachedPipelineState {
    pub blob: *const u8,
    pub size: usize,
}

/// The most of each array D3D12 accepts, so a corrupt count is refused rather than read.
pub mod limit {
    /// `D3D12_IA_VERTEX_INPUT_STRUCTURE_ELEMENT_COUNT`.
    pub const INPUT_ELEMENTS: u32 = 32;
    /// `D3D12_SO_STREAM_COUNT` times `D3D12_SO_OUTPUT_COMPONENT_COUNT`.
    pub const SO_DECLARATIONS: u32 = 4 * 128;
    /// `D3D12_SO_BUFFER_SLOT_COUNT`.
    pub const SO_STRIDES: u32 = 4;
    /// `D3D12_MAX_VIEW_INSTANCE_COUNT`.
    pub const VIEW_INSTANCES: u32 = 4;
}

/// Why a stream was not copied.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopyError {
    /// The walker could not read the stream.
    Walk(Walk),
    /// The layout table's payload size for a type copied deeply is not the size of its struct, so
    /// either the table or the mirror is wrong and reading it would misplace every pointer.
    Layout { ty: u32, size: usize },
    /// A type the layout table knows, whose payload is not known to be free of pointers.
    Unhandled(u32),
    /// An array longer than D3D12 allows.
    TooMany { ty: u32, count: u32 },
}

impl std::fmt::Display for CopyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Walk(walk) => write!(f, "the stream could not be walked: {walk:?}"),
            Self::Layout { ty, size } => write!(
                f,
                "subobject type {ty}: the layout table says {size} bytes, which is not the struct \
                 this copy reads"
            ),
            Self::Unhandled(ty) => write!(
                f,
                "subobject type {ty} is not known to be free of pointers, so it is not copied"
            ),
            Self::TooMany { ty, count } => write!(
                f,
                "subobject type {ty} declares {count} array entries, more than D3D12 allows"
            ),
        }
    }
}

/// Shader bytes shared between records, by length and FNV-1, so a vertex shader used by many
/// pipelines is held once. Holds them weakly: bytes no record uses any more are freed.
pub struct ShaderStore {
    by_key: HashMap<(usize, u64), Weak<[u8]>>,
    /// The map size at which dead entries are next swept out.
    sweep_at: usize,
}

impl Default for ShaderStore {
    fn default() -> Self {
        Self::new()
    }
}

impl ShaderStore {
    /// Entries before the first sweep.
    const FIRST_SWEEP: usize = 64;

    #[must_use]
    pub fn new() -> Self {
        Self {
            by_key: HashMap::new(),
            sweep_at: Self::FIRST_SWEEP,
        }
    }

    /// The shared copy of `bytes`. A key that matches bytes that differ (an FNV-1 collision) gets
    /// its own copy, which then takes the key.
    pub fn intern(&mut self, bytes: &[u8]) -> Arc<[u8]> {
        let key = (bytes.len(), fnv1_64(bytes));
        if let Some(found) = self.by_key.get(&key).and_then(Weak::upgrade)
            && *found == *bytes
        {
            return found;
        }
        if self.by_key.len() >= self.sweep_at {
            self.by_key.retain(|_, held| held.strong_count() > 0);
            self.sweep_at = (2 * self.by_key.len()).max(Self::FIRST_SWEEP);
        }
        let owned: Arc<[u8]> = Arc::from(bytes);
        self.by_key.insert(key, Arc::downgrade(&owned));
        owned
    }

    /// Distinct shaders some record still holds.
    #[must_use]
    pub fn live(&self) -> usize {
        self.by_key
            .values()
            .filter(|held| held.strong_count() > 0)
            .count()
    }
}

/// A pipeline state stream with every pointer in it aimed at memory this record owns.
pub struct PipelineRecord {
    /// The stream, pointer-aligned.
    stream: Box<[u64]>,
    /// Its length in bytes.
    size: usize,
    subobjects: Vec<Subobject>,
    root_signature: usize,
    /// Byte offset of the first pixel shader subobject's payload.
    ps_offset: Option<usize>,
    ps: Option<Arc<[u8]>>,
    // What the stream's pointers aim at. Boxed slices and C strings keep their heap addresses when
    // the record moves, and none of them is changed after the copy.
    shaders: Vec<Arc<[u8]>>,
    input_layouts: Vec<Box<[InputElementDesc]>>,
    so_declarations: Vec<Box<[SoDeclarationEntry]>>,
    so_strides: Vec<Box<[u32]>>,
    view_locations: Vec<Box<[ViewInstanceLocation]>>,
    names: Vec<CString>,
}

// SAFETY: every raw pointer a record holds, in its stream and in its arrays, aims at a heap
// allocation the record owns or at a root signature, a free-threaded COM object; none of them is
// written after the copy, so sharing or moving a record between threads races on nothing.
unsafe impl Send for PipelineRecord {}
// SAFETY: as for `Send`.
unsafe impl Sync for PipelineRecord {}

impl PipelineRecord {
    /// A copy of `stream` with every pointer it carries aimed at memory the record owns.
    ///
    /// # Errors
    ///
    /// [`CopyError`]: the stream cannot be walked, a type's payload is not the expected size, a
    /// type may carry pointers this module does not know, or an array is longer than D3D12 allows.
    ///
    /// # Safety
    ///
    /// Every pointer in a subobject this copies deeply must be valid for what it describes: shader
    /// bytecode for its length, element arrays for their counts, and semantic names as
    /// NUL-terminated strings. A stream D3D12 has just accepted satisfies this.
    pub unsafe fn copy(
        stream: &[u8],
        layout: &dyn Fn(u32) -> Option<Payload>,
        store: &mut ShaderStore,
    ) -> Result<Self, CopyError> {
        let found = subobjects(stream, layout).map_err(CopyError::Walk)?;
        let mut words = vec![0_u64; stream.len().div_ceil(8)].into_boxed_slice();
        // SAFETY: `words` holds at least `stream.len()` bytes, and the two do not overlap.
        unsafe {
            std::ptr::copy_nonoverlapping(
                stream.as_ptr(),
                words.as_mut_ptr().cast::<u8>(),
                stream.len(),
            );
        }
        let mut record = Self {
            stream: words,
            size: stream.len(),
            subobjects: found.clone(),
            root_signature: 0,
            ps_offset: None,
            ps: None,
            shaders: Vec::new(),
            input_layouts: Vec::new(),
            so_declarations: Vec::new(),
            so_strides: Vec::new(),
            view_locations: Vec::new(),
            names: Vec::new(),
        };
        for subobject in found {
            let ty = subobject.ty;
            let at = subobject.offset;
            // The walker found the type, so the table knows it.
            let size = layout(ty).map_or(0, |payload| payload.size);
            if POINTER_FREE_SUBOBJECTS.contains(&ty) {
                continue;
            }
            if SHADER_SUBOBJECTS.contains(&ty) {
                expect::<ShaderBytecode>(ty, size)?;
                let shader: ShaderBytecode = record.read(at);
                let owned = unsafe { record.own_shader(shader, &mut *store) };
                let copied = owned
                    .as_ref()
                    .map_or(ShaderBytecode::NONE, |bytes| ShaderBytecode {
                        bytecode: bytes.as_ptr(),
                        length: bytes.len(),
                    });
                if ty == SUBOBJECT_PS && record.ps_offset.is_none() {
                    record.ps_offset = Some(at);
                    record.ps = owned;
                }
                record.write(at, copied);
            } else if ty == SUBOBJECT_ROOT_SIGNATURE {
                expect::<usize>(ty, size)?;
                if record.root_signature == 0 {
                    record.root_signature = record.read(at);
                }
            } else if ty == SUBOBJECT_INPUT_LAYOUT {
                expect::<InputLayoutDesc>(ty, size)?;
                let desc: InputLayoutDesc = record.read(at);
                let copied = unsafe { record.own_input_layout(desc) }?;
                record.write(at, copied);
            } else if ty == SUBOBJECT_STREAM_OUTPUT {
                expect::<StreamOutputDesc>(ty, size)?;
                let desc: StreamOutputDesc = record.read(at);
                let copied = unsafe { record.own_stream_output(desc) }?;
                record.write(at, copied);
            } else if ty == SUBOBJECT_VIEW_INSTANCING {
                expect::<ViewInstancingDesc>(ty, size)?;
                let desc: ViewInstancingDesc = record.read(at);
                let copied = unsafe { record.own_view_instancing(desc) }?;
                record.write(at, copied);
            } else if ty == SUBOBJECT_CACHED_PSO {
                expect::<CachedPipelineState>(ty, size)?;
                record.write(
                    at,
                    CachedPipelineState {
                        blob: std::ptr::null(),
                        size: 0,
                    },
                );
            } else {
                return Err(CopyError::Unhandled(ty));
            }
        }
        Ok(record)
    }

    /// The stream, as D3D12 reads it.
    #[must_use]
    pub fn stream(&self) -> &[u8] {
        // SAFETY: `stream` holds at least `size` bytes, all initialized.
        unsafe { std::slice::from_raw_parts(self.stream.as_ptr().cast::<u8>(), self.size) }
    }

    #[must_use]
    pub fn subobjects(&self) -> &[Subobject] {
        &self.subobjects
    }

    /// The root signature in the stream, or 0 when it names none.
    #[must_use]
    pub fn root_signature(&self) -> usize {
        self.root_signature
    }

    /// Byte offset of the pixel shader subobject's payload, if the stream has one.
    #[must_use]
    pub fn ps_offset(&self) -> Option<usize> {
        self.ps_offset
    }

    /// The game's pixel shader bytes, as the record holds them.
    #[must_use]
    pub fn ps(&self) -> Option<&[u8]> {
        self.ps.as_deref()
    }

    /// Bytes this record holds beyond its own fields; shader bytes shared with other records are
    /// counted in each.
    #[must_use]
    pub fn owned_bytes(&self) -> usize {
        let arrays = self
            .input_layouts
            .iter()
            .map(|a| size_of_val(&**a))
            .chain(self.so_declarations.iter().map(|a| size_of_val(&**a)))
            .chain(self.so_strides.iter().map(|a| size_of_val(&**a)))
            .chain(self.view_locations.iter().map(|a| size_of_val(&**a)));
        self.stream.len() * 8
            + self.shaders.iter().map(|s| s.len()).sum::<usize>()
            + arrays.sum::<usize>()
            + self
                .names
                .iter()
                .map(|n| n.as_bytes_with_nul().len())
                .sum::<usize>()
    }

    /// The stream with its pixel shader replaced by `ps`, for a variant's creation. `None` when the
    /// stream has no pixel shader subobject.
    #[must_use]
    pub fn with_ps<'a>(&'a self, ps: &'a [u8]) -> Option<SerializedStream<'a>> {
        let at = self.ps_offset?;
        let mut words = self.stream.to_vec();
        let shader = ShaderBytecode {
            bytecode: ps.as_ptr(),
            length: ps.len(),
        };
        // SAFETY: the walker placed this payload inside the stream, and `copy` checked its size.
        unsafe {
            std::ptr::write_unaligned(
                words
                    .as_mut_ptr()
                    .cast::<u8>()
                    .add(at)
                    .cast::<ShaderBytecode>(),
                shader,
            );
        }
        Some(SerializedStream {
            words,
            size: self.size,
            _borrows: PhantomData,
        })
    }

    fn read<T: Copy>(&self, at: usize) -> T {
        assert!(at + size_of::<T>() <= self.size, "read past the stream");
        // SAFETY: inside the stream, checked above.
        unsafe { std::ptr::read_unaligned(self.stream.as_ptr().cast::<u8>().add(at).cast::<T>()) }
    }

    fn write<T: Copy>(&mut self, at: usize, value: T) {
        assert!(at + size_of::<T>() <= self.size, "write past the stream");
        // SAFETY: inside the stream, checked above.
        unsafe {
            std::ptr::write_unaligned(
                self.stream.as_mut_ptr().cast::<u8>().add(at).cast::<T>(),
                value,
            );
        }
    }

    /// The bytecode, held by the record through the store; `None` for no shader.
    unsafe fn own_shader(
        &mut self,
        shader: ShaderBytecode,
        store: &mut ShaderStore,
    ) -> Option<Arc<[u8]>> {
        if shader.bytecode.is_null() || shader.length == 0 {
            return None;
        }
        // SAFETY: the caller's contract: bytecode valid for its length.
        let bytes = unsafe { std::slice::from_raw_parts(shader.bytecode, shader.length) };
        let owned = store.intern(bytes);
        self.shaders.push(Arc::clone(&owned));
        Some(owned)
    }

    /// A semantic name, held by the record; null stays null.
    unsafe fn own_name(&mut self, name: *const c_char) -> *const c_char {
        if name.is_null() {
            return std::ptr::null();
        }
        // SAFETY: the caller's contract: a NUL-terminated string.
        let owned = unsafe { CStr::from_ptr(name) }.to_owned();
        let at = owned.as_ptr();
        self.names.push(owned);
        at
    }

    unsafe fn own_input_layout(
        &mut self,
        desc: InputLayoutDesc,
    ) -> Result<InputLayoutDesc, CopyError> {
        let mut elements: Box<[InputElementDesc]> = unsafe {
            read_array(
                desc.elements,
                desc.count,
                SUBOBJECT_INPUT_LAYOUT,
                limit::INPUT_ELEMENTS,
            )
        }?;
        for element in &mut elements {
            element.semantic_name = unsafe { self.own_name(element.semantic_name) };
        }
        let copied = InputLayoutDesc {
            elements: nonempty_pointer(&elements),
            count: elements.len() as u32,
            ..desc
        };
        self.input_layouts.push(elements);
        Ok(copied)
    }

    unsafe fn own_stream_output(
        &mut self,
        desc: StreamOutputDesc,
    ) -> Result<StreamOutputDesc, CopyError> {
        let ty = SUBOBJECT_STREAM_OUTPUT;
        let mut declarations: Box<[SoDeclarationEntry]> =
            unsafe { read_array(desc.declarations, desc.entries, ty, limit::SO_DECLARATIONS) }?;
        let strides: Box<[u32]> =
            unsafe { read_array(desc.strides, desc.stride_count, ty, limit::SO_STRIDES) }?;
        for declaration in &mut declarations {
            declaration.semantic_name = unsafe { self.own_name(declaration.semantic_name) };
        }
        let copied = StreamOutputDesc {
            declarations: nonempty_pointer(&declarations),
            entries: declarations.len() as u32,
            strides: nonempty_pointer(&strides),
            stride_count: strides.len() as u32,
            ..desc
        };
        self.so_declarations.push(declarations);
        self.so_strides.push(strides);
        Ok(copied)
    }

    unsafe fn own_view_instancing(
        &mut self,
        desc: ViewInstancingDesc,
    ) -> Result<ViewInstancingDesc, CopyError> {
        let locations: Box<[ViewInstanceLocation]> = unsafe {
            read_array(
                desc.locations,
                desc.count,
                SUBOBJECT_VIEW_INSTANCING,
                limit::VIEW_INSTANCES,
            )
        }?;
        // The count is the number of views, and a description may give it with no locations.
        let copied = ViewInstancingDesc {
            locations: nonempty_pointer(&locations),
            ..desc
        };
        self.view_locations.push(locations);
        Ok(copied)
    }
}

/// A stream with one shader changed, borrowed from the record it came from and the bytes put in it:
/// its pointers aim at both, so it cannot outlive either.
pub struct SerializedStream<'a> {
    words: Vec<u64>,
    size: usize,
    _borrows: PhantomData<&'a [u8]>,
}

impl SerializedStream<'_> {
    /// For `D3D12_PIPELINE_STATE_STREAM_DESC::pPipelineStateSubobjectStream`. D3D12 reads it and
    /// never writes it.
    #[must_use]
    pub fn pointer(&self) -> *const u8 {
        self.words.as_ptr().cast()
    }

    /// For `D3D12_PIPELINE_STATE_STREAM_DESC::SizeInBytes`.
    #[must_use]
    pub fn size(&self) -> usize {
        self.size
    }

    #[must_use]
    pub fn bytes(&self) -> &[u8] {
        // SAFETY: `words` holds at least `size` bytes, all initialized.
        unsafe { std::slice::from_raw_parts(self.words.as_ptr().cast::<u8>(), self.size) }
    }
}

fn expect<T>(ty: u32, size: usize) -> Result<(), CopyError> {
    if size == size_of::<T>() {
        Ok(())
    } else {
        Err(CopyError::Layout { ty, size })
    }
}

/// `count` elements read from `start`, which need not be aligned. Null or zero is empty.
unsafe fn read_array<T: Copy>(
    start: *const T,
    count: u32,
    ty: u32,
    most: u32,
) -> Result<Box<[T]>, CopyError> {
    if start.is_null() || count == 0 {
        return Ok(Box::new([]));
    }
    if count > most {
        return Err(CopyError::TooMany { ty, count });
    }
    // SAFETY: the caller's contract: `start` valid for `count` elements.
    Ok((0..count as usize)
        .map(|n| unsafe { std::ptr::read_unaligned(start.add(n)) })
        .collect())
}

/// An owned array's address, or null for an empty one, as D3D12 spells no array.
fn nonempty_pointer<T>(array: &[T]) -> *const T {
    if array.is_empty() {
        std::ptr::null()
    } else {
        array.as_ptr()
    }
}

#[cfg(windows)]
pub use process::{PortRecord, adopt, capture, prune, with_ports};

/// The shared shader store, and the records of the ports this process has variants for.
#[cfg(windows)]
mod process {
    use std::mem::offset_of;
    use std::sync::{LazyLock, Mutex, PoisonError};

    use windows::Win32::Graphics::Direct3D12::{
        D3D12_CACHED_PIPELINE_STATE, D3D12_IA_VERTEX_INPUT_STRUCTURE_ELEMENT_COUNT,
        D3D12_INPUT_ELEMENT_DESC, D3D12_INPUT_LAYOUT_DESC, D3D12_MAX_VIEW_INSTANCE_COUNT,
        D3D12_SHADER_BYTECODE, D3D12_SO_BUFFER_SLOT_COUNT, D3D12_SO_DECLARATION_ENTRY,
        D3D12_SO_OUTPUT_COMPONENT_COUNT, D3D12_SO_STREAM_COUNT, D3D12_STREAM_OUTPUT_DESC,
        D3D12_VIEW_INSTANCE_LOCATION, D3D12_VIEW_INSTANCING_DESC,
    };

    use super::{
        CachedPipelineState, CopyError, InputElementDesc, InputLayoutDesc, PipelineRecord,
        ShaderBytecode, ShaderStore, SoDeclarationEntry, StreamOutputDesc, ViewInstanceLocation,
        ViewInstancingDesc, limit,
    };
    use crate::graveyard::{self, Corpse, OwnedCom, com_add_ref};
    use crate::replace::{Payload, PortKind};
    use crate::table;

    /// Each mirror is laid out as the `windows` crate's struct, field for field.
    const _: () = {
        macro_rules! same_shape {
            ($ours:ty, $theirs:ty) => {
                assert!(size_of::<$ours>() == size_of::<$theirs>());
                assert!(align_of::<$ours>() == align_of::<$theirs>());
            };
        }
        macro_rules! same_offset {
            ($ours:ty, $a:ident, $theirs:ty, $b:ident) => {
                assert!(offset_of!($ours, $a) == offset_of!($theirs, $b));
            };
        }
        same_shape!(ShaderBytecode, D3D12_SHADER_BYTECODE);
        same_offset!(
            ShaderBytecode,
            bytecode,
            D3D12_SHADER_BYTECODE,
            pShaderBytecode
        );
        same_offset!(
            ShaderBytecode,
            length,
            D3D12_SHADER_BYTECODE,
            BytecodeLength
        );
        same_shape!(InputLayoutDesc, D3D12_INPUT_LAYOUT_DESC);
        same_offset!(
            InputLayoutDesc,
            elements,
            D3D12_INPUT_LAYOUT_DESC,
            pInputElementDescs
        );
        same_offset!(InputLayoutDesc, count, D3D12_INPUT_LAYOUT_DESC, NumElements);
        same_shape!(InputElementDesc, D3D12_INPUT_ELEMENT_DESC);
        same_offset!(
            InputElementDesc,
            semantic_name,
            D3D12_INPUT_ELEMENT_DESC,
            SemanticName
        );
        same_offset!(
            InputElementDesc,
            semantic_index,
            D3D12_INPUT_ELEMENT_DESC,
            SemanticIndex
        );
        same_offset!(InputElementDesc, format, D3D12_INPUT_ELEMENT_DESC, Format);
        same_offset!(
            InputElementDesc,
            input_slot,
            D3D12_INPUT_ELEMENT_DESC,
            InputSlot
        );
        same_offset!(
            InputElementDesc,
            aligned_byte_offset,
            D3D12_INPUT_ELEMENT_DESC,
            AlignedByteOffset
        );
        same_offset!(
            InputElementDesc,
            input_slot_class,
            D3D12_INPUT_ELEMENT_DESC,
            InputSlotClass
        );
        same_offset!(
            InputElementDesc,
            instance_data_step_rate,
            D3D12_INPUT_ELEMENT_DESC,
            InstanceDataStepRate
        );
        same_shape!(StreamOutputDesc, D3D12_STREAM_OUTPUT_DESC);
        same_offset!(
            StreamOutputDesc,
            declarations,
            D3D12_STREAM_OUTPUT_DESC,
            pSODeclaration
        );
        same_offset!(
            StreamOutputDesc,
            entries,
            D3D12_STREAM_OUTPUT_DESC,
            NumEntries
        );
        same_offset!(
            StreamOutputDesc,
            strides,
            D3D12_STREAM_OUTPUT_DESC,
            pBufferStrides
        );
        same_offset!(
            StreamOutputDesc,
            stride_count,
            D3D12_STREAM_OUTPUT_DESC,
            NumStrides
        );
        same_offset!(
            StreamOutputDesc,
            rasterized_stream,
            D3D12_STREAM_OUTPUT_DESC,
            RasterizedStream
        );
        same_shape!(SoDeclarationEntry, D3D12_SO_DECLARATION_ENTRY);
        same_offset!(
            SoDeclarationEntry,
            stream,
            D3D12_SO_DECLARATION_ENTRY,
            Stream
        );
        same_offset!(
            SoDeclarationEntry,
            semantic_name,
            D3D12_SO_DECLARATION_ENTRY,
            SemanticName
        );
        same_offset!(
            SoDeclarationEntry,
            semantic_index,
            D3D12_SO_DECLARATION_ENTRY,
            SemanticIndex
        );
        same_offset!(
            SoDeclarationEntry,
            start_component,
            D3D12_SO_DECLARATION_ENTRY,
            StartComponent
        );
        same_offset!(
            SoDeclarationEntry,
            component_count,
            D3D12_SO_DECLARATION_ENTRY,
            ComponentCount
        );
        same_offset!(
            SoDeclarationEntry,
            output_slot,
            D3D12_SO_DECLARATION_ENTRY,
            OutputSlot
        );
        same_shape!(ViewInstancingDesc, D3D12_VIEW_INSTANCING_DESC);
        same_offset!(
            ViewInstancingDesc,
            count,
            D3D12_VIEW_INSTANCING_DESC,
            ViewInstanceCount
        );
        same_offset!(
            ViewInstancingDesc,
            locations,
            D3D12_VIEW_INSTANCING_DESC,
            pViewInstanceLocations
        );
        same_offset!(ViewInstancingDesc, flags, D3D12_VIEW_INSTANCING_DESC, Flags);
        same_shape!(ViewInstanceLocation, D3D12_VIEW_INSTANCE_LOCATION);
        same_shape!(CachedPipelineState, D3D12_CACHED_PIPELINE_STATE);
        same_offset!(
            CachedPipelineState,
            blob,
            D3D12_CACHED_PIPELINE_STATE,
            pCachedBlob
        );
        same_offset!(
            CachedPipelineState,
            size,
            D3D12_CACHED_PIPELINE_STATE,
            CachedBlobSizeInBytes
        );
        assert!(limit::INPUT_ELEMENTS == D3D12_IA_VERTEX_INPUT_STRUCTURE_ELEMENT_COUNT);
        assert!(limit::SO_DECLARATIONS == D3D12_SO_STREAM_COUNT * D3D12_SO_OUTPUT_COMPONENT_COUNT);
        assert!(limit::SO_STRIDES == D3D12_SO_BUFFER_SLOT_COUNT);
        assert!(limit::VIEW_INSTANCES == D3D12_MAX_VIEW_INSTANCE_COUNT);
    };

    static SHADERS: LazyLock<Mutex<ShaderStore>> = LazyLock::new(|| Mutex::new(ShaderStore::new()));

    /// The ports with a published redirect. Lock order: this before the table's writer.
    static PORTS: Mutex<Vec<PortRecord>> = Mutex::new(Vec::new());

    /// Copy a stream D3D12 has just accepted, sharing its shaders with every other record.
    ///
    /// # Errors
    ///
    /// [`CopyError`], from [`PipelineRecord::copy`].
    ///
    /// # Safety
    ///
    /// [`PipelineRecord::copy`]'s: `stream` is one D3D12 has just accepted.
    pub unsafe fn capture(
        stream: &[u8],
        layout: &dyn Fn(u32) -> Option<Payload>,
    ) -> Result<PipelineRecord, CopyError> {
        let mut store = SHADERS.lock().unwrap_or_else(PoisonError::into_inner);
        unsafe { PipelineRecord::copy(stream, layout, &mut store) }
    }

    /// A game pipeline with a cel port, its description, and a reference on the root signature
    /// that description names, so a variant can be created from it for as long as it is kept.
    pub struct PortRecord {
        /// The game's pipeline: the redirect table's key.
        pub game: usize,
        pub kind: PortKind,
        pub record: PipelineRecord,
        root_signature: Option<OwnedCom>,
    }

    impl PortRecord {
        /// # Safety
        ///
        /// `record.root_signature()` must be 0 or a live root signature.
        #[must_use]
        pub unsafe fn new(game: usize, kind: PortKind, record: PipelineRecord) -> Self {
            let root_signature = (record.root_signature() != 0).then(|| {
                // SAFETY: the caller's contract; the reference taken here is the one held.
                unsafe {
                    com_add_ref(record.root_signature());
                    OwnedCom::from_raw(record.root_signature())
                }
            });
            Self {
                game,
                kind,
                record,
                root_signature,
            }
        }
    }

    /// Keep `port` and run `publish`, which puts its redirect in the table, under the registry's
    /// lock, so no record is ever pruned for a redirect that is about to be published. A record
    /// already kept for the same game pipeline is replaced.
    pub fn adopt(port: PortRecord, publish: impl FnOnce()) {
        let mut ports = PORTS.lock().unwrap_or_else(PoisonError::into_inner);
        let game = port.game;
        publish();
        prune_locked(&mut ports, Some(game));
        ports.push(port);
    }

    /// Drop the records whose game pipeline no longer has an entry in the published table, which
    /// is what the reaper leaves when the game releases one. For the worker's tick; returns how
    /// many went.
    pub fn prune() -> usize {
        let mut ports = PORTS.lock().unwrap_or_else(PoisonError::into_inner);
        prune_locked(&mut ports, None)
    }

    /// Read every kept record, under the registry's lock.
    pub fn with_ports<R>(read: impl FnOnce(&[PortRecord]) -> R) -> R {
        let ports = PORTS.lock().unwrap_or_else(PoisonError::into_inner);
        read(&ports)
    }

    fn prune_locked(ports: &mut Vec<PortRecord>, replacing: Option<usize>) -> usize {
        let (kept, gone): (Vec<PortRecord>, Vec<PortRecord>) = std::mem::take(ports)
            .into_iter()
            .partition(|port| Some(port.game) != replacing && table::get(port.game).is_some());
        *ports = kept;
        let count = gone.len();
        for port in gone {
            // The root signature reference goes to the graveyard rather than straight to the
            // driver, like everything else a variant still in flight may depend on.
            if let Some(root_signature) = port.root_signature {
                graveyard::retire(Corpse::Com(root_signature));
            }
        }
        count
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replace::test_support::{push, test_layout};

    fn bytes_of<T: Copy>(value: &T) -> Vec<u8> {
        // SAFETY: a live `T`, read as its bytes. Every type passed here is an integer or a mirror,
        // and the mirrors spell their padding out as fields, so no byte is uninitialized.
        unsafe { std::slice::from_raw_parts((value as *const T).cast::<u8>(), size_of::<T>()) }
            .to_vec()
    }

    fn push_value<T: Copy>(stream: &mut Vec<u8>, ty: u32, value: &T) -> usize {
        let start = stream.len();
        push(stream, ty, &bytes_of(value), align_of::<T>());
        start + 4_usize.div_ceil(align_of::<T>()) * align_of::<T>()
    }

    fn payload_at<T: Copy>(stream: &[u8], at: usize) -> T {
        assert!(at + size_of::<T>() <= stream.len());
        unsafe { std::ptr::read_unaligned(stream.as_ptr().add(at).cast::<T>()) }
    }

    fn name_at(pointer: *const c_char) -> String {
        unsafe { CStr::from_ptr(pointer) }
            .to_str()
            .expect("UTF-8")
            .to_owned()
    }

    /// The game's side of a description: the memory its pointers aim at.
    struct Source {
        vs: Vec<u8>,
        ps: Vec<u8>,
        names: Vec<CString>,
        elements: Vec<InputElementDesc>,
        declarations: Vec<SoDeclarationEntry>,
        strides: Vec<u32>,
        locations: Vec<ViewInstanceLocation>,
        cached: Vec<u8>,
    }

    impl Source {
        fn new() -> Self {
            let names: Vec<CString> = ["POSITION", "TEXCOORD", "SV_Position"]
                .iter()
                .map(|n| CString::new(*n).expect("no NUL"))
                .collect();
            let element = |name: &CString, index: u32| InputElementDesc {
                semantic_name: name.as_ptr(),
                semantic_index: index,
                format: 2,
                input_slot: 0,
                aligned_byte_offset: 16 * index,
                input_slot_class: 0,
                instance_data_step_rate: 0,
            };
            let elements = vec![element(&names[0], 0), element(&names[1], 1)];
            let declarations = vec![
                SoDeclarationEntry::new(0, names[2].as_ptr(), 0, 0, 4, 0),
                // A gap: no semantic.
                SoDeclarationEntry::new(0, std::ptr::null(), 0, 0, 2, 0),
            ];
            Self {
                vs: b"DXBC vertex shader".to_vec(),
                ps: b"DXBC game pixel shader".to_vec(),
                names,
                elements,
                declarations,
                strides: vec![24],
                locations: vec![ViewInstanceLocation {
                    viewport_array_index: 1,
                    render_target_array_index: 2,
                }],
                cached: vec![0xcc; 64],
            }
        }
    }

    /// Payload offsets of the subobjects [`stream_of`] builds.
    struct Offsets {
        root_signature: usize,
        vs: usize,
        ps: usize,
        input_layout: usize,
        stream_output: usize,
        view_instancing: usize,
        cached: usize,
        topology: usize,
    }

    const ROOT_SIGNATURE: usize = 0x0000_02a0_1234_5670;

    fn stream_of(source: &Source) -> (Vec<u8>, Offsets) {
        let mut stream = Vec::new();
        let topology = push_value(&mut stream, 14, &3_u32);
        let root_signature = push_value(&mut stream, 0, &ROOT_SIGNATURE);
        let vs = push_value(
            &mut stream,
            1,
            &ShaderBytecode {
                bytecode: source.vs.as_ptr(),
                length: source.vs.len(),
            },
        );
        let ps = push_value(
            &mut stream,
            2,
            &ShaderBytecode {
                bytecode: source.ps.as_ptr(),
                length: source.ps.len(),
            },
        );
        let input_layout = push_value(
            &mut stream,
            12,
            &InputLayoutDesc::new(source.elements.as_ptr(), source.elements.len() as u32),
        );
        let stream_output = push_value(
            &mut stream,
            7,
            &StreamOutputDesc::new(
                source.declarations.as_ptr(),
                source.declarations.len() as u32,
                source.strides.as_ptr(),
                source.strides.len() as u32,
                0,
            ),
        );
        let view_instancing = push_value(
            &mut stream,
            22,
            &ViewInstancingDesc::new(source.locations.len() as u32, source.locations.as_ptr(), 0),
        );
        let cached = push_value(
            &mut stream,
            19,
            &CachedPipelineState {
                blob: source.cached.as_ptr(),
                size: source.cached.len(),
            },
        );
        (
            stream,
            Offsets {
                root_signature,
                vs,
                ps,
                input_layout,
                stream_output,
                view_instancing,
                cached,
                topology,
            },
        )
    }

    fn copy(stream: &[u8], store: &mut ShaderStore) -> Result<PipelineRecord, CopyError> {
        unsafe { PipelineRecord::copy(stream, &test_layout, store) }
    }

    #[test]
    fn every_owned_pointer_differs_from_the_source_and_holds_the_same_data() {
        let source = Source::new();
        let (stream, at) = stream_of(&source);
        let mut store = ShaderStore::new();
        let record = copy(&stream, &mut store).expect("copied");
        let copied = record.stream();
        assert_eq!(copied.len(), stream.len());

        let vs: ShaderBytecode = payload_at(copied, at.vs);
        assert_ne!(vs.bytecode, source.vs.as_ptr());
        assert_eq!(
            unsafe { std::slice::from_raw_parts(vs.bytecode, vs.length) },
            &source.vs[..]
        );
        let ps: ShaderBytecode = payload_at(copied, at.ps);
        assert_ne!(ps.bytecode, source.ps.as_ptr());
        assert_eq!(record.ps(), Some(&source.ps[..]));
        assert_eq!(record.ps_offset(), Some(at.ps));

        let layout: InputLayoutDesc = payload_at(copied, at.input_layout);
        assert_ne!(layout.elements, source.elements.as_ptr());
        assert_eq!(layout.count, 2);
        let elements = unsafe { std::slice::from_raw_parts(layout.elements, 2) };
        for (copy, original) in elements.iter().zip(&source.elements) {
            assert_ne!(copy.semantic_name, original.semantic_name);
            assert_eq!(copy.aligned_byte_offset, original.aligned_byte_offset);
            assert_eq!(copy.semantic_index, original.semantic_index);
        }

        let output: StreamOutputDesc = payload_at(copied, at.stream_output);
        assert_ne!(output.declarations, source.declarations.as_ptr());
        assert_ne!(output.strides, source.strides.as_ptr());
        assert_eq!((output.entries, output.stride_count), (2, 1));
        assert_eq!(unsafe { *output.strides }, 24);

        let views: ViewInstancingDesc = payload_at(copied, at.view_instancing);
        assert_ne!(views.locations, source.locations.as_ptr());
        assert_eq!(unsafe { *views.locations }, source.locations[0]);

        // Untouched: the root signature pointer and a payload with no pointer in it.
        assert_eq!(record.root_signature(), ROOT_SIGNATURE);
        assert_eq!(
            payload_at::<usize>(copied, at.root_signature),
            ROOT_SIGNATURE
        );
        assert_eq!(payload_at::<u32>(copied, at.topology), 3);
        assert!(record.owned_bytes() > stream.len());
    }

    #[test]
    fn semantic_strings_survive_the_source_being_freed() {
        let mut source = Source::new();
        let (stream, at) = stream_of(&source);
        let mut store = ShaderStore::new();
        let record = copy(&stream, &mut store).expect("copied");
        // Scribble over the game's strings, then free everything the description pointed at.
        for name in &mut source.names {
            let mut scribbled = name.as_bytes().to_vec();
            scribbled.fill(b'x');
            *name = CString::new(scribbled).expect("no NUL");
        }
        drop(source);

        let copied = record.stream();
        let layout: InputLayoutDesc = payload_at(copied, at.input_layout);
        let elements = unsafe { std::slice::from_raw_parts(layout.elements, 2) };
        let names: Vec<String> = elements.iter().map(|e| name_at(e.semantic_name)).collect();
        assert_eq!(names, ["POSITION", "TEXCOORD"]);
        let output: StreamOutputDesc = payload_at(copied, at.stream_output);
        let declarations = unsafe { std::slice::from_raw_parts(output.declarations, 2) };
        assert_eq!(name_at(declarations[0].semantic_name), "SV_Position");
        assert!(declarations[1].semantic_name.is_null());
        let ps: ShaderBytecode = payload_at(copied, at.ps);
        assert_eq!(
            unsafe { std::slice::from_raw_parts(ps.bytecode, ps.length) },
            b"DXBC game pixel shader"
        );
    }

    #[test]
    fn the_cached_pipeline_is_zeroed() {
        let source = Source::new();
        let (stream, at) = stream_of(&source);
        let record = copy(&stream, &mut ShaderStore::new()).expect("copied");
        let cached: CachedPipelineState = payload_at(record.stream(), at.cached);
        assert_eq!(
            cached,
            CachedPipelineState {
                blob: std::ptr::null(),
                size: 0
            }
        );
    }

    #[test]
    fn the_pixel_shader_is_found_and_replaced_in_a_serialized_copy() {
        let source = Source::new();
        let (stream, at) = stream_of(&source);
        let record = copy(&stream, &mut ShaderStore::new()).expect("copied");
        let cel = b"DXBC cel variant".to_vec();
        let serialized = record.with_ps(&cel).expect("has a pixel shader");
        assert_eq!(serialized.size(), record.stream().len());
        assert_eq!(serialized.pointer() as usize % align_of::<usize>(), 0);
        let replaced: ShaderBytecode = payload_at(serialized.bytes(), at.ps);
        assert_eq!(
            replaced,
            ShaderBytecode {
                bytecode: cel.as_ptr(),
                length: cel.len()
            }
        );
        // Every other byte is the record's, and the record still holds the game's shader.
        let size = size_of::<ShaderBytecode>();
        assert_eq!(serialized.bytes()[..at.ps], record.stream()[..at.ps]);
        assert_eq!(
            serialized.bytes()[at.ps + size..],
            record.stream()[at.ps + size..]
        );
        let kept: ShaderBytecode = payload_at(record.stream(), at.ps);
        assert_eq!(
            unsafe { std::slice::from_raw_parts(kept.bytecode, kept.length) },
            &source.ps[..]
        );
    }

    #[test]
    fn a_stream_with_no_pixel_shader_has_none_to_replace() {
        let mut stream = Vec::new();
        push_value(&mut stream, 14, &3_u32);
        let record = copy(&stream, &mut ShaderStore::new()).expect("copied");
        assert_eq!(record.ps_offset(), None);
        assert!(record.with_ps(b"DXBC").is_none());
    }

    #[test]
    fn identical_shaders_are_held_once_across_records() {
        let first = Source::new();
        let mut second = Source::new();
        second.ps = b"DXBC another pixel shader".to_vec();
        let mut store = ShaderStore::new();
        let (a_stream, at) = stream_of(&first);
        let (b_stream, _) = stream_of(&second);
        let a = copy(&a_stream, &mut store).expect("copied");
        let b = copy(&b_stream, &mut store).expect("copied");
        let vs = |r: &PipelineRecord| payload_at::<ShaderBytecode>(r.stream(), at.vs).bytecode;
        let ps = |r: &PipelineRecord| payload_at::<ShaderBytecode>(r.stream(), at.ps).bytecode;
        assert_eq!(vs(&a), vs(&b));
        assert_ne!(ps(&a), ps(&b));
        assert_eq!(store.live(), 3);
        drop(a);
        drop(b);
        assert_eq!(store.live(), 0);
    }

    #[test]
    fn a_hash_collision_never_shares_different_bytes() {
        let mut store = ShaderStore::new();
        let first = store.intern(b"DXBC one");
        // Plant the first copy under the key of other bytes of the same length.
        let other = b"DXBC two";
        let key = (other.len(), fnv1_64(other));
        store.by_key.insert(key, Arc::downgrade(&first));
        let second = store.intern(other);
        assert!(!Arc::ptr_eq(&first, &second));
        assert_eq!(&*second, other);
    }

    #[test]
    fn empty_shaders_and_arrays_stay_null() {
        let mut stream = Vec::new();
        let vs = push_value(&mut stream, 1, &ShaderBytecode::NONE);
        let layout = push_value(&mut stream, 12, &InputLayoutDesc::new(std::ptr::null(), 0));
        let record = copy(&stream, &mut ShaderStore::new()).expect("copied");
        assert_eq!(
            payload_at::<ShaderBytecode>(record.stream(), vs),
            ShaderBytecode::NONE
        );
        assert!(
            payload_at::<InputLayoutDesc>(record.stream(), layout)
                .elements
                .is_null()
        );
    }

    #[test]
    fn a_type_that_may_carry_pointers_is_refused() {
        // Known to this layout, and neither copied deeply nor known to be free of pointers.
        let layout = |ty: u32| match ty {
            29 => Some(Payload { size: 16, align: 8 }),
            other => test_layout(other),
        };
        let mut stream = Vec::new();
        push(&mut stream, 29, &[0xab; 16], 8);
        let refused = unsafe { PipelineRecord::copy(&stream, &layout, &mut ShaderStore::new()) };
        assert_eq!(refused.err(), Some(CopyError::Unhandled(29)));
    }

    #[test]
    fn a_layout_that_disagrees_with_the_struct_is_refused() {
        let layout = |ty: u32| match ty {
            12 => Some(Payload { size: 24, align: 8 }),
            other => test_layout(other),
        };
        let mut stream = Vec::new();
        push(&mut stream, 12, &[0; 24], 8);
        let refused = unsafe { PipelineRecord::copy(&stream, &layout, &mut ShaderStore::new()) };
        assert_eq!(refused.err(), Some(CopyError::Layout { ty: 12, size: 24 }));
    }

    #[test]
    fn an_array_longer_than_d3d12_allows_is_refused() {
        let elements = [InputElementDesc {
            semantic_name: std::ptr::null(),
            semantic_index: 0,
            format: 0,
            input_slot: 0,
            aligned_byte_offset: 0,
            input_slot_class: 0,
            instance_data_step_rate: 0,
        }];
        let mut stream = Vec::new();
        push_value(
            &mut stream,
            12,
            &InputLayoutDesc::new(elements.as_ptr(), limit::INPUT_ELEMENTS + 1),
        );
        assert_eq!(
            copy(&stream, &mut ShaderStore::new()).err(),
            Some(CopyError::TooMany {
                ty: 12,
                count: limit::INPUT_ELEMENTS + 1
            })
        );
    }

    #[test]
    fn walker_failures_are_passed_on() {
        let mut stream = Vec::new();
        push(&mut stream, 99, &[0; 4], 4);
        assert_eq!(
            copy(&stream, &mut ShaderStore::new()).err(),
            Some(CopyError::Walk(Walk::UnknownType(99)))
        );
    }

    #[test]
    fn the_mirrors_have_the_64_bit_d3d12_sizes() {
        assert_eq!(size_of::<ShaderBytecode>(), 16);
        assert_eq!(size_of::<InputLayoutDesc>(), 16);
        assert_eq!(size_of::<InputElementDesc>(), 32);
        assert_eq!(size_of::<StreamOutputDesc>(), 32);
        assert_eq!(size_of::<SoDeclarationEntry>(), 24);
        assert_eq!(size_of::<ViewInstancingDesc>(), 24);
        assert_eq!(size_of::<ViewInstanceLocation>(), 8);
        assert_eq!(size_of::<CachedPipelineState>(), 16);
    }
}
