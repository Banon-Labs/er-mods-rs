//! The D3D12 detours: one on `D3D12CreateDevice`, and from the device it returns, the entry points
//! a root signature or a pipeline reaches the driver through.
//!
//! `D3D12CreateDevice` is the only export hooked at load. Everything after it is taken from the
//! vtable of the device the game actually created, so the same code serves the native runtime on
//! Windows and vkd3d-proton under Proton without knowing which one it is talking to.
//!
//! * `CreateRootSignature` appends the editor's parameters as far as the root signature has room
//!   ([`crate::look_binding`]).
//! * `SetGraphicsRootSignature`, on direct command lists and on bundles, binds them after the
//!   game's call.
//! * `SetPipelineState`, `Reset`, `ClearState` and `Close` on command lists, and the device's
//!   `CreateCommandList`, are [`crate::cmdlist`]'s: they bind a port's cel variant in its game
//!   pipeline's place, and copy the probe back at `Close`.
//! * The pipeline creators create every pipeline as the game described it (design 4.1). The one
//!   change they make is to give a pipeline created with no root signature the augmented one built
//!   from its own shaders, so it matches what the game binds for it; that is measured at zero
//!   occurrences and logged when it happens. The pipeline library's loaders change nothing.
//! * A pipeline whose pixel shader is a [`crate::replace::REPLACEMENTS`] pass, on a root signature
//!   carrying the whole editor ABI ([`look_binding::Level::Full`]), is a port. Once the game's
//!   pipeline exists, its description is copied into a [`record::PipelineRecord`], a second
//!   pipeline -- the cel variant -- is created from the copy with the cel port as its pixel shader,
//!   and the pair is published to [`table`]. The command-list hooks bind the variant in the game
//!   pipeline's place; the game's own pipeline is never changed.
//!
//! The game's own descriptions are never written to; every change is made to a copy.
//!
//! A build with the `d3d12-debug` feature, which exists for the native Windows gate W1 (design
//! section 13), also switches on D3D12's debug layer, GPU-based validation and DRED in the
//! `D3D12CreateDevice` detour, and writes the debug layer's messages to the log (`debug_layer`).

use core::ffi::c_void;
use std::mem::{ManuallyDrop, align_of, size_of};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{Mutex, PoisonError};

use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};
use windows::Win32::Foundation::{E_INVALIDARG, E_NOINTERFACE, S_OK};
use windows::Win32::Graphics::Direct3D12::{
    D3D12_BLEND_DESC, D3D12_CACHED_PIPELINE_STATE, D3D12_COMMAND_LIST_TYPE,
    D3D12_COMMAND_LIST_TYPE_BUNDLE, D3D12_COMMAND_LIST_TYPE_DIRECT,
    D3D12_COMPUTE_PIPELINE_STATE_DESC, D3D12_DEPTH_STENCIL_DESC, D3D12_DEPTH_STENCIL_DESC1,
    D3D12_DEPTH_STENCIL_DESC2, D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    D3D12_INDEX_BUFFER_STRIP_CUT_VALUE, D3D12_INPUT_LAYOUT_DESC, D3D12_PIPELINE_STATE_FLAGS,
    D3D12_PIPELINE_STATE_STREAM_DESC, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_BLEND, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_CACHED_PSO,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL_FORMAT,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DS, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_FLAGS,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_GS, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_HS,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_IB_STRIP_CUT_VALUE,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_INPUT_LAYOUT,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_NODE_MASK,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PRIMITIVE_TOPOLOGY, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RASTERIZER,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RENDER_TARGET_FORMATS,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_SAMPLE_DESC,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_SAMPLE_MASK,
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_STREAM_OUTPUT, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_VS,
    D3D12_PRIMITIVE_TOPOLOGY_TYPE, D3D12_RASTERIZER_DESC, D3D12_RASTERIZER_DESC1,
    D3D12_RASTERIZER_DESC2, D3D12_RT_FORMAT_ARRAY, D3D12_SHADER_BYTECODE, D3D12_STREAM_OUTPUT_DESC,
    D3D12_VIEW_INSTANCING_DESC, ID3D12CommandAllocator, ID3D12Device, ID3D12Device1, ID3D12Device2,
    ID3D12GraphicsCommandList, ID3D12PipelineLibrary, ID3D12PipelineLibrary1, ID3D12PipelineState,
    ID3D12RootSignature,
};
use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT, DXGI_SAMPLE_DESC};
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::core::{GUID, HRESULT, IUnknown, Interface, PCWSTR, s, w};

use crate::look_binding::{self, Level, SET_ROOT_SIGNATURE, Skip};
use crate::record::{self, PipelineRecord, PortRecord};
use crate::replace::{
    self, Payload, PortKind, Replacement, SHADER_SUBOBJECTS, SUBOBJECT_PS,
    SUBOBJECT_ROOT_SIGNATURE, Subobject,
};
use crate::table::{self, EntrySpec};
use crate::{cmdlist, log};

type CreateDeviceFn =
    unsafe extern "system" fn(*mut c_void, i32, *const GUID, *mut *mut c_void) -> HRESULT;
