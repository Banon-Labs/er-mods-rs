//! Getting the menu's look to the replaced shaders while the game runs.
//!
//! # The mechanism
//!
//! The replacement shaders read `CelLook` at `b0, space42` (`shaders/cel_look.hlsli`). Nothing in
//! the game binds that, so this module does, in three steps:
//!
//! 1. every root signature the game creates gets one more parameter appended, a root constant
//!    buffer view at `b0, space42` visible to pixel shaders. Appending leaves every parameter the
//!    game knows about at its index, so the game's own `SetGraphicsRoot*` calls are unchanged;
//! 2. each device gets one 256-byte upload buffer, mapped for the life of the process, holding
//!    the look;
//! 3. whenever a command list sets one of those root signatures, the buffer's address is bound
//!    to the appended parameter.
//!
//! A slider then writes the mapped buffer and the next frame reads it. There is no pipeline to
//! recreate, which is what makes the menu live.
//!
//! # Why every root signature, not only the replaced passes' ones
//!
//! A root signature is created long before any pipeline that uses it, and a pipeline must be
//! created with the very root signature the game binds for it -- two root signatures with
//! different parameter lists are not interchangeable. So the only point where the look's
//! parameter can be added is creation, before it is known which root signatures the replaced
//! passes will use. One left without room (more than 62 of its 64 `DWORD`s spent), or that is a
//! ray-tracing local root signature, or denies pixel shaders root access, is left as the game
//! made it, and a replaced pass whose pipeline uses it is not swapped (see `hooks`).
//!
//! A pipeline created with no root signature takes the one serialized into its shaders, which
//! would not carry the appended parameter while the root signature the game binds does. `hooks`
//! gives such a pipeline the augmented root signature built from those same shader bytes, through
//! [`augmented_from_shader`].

use core::ffi::c_void;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{LazyLock, Mutex, RwLock};

use windows::Win32::Graphics::Direct3D::ID3DBlob;
use windows::Win32::Graphics::Direct3D12::{
    D3D_ROOT_SIGNATURE_VERSION_1_0, D3D_ROOT_SIGNATURE_VERSION_1_1, D3D_ROOT_SIGNATURE_VERSION_1_2,
    D3D12_CPU_PAGE_PROPERTY_UNKNOWN, D3D12_DESCRIPTOR_RANGE, D3D12_DESCRIPTOR_RANGE1,
    D3D12_HEAP_FLAG_NONE, D3D12_HEAP_PROPERTIES, D3D12_HEAP_TYPE_UPLOAD, D3D12_MEMORY_POOL_UNKNOWN,
    D3D12_RANGE, D3D12_RESOURCE_DESC, D3D12_RESOURCE_DIMENSION_BUFFER, D3D12_RESOURCE_FLAG_NONE,
    D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_ROOT_DESCRIPTOR,
    D3D12_ROOT_DESCRIPTOR_FLAG_DATA_VOLATILE, D3D12_ROOT_DESCRIPTOR1, D3D12_ROOT_PARAMETER,
    D3D12_ROOT_PARAMETER_0, D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS,
    D3D12_ROOT_PARAMETER_TYPE_CBV, D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE,
    D3D12_ROOT_PARAMETER1, D3D12_ROOT_PARAMETER1_0, D3D12_ROOT_SIGNATURE_DESC,
    D3D12_ROOT_SIGNATURE_DESC1, D3D12_ROOT_SIGNATURE_DESC2,
    D3D12_ROOT_SIGNATURE_FLAG_DENY_PIXEL_SHADER_ROOT_ACCESS,
    D3D12_ROOT_SIGNATURE_FLAG_LOCAL_ROOT_SIGNATURE, D3D12_ROOT_SIGNATURE_FLAGS,
    D3D12_SHADER_VISIBILITY_PIXEL, D3D12_TEXTURE_LAYOUT_ROW_MAJOR,
    D3D12_VERSIONED_ROOT_SIGNATURE_DESC, D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0,
    D3D12CreateVersionedRootSignatureDeserializer, D3D12SerializeVersionedRootSignature,
    ID3D12Device, ID3D12GraphicsCommandList_Vtbl, ID3D12Resource, ID3D12RootSignature,
    ID3D12VersionedRootSignatureDeserializer,
};
use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT_UNKNOWN, DXGI_SAMPLE_DESC};
use windows::core::{IUnknown, Interface};

