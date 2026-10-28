//! D3D12 Device Removed Extended Data: what the GPU was doing when the device was lost.
//!
//! # The crash this is for
//!
//! The det-26-10-27 report: a game that died on 2026-10-27 at 17:43:01Z with an access violation
//! in `eldenring.exe+0x251ff2b`,
//! an 8-byte copy into a null pointer. That was the aftermath. For thirty seconds before it,
//! `D3D12Core.dll` had been throwing `_com_error` with HRESULT `0x887a0005`,
//! `DXGI_ERROR_DEVICE_REMOVED`: the GPU had already been reset. The player had set out to crash
//! his GPU and the game let him. The log recorded the null pointer and the lost device, and it
//! could not say which GPU work hung, because nothing had asked D3D12 to keep track.
//!
//! # What this records
//!
//! DRED has to be switched on before the device exists, so [`start`] does two things as early as
//! the DLL can: it forces DRED's auto-breadcrumbs and page-fault tracking on, and it detours
//! `d3d12.dll!D3D12CreateDevice` to keep a reference to the device the game creates. Every
//! exception record then carries one `dred_state=` line, and once the device reports itself removed
//! the record also carries the removal reason, the command lists the GPU had not finished with the
//! operation each stopped on, and on a page fault the faulting GPU address with the allocations
//! near it.
//!
//! Auto-breadcrumbs cost GPU time on every command list. Microsoft puts that cost in the low single
//! digits of a percent. It is paid only by players who load this DLL, which is the crash logger.

use core::ffi::c_void;
use core::fmt::Write as _;
use std::sync::atomic::{AtomicI32, AtomicUsize, Ordering};

#[repr(C)]
struct Guid {
    data1: u32,
    data2: u16,
    data3: u16,
    data4: [u8; 8],
}

/// `ID3D12DeviceRemovedExtendedDataSettings`, from `d3d12.h`.
const IID_DRED_SETTINGS: Guid = Guid {
    data1: 0x82bc_481c,
    data2: 0x6b9b,
    data3: 0x4030,
    data4: [0xae, 0xdb, 0x7e, 0xe3, 0xd1, 0xdf, 0x1e, 0x63],
};

/// `ID3D12DeviceRemovedExtendedData`, from `d3d12.h`.
const IID_DRED: Guid = Guid {
    data1: 0x9893_1d33,
    data2: 0x5ae8,
    data3: 0x4791,
    data4: [0xaa, 0x3c, 0x1a, 0x73, 0xa2, 0x93, 0x4e, 0x71],
};

/// `D3D12_DRED_ENABLEMENT_FORCED_ON`.
const DRED_FORCED_ON: u32 = 2;

// Vtable slots. IUnknown is 0..3 everywhere.
const VT_ADD_REF: usize = 1;
const VT_RELEASE: usize = 2;
const VT_QUERY_INTERFACE: usize = 0;
/// `ID3D12DeviceRemovedExtendedDataSettings::SetAutoBreadcrumbsEnablement`.
const VT_SET_AUTO_BREADCRUMBS: usize = 3;
/// `ID3D12DeviceRemovedExtendedDataSettings::SetPageFaultEnablement`.
const VT_SET_PAGE_FAULT: usize = 4;
/// `ID3D12Device::GetDeviceRemovedReason`: IUnknown (3) + ID3D12Object (4) + 30 device methods.
const VT_GET_DEVICE_REMOVED_REASON: usize = 37;
/// `ID3D12DeviceRemovedExtendedData::GetAutoBreadcrumbsOutput`.
const VT_GET_AUTO_BREADCRUMBS: usize = 3;
/// `ID3D12DeviceRemovedExtendedData::GetPageFaultAllocationOutput`.
const VT_GET_PAGE_FAULT: usize = 4;

/// Command lists listed per record. A lost device leaves a handful unfinished; the cap only stops
/// a runaway list from becoming the whole record.
const MAX_NODES: usize = 16;
/// Allocations listed per page-fault list, for the same reason.
const MAX_ALLOCATIONS: usize = 8;

/// HRESULT of the DRED settings call, or [`NOT_TRIED`] until [`start`] has run.
static ENABLE_HR: AtomicI32 = AtomicI32::new(NOT_TRIED);
const NOT_TRIED: i32 = 1;
/// The newest device `D3D12CreateDevice` handed out, with a reference this module holds.
static DEVICE: AtomicUsize = AtomicUsize::new(0);
static CREATE_DEVICE_ORIGINAL: AtomicUsize = AtomicUsize::new(0);