type CreateRootSignatureFn = unsafe extern "system" fn(
    *mut c_void,
    u32,
    *const c_void,
    usize,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;
type CreateGraphicsFn = unsafe extern "system" fn(
    *mut c_void,
    *const D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;
type CreateComputeFn = unsafe extern "system" fn(
    *mut c_void,
    *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;
type CreateStreamFn = unsafe extern "system" fn(
    *mut c_void,
    *const D3D12_PIPELINE_STATE_STREAM_DESC,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;
type CreateLibraryFn = unsafe extern "system" fn(
    *mut c_void,
    *const c_void,
    usize,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;
type LoadGraphicsFn = unsafe extern "system" fn(
    *mut c_void,
    PCWSTR,
    *const D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;
type LoadStreamFn = unsafe extern "system" fn(
    *mut c_void,
    PCWSTR,
    *const D3D12_PIPELINE_STATE_STREAM_DESC,
    *const GUID,
    *mut *mut c_void,
) -> HRESULT;

static CREATE_DEVICE: AtomicUsize = AtomicUsize::new(0);
static CREATE_ROOT_SIGNATURE: AtomicUsize = AtomicUsize::new(0);
static CREATE_GRAPHICS: AtomicUsize = AtomicUsize::new(0);
static CREATE_COMPUTE: AtomicUsize = AtomicUsize::new(0);
/// The trampoline of `ID3D12Device2::CreatePipelineState`. Cel variants are created through it, so
/// they never pass through [`create_stream_hook`] themselves.
static CREATE_STREAM: AtomicUsize = AtomicUsize::new(0);
static CREATE_LIBRARY: AtomicUsize = AtomicUsize::new(0);
static LOAD_GRAPHICS: AtomicUsize = AtomicUsize::new(0);
static LOAD_STREAM: AtomicUsize = AtomicUsize::new(0);

// No device is kept here. Every device `D3D12CreateDevice` returns passes through
// [`create_device_hook`], and the first is not necessarily the game's: hudhook's
// `get_target_addrs` creates one of its own to read the vtables it hooks and releases it on
// return, and both runtimes keep one device per adapter only while it is alive. A device kept from
// the first call could therefore be freed memory by the time the game's pipelines arrive. Each use
// takes the device from the call in hand instead: the creation hooks are handed it, and a pipeline
// library is a device child that names its own (`device_of_library`).

/// Pipelines created through each path, so a log line can say how many shaders went past before
/// a port -- and a run with no variant can say whether pipelines were seen at all.
static SEEN_GRAPHICS: AtomicU32 = AtomicU32::new(0);
static SEEN_STREAM: AtomicU32 = AtomicU32::new(0);
static SEEN_LIBRARY: AtomicU32 = AtomicU32::new(0);

/// Cel variants created, counted on success.
pub static VARIANTS: AtomicUsize = AtomicUsize::new(0);
/// Cel variants created per port kind, indexed by `PortKind as usize`.
pub static PORTS_READY: [AtomicUsize; PortKind::COUNT] =
    [const { AtomicUsize::new(0) }; PortKind::COUNT];
/// Ports left vanilla before a variant was tried: a root signature below `Full`, a description that
/// arrived through `CreateGraphicsPipelineState`, or a stream that could not be copied.
pub static PORTS_LEFT_VANILLA: AtomicUsize = AtomicUsize::new(0);
/// Pipelines of ours the runtime refused: a description with its root signature filled in, which
/// is then created as the game described it, or a cel variant, whose pass then stays vanilla.
pub static REWRITES_REFUSED: AtomicUsize = AtomicUsize::new(0);
/// Pipelines created with no root signature that were given the augmented one from their shaders.
pub static NO_ROOT_SIGNATURE_FIXED: AtomicUsize = AtomicUsize::new(0);
/// Pipelines created with no root signature and no shader carrying one.
pub static NO_ROOT_SIGNATURE_LEFT: AtomicUsize = AtomicUsize::new(0);

/// `variant ready` lines logged at most; a session normally creates one per port kind seen.
const VARIANT_LOG_LIMIT: usize = 32;

/// Addresses already detoured. vkd3d-proton and the native runtime each serve every device
/// version from one vtable, so a second device must not create a second detour on one function.
static DETOURED: Mutex<Vec<usize>> = Mutex::new(Vec::new());

/// `(pass, reason)` pairs already logged as left vanilla, so a pass recreated on every load logs
/// once.
static LOGGED_VANILLA: Mutex<Vec<(&'static str, &'static str)>> = Mutex::new(Vec::new());

/// Hook `D3D12CreateDevice`. Called from `DllMain`, because the game creates its device during
/// start-up and every root signature and pipeline after it; a hook installed later would see none
/// of them.
pub fn install() -> Result<(), String> {
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => return Err(format!("MH_Initialize failed: {status:?}")),
    }
    // `eldenring.exe` imports d3d12.dll, so it is mapped before any me3 native runs.
    let module = unsafe { GetModuleHandleW(w!("d3d12.dll")) }
        .map_err(|error| format!("d3d12.dll is not loaded: {error}"))?;
    let export = unsafe { GetProcAddress(module, s!("D3D12CreateDevice")) }
        .ok_or_else(|| "d3d12.dll has no D3D12CreateDevice export".to_string())?;
    #[cfg(feature = "d3d12-debug")]
    log(format_args!(
        "d3d12-debug build: the debug layer, GPU-based validation and DRED are switched on at the \
         first D3D12CreateDevice, and the debug layer's messages are written to this log"
    ));
    detour(
        "D3D12CreateDevice",
        export as usize,
        create_device_hook as *const () as usize,
        &CREATE_DEVICE,
    )
}

fn detour(what: &str, target: usize, hook: usize, original: &AtomicUsize) -> Result<(), String> {
    let Ok(mut detoured) = DETOURED.lock() else {
        return Err(format!("{what}: detour registry poisoned"));
    };
    if detoured.contains(&target) {
        return Ok(());
    }
    let installed = unsafe { MhHook::new(target as *mut c_void, hook as *mut c_void) }
        .map_err(|status| format!("{what}: MH_CreateHook 0x{target:x} -> {status:?}"))?;
    // Stored before enabling: the first call through the detour may land on another thread the
    // moment MinHook releases it.
    original.store(installed.trampoline() as usize, Ordering::SeqCst);
    unsafe { installed.queue_enable() }
        .map_err(|status| format!("{what}: queue_enable -> {status:?}"))?;
    unsafe { MH_ApplyQueued() }
        .ok()
        .map_err(|status| format!("{what}: MH_ApplyQueued -> {status:?}"))?;
    detoured.push(target);
    // The hook must outlive every caller; the process ends before it would be released.
    let _kept = Box::leak(Box::new(installed));
    log(format_args!("detoured {what} at 0x{target:x}"));
    Ok(())
}

fn report(result: Result<(), String>) {
    if let Err(error) = result {
        log(format_args!("hook install failed: {error}"));
    }
}

unsafe extern "system" fn create_device_hook(
    adapter: *mut c_void,
    feature_level: i32,
    riid: *const GUID,
    device: *mut *mut c_void,
) -> HRESULT {
    let original: CreateDeviceFn =
        unsafe { std::mem::transmute(CREATE_DEVICE.load(Ordering::SeqCst)) };
    // Before the original, because the debug layer, GPU-based validation and DRED apply only to
    // devices created after they are switched on.
    #[cfg(feature = "d3d12-debug")]
    debug_layer::before_create_device();
    let result = unsafe { original(adapter, feature_level, riid, device) };
    // A null out pointer is the "is this adapter supported" probe; there is no device to read.
    if result.is_ok() && !device.is_null() {
        let raw = unsafe { *device };
        if !raw.is_null() {
            // Before `hook_device`, so the messages its throwaway command lists raise are logged.
            #[cfg(feature = "d3d12-debug")]
            unsafe {
                debug_layer::watch_messages(raw)
            };
            unsafe { hook_device(raw) };
        }
    }
    result
}

unsafe fn hook_device(raw: *mut c_void) {
    let Some(unknown) = (unsafe { IUnknown::from_raw_borrowed(&raw) }) else {
        return;
    };
    if let Ok(device) = unknown.cast::<ID3D12Device>() {
        let vtable = device.vtable();
        report(detour(
            "CreateRootSignature",
            vtable.CreateRootSignature as usize,
            create_root_signature_hook as *const () as usize,
            &CREATE_ROOT_SIGNATURE,
        ));
        report(detour(
            "CreateGraphicsPipelineState",
            vtable.CreateGraphicsPipelineState as usize,
            create_graphics_hook as *const () as usize,
            &CREATE_GRAPHICS,
        ));
        report(detour(
            "CreateComputePipelineState",
            vtable.CreateComputePipelineState as usize,
            create_compute_hook as *const () as usize,
            &CREATE_COMPUTE,
        ));
        unsafe { hook_command_lists(&device) };
        // After the throwaway lists above, so their creation does not pass through it.
        report(detour(
            "CreateCommandList",
            vtable.CreateCommandList as usize,
            cmdlist::create_command_list_hook as *const () as usize,
            &cmdlist::CREATE_COMMAND_LIST,
        ));
    }
    if let Ok(device) = unknown.cast::<ID3D12Device1>() {
        report(detour(
            "CreatePipelineLibrary",
            device.vtable().CreatePipelineLibrary as usize,
            create_library_hook as *const () as usize,
            &CREATE_LIBRARY,
        ));
    }
    if let Ok(device) = unknown.cast::<ID3D12Device2>() {
        report(detour(
            "CreatePipelineState",
            device.vtable().CreatePipelineState as usize,
            create_stream_hook as *const () as usize,
            &CREATE_STREAM,
        ));
    }
}

/// Detour `SetGraphicsRootSignature` for direct command lists and for bundles, and the methods
/// [`crate::cmdlist`] needs off the direct list, read off the vtables of one throwaway list of each
/// kind. Created and closed here, before the game has the device, so nothing the game records is
/// touched; both lists are closed before the recording methods are detoured, so neither `Close`
/// passes through [`crate::cmdlist`].
///
/// The recording methods are read off the direct list only: every command-list vtable the census
/// found shares their addresses, and the game creates no bundle allocator.
unsafe fn hook_command_lists(device: &ID3D12Device) {
    let kinds: [(&str, D3D12_COMMAND_LIST_TYPE, usize); 2] = [
        (
            "SetGraphicsRootSignature (direct)",
            D3D12_COMMAND_LIST_TYPE_DIRECT,
            look_binding::set_root_signature_hook::<0> as *const () as usize,
        ),
        (
            "SetGraphicsRootSignature (bundle)",
            D3D12_COMMAND_LIST_TYPE_BUNDLE,
            look_binding::set_root_signature_hook::<1> as *const () as usize,
        ),
    ];
    let mut recording = None;
    for (slot, (what, kind, hook)) in kinds.into_iter().enumerate() {
        let allocator: ID3D12CommandAllocator = match unsafe { device.CreateCommandAllocator(kind) }
        {
            Ok(allocator) => allocator,
            Err(error) => {
                log(format_args!("{what}: no command allocator: {error}"));
                continue;
            }
        };
        let list: ID3D12GraphicsCommandList = match unsafe {
            device.CreateCommandList(0, kind, &allocator, None::<&ID3D12PipelineState>)
        } {
            Ok(list) => list,
            Err(error) => {
                log(format_args!("{what}: no command list: {error}"));
                continue;
            }
        };
        let vtable = list.vtable();
        let target = vtable.SetGraphicsRootSignature as usize;
        if kind == D3D12_COMMAND_LIST_TYPE_DIRECT {
            let methods: [(&str, usize, usize, &AtomicUsize); 4] = [
                (
                    "SetPipelineState",
                    vtable.SetPipelineState as usize,
                    cmdlist::set_pipeline_state_hook as *const () as usize,
                    &cmdlist::SET_PIPELINE_STATE,
                ),
                (
                    "Reset",
                    vtable.Reset as usize,
                    cmdlist::reset_hook as *const () as usize,
                    &cmdlist::RESET,
                ),
                (
                    "ClearState",
                    vtable.ClearState as usize,
                    cmdlist::clear_state_hook as *const () as usize,
                    &cmdlist::CLEAR_STATE,
                ),
                (
                    "Close",
                    vtable.Close as usize,
                    cmdlist::close_hook as *const () as usize,
                    &cmdlist::CLOSE,
                ),
            ];
            recording = Some(methods);
        }
        let _ = unsafe { list.Close() };
        report(detour(what, target, hook, &SET_ROOT_SIGNATURE[slot]));
    }
    // Once both throwaway lists are closed, so neither `Close` passes through the new detour.
    for (what, target, hook, original) in recording.into_iter().flatten() {
        report(detour(what, target, hook, original));
    }
}

unsafe extern "system" fn create_root_signature_hook(
    device: *mut c_void,
    node_mask: u32,
    blob: *const c_void,
    length: usize,
    riid: *const GUID,
    out: *mut *mut c_void,
) -> HRESULT {
    let original: CreateRootSignatureFn =
        unsafe { std::mem::transmute(CREATE_ROOT_SIGNATURE.load(Ordering::SeqCst)) };
    let created = |out: *mut *mut c_void| -> usize {
        if out.is_null() || unsafe { *out }.is_null() {
            0
        } else {
            unsafe { look_binding::root_signature_pointer(*out) }
        }
    };
    if let Some(handle) = unsafe { ID3D12Device::from_raw_borrowed(&device) } {
        match unsafe { look_binding::augment(handle, blob, length) } {
            Ok(augmented) => {
                let result = unsafe {
                    original(
                        device,
                        node_mask,
                        augmented.blob.as_ptr().cast(),
                        augmented.blob.len(),
                        riid,
                        out,
                    )
                };
                if result.is_ok() {
                    look_binding::record(created(out), Some(&augmented));
                    return result;
                }
                look_binding::note_skip(&Skip::Failed(format!(
                    "the runtime refused the augmented blob: hr=0x{:08x}",
                    result.0 as u32
                )));
            }
            Err(skip) => look_binding::note_skip(&skip),
        }
    }
    let result = unsafe { original(device, node_mask, blob, length, riid, out) };
    if result.is_ok() {
        look_binding::record(created(out), None);
    }
    result
}

unsafe extern "system" fn create_library_hook(
    device: *mut c_void,
    blob: *const c_void,
    length: usize,
    riid: *const GUID,
    library: *mut *mut c_void,
) -> HRESULT {
    let original: CreateLibraryFn =
        unsafe { std::mem::transmute(CREATE_LIBRARY.load(Ordering::SeqCst)) };
    let result = unsafe { original(device, blob, length, riid, library) };
    if result.is_ok() && !library.is_null() {
        let raw = unsafe { *library };
        if let Some(unknown) = unsafe { IUnknown::from_raw_borrowed(&raw) } {
            if let Ok(library) = unknown.cast::<ID3D12PipelineLibrary>() {
                report(detour(
                    "LoadGraphicsPipeline",
                    library.vtable().LoadGraphicsPipeline as usize,
                    load_graphics_hook as *const () as usize,
                    &LOAD_GRAPHICS,
                ));
            }
            if let Ok(library) = unknown.cast::<ID3D12PipelineLibrary1>() {
                report(detour(
                    "LoadPipeline",
                    library.vtable().LoadPipeline as usize,
                    load_stream_hook as *const () as usize,
                    &LOAD_STREAM,
                ));
            }
        }
    }
    result
}

/// A shader's bytes, when the description names one.
unsafe fn bytecode<'a>(shader: &D3D12_SHADER_BYTECODE) -> Option<&'a [u8]> {
    if shader.pShaderBytecode.is_null() || shader.BytecodeLength == 0 {
        return None;
    }
    Some(unsafe {
        std::slice::from_raw_parts(shader.pShaderBytecode.cast::<u8>(), shader.BytecodeLength)
    })
}

/// The augmented root signature serialized into the first of these shaders that carries one,
/// created on `device`: the device the pipeline is being created on, which the root signature must
/// belong to.
unsafe fn root_signature_from_shaders<'a>(
    device: &ID3D12Device,
    shaders: impl IntoIterator<Item = &'a D3D12_SHADER_BYTECODE>,
) -> Option<usize> {
    shaders.into_iter().find_map(|shader| {
        let bytes = unsafe { bytecode(shader) }?;
        unsafe { look_binding::augmented_from_shader(device, bytes) }
    })
}

/// Log, once per pass and reason, that a port was left vanilla, and count it.
fn left_vanilla(replacement: &Replacement, reason: &'static str, detail: std::fmt::Arguments<'_>) {
    PORTS_LEFT_VANILLA.fetch_add(1, Ordering::Relaxed);
    let mut logged = LOGGED_VANILLA
        .lock()
        .unwrap_or_else(PoisonError::into_inner);
    if logged.contains(&(replacement.name, reason)) {
        return;
    }
    logged.push((replacement.name, reason));
    log(format_args!(
        "{} (kind {}) left vanilla: {reason}: {detail}",
        replacement.name, replacement.kind as u8
    ));
}

/// A variant reads parameters A to C, so its root signature must carry the whole ABI, and the probe
/// it writes must exist; a port on anything less stays the game's own pipeline.
fn root_signature_is_full(root_signature: usize, replacement: &Replacement, path: &str) -> bool {
    let level = look_binding::binding(root_signature).map_or(Level::None, |b| b.level);
    if level != Level::Full {
        left_vanilla(
            replacement,
            "its root signature is below full",
            format_args!(
                "root signature 0x{root_signature:x} at level {} via {path}",
                level.name()
            ),
        );
        return false;
    }
    if crate::probe::probe_address() == 0 {
        left_vanilla(
            replacement,
            "the probe does not exist",
            format_args!("via {path}"),
        );
        return false;
    }
    true
}

fn count_no_root_signature(fixed: bool) {
    let counter = if fixed {
        &NO_ROOT_SIGNATURE_FIXED
    } else {
        &NO_ROOT_SIGNATURE_LEFT
    };
    if counter.fetch_add(1, Ordering::Relaxed) == 0 {
        log(format_args!(
            "first pipeline with no root signature: {}",
            if fixed {
                "given the augmented one from its shaders"
            } else {
                "none of its shaders carries one; left as is"
            }
        ));
    }
}

fn raw_root_signature(field: &ManuallyDrop<Option<ID3D12RootSignature>>) -> usize {
    field.as_ref().map_or(0, |rs| rs.as_raw() as usize)
}

/// A borrowed root signature in a description field. `ManuallyDrop`, so the copy holding it never
/// releases a reference it did not take.
fn borrowed_root_signature(raw: usize) -> ManuallyDrop<Option<ID3D12RootSignature>> {
    ManuallyDrop::new(Some(unsafe {
        ID3D12RootSignature::from_raw(raw as *mut c_void)
    }))
}

/// The device a creation hook was called on, borrowed for the length of the call. Every device
/// interface is single inheritance from `ID3D12Device`, so the pointer any of them is called with
/// is also a valid `ID3D12Device*`.
fn calling_device(device: &*mut c_void) -> Option<&ID3D12Device> {
    unsafe { ID3D12Device::from_raw_borrowed(device) }
}

/// A copy of a graphics description with its missing root signature filled in; `None` when it
/// names one, or when none of its shaders carries one either. A root signature that has to be made
/// is made on `device`, the device the pipeline is being created on.
unsafe fn fill_graphics(
    device: &ID3D12Device,
    desc: *const D3D12_GRAPHICS_PIPELINE_STATE_DESC,
) -> Option<D3D12_GRAPHICS_PIPELINE_STATE_DESC> {
    if desc.is_null() {
        return None;
    }
    let original = unsafe { &*desc };
    if raw_root_signature(&original.pRootSignature) != 0 {
        return None;
    }
    let shaders = [
        &original.VS,
        &original.PS,
        &original.DS,
        &original.HS,
        &original.GS,
    ];
    let Some(found) = (unsafe { root_signature_from_shaders(device, shaders) }) else {
        count_no_root_signature(false);
        return None;
    };
    count_no_root_signature(true);
    // A bitwise copy: the root signature is a `ManuallyDrop` field, so the copy takes no
    // reference and releases none.
    let mut copy = unsafe { std::ptr::read(desc) };
    copy.pRootSignature = borrowed_root_signature(found);
    Some(copy)
}

/// The compute version of [`fill_graphics`].
unsafe fn fill_compute(
    device: &ID3D12Device,
    desc: *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
) -> Option<D3D12_COMPUTE_PIPELINE_STATE_DESC> {
    if desc.is_null() {
        return None;
    }
    let original = unsafe { &*desc };
    if raw_root_signature(&original.pRootSignature) != 0 {
        return None;
    }
    let Some(found) = (unsafe { root_signature_from_shaders(device, [&original.CS]) }) else {
        count_no_root_signature(false);
        return None;
    };
    count_no_root_signature(true);
    let mut copy = unsafe { std::ptr::read(desc) };
    copy.pRootSignature = borrowed_root_signature(found);
    Some(copy)
}

/// A runtime that refuses a description with its root signature filled in gets the game's own
/// instead, so the worst a stricter driver than vkd3d-proton can do is leave the pipeline as the
/// game described it.
fn note_refused(path: &'static str, result: HRESULT, root_signature: usize) {
    if REWRITES_REFUSED.fetch_add(1, Ordering::Relaxed) < 8 {
        log(format_args!(
            "pipeline with its root signature filled in refused via {path}: hr=0x{:08x} root signature 0x{root_signature:x}; created as the game described it",
            result.0 as u32
        ));
    }
}

/// The `ID3D12PipelineState` pointer for whatever interface a creation returned: the pointer
/// `SetPipelineState` will be called with, and so the redirect table's key.
///
/// # Safety
///
/// `raw` must be a live COM object.
unsafe fn pipeline_state_pointer(raw: *mut c_void) -> usize {
    let Some(unknown) = (unsafe { IUnknown::from_raw_borrowed(&raw) }) else {
        return 0;
    };
    // The cast takes a reference and the drop releases it, so the count is unchanged.
    unknown
        .cast::<ID3D12PipelineState>()
        .map_or(0, |pso| pso.as_raw() as usize)
}

/// Create the cel variant of a recorded port: the record's stream with `ps` as its pixel shader,
/// through the `CreatePipelineState` trampoline on `device`. Returns the variant as a raw
/// `ID3D12PipelineState*` holding one reference.
///
/// # Safety
///
/// `device` must be a live `ID3D12Device2`, the one the record's root signature was created on.
pub(crate) unsafe fn create_variant(
    device: usize,
    record: &PipelineRecord,
    ps: &[u8],
) -> Result<usize, HRESULT> {
    let original = CREATE_STREAM.load(Ordering::SeqCst);
    if device == 0 || original == 0 {
        return Err(E_NOINTERFACE);
    }
    // SAFETY: the trampoline MinHook returned for `ID3D12Device2::CreatePipelineState`.
    let original: CreateStreamFn = unsafe { std::mem::transmute(original) };
    let stream = record.with_ps(ps).ok_or(E_INVALIDARG)?;
    let desc = D3D12_PIPELINE_STATE_STREAM_DESC {
        SizeInBytes: stream.size(),
        pPipelineStateSubobjectStream: stream.pointer().cast_mut().cast(),
    };
    let mut created: *mut c_void = std::ptr::null_mut();
    let result = unsafe {
        original(
            device as *mut c_void,
            &desc,
            &ID3D12PipelineState::IID,
            &mut created,
        )
    };
    if result != S_OK {
        return Err(result);
    }
    if created.is_null() {
        return Err(E_INVALIDARG);
    }
    Ok(created as usize)
}

/// What the game created, which description it was created from, and where.
struct Created<'a> {
    /// The device the variant is created on, as an `ID3D12Device2`.
    device: usize,
    /// The creation's out parameter.
    pipeline: *mut *mut c_void,
    /// The stream the game's pipeline was created from.
    stream: &'a [u8],
    /// Its root signature, as the binding table knows it.
    root_signature: usize,
    path: &'static str,
    /// Pipelines seen on this path so far, for the log.
    seen: u32,
}

/// The game's pipeline exists and its pixel shader is `replacement`'s pass: record it, create its
/// cel variant, and publish the pair. Anything that goes wrong leaves the pass vanilla; a panic is
/// caught here, so it can never unwind into the game's creation call.
fn adopt(replacement: &'static Replacement, created: &Created<'_>) {
    let outcome = catch_unwind(AssertUnwindSafe(|| unsafe {
        adopt_inner(replacement, created)
    }));
    if outcome.is_err() {
        left_vanilla(
            replacement,
            "a panic while creating its variant",
            format_args!("via {}", created.path),
        );
    }
}

unsafe fn adopt_inner(replacement: &'static Replacement, created: &Created<'_>) {
    if created.pipeline.is_null() || unsafe { *created.pipeline }.is_null() {
        return;
    }
    let game = unsafe { pipeline_state_pointer(*created.pipeline) };
    if game == 0 {
        left_vanilla(
            replacement,
            "the created object is not an ID3D12PipelineState",
            format_args!("via {}", created.path),
        );
        return;
    }
    if !root_signature_is_full(created.root_signature, replacement, created.path) {
        return;
    }
    let record = match unsafe { record::capture(created.stream, &subobject_payload) } {
        Ok(record) => record,
        Err(error) => {
            left_vanilla(
                replacement,
                "its description could not be copied",
                format_args!("{error} via {}", created.path),
            );
            return;
        }
    };
    let kind = replacement.kind;
    // Timed because it runs synchronously on the game's creating thread (design 11.10).
    let started = std::time::Instant::now();
    let created_variant = unsafe { create_variant(created.device, &record, replacement.dxil) };
    let create_ms = crate::exports::note_create(started.elapsed());
    let variant = match created_variant {
        Ok(variant) => variant,
        Err(refused) => {
            let count = REWRITES_REFUSED.fetch_add(1, Ordering::Relaxed);
            if count < 8 {
                log(format_args!(
                    "variant refused kind={} game=0x{game:x} hr=0x{:08x} name={} via={}; the pass \
                     stays vanilla",
                    kind as u8, refused.0 as u32, replacement.name, created.path
                ));
            }
            return;
        }
    };
    // SAFETY: the record's root signature is the one the game's pipeline was just created with.
    let port = unsafe { PortRecord::new(game, kind, record) };
    record::adopt(port, || {
        // The table takes over the variant's reference and takes its own on `game`.
        table::update(|builder| {
            builder.insert(EntrySpec::port(game, variant, kind as u8));
        });
    });
    // The bind hooks skip the table until some entry has a variant.
    cmdlist::refresh_active();
    PORTS_READY[kind as usize].fetch_add(1, Ordering::Relaxed);
    if VARIANTS.fetch_add(1, Ordering::Relaxed) < VARIANT_LOG_LIMIT {
        log(format_args!(
            "variant ready kind={} game=0x{game:x} variant=0x{variant:x} hr=0x00000000 name={} \
             via={} pipelines_seen_on_this_path={} create_ms={create_ms:.2}",
            kind as u8, replacement.name, created.path, created.seen
        ));
    }
}

unsafe extern "system" fn create_graphics_hook(
    device: *mut c_void,
    desc: *const D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: CreateGraphicsFn =
        unsafe { std::mem::transmute(CREATE_GRAPHICS.load(Ordering::SeqCst)) };
    let seen = SEEN_GRAPHICS.fetch_add(1, Ordering::Relaxed) + 1;
    const PATH: &str = "CreateGraphicsPipelineState";
    let mut result = None;
    if let Some(copy) = calling_device(&device).and_then(|d| unsafe { fill_graphics(d, desc) }) {
        let filled = unsafe { original(device, &copy, riid, pipeline) };
        if filled.is_ok() {
            result = Some(filled);
        } else {
            note_refused(PATH, filled, raw_root_signature(&copy.pRootSignature));
        }
    }
    let result = result.unwrap_or_else(|| unsafe { original(device, desc, riid, pipeline) });
    // This path has never been seen creating a port, so one arriving here is noted and left as the
    // game's own pipeline rather than recorded.
    if result == S_OK
        && let Some(replacement) = (unsafe { desc.as_ref() })
            .and_then(|d| unsafe { bytecode(&d.PS) }.and_then(replace::find))
    {
        left_vanilla(
            replacement,
            "it arrived through CreateGraphicsPipelineState",
            format_args!("pipelines_seen_on_this_path={seen}"),
        );
    }
    result
}

unsafe extern "system" fn create_compute_hook(
    device: *mut c_void,
    desc: *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: CreateComputeFn =
        unsafe { std::mem::transmute(CREATE_COMPUTE.load(Ordering::SeqCst)) };
    if let Some(copy) = calling_device(&device).and_then(|d| unsafe { fill_compute(d, desc) }) {
        let result = unsafe { original(device, &copy, riid, pipeline) };
        if result.is_ok() {
            return result;
        }
        note_refused(
            "CreateComputePipelineState",
            result,
            raw_root_signature(&copy.pRootSignature),
        );
    }
    unsafe { original(device, desc, riid, pipeline) }
}

/// Append one field of a graphics description as a stream subobject of type `ty`. `None` when the
/// layout table disagrees with the field's size, which would make the stream unreadable.
fn push_field<T>(
    stream: &mut Vec<u8>,
    ty: D3D12_PIPELINE_STATE_SUBOBJECT_TYPE,
    field: &T,
) -> Option<()> {
    let ty = u32::try_from(ty.0).ok()?;
    let payload = subobject_payload(ty).filter(|payload| payload.size == size_of::<T>())?;
    // SAFETY: a live `T`, read as its bytes.
    let bytes =
        unsafe { std::slice::from_raw_parts((field as *const T).cast::<u8>(), size_of::<T>()) };
    replace::push_subobject(stream, ty, bytes, payload.align);
    Some(())
}

/// A graphics description as the equivalent pipeline state stream, one subobject per field as
/// d3dx12's `CD3DX12_PIPELINE_STATE_STREAM` builds it, so a pipeline loaded through
/// `LoadGraphicsPipeline` is recorded like any other. Its pointers still aim at the game's memory;
/// the record copies what they aim at.
fn graphics_stream(desc: &D3D12_GRAPHICS_PIPELINE_STATE_DESC) -> Option<Vec<u8>> {
    let root_signature = raw_root_signature(&desc.pRootSignature);
    let formats = D3D12_RT_FORMAT_ARRAY {
        RTFormats: desc.RTVFormats,
        NumRenderTargets: desc.NumRenderTargets,
    };
    let mut stream = Vec::with_capacity(1024);
    let s = &mut stream;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE,
        &root_signature,
    )?;
    push_field(s, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_VS, &desc.VS)?;
    push_field(s, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS, &desc.PS)?;
    push_field(s, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DS, &desc.DS)?;
    push_field(s, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_HS, &desc.HS)?;
    push_field(s, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_GS, &desc.GS)?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_STREAM_OUTPUT,
        &desc.StreamOutput,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_BLEND,
        &desc.BlendState,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_SAMPLE_MASK,
        &desc.SampleMask,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RASTERIZER,
        &desc.RasterizerState,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL,
        &desc.DepthStencilState,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_INPUT_LAYOUT,
        &desc.InputLayout,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_IB_STRIP_CUT_VALUE,
        &desc.IBStripCutValue,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PRIMITIVE_TOPOLOGY,
        &desc.PrimitiveTopologyType,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RENDER_TARGET_FORMATS,
        &formats,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL_FORMAT,
        &desc.DSVFormat,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_SAMPLE_DESC,
        &desc.SampleDesc,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_NODE_MASK,
        &desc.NodeMask,
    )?;
    push_field(
        s,
        D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_CACHED_PSO,
        &desc.CachedPSO,
    )?;
    push_field(s, D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_FLAGS, &desc.Flags)?;
    Some(stream)
}

/// The device a pipeline library belongs to, as the `ID3D12Device2` a variant is created on,
/// holding one reference for as long as the caller keeps it. A library is a device child and
/// names its own device, which is the one its pipelines and their root signatures belong to.
///
/// # Safety
///
/// `library` must be a live `ID3D12PipelineLibrary`.
unsafe fn device_of_library(library: &*mut c_void) -> Option<ID3D12Device2> {
    let library = unsafe { ID3D12PipelineLibrary::from_raw_borrowed(library) }?;
    let mut device: Option<ID3D12Device2> = None;
    unsafe { library.GetDevice(&mut device) }.ok()?;
    device
}

unsafe extern "system" fn load_graphics_hook(
    library: *mut c_void,
    name: PCWSTR,
    desc: *const D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: LoadGraphicsFn =
        unsafe { std::mem::transmute(LOAD_GRAPHICS.load(Ordering::SeqCst)) };
    let seen = SEEN_LIBRARY.fetch_add(1, Ordering::Relaxed) + 1;
    // Forwarded unchanged: a library entry is only returned for the very description it was stored
    // under, which is the game's own. Only a warm library on native Windows says `S_OK`.
    let result = unsafe { original(library, name, desc, riid, pipeline) };
    if result != S_OK {
        return result;
    }
    let Some(desc) = (unsafe { desc.as_ref() }) else {
        return result;
    };
    let Some(replacement) = (unsafe { bytecode(&desc.PS) }).and_then(replace::find) else {
        return result;
    };
    const PATH: &str = "LoadGraphicsPipeline";
    let Some(stream) = graphics_stream(desc) else {
        left_vanilla(
            replacement,
            "its description could not be written as a stream",
            format_args!("via {PATH}"),
        );
        return result;
    };
    // Held until `adopt` returns, so the variant is created on a device that is alive.
    let Some(device) = (unsafe { device_of_library(&library) }) else {
        left_vanilla(
            replacement,
            "its library's device could not be read",
            format_args!("via {PATH}"),
        );
        return result;
    };
    adopt(
        replacement,
        &Created {
            device: device.as_raw() as usize,
            pipeline,
            stream: &stream,
            root_signature: raw_root_signature(&desc.pRootSignature),
            path: PATH,
            seen,
        },
    );
    result
}

fn payload_of<T>() -> Payload {
    Payload {
        size: size_of::<T>(),
        align: align_of::<T>(),
    }
}

/// Payload layout of each `D3D12_PIPELINE_STATE_SUBOBJECT_TYPE`, taken from the `windows` crate's
/// own definitions rather than written out by hand.
fn subobject_payload(ty: u32) -> Option<Payload> {
    Some(match ty {
        // ROOT_SIGNATURE: an `ID3D12RootSignature*`.
        0 => payload_of::<usize>(),
        // `VS`, `PS`, `DS`, `HS`, `GS`, `CS`, `AS`, `MS`.
        1..=6 | 24 | 25 => payload_of::<D3D12_SHADER_BYTECODE>(),
        7 => payload_of::<D3D12_STREAM_OUTPUT_DESC>(),
        8 => payload_of::<D3D12_BLEND_DESC>(),
        // SAMPLE_MASK, NODE_MASK.
        9 | 18 => payload_of::<u32>(),
        10 => payload_of::<D3D12_RASTERIZER_DESC>(),
        11 => payload_of::<D3D12_DEPTH_STENCIL_DESC>(),
        12 => payload_of::<D3D12_INPUT_LAYOUT_DESC>(),
        13 => payload_of::<D3D12_INDEX_BUFFER_STRIP_CUT_VALUE>(),
        14 => payload_of::<D3D12_PRIMITIVE_TOPOLOGY_TYPE>(),
        15 => payload_of::<D3D12_RT_FORMAT_ARRAY>(),
        16 => payload_of::<DXGI_FORMAT>(),
        17 => payload_of::<DXGI_SAMPLE_DESC>(),
        19 => payload_of::<D3D12_CACHED_PIPELINE_STATE>(),
        20 => payload_of::<D3D12_PIPELINE_STATE_FLAGS>(),
        21 => payload_of::<D3D12_DEPTH_STENCIL_DESC1>(),
        22 => payload_of::<D3D12_VIEW_INSTANCING_DESC>(),
        26 => payload_of::<D3D12_DEPTH_STENCIL_DESC2>(),
        27 => payload_of::<D3D12_RASTERIZER_DESC1>(),
        28 => payload_of::<D3D12_RASTERIZER_DESC2>(),
        _ => return None,
    })
}

/// A pipeline state stream the game passed, walked.
struct Walked<'a> {
    stream: &'a [u8],
    all: Vec<Subobject>,
    /// Payload offset of the root signature subobject, if there is one.
    root_at: Option<usize>,
    root_signature: usize,
}

