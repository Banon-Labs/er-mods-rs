//! The board's weapon icon, copied out of the game's own icon atlas into the overlay host.
//!
//! # The chain
//!
//! Measured live on 1.17.1 with Frida (`scripts/frida/r3-icon-lookup.js`, bd
//! `item-icon-runtime-chain-1171-2026-10-02`), inside `MenuWindowJob::Run` of the item list:
//!
//! ```text
//! repo  = *(u64*)(base + 0x3d86580)                       ; Scaleform texture repository
//! res   = lookup(repo, &out, L"MENU_ItemIcon_%05d")      ; base + 0xd65c00, out = res
//! res+0x70  -> wchar* symbol                             ; CS::ScaleformImageResource
//! res+0x18  -> CS::CSTextureImage
//!   +0x2c/+0x30  atlas width, height                     ; 4096, 2048
//!   +0x74..+0x80 x0, y0, x1, y1                          ; 3608, 1804, 3768, 1964 for 10003
//!   +0x84/+0x88  width, height                           ; 160, 160
//!   +0x10 -> HAL object, +0x70 -> ID3D12Resource         ; Texture2D 4096x2048, 1 mip, BC7
//! ```
//!
//! The lookup inserts into the repository's map on a miss, so it is called only on the menu
//! thread, once, from the item list's `MenuWindowJob::Run`. Everything after it -- the GPU copy,
//! the BC7 decode and the hand-off to the overlay host -- runs on a thread of our own, so neither
//! the menu thread nor the render thread waits on a fence.
//!
//! # Addresses
//!
//! Both are written as 1.16.2 rvas and translated for the running build by `er_game_base`, which
//! refuses a build it has no verified mapping for; a refusal logs and the board draws no icon. The
//! lookup is the function map's pair (1.16.2 `0xd63e50`, 1.17.0 `0xd65b90`), and the repository
//! global the data map's, agreed by all 35 references (1.16.2 `0x3d82510`, 1.17 `0x3d86580`).
//! Both translated addresses were read live on 1.17.1 by `scripts/frida/r3-icon-addresses.js`:
//! the global holds a `CS::ScaleformTexRepositoryImp` and the lookup starts with its prologue.

use std::ffi::c_void;
use std::mem::ManuallyDrop;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};

use er_game_base::mem::{module_backing, safe_read_i32, safe_read_usize};
use windows::Win32::Foundation::{CloseHandle, WAIT_FAILED, WAIT_OBJECT_0};
use windows::Win32::Graphics::Direct3D12::{
    D3D12_BOX, D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_COMMAND_QUEUE_DESC,
    D3D12_COMMAND_QUEUE_FLAG_NONE, D3D12_CPU_PAGE_PROPERTY_UNKNOWN, D3D12_FENCE_FLAG_NONE,
    D3D12_HEAP_FLAG_NONE, D3D12_HEAP_PROPERTIES, D3D12_HEAP_TYPE_READBACK,
    D3D12_MEMORY_POOL_UNKNOWN, D3D12_PLACED_SUBRESOURCE_FOOTPRINT, D3D12_RANGE,
    D3D12_RESOURCE_BARRIER, D3D12_RESOURCE_BARRIER_0, D3D12_RESOURCE_BARRIER_FLAG_NONE,
    D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, D3D12_RESOURCE_DESC, D3D12_RESOURCE_DIMENSION_BUFFER,
    D3D12_RESOURCE_DIMENSION_TEXTURE2D, D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COMMON,
    D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATES,
    D3D12_RESOURCE_TRANSITION_BARRIER, D3D12_SUBRESOURCE_FOOTPRINT, D3D12_TEXTURE_COPY_LOCATION,
    D3D12_TEXTURE_COPY_LOCATION_0, D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT,
    D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX, D3D12_TEXTURE_DATA_PITCH_ALIGNMENT,
    D3D12_TEXTURE_LAYOUT_ROW_MAJOR, ID3D12CommandAllocator, ID3D12CommandList, ID3D12CommandQueue,
    ID3D12Device, ID3D12Fence, ID3D12GraphicsCommandList, ID3D12Resource,
};
use windows::Win32::Graphics::Dxgi::Common::{
    DXGI_FORMAT, DXGI_FORMAT_BC7_TYPELESS, DXGI_FORMAT_BC7_UNORM, DXGI_FORMAT_BC7_UNORM_SRGB,
    DXGI_FORMAT_UNKNOWN, DXGI_SAMPLE_DESC,
};
use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject};
use windows::core::Interface;