#[cfg(windows)]
unsafe extern "system" {
    fn LoadLibraryA(name: *const u8) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
}

type CreateDeviceFn =
    unsafe extern "system" fn(*mut c_void, u32, *const Guid, *mut *mut c_void) -> i32;
type GetDebugInterfaceFn = unsafe extern "system" fn(*const Guid, *mut *mut c_void) -> i32;

/// Switch DRED on and start capturing the device. Runs once, from its own thread, before the game
/// has created its device: DRED settings only apply to devices created after them.
#[cfg(windows)]
pub(crate) fn start(log: fn(core::fmt::Arguments<'_>)) {
    er_crash_logging_core::set_report_extension(write_fields);
    let d3d12 = unsafe { LoadLibraryA(c"d3d12.dll".as_ptr().cast()) };
    if d3d12.is_null() {
        log(format_args!(
            "dred: d3d12.dll did not load; no GPU crash diagnostics"
        ));
        return;
    }
    let hr = unsafe { enable(d3d12) };
    ENABLE_HR.store(hr, Ordering::SeqCst);
    log(format_args!(
        "dred: auto-breadcrumbs and page-fault tracking forced on: hr=0x{:08x}{}",
        hr as u32,
        if hr < 0 { " (refused)" } else { "" }
    ));
    match unsafe { hook_create_device(d3d12) } {
        Ok(()) => log(format_args!(
            "dred: D3D12CreateDevice detoured to capture the device"
        )),
        Err(why) => log(format_args!("dred: no device capture: {why}")),
    }
}

/// # Safety
///
/// `d3d12` must be the loaded `d3d12.dll`.
#[cfg(windows)]
unsafe fn enable(d3d12: *mut c_void) -> i32 {
    let proc = unsafe { GetProcAddress(d3d12, c"D3D12GetDebugInterface".as_ptr().cast()) };
    if proc.is_null() {
        return E_NOINTERFACE;
    }
    let get_debug_interface: GetDebugInterfaceFn = unsafe { core::mem::transmute(proc) };
    let mut settings: *mut c_void = core::ptr::null_mut();
    let hr = unsafe { get_debug_interface(&IID_DRED_SETTINGS, &mut settings) };
    if hr < 0 || settings.is_null() {
        return hr;
    }
    unsafe {
        let set: unsafe extern "system" fn(*mut c_void, u32) =
            vfn(settings, VT_SET_AUTO_BREADCRUMBS);
        set(settings, DRED_FORCED_ON);
        let set: unsafe extern "system" fn(*mut c_void, u32) = vfn(settings, VT_SET_PAGE_FAULT);
        set(settings, DRED_FORCED_ON);
        release(settings);
    }
    hr
}

const E_NOINTERFACE: i32 = 0x8000_4002_u32 as i32;

/// # Safety
///
/// `d3d12` must be the loaded `d3d12.dll`.
#[cfg(windows)]
unsafe fn hook_create_device(d3d12: *mut c_void) -> Result<(), String> {
    use er_hook::{MH_EnableHook, MH_Initialize, MH_STATUS, MhHook};

    let target = unsafe { GetProcAddress(d3d12, c"D3D12CreateDevice".as_ptr().cast()) };
    if target.is_null() {
        return Err("d3d12.dll exports no D3D12CreateDevice".to_owned());
    }
    match unsafe { MH_Initialize() } {
        MH_STATUS::MH_OK | MH_STATUS::MH_ERROR_ALREADY_INITIALIZED => {}
        status => return Err(format!("MH_Initialize: {status:?}")),
    }
    // `MhHook::new` checks the game allowlist only for addresses inside the game image; this one
    // is a `d3d12.dll` export, which is a function entry by definition.
    let detour = create_device_detour as CreateDeviceFn as *mut c_void;
    let hook = unsafe { MhHook::new(target, detour) }
        .map_err(|status| format!("MhHook::new: {status:?}"))?;
    CREATE_DEVICE_ORIGINAL.store(hook.trampoline() as usize, Ordering::SeqCst);
    let status = unsafe { MH_EnableHook(target) };
    if status != MH_STATUS::MH_OK {
        return Err(format!("MH_EnableHook: {status:?}"));
    }
    Ok(())
}

unsafe extern "system" fn create_device_detour(
    adapter: *mut c_void,
    feature_level: u32,
    riid: *const Guid,
    out: *mut *mut c_void,
) -> i32 {
    let original: CreateDeviceFn =
        unsafe { core::mem::transmute(CREATE_DEVICE_ORIGINAL.load(Ordering::SeqCst)) };
    let hr = unsafe { original(adapter, feature_level, riid, out) };
    // A null `out` is the caller asking whether a device could be created; nothing was.
    if hr >= 0 && !out.is_null() {
        let device = unsafe { *out };
        if !device.is_null() {
            unsafe { add_ref(device) };
            // The newest device is the game's: anything earlier was an adapter probe. Holding a
            // probe's reference would keep a whole device alive on a GPU the game does not use.
            // Released even when it is the same device: D3D12 devices are per-adapter singletons,
            // and the game asked for this one three times on 2026-10-27, each call one more
            // reference taken above.
            let previous = DEVICE.swap(device as usize, Ordering::SeqCst);
            if previous != 0 {
                unsafe { release(previous as *mut c_void) };
            }
            er_crash_logging_core::append_log(format_args!(
                "dred: captured device 0x{:x} (feature level 0x{feature_level:x}, previous 0x{previous:x})",
                device as usize
            ));
        }
    }
    hr
}

/// The [`er_crash_logging_core::ReportExtension`]: one `dred_state=` line, and the breadcrumbs
/// once the device has been removed.
fn write_fields(out: &mut String) {
    let enable_hr = ENABLE_HR.load(Ordering::SeqCst);
    let device = DEVICE.load(Ordering::SeqCst) as *mut c_void;
    if device.is_null() {
        let _ = writeln!(
            out,
            "dred_state=no-device enable_hr=0x{:08x}",
            enable_hr as u32
        );
        return;
    }
    let get_reason: unsafe extern "system" fn(*mut c_void) -> i32 =
        unsafe { vfn(device, VT_GET_DEVICE_REMOVED_REASON) };
    let reason = unsafe { get_reason(device) };
    if reason >= 0 {
        let _ = writeln!(
            out,
            "dred_state=device-ok enable_hr=0x{:08x}",
            enable_hr as u32
        );
        return;
    }
    let _ = writeln!(
        out,
        "dred_state=device-removed reason=0x{:08x}({}) enable_hr=0x{:08x}",
        reason as u32,
        removed_reason_label(reason as u32),
        enable_hr as u32
    );
    let mut dred: *mut c_void = core::ptr::null_mut();
    let query: unsafe extern "system" fn(*mut c_void, *const Guid, *mut *mut c_void) -> i32 =
        unsafe { vfn(device, VT_QUERY_INTERFACE) };
    let hr = unsafe { query(device, &IID_DRED, &mut dred) };
    if hr < 0 || dred.is_null() {
        let _ = writeln!(out, "dred_breadcrumbs=unavailable hr=0x{:08x}", hr as u32);
        return;
    }
    unsafe {
        write_breadcrumbs(out, dred);
        write_page_fault(out, dred);
        release(dred);
    }
}

/// `D3D12_AUTO_BREADCRUMB_NODE`. Only the fields read here are typed; the layout is `d3d12.h`'s.
#[repr(C)]
struct BreadcrumbNode {
    list_name_a: *const u8,
    list_name_w: *const u16,
    queue_name_a: *const u8,
    queue_name_w: *const u16,
    list: *mut c_void,
    queue: *mut c_void,
    count: u32,
    last_value: *const u32,
    history: *const u32,
    next: *const BreadcrumbNode,
}

/// # Safety
///
/// `dred` must be a live `ID3D12DeviceRemovedExtendedData`.
unsafe fn write_breadcrumbs(out: &mut String, dred: *mut c_void) {
    let mut head: *const BreadcrumbNode = core::ptr::null();
    let get: unsafe extern "system" fn(*mut c_void, *mut *const BreadcrumbNode) -> i32 =
        unsafe { vfn(dred, VT_GET_AUTO_BREADCRUMBS) };
    let hr = unsafe { get(dred, &mut head) };
    if hr < 0 {
        let _ = writeln!(out, "dred_breadcrumbs=unavailable hr=0x{:08x}", hr as u32);
        return;
    }
    let _ = write!(out, "dred_breadcrumbs=[");
    let (mut node, mut listed, mut total) = (head, 0_usize, 0_usize);
    while !node.is_null() {
        let n = unsafe { &*node };
        total += 1;
        let done = if n.last_value.is_null() {
            0
        } else {
            unsafe { *n.last_value }
        };
        // A list the GPU finished says nothing about the hang.
        if done < n.count && listed < MAX_NODES {
            let op = |i: u32| -> &'static str {
                if i < n.count && !n.history.is_null() {
                    op_label(unsafe { *n.history.add(i as usize) })
                } else {
                    "-"
                }
            };
            let _ = write!(
                out,
                "{}queue={} list={} done={done}/{} stopped_at={} before={} after={}",
                if listed == 0 { "" } else { "," },
                unsafe { name(n.queue_name_w, n.queue_name_a) },
                unsafe { name(n.list_name_w, n.list_name_a) },
                n.count,
                op(done),
                if done == 0 { "-" } else { op(done - 1) },
                op(done + 1),
            );
            listed += 1;
        }
        node = n.next;
    }
    let _ = writeln!(out, "] unfinished_listed={listed} nodes={total}");
}