use crate::log;
use crate::replace::{
    self, RootParameterCost, container_part, has_room_for_look, root_signature_dwords,
};
use crate::settings::GPU_FLOATS;

/// The register space `CelLook` is declared in. Unusual on purpose: the game's root signatures
/// would have to use space 42 for the two to collide, and one that does is left alone.
pub const LOOK_REGISTER_SPACE: u32 = 42;

/// Size of the upload buffer: one constant buffer's minimum alignment, far more than the look.
const LOOK_BUFFER_BYTES: u64 = 256;

/// Where the look is bound in one augmented root signature.
#[derive(Clone, Copy, Debug)]
pub struct LookBinding {
    /// The appended parameter's index: the original parameter count.
    pub index: u32,
    /// GPU address of the device's look buffer.
    pub address: u64,
}

/// Augmented root signatures by the pointer the game holds. Replaced whole on every creation, so
/// an address freed and reused for a new root signature never keeps a stale entry.
static AUGMENTED: LazyLock<RwLock<HashMap<usize, LookBinding>>> =
    LazyLock::new(|| RwLock::new(HashMap::new()));

/// One device's look buffer.
struct DeviceBuffer {
    device: usize,
    mapped: usize,
    address: u64,
    /// Kept for the life of the process: the address is bound into command lists the GPU may still
    /// be reading.
    _resource: ID3D12Resource,
}

// SAFETY: `mapped` is a CPU address of persistently mapped upload memory, written with volatile
// stores from any thread; the resource is a free-threaded D3D12 object.
unsafe impl Send for DeviceBuffer {}

static BUFFERS: Mutex<Vec<DeviceBuffer>> = Mutex::new(Vec::new());

/// The look as last published, one `f32` bit pattern per float, so a buffer created later starts
/// with it.
static LOOK: [AtomicU32; GPU_FLOATS] = [const { AtomicU32::new(0) }; GPU_FLOATS];

/// Root signatures built from a pipeline's own shader bytes, by FNV-1 of the `RTS0` part. Kept for
/// the life of the process, like the game's own.
static FROM_SHADER: LazyLock<Mutex<HashMap<u64, usize>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// Counters for the menu's diagnostics and the log.
pub static AUGMENTED_COUNT: AtomicUsize = AtomicUsize::new(0);
pub static SKIPPED_NO_ROOM: AtomicUsize = AtomicUsize::new(0);
pub static SKIPPED_OTHER: AtomicUsize = AtomicUsize::new(0);
pub static BINDS: AtomicUsize = AtomicUsize::new(0);

/// Write the look into every device's buffer, and remember it for buffers created later.
pub fn publish(block: [f32; GPU_FLOATS]) {
    for (index, value) in block.into_iter().enumerate() {
        LOOK[index].store(value.to_bits(), Ordering::Relaxed);
    }
    let Ok(buffers) = BUFFERS.lock() else {
        return;
    };
    for buffer in buffers.iter() {
        write_look(buffer.mapped);
    }
}

fn write_look(mapped: usize) {
    let target = mapped as *mut f32;
    for (index, slot) in LOOK.iter().enumerate() {
        let value = f32::from_bits(slot.load(Ordering::Relaxed));
        // SAFETY: `mapped` is the start of a live 256-byte upload buffer and `index` is below 16.
        unsafe { target.add(index).write_volatile(value) };
    }
}

/// The look's binding in this root signature, if it carries one.
pub fn binding(root_signature: usize) -> Option<LookBinding> {
    AUGMENTED.read().ok()?.get(&root_signature).copied()
}

/// How many root signatures carry the look.
pub fn augmented_root_signatures() -> usize {
    AUGMENTED.read().map_or(0, |map| map.len())
}

/// Record what a root signature creation produced: the look's binding, or none.
pub fn record(root_signature: usize, look: Option<LookBinding>) {
    let Ok(mut map) = AUGMENTED.write() else {
        return;
    };
    match look {
        Some(look) => {
            map.insert(root_signature, look);
        }
        None => {
            map.remove(&root_signature);
        }
    }
}