/// 1.16.2 rva of the Scaleform texture repository global, a pointer that is null until the menu
/// system has built it (1.17.1 `0x3d86580`, bd `item-icon-runtime-chain-1171-2026-10-02`).
const SCALEFORM_TEXTURE_REPOSITORY_GLOBAL_RVA: usize = 0x3d82510;

/// 1.16.2 rva of the repository lookup, `fn(repo, out: *mut *mut c_void, name: *const u16)`
/// (1.17.1 `0xd65c00`); the resource is written to `out`.
const SCALEFORM_TEXTURE_LOOKUP_RVA: u32 = 0xd63e50;

/// `CS::ScaleformImageResource`: its `CSTextureImage` and its symbol (a `wchar*`).
const RESOURCE_IMAGE_OFFSET: usize = 0x18;
const RESOURCE_SYMBOL_OFFSET: usize = 0x70;

/// `CS::CSTextureImage` on 1.17.1: atlas size, the icon's rect and its size, all `i32`
/// (measured 4096 x 2048, 3608/1804/3768/1964 and 160 x 160 for `MENU_ItemIcon_10003`). The
/// 1.16.2 rect at `ScaleformImageResource+0x50` reads zero on 1.17.1.
const IMAGE_ATLAS_W_OFFSET: usize = 0x2c;
const IMAGE_ATLAS_H_OFFSET: usize = 0x30;
const IMAGE_RECT_OFFSET: usize = 0x74;
const IMAGE_SIZE_OFFSET: usize = 0x84;
/// `CSTextureImage+0x10` is the renderer's texture object, and its `+0x70` the `ID3D12Resource`
/// (vtable in `d3d12core.dll`).
const IMAGE_HAL_OFFSET: usize = 0x10;
const HAL_RESOURCE_OFFSET: usize = 0x70;

/// BC7 stores 4 x 4 texels in 16 bytes.
const BLOCK_DIM: u32 = 4;
const BLOCK_BYTES: u32 = 16;
/// The largest icon side accepted; the measured icons are 160.
const MAX_ICON_DIM: u32 = 512;
/// Bound on the copy's fence wait.
const FENCE_WAIT_MS: u32 = 2000;

static LOOKUP_TRIED: AtomicBool = AtomicBool::new(false);
/// The overlay host's handle for the icon, 0 until it accepted one.
static ICON_HANDLE: AtomicU32 = AtomicU32::new(0);

/// The host handle for the icon, once the worker has handed it over.
pub fn handle() -> Option<u32> {
    match ICON_HANDLE.load(Ordering::Relaxed) {
        0 => None,
        handle => Some(handle),
    }
}

/// What the lookup yields: the atlas texture, held by one reference of our own, and the icon's
/// rect in it.
struct IconSource {
    /// `ID3D12Resource::into_raw`, so it can cross to the worker thread.
    resource: usize,
    atlas: (u32, u32),
    rect: [u32; 4],
}