/// `D3D12_DRED_PAGE_FAULT_OUTPUT`.
#[repr(C)]
struct PageFaultOutput {
    va: u64,
    existing: *const AllocationNode,
    recently_freed: *const AllocationNode,
}

/// `D3D12_DRED_ALLOCATION_NODE`.
#[repr(C)]
struct AllocationNode {
    name_a: *const u8,
    name_w: *const u16,
    kind: u32,
    next: *const AllocationNode,
}

/// # Safety
///
/// `dred` must be a live `ID3D12DeviceRemovedExtendedData`.
unsafe fn write_page_fault(out: &mut String, dred: *mut c_void) {
    let mut fault = PageFaultOutput {
        va: 0,
        existing: core::ptr::null(),
        recently_freed: core::ptr::null(),
    };
    let get: unsafe extern "system" fn(*mut c_void, *mut PageFaultOutput) -> i32 =
        unsafe { vfn(dred, VT_GET_PAGE_FAULT) };
    let hr = unsafe { get(dred, &mut fault) };
    if hr < 0 || fault.va == 0 {
        // No page fault: the GPU hung or was reset, it did not read a bad address.
        let _ = writeln!(out, "dred_page_fault=none");
        return;
    }
    let _ = writeln!(
        out,
        "dred_page_fault=0x{:x} existing=[{}] recently_freed=[{}]",
        fault.va,
        unsafe { allocations(fault.existing) },
        unsafe { allocations(fault.recently_freed) }
    );
}