impl Walked<'_> {
    fn shader(&self, at: usize) -> D3D12_SHADER_BYTECODE {
        unsafe { std::ptr::read_unaligned(self.stream.as_ptr().add(at).cast()) }
    }

    /// The pass whose port this stream's pixel shader is, if it is one.
    fn port(&self) -> Option<&'static Replacement> {
        let at = self.all.iter().find(|s| s.ty == SUBOBJECT_PS)?.offset;
        let ps = self.shader(at);
        unsafe { bytecode(&ps) }.and_then(replace::find)
    }
}

/// Walk the stream a description names. `None` for no stream, or one the walker cannot read, which
/// is passed through unchanged and logged.
unsafe fn walk_stream<'a>(desc: *const D3D12_PIPELINE_STATE_STREAM_DESC) -> Option<Walked<'a>> {
    if desc.is_null() {
        return None;
    }
    let (start, length) = unsafe { ((*desc).pPipelineStateSubobjectStream, (*desc).SizeInBytes) };
    if start.is_null() || length == 0 {
        return None;
    }
    let stream = unsafe { std::slice::from_raw_parts(start.cast::<u8>(), length) };
    let all = match replace::subobjects(stream, &subobject_payload) {
        Ok(all) => all,
        Err(walk) => {
            log(format_args!(
                "pipeline stream not walked ({walk:?}); passed through unchanged"
            ));
            return None;
        }
    };
    let root_at = all
        .iter()
        .find(|s| s.ty == SUBOBJECT_ROOT_SIGNATURE)
        .map(|s| s.offset);
    let root_signature = root_at.map_or(0, |at| unsafe {
        std::ptr::read_unaligned(stream.as_ptr().add(at).cast::<usize>())
    });
    Some(Walked {
        stream,
        all,
        root_at,
        root_signature,
    })
}