/// The device's look buffer, created on first use.
unsafe fn buffer_address(device: &ID3D12Device) -> Option<u64> {
    let raw = device.as_raw() as usize;
    let mut buffers = BUFFERS.lock().ok()?;
    if let Some(buffer) = buffers.iter().find(|b| b.device == raw) {
        return Some(buffer.address);
    }
    let heap = D3D12_HEAP_PROPERTIES {
        Type: D3D12_HEAP_TYPE_UPLOAD,
        CPUPageProperty: D3D12_CPU_PAGE_PROPERTY_UNKNOWN,
        MemoryPoolPreference: D3D12_MEMORY_POOL_UNKNOWN,
        CreationNodeMask: 0,
        VisibleNodeMask: 0,
    };
    let desc = D3D12_RESOURCE_DESC {
        Dimension: D3D12_RESOURCE_DIMENSION_BUFFER,
        Alignment: 0,
        Width: LOOK_BUFFER_BYTES,
        Height: 1,
        DepthOrArraySize: 1,
        MipLevels: 1,
        Format: DXGI_FORMAT_UNKNOWN,
        SampleDesc: DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        Layout: D3D12_TEXTURE_LAYOUT_ROW_MAJOR,
        Flags: D3D12_RESOURCE_FLAG_NONE,
    };
    let mut resource: Option<ID3D12Resource> = None;
    if let Err(error) = unsafe {
        device.CreateCommittedResource(
            &heap,
            D3D12_HEAP_FLAG_NONE,
            &desc,
            D3D12_RESOURCE_STATE_GENERIC_READ,
            None,
            &mut resource,
        )
    } {
        log(format_args!(
            "look buffer: CreateCommittedResource failed: {error}"
        ));
        return None;
    }
    let resource = resource?;
    let mut mapped: *mut c_void = std::ptr::null_mut();
    // An empty read range: the CPU never reads this buffer back.
    let no_read = D3D12_RANGE { Begin: 0, End: 0 };
    if let Err(error) = unsafe { resource.Map(0, Some(&no_read), Some(&mut mapped)) } {
        log(format_args!("look buffer: Map failed: {error}"));
        return None;
    }
    let address = unsafe { resource.GetGPUVirtualAddress() };
    write_look(mapped as usize);
    log(format_args!(
        "look buffer: device 0x{raw:x} gpu 0x{address:x} ({LOOK_BUFFER_BYTES} bytes, mapped)"
    ));
    buffers.push(DeviceBuffer {
        device: raw,
        mapped: mapped as usize,
        address,
        _resource: resource,
    });
    Some(address)
}

/// Why a root signature was left as the game made it.
pub enum Skip {
    NoRoom(u32),
    Local,
    DeniesPixelShaders,
    SpaceTaken,
    Version(i32),
    Failed(String),
}

impl std::fmt::Display for Skip {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoRoom(used) => write!(f, "{used} of 64 DWORDs used, no room for the look"),
            Self::Local => f.write_str("a ray-tracing local root signature"),
            Self::DeniesPixelShaders => f.write_str("denies pixel shaders root access"),
            Self::SpaceTaken => write!(f, "already uses register space {LOOK_REGISTER_SPACE}"),
            Self::Version(version) => write!(f, "root signature version {version} not handled"),
            Self::Failed(why) => f.write_str(why),
        }
    }
}

/// A root signature blob with the look's parameter appended, and that parameter's index.
pub struct Augmented {
    pub blob: Vec<u8>,
    pub index: u32,
    pub address: u64,
}

fn uses_space(space: u32) -> bool {
    space == LOOK_REGISTER_SPACE
}

fn check_flags(flags: D3D12_ROOT_SIGNATURE_FLAGS) -> Result<(), Skip> {
    if flags.0 & D3D12_ROOT_SIGNATURE_FLAG_LOCAL_ROOT_SIGNATURE.0 != 0 {
        return Err(Skip::Local);
    }
    if flags.0 & D3D12_ROOT_SIGNATURE_FLAG_DENY_PIXEL_SHADER_ROOT_ACCESS.0 != 0 {
        return Err(Skip::DeniesPixelShaders);
    }
    Ok(())
}