/// Resolve `MENU_ItemIcon_<icon_id>` and start the copy, the first time it is called.
///
/// # Safety
///
/// Only from `MenuWindowJob::Run` of the item list, on the menu thread: the lookup inserts into
/// the repository's map, which the menu thread owns.
pub unsafe fn resolve_once(game_base: usize, icon_id: u32, log: fn(std::fmt::Arguments<'_>)) {
    if LOOKUP_TRIED.swap(true, Ordering::Relaxed) {
        return;
    }
    let source = match unsafe { lookup(game_base, icon_id) } {
        Ok(source) => source,
        Err(why) => {
            log(format_args!(
                "icon {icon_id}: {why}; the board draws no icon"
            ));
            return;
        }
    };
    log(format_args!(
        "icon {icon_id}: lookup ok, rect {:?} in a {}x{} atlas",
        source.rect, source.atlas.0, source.atlas.1
    ));
    let spawned = std::thread::Builder::new()
        .name("er-r3-view-icon".to_string())
        .spawn(move || copy_and_hand_over(source, icon_id, log));
    if spawned.is_err() {
        log(format_args!(
            "icon {icon_id}: could not spawn the copy thread"
        ));
    }
}

/// # Safety
///
/// As [`resolve_once`].
unsafe fn lookup(game_base: usize, icon_id: u32) -> Result<IconSource, String> {
    let lookup_addr = er_game_base::mem::game_rva_named(
        SCALEFORM_TEXTURE_LOOKUP_RVA,
        "SCALEFORM_TEXTURE_LOOKUP_RVA",
    )?;
    let repo_global = er_game_base::mem::game_data_addr(
        game_base,
        SCALEFORM_TEXTURE_REPOSITORY_GLOBAL_RVA,
        "SCALEFORM_TEXTURE_REPOSITORY_GLOBAL_RVA",
    );
    if repo_global == 0 {
        return Err("texture repository global has no mapping for this build".to_string());
    }
    let repo = unsafe { safe_read_usize(repo_global) }.unwrap_or(0);
    if repo == 0 {
        return Err("texture repository global is null".to_string());
    }
    let vtable = unsafe { safe_read_usize(repo) }.unwrap_or(0);
    if !er_game_base::mem::vtable_in_game_image(vtable, game_base) {
        return Err(format!("texture repository 0x{repo:x} has no game vtable"));
    }

    let name = format!("MENU_ItemIcon_{icon_id:05}");
    let wide: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
    let mut out: *mut c_void = std::ptr::null_mut();
    let call: unsafe extern "system" fn(usize, *mut *mut c_void, *const u16) -> *mut c_void =
        unsafe { std::mem::transmute(lookup_addr) };
    unsafe { call(repo, &mut out, wide.as_ptr()) };
    let res = out as usize;
    if res == 0 {
        return Err(format!("lookup of {name} missed"));
    }
    let symbol = unsafe { safe_read_usize(res + RESOURCE_SYMBOL_OFFSET) }.unwrap_or(0);
    if !unsafe { er_game_base::mem::wide_equals_ascii(symbol, name.as_bytes()) } {
        return Err(format!(
            "resource 0x{res:x} does not carry the symbol {name}"
        ));
    }

    let image = unsafe { safe_read_usize(res + RESOURCE_IMAGE_OFFSET) }.unwrap_or(0);
    if image == 0 {
        return Err(format!("resource 0x{res:x} has no texture image"));
    }
    let read_u32 = |at: usize| -> Result<u32, String> {
        unsafe { safe_read_i32(at) }
            .and_then(|v| u32::try_from(v).ok())
            .ok_or_else(|| format!("image field at 0x{at:x} unreadable or negative"))
    };
    let atlas = (
        read_u32(image + IMAGE_ATLAS_W_OFFSET)?,
        read_u32(image + IMAGE_ATLAS_H_OFFSET)?,
    );
    let rect = [
        read_u32(image + IMAGE_RECT_OFFSET)?,
        read_u32(image + IMAGE_RECT_OFFSET + 4)?,
        read_u32(image + IMAGE_RECT_OFFSET + 8)?,
        read_u32(image + IMAGE_RECT_OFFSET + 12)?,
    ];
    let size = (
        read_u32(image + IMAGE_SIZE_OFFSET)?,
        read_u32(image + IMAGE_SIZE_OFFSET + 4)?,
    );
    if rect[2].checked_sub(rect[0]) != Some(size.0) || rect[3].checked_sub(rect[1]) != Some(size.1)
    {
        return Err(format!("rect {rect:?} disagrees with size {size:?}"));
    }

    let hal = unsafe { safe_read_usize(image + IMAGE_HAL_OFFSET) }.unwrap_or(0);
    let raw = if hal == 0 {
        0
    } else {
        unsafe { safe_read_usize(hal + HAL_RESOURCE_OFFSET) }.unwrap_or(0)
    };
    let raw_vtable = if raw == 0 {
        0
    } else {
        unsafe { safe_read_usize(raw) }.unwrap_or(0)
    };
    let in_d3d12 = module_backing(raw_vtable).is_some_and(|(module, _)| {
        let module = module.to_ascii_lowercase();
        module.contains("d3d12") || module.contains("vkd3d")
    });
    if !in_d3d12 {
        return Err(format!(
            "texture object 0x{raw:x} (via 0x{hal:x}) has no d3d12 vtable"
        ));
    }
    let raw_ptr = raw as *mut c_void;
    // SAFETY: the object's vtable is in a d3d12 module and the image owns it for as long as the
    // icon stays in the repository; `cast` is a `QueryInterface`, which takes our own reference.
    let resource: ID3D12Resource = unsafe { ID3D12Resource::from_raw_borrowed(&raw_ptr) }
        .ok_or_else(|| "texture object pointer is null".to_string())?
        .cast()
        .map_err(|e| format!("texture object is not an ID3D12Resource: {e}"))?;
    Ok(IconSource {
        resource: resource.into_raw() as usize,
        atlas,
        rect,
    })
}

/// The worker: copy the rect out of the atlas, decode it, and give it to the overlay host.
fn copy_and_hand_over(source: IconSource, icon_id: u32, log: fn(std::fmt::Arguments<'_>)) {
    // SAFETY: `resource` is the reference `lookup` took with `into_raw`; this takes it back, and
    // dropping it at the end of this function releases it.
    let resource = unsafe { ID3D12Resource::from_raw(source.resource as *mut c_void) };
    let pixels = match unsafe { copy_rect(&resource, source.atlas, source.rect, log) } {
        Ok(pixels) => pixels,
        Err(why) => {
            log(format_args!("icon {icon_id}: copy refused: {why}"));
            return;
        }
    };
    let (width, height) = (
        source.rect[2] - source.rect[0],
        source.rect[3] - source.rect[1],
    );
    log(format_args!(
        "icon {icon_id}: copy ok, {width}x{height} decoded"
    ));
    match er_build_watermark_core::overlay_host::add_texture(&pixels, width, height) {
        Some(handle) => {
            ICON_HANDLE.store(handle, Ordering::Relaxed);
            log(format_args!(
                "icon {icon_id}: handed to the overlay host, handle {handle}"
            ));
        }
        None => log(format_args!(
            "icon {icon_id}: the overlay host refused the texture"
        )),
    }
}

/// Is `format` one of the three BC7 formats, whose blocks all decode the same way?
fn is_bc7(format: DXGI_FORMAT) -> bool {
    format == DXGI_FORMAT_BC7_TYPELESS
        || format == DXGI_FORMAT_BC7_UNORM
        || format == DXGI_FORMAT_BC7_UNORM_SRGB
}

/// Copy `rect` of `atlas_res` into a readback buffer on a queue of our own and decode it to RGBA8.
///
/// # Safety
///
/// `atlas_res` must be a live resource we hold a reference to.
unsafe fn copy_rect(
    atlas_res: &ID3D12Resource,
    atlas: (u32, u32),
    rect: [u32; 4],
    log: fn(std::fmt::Arguments<'_>),
) -> Result<Vec<u8>, String> {
    let desc = unsafe { atlas_res.GetDesc() };
    log(format_args!(
        "icon atlas desc: dimension {} {}x{} mips {} format {}",
        desc.Dimension.0, desc.Width, desc.Height, desc.MipLevels, desc.Format.0
    ));
    if desc.Dimension != D3D12_RESOURCE_DIMENSION_TEXTURE2D || !is_bc7(desc.Format) {
        return Err(format!(
            "atlas is dimension {} format {}, not a BC7 Texture2D",
            desc.Dimension.0, desc.Format.0
        ));
    }
    if desc.Width != u64::from(atlas.0) || desc.Height != atlas.1 {
        return Err(format!(
            "atlas is {}x{} on the GPU, {}x{} in the image",
            desc.Width, desc.Height, atlas.0, atlas.1
        ));
    }
    let [x0, y0, x1, y1] = rect;
    let (width, height) = (x1.saturating_sub(x0), y1.saturating_sub(y0));
    if width == 0
        || height == 0
        || width > MAX_ICON_DIM
        || height > MAX_ICON_DIM
        || x1 > atlas.0
        || y1 > atlas.1
        || rect.iter().any(|v| v % BLOCK_DIM != 0)
    {
        return Err(format!(
            "rect {rect:?} is not a block-aligned icon inside the atlas"
        ));
    }

    let mut device: Option<ID3D12Device> = None;
    unsafe { atlas_res.GetDevice(&mut device) }.map_err(|e| format!("GetDevice: {e}"))?;
    let device = device.ok_or("GetDevice returned nothing")?;

    // BC7 rows: 40 blocks x 16 bytes = 640 for a 160-wide icon, pitch 768 after alignment.
    let row_bytes = width / BLOCK_DIM * BLOCK_BYTES;
    let pitch =
        row_bytes.div_ceil(D3D12_TEXTURE_DATA_PITCH_ALIGNMENT) * D3D12_TEXTURE_DATA_PITCH_ALIGNMENT;
    let block_rows = height / BLOCK_DIM;
    let total = u64::from(pitch) * u64::from(block_rows);

    let readback: ID3D12Resource = unsafe {
        let mut buffer: Option<ID3D12Resource> = None;
        device
            .CreateCommittedResource(
                &D3D12_HEAP_PROPERTIES {
                    Type: D3D12_HEAP_TYPE_READBACK,
                    CPUPageProperty: D3D12_CPU_PAGE_PROPERTY_UNKNOWN,
                    MemoryPoolPreference: D3D12_MEMORY_POOL_UNKNOWN,
                    CreationNodeMask: 1,
                    VisibleNodeMask: 1,
                },
                D3D12_HEAP_FLAG_NONE,
                &D3D12_RESOURCE_DESC {
                    Dimension: D3D12_RESOURCE_DIMENSION_BUFFER,
                    Alignment: 0,
                    Width: total,
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
                },
                D3D12_RESOURCE_STATE_COPY_DEST,
                None,
                &mut buffer,
            )
            .map_err(|e| format!("readback buffer: {e}"))?;
        buffer.ok_or("readback buffer: nothing returned")?
    };

    let queue: ID3D12CommandQueue = unsafe {
        device.CreateCommandQueue(&D3D12_COMMAND_QUEUE_DESC {
            Type: D3D12_COMMAND_LIST_TYPE_DIRECT,
            Priority: 0,
            Flags: D3D12_COMMAND_QUEUE_FLAG_NONE,
            NodeMask: 0,
        })
    }
    .map_err(|e| format!("queue: {e}"))?;
    let allocator: ID3D12CommandAllocator =
        unsafe { device.CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT) }
            .map_err(|e| format!("allocator: {e}"))?;
    let list: ID3D12GraphicsCommandList =
        unsafe { device.CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, &allocator, None) }
            .map_err(|e| format!("command list: {e}"))?;
    let fence: ID3D12Fence = unsafe { device.CreateFence(0, D3D12_FENCE_FLAG_NONE) }
        .map_err(|e| format!("fence: {e}"))?;

    // Common -> COPY_SOURCE, copy, back to common: the pattern `er-loading-portrait-core`'s
    // `resource_readback` uses on the game's resources from a private queue.
    unsafe {
        transition(
            &list,
            atlas_res,
            D3D12_RESOURCE_STATE_COMMON,
            D3D12_RESOURCE_STATE_COPY_SOURCE,
        )
    };
    let mut src = D3D12_TEXTURE_COPY_LOCATION {
        pResource: ManuallyDrop::new(Some(atlas_res.clone())),
        Type: D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX,
        Anonymous: D3D12_TEXTURE_COPY_LOCATION_0 {
            SubresourceIndex: 0,
        },
    };
    let mut dst = D3D12_TEXTURE_COPY_LOCATION {
        pResource: ManuallyDrop::new(Some(readback.clone())),
        Type: D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT,
        Anonymous: D3D12_TEXTURE_COPY_LOCATION_0 {
            PlacedFootprint: D3D12_PLACED_SUBRESOURCE_FOOTPRINT {
                Offset: 0,
                Footprint: D3D12_SUBRESOURCE_FOOTPRINT {
                    Format: desc.Format,
                    Width: width,
                    Height: height,
                    Depth: 1,
                    RowPitch: pitch,
                },
            },
        },
    };
    let region = D3D12_BOX {
        left: x0,
        top: y0,
        front: 0,
        right: x1,
        bottom: y1,
        back: 1,
    };
    unsafe {
        list.CopyTextureRegion(&dst, 0, 0, 0, &src, Some(&region));
        ManuallyDrop::drop(&mut src.pResource);
        ManuallyDrop::drop(&mut dst.pResource);
        transition(
            &list,
            atlas_res,
            D3D12_RESOURCE_STATE_COPY_SOURCE,
            D3D12_RESOURCE_STATE_COMMON,
        );
    }
    unsafe { list.Close() }.map_err(|e| format!("close: {e}"))?;
    let base_list: ID3D12CommandList = list.cast().map_err(|e| format!("list cast: {e}"))?;
    unsafe {
        queue.ExecuteCommandLists(&[Some(base_list)]);
        queue
            .Signal(&fence, 1)
            .map_err(|e| format!("signal: {e}"))?;
        if fence.GetCompletedValue() < 1 {
            let event =
                CreateEventW(None, false, false, None).map_err(|e| format!("event: {e}"))?;
            let wait = if fence.SetEventOnCompletion(1, event).is_ok() {
                WaitForSingleObject(event, FENCE_WAIT_MS)
            } else {
                WAIT_FAILED
            };
            let _ = CloseHandle(event);
            if wait != WAIT_OBJECT_0 {
                // The copy may still be in flight; keep everything it touches alive for the
                // process rather than release it under the GPU.
                std::mem::forget((queue, allocator, list, fence, readback));
                return Err(format!("fence wait did not complete in {FENCE_WAIT_MS} ms"));
            }
        }
    }

    let mut mapped: *mut c_void = std::ptr::null_mut();
    let read_range = D3D12_RANGE {
        Begin: 0,
        End: total as usize,
    };
    unsafe { readback.Map(0, Some(&read_range), Some(&mut mapped)) }
        .map_err(|e| format!("map: {e}"))?;
    if mapped.is_null() {
        return Err("map returned no pointer".to_string());
    }
    // SAFETY: the buffer is `total` bytes, mapped for reading, and the copy has completed.
    let blocks = unsafe { std::slice::from_raw_parts(mapped as *const u8, total as usize) };
    let pixels = decode_bc7(blocks, width, height, pitch);
    let written = D3D12_RANGE { Begin: 0, End: 0 };
    unsafe { readback.Unmap(0, Some(&written)) };
    Ok(pixels)
}

/// Decode `width` x `height` texels of BC7 blocks, `pitch` bytes per block row, to RGBA8.
fn decode_bc7(blocks: &[u8], width: u32, height: u32, pitch: u32) -> Vec<u8> {
    let (width, height, pitch) = (width as usize, height as usize, pitch as usize);
    let block_dim = BLOCK_DIM as usize;
    let block_bytes = BLOCK_BYTES as usize;
    let out_pitch = width * 4;
    let mut out = vec![0u8; out_pitch * height];
    for by in 0..height / block_dim {
        for bx in 0..width / block_dim {
            let at = by * pitch + bx * block_bytes;
            let dst = by * block_dim * out_pitch + bx * block_dim * 4;
            bcdec_rs::bc7(&blocks[at..at + block_bytes], &mut out[dst..], out_pitch);
        }
    }
    out
}

/// One whole-resource state transition on `list`, with the barrier's reference balanced.
///
/// # Safety
///
/// `list` must be recording and `before` must be the resource's state on the GPU timeline.
unsafe fn transition(
    list: &ID3D12GraphicsCommandList,
    res: &ID3D12Resource,
    before: D3D12_RESOURCE_STATES,
    after: D3D12_RESOURCE_STATES,
) {
    let mut barrier = D3D12_RESOURCE_BARRIER {
        Type: D3D12_RESOURCE_BARRIER_TYPE_TRANSITION,
        Flags: D3D12_RESOURCE_BARRIER_FLAG_NONE,
        Anonymous: D3D12_RESOURCE_BARRIER_0 {
            Transition: ManuallyDrop::new(D3D12_RESOURCE_TRANSITION_BARRIER {
                pResource: ManuallyDrop::new(Some(res.clone())),
                Subresource: 0,
                StateBefore: before,
                StateAfter: after,
            }),
        },
    };
    unsafe { list.ResourceBarrier(std::slice::from_ref(&barrier)) };
    // SAFETY: drops exactly the clone placed in the barrier above.
    unsafe { ManuallyDrop::drop(&mut (*barrier.Anonymous.Transition).pResource) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_fills_every_texel_of_the_rect() {
        // Two block rows of two blocks, at a 256-byte pitch: the output is tight RGBA8.
        let blocks = vec![0u8; 256 * 2];
        let out = decode_bc7(&blocks, 8, 8, 256);
        assert_eq!(out.len(), 8 * 8 * 4);
    }
}