unsafe fn allocations(mut node: *const AllocationNode) -> String {
    let mut out = String::new();
    let mut listed = 0;
    while !node.is_null() && listed < MAX_ALLOCATIONS {
        let n = unsafe { &*node };
        let _ = write!(
            out,
            "{}{}:type{}",
            if listed == 0 { "" } else { "," },
            unsafe { name(n.name_w, n.name_a) },
            n.kind
        );
        listed += 1;
        node = n.next;
    }
    out
}

/// A debug name, wide spelling first. The game names few of its objects; `-` is the common answer.
unsafe fn name(wide: *const u16, narrow: *const u8) -> String {
    const LIMIT: usize = 64;
    let mut out = String::new();
    if !wide.is_null() {
        for i in 0..LIMIT {
            let c = unsafe { *wide.add(i) };
            if c == 0 {
                break;
            }
            out.push(char::from_u32(c.into()).unwrap_or('?'));
        }
    } else if !narrow.is_null() {
        for i in 0..LIMIT {
            let c = unsafe { *narrow.add(i) };
            if c == 0 {
                break;
            }
            out.push(char::from(c));
        }
    }
    if out.is_empty() {
        out.push('-');
    }
    // The record is `key=value` lines with `,` between list items.
    out.replace([',', '\n', '=', ']'], "_")
}