/// Budget and register-space checks for a 1.0 parameter list.
unsafe fn check_params_1_0(params: &[D3D12_ROOT_PARAMETER]) -> Result<(), Skip> {
    let mut costs = Vec::with_capacity(params.len());
    for param in params {
        if param.ParameterType == D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE {
            costs.push(RootParameterCost::Table);
            let table = unsafe { param.Anonymous.DescriptorTable };
            let ranges: &[D3D12_DESCRIPTOR_RANGE] =
                unsafe { slice_or_empty(table.pDescriptorRanges, table.NumDescriptorRanges) };
            if ranges.iter().any(|r| uses_space(r.RegisterSpace)) {
                return Err(Skip::SpaceTaken);
            }
        } else if param.ParameterType == D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS {
            let constants = unsafe { param.Anonymous.Constants };
            costs.push(RootParameterCost::Constants(constants.Num32BitValues));
            if uses_space(constants.RegisterSpace) {
                return Err(Skip::SpaceTaken);
            }
        } else {
            costs.push(RootParameterCost::Descriptor);
            if uses_space(unsafe { param.Anonymous.Descriptor }.RegisterSpace) {
                return Err(Skip::SpaceTaken);
            }
        }
    }
    room(&costs)
}

/// Budget and register-space checks for a 1.1 or 1.2 parameter list.
unsafe fn check_params_1_1(params: &[D3D12_ROOT_PARAMETER1]) -> Result<(), Skip> {
    let mut costs = Vec::with_capacity(params.len());
    for param in params {
        if param.ParameterType == D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE {
            costs.push(RootParameterCost::Table);
            let table = unsafe { param.Anonymous.DescriptorTable };
            let ranges: &[D3D12_DESCRIPTOR_RANGE1] =
                unsafe { slice_or_empty(table.pDescriptorRanges, table.NumDescriptorRanges) };
            if ranges.iter().any(|r| uses_space(r.RegisterSpace)) {
                return Err(Skip::SpaceTaken);
            }
        } else if param.ParameterType == D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS {
            let constants = unsafe { param.Anonymous.Constants };
            costs.push(RootParameterCost::Constants(constants.Num32BitValues));
            if uses_space(constants.RegisterSpace) {
                return Err(Skip::SpaceTaken);
            }
        } else {
            costs.push(RootParameterCost::Descriptor);
            if uses_space(unsafe { param.Anonymous.Descriptor }.RegisterSpace) {
                return Err(Skip::SpaceTaken);
            }
        }
    }
    room(&costs)
}

fn room(costs: &[RootParameterCost]) -> Result<(), Skip> {
    let used = root_signature_dwords(costs.iter().copied());
    if has_room_for_look(used) {
        Ok(())
    } else {
        Err(Skip::NoRoom(used))
    }
}

unsafe fn slice_or_empty<'a, T>(start: *const T, count: u32) -> &'a [T] {
    if start.is_null() || count == 0 {
        &[]
    } else {
        unsafe { std::slice::from_raw_parts(start, count as usize) }
    }
}

fn look_parameter_1_0() -> D3D12_ROOT_PARAMETER {
    D3D12_ROOT_PARAMETER {
        ParameterType: D3D12_ROOT_PARAMETER_TYPE_CBV,
        Anonymous: D3D12_ROOT_PARAMETER_0 {
            Descriptor: D3D12_ROOT_DESCRIPTOR {
                ShaderRegister: 0,
                RegisterSpace: LOOK_REGISTER_SPACE,
            },
        },
        ShaderVisibility: D3D12_SHADER_VISIBILITY_PIXEL,
    }
}

fn look_parameter_1_1() -> D3D12_ROOT_PARAMETER1 {
    D3D12_ROOT_PARAMETER1 {
        ParameterType: D3D12_ROOT_PARAMETER_TYPE_CBV,
        Anonymous: D3D12_ROOT_PARAMETER1_0 {
            Descriptor: D3D12_ROOT_DESCRIPTOR1 {
                ShaderRegister: 0,
                RegisterSpace: LOOK_REGISTER_SPACE,
                // The menu writes the buffer while frames are in flight; volatile tells the driver
                // not to assume it stays put for a whole command list.
                Flags: D3D12_ROOT_DESCRIPTOR_FLAG_DATA_VOLATILE,
            },
        },
        ShaderVisibility: D3D12_SHADER_VISIBILITY_PIXEL,
    }
}

