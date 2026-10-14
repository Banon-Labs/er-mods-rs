//! The D3D12 detours: one on `D3D12CreateDevice`, and from the device it returns, the entry points
//! a pixel shader or a root signature can reach the driver through.
//!
//! `D3D12CreateDevice` is the only export hooked at load. Everything after it is taken from the
//! vtable of the device the game actually created, so the same code serves the native runtime on
//! Windows and vkd3d-proton under Proton without knowing which one it is talking to.
//!
//! * `CreateRootSignature` appends the look's parameter ([`crate::look_binding`]).
//! * `SetGraphicsRootSignature`, on direct command lists and on bundles, binds the look after the
//!   game's call.
//! * The pipeline creators and the pipeline library's loaders swap the pixel shader of a
//!   [`crate::replace::REPLACEMENTS`] pass for ours, but only when the pipeline's root signature
//!   carries the look -- the replacement reads it, and a pipeline whose root signature lacks a
//!   binding its shader uses is refused by the runtime. They also give a pipeline created with no
//!   root signature the augmented one built from its own shaders, so it matches what the game
//!   binds for it.
//!
//! The game's own descriptions are never written to; every change is made to a copy.

use core::ffi::c_void;
use std::mem::{ManuallyDrop, align_of, size_of};
use std::sync::Mutex;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};

use er_hook::{MH_ApplyQueued, MH_Initialize, MH_STATUS, MhHook};
use windows::Win32::Graphics::Direct3D12::{
    D3D12_BLEND_DESC, D3D12_CACHED_PIPELINE_STATE, D3D12_COMMAND_LIST_TYPE,
    D3D12_COMMAND_LIST_TYPE_BUNDLE, D3D12_COMMAND_LIST_TYPE_DIRECT,
    D3D12_COMPUTE_PIPELINE_STATE_DESC, D3D12_DEPTH_STENCIL_DESC, D3D12_DEPTH_STENCIL_DESC1,
    D3D12_DEPTH_STENCIL_DESC2, D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    D3D12_INDEX_BUFFER_STRIP_CUT_VALUE, D3D12_INPUT_LAYOUT_DESC, D3D12_PIPELINE_STATE_FLAGS,
    D3D12_PIPELINE_STATE_STREAM_DESC, D3D12_PRIMITIVE_TOPOLOGY_TYPE, D3D12_RASTERIZER_DESC,
    D3D12_RASTERIZER_DESC1, D3D12_RASTERIZER_DESC2, D3D12_RT_FORMAT_ARRAY, D3D12_SHADER_BYTECODE,
    D3D12_STREAM_OUTPUT_DESC, D3D12_VIEW_INSTANCING_DESC, ID3D12CommandAllocator, ID3D12Device,
    ID3D12Device1, ID3D12Device2, ID3D12GraphicsCommandList, ID3D12PipelineLibrary,
    ID3D12PipelineLibrary1, ID3D12PipelineState, ID3D12RootSignature,
};
use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT, DXGI_SAMPLE_DESC};
use windows::Win32::System::LibraryLoader::{GetModuleHandleW, GetProcAddress};
use windows::core::{GUID, HRESULT, IUnknown, Interface, PCWSTR, s, w};