/// A pointer-aligned copy of a stream with its missing root signature filled in, its size, and the
/// root signature; `None` when it names one, or when none of its shaders carries one either. A
/// root signature that has to be made is made on `device`, the device creating the pipeline.
unsafe fn fill_stream(
    device: &ID3D12Device,
    walked: &Walked<'_>,
) -> Option<(Vec<u64>, usize, usize)> {
    if walked.root_signature != 0 {
        return None;
    }
    let shaders: Vec<D3D12_SHADER_BYTECODE> = SHADER_SUBOBJECTS
        .iter()
        .filter_map(|ty| walked.all.iter().find(|s| s.ty == *ty))
        .map(|s| walked.shader(s.offset))
        .collect();
    let Some(root_signature) = (unsafe { root_signature_from_shaders(device, shaders.iter()) })
    else {
        count_no_root_signature(false);
        return None;
    };
    count_no_root_signature(true);

    // Room for one appended root signature subobject: its type, padding to pointer alignment, and
    // the pointer.
    const APPENDED: usize = 16;
    let length = walked.stream.len();
    let mut copy = vec![0_u64; (length + APPENDED).div_ceil(8)];
    let bytes = copy.as_mut_ptr().cast::<u8>();
    let mut size = length;
    unsafe { std::ptr::copy_nonoverlapping(walked.stream.as_ptr(), bytes, length) };
    match walked.root_at {
        Some(at) => unsafe {
            std::ptr::write_unaligned(bytes.add(at).cast::<usize>(), root_signature);
        },
        None => {
            let at = length.div_ceil(8) * 8;
            unsafe {
                std::ptr::write_unaligned(bytes.add(at).cast::<u32>(), SUBOBJECT_ROOT_SIGNATURE);
                std::ptr::write_unaligned(bytes.add(at + 8).cast::<usize>(), root_signature);
            }
            size = at + APPENDED;
        }
    }
    Some((copy, size, root_signature))
}