/// The game's root signature blob with the look's parameter appended, serialized at the version
/// the game wrote it in.
///
/// # Safety
///
/// `blob` must be readable for `length` bytes.
pub unsafe fn augment(
    device: &ID3D12Device,
    blob: *const c_void,
    length: usize,
) -> Result<Augmented, Skip> {
    let mut raw: *mut c_void = std::ptr::null_mut();
    unsafe {
        D3D12CreateVersionedRootSignatureDeserializer(
            blob,
            length,
            &ID3D12VersionedRootSignatureDeserializer::IID,
            &mut raw,
        )
    }
    .map_err(|e| Skip::Failed(format!("deserialize: {e}")))?;
    // SAFETY: the call above succeeded and handed over one reference.
    let deserializer = unsafe { ID3D12VersionedRootSignatureDeserializer::from_raw(raw) };
    // Owned by the deserializer, which outlives every use below.
    let original = unsafe { &*deserializer.GetUnconvertedRootSignatureDesc() };
    let version = original.Version;

    let mut serialized: Option<ID3DBlob> = None;
    let mut errors: Option<ID3DBlob> = None;
    let index;
    if version == D3D_ROOT_SIGNATURE_VERSION_1_0 {
        let desc = unsafe { original.Anonymous.Desc_1_0 };
        check_flags(desc.Flags)?;
        let params = unsafe { slice_or_empty(desc.pParameters, desc.NumParameters) };
        unsafe { check_params_1_0(params) }?;
        let mut extended = params.to_vec();
        extended.push(look_parameter_1_0());
        index = desc.NumParameters;
        let augmented = D3D12_VERSIONED_ROOT_SIGNATURE_DESC {
            Version: version,
            Anonymous: D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0 {
                Desc_1_0: D3D12_ROOT_SIGNATURE_DESC {
                    NumParameters: index + 1,
                    pParameters: extended.as_ptr(),
                    ..desc
                },
            },
        };
        unsafe {
            D3D12SerializeVersionedRootSignature(&augmented, &mut serialized, Some(&mut errors))
        }
        .map_err(|e| Skip::Failed(format!("serialize 1.0: {e} {}", blob_text(&errors))))?;
    } else if version == D3D_ROOT_SIGNATURE_VERSION_1_1 {
        let desc = unsafe { original.Anonymous.Desc_1_1 };
        check_flags(desc.Flags)?;
        let params = unsafe { slice_or_empty(desc.pParameters, desc.NumParameters) };
        unsafe { check_params_1_1(params) }?;
        let mut extended = params.to_vec();
        extended.push(look_parameter_1_1());
        index = desc.NumParameters;
        let augmented = D3D12_VERSIONED_ROOT_SIGNATURE_DESC {
            Version: version,
            Anonymous: D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0 {
                Desc_1_1: D3D12_ROOT_SIGNATURE_DESC1 {
                    NumParameters: index + 1,
                    pParameters: extended.as_ptr(),
                    ..desc
                },
            },
        };
        unsafe {
            D3D12SerializeVersionedRootSignature(&augmented, &mut serialized, Some(&mut errors))
        }
        .map_err(|e| Skip::Failed(format!("serialize 1.1: {e} {}", blob_text(&errors))))?;
    } else if version == D3D_ROOT_SIGNATURE_VERSION_1_2 {
        let desc = unsafe { original.Anonymous.Desc_1_2 };
        check_flags(desc.Flags)?;
        let params = unsafe { slice_or_empty(desc.pParameters, desc.NumParameters) };
        unsafe { check_params_1_1(params) }?;
        let mut extended = params.to_vec();
        extended.push(look_parameter_1_1());
        index = desc.NumParameters;
        let augmented = D3D12_VERSIONED_ROOT_SIGNATURE_DESC {
            Version: version,
            Anonymous: D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0 {
                Desc_1_2: D3D12_ROOT_SIGNATURE_DESC2 {
                    NumParameters: index + 1,
                    pParameters: extended.as_ptr(),
                    ..desc
                },
            },
        };
        unsafe {
            D3D12SerializeVersionedRootSignature(&augmented, &mut serialized, Some(&mut errors))
        }
        .map_err(|e| Skip::Failed(format!("serialize 1.2: {e} {}", blob_text(&errors))))?;
    } else {
        return Err(Skip::Version(version.0));
    }
    let serialized =
        serialized.ok_or_else(|| Skip::Failed("serializer returned no blob".into()))?;
    let bytes = unsafe {
        std::slice::from_raw_parts(
            serialized.GetBufferPointer().cast::<u8>(),
            serialized.GetBufferSize(),
        )
    }
    .to_vec();
    let address = unsafe { buffer_address(device) }
        .ok_or_else(|| Skip::Failed("no look buffer on this device".into()))?;
    Ok(Augmented {
        blob: bytes,
        index,
        address,
    })
}