use crate::log;
use crate::look_binding::{self, LookBinding, SET_ROOT_SIGNATURE, Skip};
use crate::replace::{self, Payload, Replacement, SUBOBJECT_PS, SUBOBJECT_ROOT_SIGNATURE};

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
type LoadComputeFn = unsafe extern "system" fn(
    *mut c_void,
    PCWSTR,
    *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
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
static CREATE_STREAM: AtomicUsize = AtomicUsize::new(0);
static CREATE_LIBRARY: AtomicUsize = AtomicUsize::new(0);
static LOAD_GRAPHICS: AtomicUsize = AtomicUsize::new(0);
static LOAD_COMPUTE: AtomicUsize = AtomicUsize::new(0);
static LOAD_STREAM: AtomicUsize = AtomicUsize::new(0);

/// The game's device. A pipeline library's loader is handed the library, not the device, and a
/// root signature built for a pipeline that has none must be created on it. Both D3D12 and
/// vkd3d-proton return one device per adapter, so the first is the one.
static DEVICE: AtomicUsize = AtomicUsize::new(0);

/// Pipelines created through each path, so a log line can say how many shaders went past before
/// a swap -- and a run with zero swaps can say whether pipelines were seen at all.
static SEEN_GRAPHICS: AtomicU32 = AtomicU32::new(0);
static SEEN_STREAM: AtomicU32 = AtomicU32::new(0);
static SEEN_LIBRARY: AtomicU32 = AtomicU32::new(0);

/// Pipelines whose pixel shader was replaced, counted on success.
pub static SWAPS: AtomicUsize = AtomicUsize::new(0);
/// Replacement passes left vanilla because their root signature carries no look.
pub static SWAPS_REFUSED: AtomicUsize = AtomicUsize::new(0);
/// Rewritten pipelines the runtime refused at creation, each then created as the game described it.
pub static REWRITES_REFUSED: AtomicUsize = AtomicUsize::new(0);
/// Pipelines created with no root signature that were given the augmented one from their shaders.
pub static NO_ROOT_SIGNATURE_FIXED: AtomicUsize = AtomicUsize::new(0);
/// Pipelines created with no root signature and no shader carrying one.
pub static NO_ROOT_SIGNATURE_LEFT: AtomicUsize = AtomicUsize::new(0);

/// Addresses already detoured. vkd3d-proton and the native runtime each serve every device
/// version from one vtable, so a second device must not create a second detour on one function.
static DETOURED: Mutex<Vec<usize>> = Mutex::new(Vec::new());

/// `(replacement, path)` pairs already logged, so a pass recreated on every load logs once.
static LOGGED_SWAPS: Mutex<Vec<(&'static str, &'static str)>> = Mutex::new(Vec::new());

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
    let result = unsafe { original(adapter, feature_level, riid, device) };
    // A null out pointer is the "is this adapter supported" probe; there is no device to read.
    if result.is_ok() && !device.is_null() {
        let raw = unsafe { *device };
        if !raw.is_null() {
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
        let _ = DEVICE.compare_exchange(
            0,
            device.as_raw() as usize,
            Ordering::SeqCst,
            Ordering::SeqCst,
        );
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

/// Detour `SetGraphicsRootSignature` for direct command lists and for bundles, read off the
/// vtables of one throwaway list of each kind. Created and closed here, before the game has the
/// device, so nothing the game records is touched.
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
        let target = list.vtable().SetGraphicsRootSignature as usize;
        let _ = unsafe { list.Close() };
        report(detour(what, target, hook, &SET_ROOT_SIGNATURE[slot]));
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
                    let root_signature = created(out);
                    if root_signature != 0 {
                        look_binding::record(
                            root_signature,
                            Some(LookBinding {
                                index: augmented.index,
                                address: augmented.address,
                            }),
                        );
                    }
                    let count = look_binding::AUGMENTED_COUNT.fetch_add(1, Ordering::Relaxed);
                    if count < 4 {
                        log(format_args!(
                            "root signature 0x{root_signature:x} carries the look at parameter {}",
                            augmented.index
                        ));
                    }
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
        let root_signature = created(out);
        if root_signature != 0 {
            look_binding::record(root_signature, None);
        }
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
                let vtable = library.vtable();
                report(detour(
                    "LoadGraphicsPipeline",
                    vtable.LoadGraphicsPipeline as usize,
                    load_graphics_hook as *const () as usize,
                    &LOAD_GRAPHICS,
                ));
                report(detour(
                    "LoadComputePipeline",
                    vtable.LoadComputePipeline as usize,
                    load_compute_hook as *const () as usize,
                    &LOAD_COMPUTE,
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

fn replacement_bytecode(replacement: &Replacement) -> D3D12_SHADER_BYTECODE {
    D3D12_SHADER_BYTECODE {
        pShaderBytecode: replacement.dxil.as_ptr().cast(),
        BytecodeLength: replacement.dxil.len(),
    }
}

/// The augmented root signature serialized into the first of these shaders that carries one.
unsafe fn root_signature_from_shaders<'a>(
    shaders: impl IntoIterator<Item = &'a D3D12_SHADER_BYTECODE>,
) -> Option<usize> {
    let raw = DEVICE.load(Ordering::SeqCst) as *mut c_void;
    let device = unsafe { ID3D12Device::from_raw_borrowed(&raw) }?;
    shaders.into_iter().find_map(|shader| {
        let bytes = unsafe { bytecode(shader) }?;
        unsafe { look_binding::augmented_from_shader(device, bytes) }
    })
}

/// A root signature with no look in it is fine for the game's own shaders and fatal for ours, so
/// a replacement is only used when the look is bound.
fn look_bound(root_signature: usize, replacement: &Replacement, path: &'static str) -> bool {
    if look_binding::binding(root_signature).is_some() {
        return true;
    }
    if SWAPS_REFUSED.fetch_add(1, Ordering::Relaxed) < 8 {
        log(format_args!(
            "{} via {path} left vanilla: root signature 0x{root_signature:x} carries no look",
            replacement.name
        ));
    }
    false
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

/// What a rewrite of a description changed.
struct Rewrite {
    replacement: Option<&'static Replacement>,
    root_signature: usize,
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

/// A copy of a graphics description with its pixel shader replaced and its missing root signature
/// filled in, as each applies; `None` when neither does.
unsafe fn rewrite_graphics(
    desc: *const D3D12_GRAPHICS_PIPELINE_STATE_DESC,
    path: &'static str,
) -> Option<(D3D12_GRAPHICS_PIPELINE_STATE_DESC, Rewrite)> {
    if desc.is_null() {
        return None;
    }
    let original = unsafe { &*desc };
    let mut root_signature = raw_root_signature(&original.pRootSignature);
    let mut substituted = false;
    if root_signature == 0 {
        let shaders = [
            &original.VS,
            &original.PS,
            &original.DS,
            &original.HS,
            &original.GS,
        ];
        match unsafe { root_signature_from_shaders(shaders) } {
            Some(found) => {
                root_signature = found;
                substituted = true;
            }
            None => count_no_root_signature(false),
        }
    }
    let replacement = unsafe { bytecode(&original.PS) }
        .and_then(replace::find)
        .filter(|r| look_bound(root_signature, r, path));
    if !substituted && replacement.is_none() {
        return None;
    }
    // A bitwise copy: the root signature is a `ManuallyDrop` field, so the copy takes no
    // reference and releases none.
    let mut copy = unsafe { std::ptr::read(desc) };
    if substituted {
        count_no_root_signature(true);
        copy.pRootSignature = borrowed_root_signature(root_signature);
    }
    if let Some(replacement) = replacement {
        copy.PS = replacement_bytecode(replacement);
    }
    Some((
        copy,
        Rewrite {
            replacement,
            root_signature,
        },
    ))
}

/// The compute version of [`rewrite_graphics`]: no shader is replaced, only a missing root
/// signature filled in.
unsafe fn rewrite_compute(
    desc: *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
) -> Option<D3D12_COMPUTE_PIPELINE_STATE_DESC> {
    if desc.is_null() {
        return None;
    }
    let original = unsafe { &*desc };
    if raw_root_signature(&original.pRootSignature) != 0 {
        return None;
    }
    let Some(found) = (unsafe { root_signature_from_shaders([&original.CS]) }) else {
        count_no_root_signature(false);
        return None;
    };
    count_no_root_signature(true);
    let mut copy = unsafe { std::ptr::read(desc) };
    copy.pRootSignature = borrowed_root_signature(found);
    Some(copy)
}

fn log_swap(replacement: &Replacement, path: &'static str, result: HRESULT, seen: u32) {
    if result.is_ok() {
        SWAPS.fetch_add(1, Ordering::Relaxed);
    }
    let Ok(mut logged) = LOGGED_SWAPS.lock() else {
        return;
    };
    if logged.contains(&(replacement.name, path)) {
        return;
    }
    logged.push((replacement.name, path));
    log(format_args!(
        "swapped {} via {path}: hr=0x{:08x} pipelines_seen_on_this_path={seen}",
        replacement.name, result.0 as u32
    ));
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
    if let Some((copy, rewrite)) = unsafe { rewrite_graphics(desc, PATH) } {
        let result = unsafe { original(device, &copy, riid, pipeline) };
        if let Some(replacement) = rewrite.replacement {
            log_swap(replacement, PATH, result, seen);
        }
        if result.is_ok() {
            return result;
        }
        note_refused(PATH, result, rewrite.root_signature);
    }
    unsafe { original(device, desc, riid, pipeline) }
}

/// A runtime that refuses the rewritten pipeline gets the game's own description instead, so the
/// worst a stricter driver than vkd3d-proton can do is leave that pass vanilla.
fn note_refused(path: &'static str, result: HRESULT, root_signature: usize) {
    if REWRITES_REFUSED.fetch_add(1, Ordering::Relaxed) < 8 {
        log(format_args!(
            "rewritten pipeline refused via {path}: hr=0x{:08x} root signature 0x{root_signature:x}; created as the game described it",
            result.0 as u32
        ));
    }
}

unsafe extern "system" fn create_compute_hook(
    device: *mut c_void,
    desc: *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: CreateComputeFn =
        unsafe { std::mem::transmute(CREATE_COMPUTE.load(Ordering::SeqCst)) };
    if let Some(copy) = unsafe { rewrite_compute(desc) } {
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
    const PATH: &str = "LoadGraphicsPipeline";
    // A library entry stored under the original shader or root signature no longer matches, and
    // the runtime says so with E_INVALIDARG; the game then creates the pipeline, which the hooks
    // above rewrite, and stores that one. Forwarding the original description instead would hand
    // back vanilla.
    if let Some((copy, rewrite)) = unsafe { rewrite_graphics(desc, PATH) } {
        let result = unsafe { original(library, name, &copy, riid, pipeline) };
        if let Some(replacement) = rewrite.replacement {
            log_swap(replacement, PATH, result, seen);
        }
        return result;
    }
    unsafe { original(library, name, desc, riid, pipeline) }
}

unsafe extern "system" fn load_compute_hook(
    library: *mut c_void,
    name: PCWSTR,
    desc: *const D3D12_COMPUTE_PIPELINE_STATE_DESC,
    riid: *const GUID,
    pipeline: *mut *mut c_void,
) -> HRESULT {
    let original: LoadComputeFn =
        unsafe { std::mem::transmute(LOAD_COMPUTE.load(Ordering::SeqCst)) };
    if let Some(copy) = unsafe { rewrite_compute(desc) } {
        return unsafe { original(library, name, &copy, riid, pipeline) };
    }
    unsafe { original(library, name, desc, riid, pipeline) }
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

/// Subobject types that carry shader bytecode, in the order a root signature is looked for.
const SHADER_SUBOBJECTS: [u32; 8] = [1, 2, 3, 4, 5, 6, 24, 25];

/// A pointer-aligned copy of a stream with its pixel shader replaced and its missing root
/// signature filled in, as each applies.
unsafe fn rewrite_stream(
    desc: *const D3D12_PIPELINE_STATE_STREAM_DESC,
    path: &'static str,
) -> Option<(Vec<u64>, usize, Rewrite)> {
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
    let read_shader = |offset: usize| -> D3D12_SHADER_BYTECODE {
        unsafe { std::ptr::read_unaligned(stream.as_ptr().add(offset).cast()) }
    };
    let root_at = all
        .iter()
        .find(|s| s.ty == SUBOBJECT_ROOT_SIGNATURE)
        .map(|s| s.offset);
    let mut root_signature = root_at.map_or(0, |at| unsafe {
        std::ptr::read_unaligned(stream.as_ptr().add(at).cast::<usize>())
    });
    let mut substituted = false;
    if root_signature == 0 {
        let shaders: Vec<D3D12_SHADER_BYTECODE> = SHADER_SUBOBJECTS
            .iter()
            .filter_map(|ty| all.iter().find(|s| s.ty == *ty))
            .map(|s| read_shader(s.offset))
            .collect();
        match unsafe { root_signature_from_shaders(shaders.iter()) } {
            Some(found) => {
                root_signature = found;
                substituted = true;
            }
            None => count_no_root_signature(false),
        }
    }
    let ps_at = all.iter().find(|s| s.ty == SUBOBJECT_PS).map(|s| s.offset);
    let replacement = ps_at
        .and_then(|at| {
            let ps = read_shader(at);
            unsafe { bytecode(&ps) }.and_then(replace::find)
        })
        .filter(|r| look_bound(root_signature, r, path));
    if !substituted && replacement.is_none() {
        return None;
    }

    // Room for one appended root signature subobject: its type, padding to pointer alignment, and
    // the pointer.
    const APPENDED: usize = 16;
    let mut copy = vec![0_u64; (length + APPENDED).div_ceil(8)];
    let bytes = copy.as_mut_ptr().cast::<u8>();
    let mut size = length;
    unsafe { std::ptr::copy_nonoverlapping(stream.as_ptr(), bytes, length) };
    if substituted {
        count_no_root_signature(true);
        match root_at {
            Some(at) => unsafe {
                std::ptr::write_unaligned(bytes.add(at).cast::<usize>(), root_signature);
            },
            None => {
                let at = length.div_ceil(8) * 8;
                unsafe {
                    std::ptr::write_unaligned(
                        bytes.add(at).cast::<u32>(),
                        SUBOBJECT_ROOT_SIGNATURE,
                    );
                    std::ptr::write_unaligned(bytes.add(at + 8).cast::<usize>(), root_signature);
                }
                size = at + APPENDED;
            }
        }
    }
    if let (Some(replacement), Some(at)) = (replacement, ps_at) {
        unsafe {
            std::ptr::write_unaligned(
                bytes.add(at).cast::<D3D12_SHADER_BYTECODE>(),
                replacement_bytecode(replacement),
            );
        }
    }
    Some((
        copy,
        size,
        Rewrite {
            replacement,
            root_signature,
        },
    ))
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
    if let Some((mut copy, size, rewrite)) = unsafe { rewrite_stream(desc, PATH) } {
        let patched = D3D12_PIPELINE_STATE_STREAM_DESC {
            SizeInBytes: size,
            pPipelineStateSubobjectStream: copy.as_mut_ptr().cast(),
        };
        let result = unsafe { original(device, &patched, riid, pipeline) };
        if let Some(replacement) = rewrite.replacement {
            log_swap(replacement, PATH, result, seen);
        }
        if result.is_ok() {
            return result;
        }
        note_refused(PATH, result, rewrite.root_signature);
    }
    unsafe { original(device, desc, riid, pipeline) }
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
    const PATH: &str = "LoadPipeline";
    if let Some((mut copy, size, rewrite)) = unsafe { rewrite_stream(desc, PATH) } {
        let patched = D3D12_PIPELINE_STATE_STREAM_DESC {
            SizeInBytes: size,
            pPipelineStateSubobjectStream: copy.as_mut_ptr().cast(),
        };
        let result = unsafe { original(library, name, &patched, riid, pipeline) };
        if let Some(replacement) = rewrite.replacement {
            log_swap(replacement, PATH, result, seen);
        }
        return result;
    }
    unsafe { original(library, name, desc, riid, pipeline) }
}