/// `D3D12_AUTO_BREADCRUMB_OP`, from `d3d12.h`.
pub(crate) fn op_label(op: u32) -> &'static str {
    const OPS: [&str; 45] = [
        "SetMarker",
        "BeginEvent",
        "EndEvent",
        "DrawInstanced",
        "DrawIndexedInstanced",
        "ExecuteIndirect",
        "Dispatch",
        "CopyBufferRegion",
        "CopyTextureRegion",
        "CopyResource",
        "CopyTiles",
        "ResolveSubresource",
        "ClearRenderTargetView",
        "ClearUnorderedAccessView",
        "ClearDepthStencilView",
        "ResourceBarrier",
        "ExecuteBundle",
        "Present",
        "ResolveQueryData",
        "BeginSubmission",
        "EndSubmission",
        "DecodeFrame",
        "ProcessFrames",
        "AtomicCopyBufferUint",
        "AtomicCopyBufferUint64",
        "ResolveSubresourceRegion",
        "WriteBufferImmediate",
        "DecodeFrame1",
        "SetProtectedResourceSession",
        "DecodeFrame2",
        "ProcessFrames1",
        "BuildRaytracingAccelerationStructure",
        "EmitRaytracingAccelerationStructurePostbuildInfo",
        "CopyRaytracingAccelerationStructure",
        "DispatchRays",
        "InitializeMetaCommand",
        "ExecuteMetaCommand",
        "EstimateMotion",
        "ResolveMotionVectorHeap",
        "SetPipelineState1",
        "InitializeExtensionCommand",
        "ExecuteExtensionCommand",
        "DispatchMesh",
        "EncodeFrame",
        "ResolveEncoderOutputMetadata",
    ];
    OPS.get(op as usize).copied().unwrap_or("Unknown")
}

/// The `GetDeviceRemovedReason` answers, from `winerror.h`.
pub(crate) fn removed_reason_label(hr: u32) -> &'static str {
    match hr {
        0x887a_0005 => "DXGI_ERROR_DEVICE_REMOVED",
        0x887a_0006 => "DXGI_ERROR_DEVICE_HUNG",
        0x887a_0007 => "DXGI_ERROR_DEVICE_RESET",
        0x887a_0020 => "DXGI_ERROR_DRIVER_INTERNAL_ERROR",
        0x887a_0001 => "DXGI_ERROR_INVALID_CALL",
        0x8007_000e => "E_OUTOFMEMORY",
        _ => "unrecognized",
    }
}

/// # Safety
///
/// `object` must be a live COM object whose vtable has a slot `index` of type `F`.
unsafe fn vfn<F: Copy>(object: *mut c_void, index: usize) -> F {
    let vtable = unsafe { *(object as *const *const usize) };
    let slot = unsafe { *vtable.add(index) };
    unsafe { core::mem::transmute_copy(&slot) }
}

unsafe fn add_ref(object: *mut c_void) {
    let f: unsafe extern "system" fn(*mut c_void) -> u32 = unsafe { vfn(object, VT_ADD_REF) };
    unsafe { f(object) };
}

unsafe fn release(object: *mut c_void) {
    let f: unsafe extern "system" fn(*mut c_void) -> u32 = unsafe { vfn(object, VT_RELEASE) };
    unsafe { f(object) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn breadcrumb_ops_match_d3d12_h() {
        assert_eq!(op_label(0), "SetMarker");
        assert_eq!(op_label(17), "Present");
        assert_eq!(op_label(34), "DispatchRays");
        assert_eq!(op_label(44), "ResolveEncoderOutputMetadata");
        assert_eq!(op_label(45), "Unknown");
    }

    #[test]
    fn det_2026_10_27_reason_is_named() {
        assert_eq!(
            removed_reason_label(0x887a_0005),
            "DXGI_ERROR_DEVICE_REMOVED"
        );
        assert_eq!(removed_reason_label(0x887a_0006), "DXGI_ERROR_DEVICE_HUNG");
    }

    #[test]
    fn breadcrumb_node_layout_matches_d3d12_h() {
        assert_eq!(core::mem::offset_of!(BreadcrumbNode, count), 0x30);
        assert_eq!(core::mem::offset_of!(BreadcrumbNode, last_value), 0x38);
        assert_eq!(core::mem::offset_of!(BreadcrumbNode, history), 0x40);
        assert_eq!(core::mem::offset_of!(BreadcrumbNode, next), 0x48);
        assert_eq!(core::mem::offset_of!(AllocationNode, next), 0x18);
    }
}