fn blob_text(blob: &Option<ID3DBlob>) -> String {
    let Some(blob) = blob else {
        return String::new();
    };
    // SAFETY: a live blob's pointer and size.
    let bytes = unsafe {
        std::slice::from_raw_parts(blob.GetBufferPointer().cast::<u8>(), blob.GetBufferSize())
    };
    String::from_utf8(bytes.to_vec())
        .unwrap_or_default()
        .trim_end_matches('\0')
        .to_owned()
}

/// Count a skip, and log the first few of each kind.
pub fn note_skip(skip: &Skip) {
    let seen = match skip {
        Skip::NoRoom(_) => SKIPPED_NO_ROOM.fetch_add(1, Ordering::Relaxed),
        _ => SKIPPED_OTHER.fetch_add(1, Ordering::Relaxed),
    };
    if seen < 8 {
        log(format_args!(
            "root signature left as the game made it: {skip}"
        ));
    }
}

/// The augmented root signature a pipeline created without one should use: the one serialized
/// into its shaders, with the look appended, created once per distinct `RTS0` part.
///
/// Created through the device's own `CreateRootSignature`, so it goes through the hook in
/// `hooks` and is augmented and recorded exactly like the game's.
///
/// # Safety
///
/// `container` must be shader bytecode the game passed to a pipeline creation.
pub unsafe fn augmented_from_shader(device: &ID3D12Device, container: &[u8]) -> Option<usize> {
    let part = container_part(container, replace::ROOT_SIGNATURE_PART)?;
    let key = replace::fnv1_64(part);
    if let Some(found) = FROM_SHADER.lock().ok()?.get(&key).copied() {
        return Some(found);
    }
    // Not held across the call: creation re-enters this module through the hook.
    let created: ID3D12RootSignature = match unsafe { device.CreateRootSignature(0, container) } {
        Ok(created) => created,
        Err(error) => {
            log(format_args!(
                "root signature from shader bytes failed: {error}"
            ));
            return None;
        }
    };
    let raw = created.into_raw() as usize;
    let mut cache = FROM_SHADER.lock().ok()?;
    Some(*cache.entry(key).or_insert(raw))
}

/// `ID3D12GraphicsCommandList::SetGraphicsRootSignature`.
pub type SetRootSignatureFn = unsafe extern "system" fn(*mut c_void, *mut c_void);

/// The originals behind the two `SetGraphicsRootSignature` detours: direct command lists and
/// bundles, which vkd3d-proton serves from different vtables. Both may be the same function, in
/// which case only the first is hooked.
pub static SET_ROOT_SIGNATURE: [AtomicUsize; 2] = [const { AtomicUsize::new(0) }; 2];

/// Calls the original, then binds the look if the root signature carries it.
///
/// The bind follows every set rather than only the first: setting a root signature invalidates
/// every root argument, the appended one included.
pub unsafe extern "system" fn set_root_signature_hook<const SLOT: usize>(
    list: *mut c_void,
    root_signature: *mut c_void,
) {
    let original = SET_ROOT_SIGNATURE[SLOT].load(Ordering::Acquire);
    if original == 0 {
        return;
    }
    // SAFETY: the trampoline MinHook returned for this exact function.
    let original: SetRootSignatureFn = unsafe { std::mem::transmute(original) };
    unsafe { original(list, root_signature) };
    if root_signature.is_null() {
        return;
    }
    let Some(look) = binding(root_signature as usize) else {
        return;
    };
    // SAFETY: `list` is a live graphics command list or bundle; its own vtable's entry is called,
    // which is not hooked.
    unsafe {
        let vtable = *(list as *const *const ID3D12GraphicsCommandList_Vtbl);
        ((*vtable).SetGraphicsRootConstantBufferView)(list, look.index, look.address);
    }
    BINDS.fetch_add(1, Ordering::Relaxed);
}

/// The `ID3D12RootSignature` pointer for whatever interface a creation returned.
///
/// # Safety
///
/// `raw` must be a live COM object.
pub unsafe fn root_signature_pointer(raw: *mut c_void) -> usize {
    let Some(unknown) = (unsafe { IUnknown::from_raw_borrowed(&raw) }) else {
        return 0;
    };
    // The cast takes a reference and the drop releases it, so the count is unchanged.
    unknown
        .cast::<ID3D12RootSignature>()
        .map_or(raw as usize, |rs| rs.as_raw() as usize)
}