unsafe extern "system" fn create_stream_hook(
    device: *mut c_void,
    desc: *const D3D12_PIPELINE_STATE_STREAM_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: CreateStreamFn =
        unsafe { std::mem::transmute(CREATE_STREAM.load(Ordering::SeqCst)) };
    let seen = SEEN_STREAM.fetch_add(1, Ordering::Relaxed) + 1;
    const PATH: &str = "CreatePipelineState";
    let Some(walked) = (unsafe { walk_stream(desc) }) else {
        return unsafe { original(device, desc, riid, pipeline) };
    };
    let port = walked.port();
    let with_root_signature =
        calling_device(&device).and_then(|d| unsafe { fill_stream(d, &walked) });
    if let Some((mut copy, size, root_signature)) = with_root_signature {
        let patched = D3D12_PIPELINE_STATE_STREAM_DESC {
            SizeInBytes: size,
            pPipelineStateSubobjectStream: copy.as_mut_ptr().cast(),
        };
        let result = unsafe { original(device, &patched, riid, pipeline) };
        if result.is_ok() {
            if let Some(replacement) = port
                && result == S_OK
            {
                // SAFETY: `copy` holds `size` initialized bytes.
                let filled =
                    unsafe { std::slice::from_raw_parts(copy.as_ptr().cast::<u8>(), size) };
                adopt(
                    replacement,
                    &Created {
                        device: device as usize,
                        pipeline,
                        stream: filled,
                        root_signature,
                        path: PATH,
                        seen,
                    },
                );
            }
            return result;
        }
        note_refused(PATH, result, root_signature);
    }
    let result = unsafe { original(device, desc, riid, pipeline) };
    if let Some(replacement) = port
        && result == S_OK
    {
        adopt(
            replacement,
            &Created {
                device: device as usize,
                pipeline,
                stream: walked.stream,
                root_signature: walked.root_signature,
                path: PATH,
                seen,
            },
        );
    }
    result
}

unsafe extern "system" fn load_stream_hook(
    library: *mut c_void,
    name: PCWSTR,
    desc: *const D3D12_PIPELINE_STATE_STREAM_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: LoadStreamFn = unsafe { std::mem::transmute(LOAD_STREAM.load(Ordering::SeqCst)) };
    let seen = SEEN_LIBRARY.fetch_add(1, Ordering::Relaxed) + 1;
    // Forwarded unchanged, like `load_graphics_hook`.
    let result = unsafe { original(library, name, desc, riid, pipeline) };
    if result != S_OK {
        return result;
    }
    let Some(walked) = (unsafe { walk_stream(desc) }) else {
        return result;
    };
    if let Some(replacement) = walked.port() {
        const PATH: &str = "LoadPipeline";
        // Held until `adopt` returns, as in `load_graphics_hook`.
        let Some(device) = (unsafe { device_of_library(&library) }) else {
            left_vanilla(
                replacement,
                "its library's device could not be read",
                format_args!("via {PATH}"),
            );
            return result;
        };
        adopt(
            replacement,
            &Created {
                device: device.as_raw() as usize,
                pipeline,
                stream: walked.stream,
                root_signature: walked.root_signature,
                path: PATH,
                seen,
            },
        );
    }
    result
}

/// The `d3d12-debug` development build, for the native Windows gate W1 (design section 13): D3D12's
/// debug layer with GPU-based validation, and DRED, switched on before the game's device exists,
/// and every message the debug layer raises written to `er-cel-shade.log`.
///
/// W1 passes on "zero info-queue errors", so this module says in the log whenever it cannot see
/// them: no debug interface, no `ID3D12InfoQueue1` on the device, or a refused registration. A
/// silent log from a build that never registered would otherwise read as a clean run.
#[cfg(feature = "d3d12-debug")]
mod debug_layer {
    use core::ffi::c_void;
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use std::sync::Once;
    use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};

    use er_game_base::mem::safe_read_cstr;
    use windows::Win32::Foundation::E_POINTER;
    use windows::Win32::Graphics::Direct3D12::{
        D3D12_DRED_ENABLEMENT_FORCED_ON, D3D12_MESSAGE_CALLBACK_FLAG_NONE, D3D12_MESSAGE_CATEGORY,
        D3D12_MESSAGE_ID, D3D12_MESSAGE_SEVERITY, ID3D12Debug, ID3D12Debug1, ID3D12Device,
        ID3D12DeviceRemovedExtendedDataSettings, ID3D12InfoQueue, ID3D12InfoQueue1,
    };
    use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
    use windows::core::{GUID, HRESULT, IUnknown, Interface, PCSTR, s, w};

    use crate::log;

    type GetDebugInterfaceFn = unsafe extern "system" fn(*const GUID, *mut *mut c_void) -> HRESULT;
    type FarProc = unsafe extern "system" fn() -> isize;

    /// `DXGI_ERROR_SDK_COMPONENT_MISSING`: what `D3D12GetDebugInterface` returns for the debug layer
    /// on a Windows machine without the Graphics Tools optional feature.
    const SDK_COMPONENT_MISSING: HRESULT = HRESULT(0x887A_002D_u32 as i32);

    /// Private data set on a device once its messages are routed here. A device is then registered
    /// once however often `D3D12CreateDevice` hands it back (both runtimes keep one device per
    /// adapter), and a new device that lands at a released one's address is still registered,
    /// which a list of addresses would get wrong. That case is real: hudhook's `get_target_addrs`
    /// creates a device of its own through this detour to find the functions it hooks, and
    /// releases it on return, so the game's device can be the second one this detour sees.
    const WATCHED: GUID = GUID::from_u128(0x5f0c3a71_b2d4_4e86_9c1b_d7a0e4f26b38);

    static ENABLE: Once = Once::new();

    /// The longest message text read, terminator included. A debug-layer message is a sentence or a
    /// paragraph; a pointer with no terminator this far in is not one, and is logged as unreadable.
    const MESSAGE_BYTES: usize = 16 * 1024;

    /// Severity names, indexed by `D3D12_MESSAGE_SEVERITY`, with a last slot for a value outside it.
    const SEVERITIES: [&str; 6] = [
        "corruption",
        "error",
        "warning",
        "info",
        "message",
        "unknown",
    ];
    /// Category names, indexed by `D3D12_MESSAGE_CATEGORY`.
    const CATEGORIES: [&str; 11] = [
        "application_defined",
        "miscellaneous",
        "initialization",
        "cleanup",
        "compilation",
        "state_creation",
        "state_setting",
        "state_getting",
        "resource_manipulation",
        "execution",
        "shader",
    ];

    /// Messages raised per severity, in [`SEVERITIES`] order.
    static TOTALS: [AtomicU64; SEVERITIES.len()] = [const { AtomicU64::new(0) }; SEVERITIES.len()];

    /// Repeat counters per message id. `D3D12_MESSAGE_ID` ends at 1421 in the `windows` crate this
    /// builds against, so every id it knows has a slot of its own; a newer id past the table shares
    /// a slot with a lower one, which only merges their repeat counts.
    const ID_SLOTS: usize = 2048;
    static REPEATS: [AtomicU32; ID_SLOTS] = [const { AtomicU32::new(0) }; ID_SLOTS];

    /// Occurrences of one message id written in full. After that only the powers of two are, each
    /// with its count: GPU-based validation can raise one message on every draw, about 1600 a
    /// frame, and a log line per draw would put a file write under the log's lock on every
    /// recording thread. Every distinct message still reaches the log at least once.
    const WRITTEN_IN_FULL: u32 = 4;

    /// Switch on the debug layer, GPU-based validation and DRED, once per process, before the
    /// original `D3D12CreateDevice` runs. All three apply only to devices created afterwards, and
    /// switching the debug layer on once a device exists removes that device, so this is the
    /// first thing the detour does. It runs on the game's thread rather than in `DllMain`, because
    /// `D3D12GetDebugInterface` loads the debug layer's DLL.
    pub(super) fn before_create_device() {
        ENABLE.call_once(|| {
            if catch_unwind(|| unsafe { enable() }).is_err() {
                log(format_args!(
                    "d3d12-debug: a panic while switching the debug layer on; it may be off"
                ));
            }
        });
    }

    /// `D3D12GetDebugInterface`, read off the d3d12.dll the game already has loaded rather than
    /// imported, so a runtime without the export degrades to a log line instead of a DLL that
    /// fails to load.
    fn get_debug_interface() -> Option<GetDebugInterfaceFn> {
        let module = unsafe { GetModuleHandleW(w!("d3d12.dll")) }.ok()?;
        let export = unsafe { GetProcAddress(module, s!("D3D12GetDebugInterface")) }?;
        // SAFETY: `D3D12GetDebugInterface(REFIID, void**)` is the export's documented signature.
        Some(unsafe { std::mem::transmute::<FarProc, GetDebugInterfaceFn>(export) })
    }

    /// One debug interface of type `T`, holding the reference the call returned.
    ///
    /// # Safety
    ///
    /// `get` must be `D3D12GetDebugInterface`.
    unsafe fn debug_interface<T: Interface>(get: GetDebugInterfaceFn) -> Result<T, HRESULT> {
        let mut raw = core::ptr::null_mut();
        let result = unsafe { get(&T::IID, &mut raw) };
        if result.is_err() {
            return Err(result);
        }
        if raw.is_null() {
            return Err(E_POINTER);
        }
        // SAFETY: a `T` the runtime returned for `T::IID`, with one reference that is now ours.
        Ok(unsafe { T::from_raw(raw) })
    }

    unsafe fn enable() {
        let Some(get) = get_debug_interface() else {
            log(format_args!(
                "d3d12-debug: d3d12.dll has no D3D12GetDebugInterface export; the debug layer, \
                 GPU-based validation and DRED stay off"
            ));
            return;
        };
        match unsafe { debug_interface::<ID3D12Debug>(get) } {
            Ok(debug) => {
                unsafe { debug.EnableDebugLayer() };
                match debug.cast::<ID3D12Debug1>() {
                    Ok(debug1) => {
                        unsafe { debug1.SetEnableGPUBasedValidation(true) };
                        log(format_args!(
                            "d3d12-debug: debug layer on, GPU-based validation on"
                        ));
                    }
                    Err(error) => log(format_args!(
                        "d3d12-debug: debug layer on; GPU-based validation stays off, because \
                         there is no ID3D12Debug1 ({error})"
                    )),
                }
            }
            Err(result) => log(format_args!(
                "d3d12-debug: no ID3D12Debug (hr=0x{:08x}{}); the debug layer and GPU-based \
                 validation stay off",
                result.0 as u32,
                if result == SDK_COMPONENT_MISSING {
                    ": the Graphics Tools optional feature is not installed"
                } else {
                    ""
                }
            )),
        }
        match unsafe { debug_interface::<ID3D12DeviceRemovedExtendedDataSettings>(get) } {
            Ok(dred) => {
                unsafe {
                    dred.SetAutoBreadcrumbsEnablement(D3D12_DRED_ENABLEMENT_FORCED_ON);
                    dred.SetPageFaultEnablement(D3D12_DRED_ENABLEMENT_FORCED_ON);
                    dred.SetWatsonDumpEnablement(D3D12_DRED_ENABLEMENT_FORCED_ON);
                }
                log(format_args!(
                    "d3d12-debug: DRED auto-breadcrumbs, page-fault reporting and Watson dumps \
                     forced on"
                ));
            }
            Err(result) => log(format_args!(
                "d3d12-debug: no DRED settings interface (hr=0x{:08x}); DRED is left as the \
                 system has it",
                result.0 as u32
            )),
        }
    }

    /// Route the debug layer's messages for the device `D3D12CreateDevice` just returned into the
    /// log, unless this device's are already routed (see [`WATCHED`]).
    ///
    /// # Safety
    ///
    /// `raw` must be the live device the original `D3D12CreateDevice` returned.
    pub(super) unsafe fn watch_messages(raw: *mut c_void) {
        if catch_unwind(AssertUnwindSafe(|| unsafe { register(raw) })).is_err() {
            log(format_args!(
                "d3d12-debug: a panic while registering for the debug layer's messages; they may \
                 not be captured"
            ));
        }
    }

    unsafe fn register(raw: *mut c_void) {
        let Some(unknown) = (unsafe { IUnknown::from_raw_borrowed(&raw) }) else {
            return;
        };
        let Ok(device) = unknown.cast::<ID3D12Device>() else {
            return;
        };
        let mut cookie = 0_u32;
        let mut size = size_of::<u32>() as u32;
        let marked = unsafe {
            device.GetPrivateData(
                &WATCHED,
                &mut size,
                Some((&raw mut cookie).cast::<c_void>()),
            )
        };
        if marked.is_ok() {
            return;
        }
        let address = raw as usize;
        let queue = match unknown.cast::<ID3D12InfoQueue1>() {
            Ok(queue) => queue,
            Err(error) => {
                let why = if unknown.cast::<ID3D12InfoQueue>().is_ok() {
                    "the debug layer is on, but its info queue has no ID3D12InfoQueue1"
                } else {
                    "the device has no info queue, so the debug layer is not on"
                };
                log(format_args!(
                    "d3d12-debug: device 0x{address:x}: {why} ({error}); its messages are not \
                     captured"
                ));
                return;
            }
        };
        let registered = unsafe {
            queue.RegisterMessageCallback(
                Some(on_message),
                D3D12_MESSAGE_CALLBACK_FLAG_NONE,
                core::ptr::null_mut(),
                &mut cookie,
            )
        };
        if let Err(error) = registered {
            log(format_args!(
                "d3d12-debug: device 0x{address:x}: RegisterMessageCallback refused ({error}); its \
                 messages are not captured"
            ));
            return;
        }
        // A device that cannot keep the mark is registered again if it is handed back, which logs
        // its messages twice rather than not at all.
        let _ = unsafe {
            device.SetPrivateData(
                &WATCHED,
                size_of::<u32>() as u32,
                Some((&raw const cookie).cast::<c_void>()),
            )
        };
        log(format_args!(
            "d3d12-debug: device 0x{address:x}: the debug layer's messages go to this log \
             (callback cookie {cookie})"
        ));
    }

    /// The registered callback. The debug layer calls it on whichever thread raised the message,
    /// inside the D3D12 call that raised it, so nothing may unwind out of it.
    unsafe extern "system" fn on_message(
        category: D3D12_MESSAGE_CATEGORY,
        severity: D3D12_MESSAGE_SEVERITY,
        id: D3D12_MESSAGE_ID,
        description: PCSTR,
        _context: *mut c_void,
    ) {
        let _ = catch_unwind(AssertUnwindSafe(|| unsafe {
            note(category, severity, id, description)
        }));
    }

    unsafe fn note(
        category: D3D12_MESSAGE_CATEGORY,
        severity: D3D12_MESSAGE_SEVERITY,
        id: D3D12_MESSAGE_ID,
        description: PCSTR,
    ) {
        let slot = usize::try_from(severity.0)
            .ok()
            .filter(|&slot| slot < SEVERITIES.len() - 1)
            .unwrap_or(SEVERITIES.len() - 1);
        let total = TOTALS[slot].fetch_add(1, Ordering::Relaxed) + 1;
        let repeat = REPEATS[id.0 as u32 as usize % ID_SLOTS].fetch_add(1, Ordering::Relaxed) + 1;
        if repeat > WRITTEN_IN_FULL && !repeat.is_power_of_two() {
            return;
        }
        let category = usize::try_from(category.0)
            .ok()
            .and_then(|at| CATEGORIES.get(at))
            .copied()
            .unwrap_or("unknown");
        let read = if description.is_null() {
            Some(Vec::new())
        } else {
            // SAFETY: a fault-safe read; the debug layer's string is live for this call.
            unsafe { safe_read_cstr(description.0 as usize, MESSAGE_BYTES) }
        };
        let text: &[u8] = read
            .as_deref()
            .unwrap_or(b"(text unreadable, or no terminator within MESSAGE_BYTES)");
        log(format_args!(
            "d3d12-debug: severity={} id={} category={category} repeat={repeat} \
             severity_total={total}: {}",
            SEVERITIES[slot],
            id.0,
            Text(text)
        ));
    }

    /// A message's text: as written when it is UTF-8, escaped byte by byte when it is not.
    struct Text<'a>(&'a [u8]);

    impl std::fmt::Display for Text<'_> {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            match std::str::from_utf8(self.0) {
                Ok(text) => f.write_str(text),
                Err(_) => write!(f, "{}", self.0.escape_ascii()),
            }
        }
    }
}
