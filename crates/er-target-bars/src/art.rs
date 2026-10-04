//! The game's own HP-bar art, copied out of the GPU at runtime and handed to the overlay host.
//!
//! The copies under the game's bar are drawn with the bar's real images, not with flat rectangles:
//! `MENU_FL_HP_Base` (the dark base the fill sits on) drawn as it is, and `MENU_FL_Red` (the fill)
//! recoloured per channel. Nothing is read from disk and nothing is committed: both images are
//! looked up in the game's Scaleform texture repository and copied out of the atlas it already
//! holds on the GPU, every launch.
//!
//! # What was measured
//!
//! `scripts/frida/target-bars-hud-art-probe.js`, live on 1.17.1, 2026-10-04: both names resolve
//! through the repository lookup (the call `er-r3-view` makes for item icons) to rects of one
//! 4096 x 512 BC7 atlas (`DXGI_FORMAT_BC7_UNORM`, 98): `MENU_FL_Red` at `0,113..2898,145` and
//! `MENU_FL_HP_Base` at `0,29..2926,73`. Neither rect is 4-texel aligned, so the copy takes the
//! enclosing block-aligned region and crops after decoding. The same probe saw `MenuWindowJob::Run`
//! and the lock-on update on one thread (2093 and 300 calls, both thread 384): the frame-begin task
//! this DLL runs in is on the game's main thread, which is the thread the repository's owner uses.
//! Both lookups hit, so the call inserts nothing.
//!
//! # Recolouring
//!
//! The fill is stored as its value channel, `max(r, g, b)`, with its alpha untouched, and drawn
//! through imgui's per-vertex tint by [`tint`]: the channel's colour scaled so its largest
//! component is 1. A texel of value `v` therefore comes out as the channel's hue and saturation at
//! value `v` -- an HSV hue and saturation replacement that keeps the game's value channel, so its
//! shading and edge falloff survive and only the colour changes. A plain multiply of the red art
//! by a colour cannot do this: red times green is black.
//!
//! Why not a true hue rotation of the red art per channel: it needs either a pixel shader, which
//! the shared imgui host does not offer a guest, or one uploaded texture per channel (ten), from a
//! host that accepts 32 for the whole process and never frees one. One grey texture and a vertex
//! colour cost one upload and draw any colour.

// Several items here are consumed only by the Windows build; the host build keeps them for tests.
#![cfg_attr(not(windows), allow(dead_code))]

/// The HUD movie's export names for the two images.
pub const BASE_IMAGE: &str = "MENU_FL_HP_Base";
pub const FILL_IMAGE: &str = "MENU_FL_Red";

/// BC7 stores 4 x 4 texels in 16 bytes.
pub const BLOCK_DIM: u32 = 4;
pub const BLOCK_BYTES: u32 = 16;

/// The block-aligned region of `atlas` enclosing `rect` (`[x0, y0, x1, y1]`), or `None` when the
/// rect is empty or outside the atlas.
pub fn block_region(rect: [u32; 4], atlas: (u32, u32)) -> Option<[u32; 4]> {
    let [x0, y0, x1, y1] = rect;
    if x0 >= x1 || y0 >= y1 || x1 > atlas.0 || y1 > atlas.1 {
        return None;
    }
    let down = |v: u32| v - v % BLOCK_DIM;
    let up = |v: u32| v.div_ceil(BLOCK_DIM) * BLOCK_DIM;
    let region = [down(x0), down(y0), up(x1), up(y1)];
    (region[2] <= atlas.0 && region[3] <= atlas.1).then_some(region)
}

/// The smallest region covering both rects, block-aligned.
pub fn union_region(a: [u32; 4], b: [u32; 4], atlas: (u32, u32)) -> Option<[u32; 4]> {
    block_region(
        [
            a[0].min(b[0]),
            a[1].min(b[1]),
            a[2].max(b[2]),
            a[3].max(b[3]),
        ],
        atlas,
    )
}

/// Cut `rect` out of tightly packed RGBA8 `pixels` that cover `region`.
pub fn crop(pixels: &[u8], region: [u32; 4], rect: [u32; 4]) -> Option<Vec<u8>> {
    let region_width = (region[2] - region[0]) as usize;
    let region_height = (region[3] - region[1]) as usize;
    if pixels.len() != region_width * region_height * 4
        || rect[0] < region[0]
        || rect[1] < region[1]
        || rect[2] > region[2]
        || rect[3] > region[3]
        || rect[0] >= rect[2]
        || rect[1] >= rect[3]
    {
        return None;
    }
    let width = (rect[2] - rect[0]) as usize;
    let mut out = Vec::with_capacity(width * (rect[3] - rect[1]) as usize * 4);
    for y in rect[1]..rect[3] {
        let row = (y - region[1]) as usize * region_width;
        let start = (row + (rect[0] - region[0]) as usize) * 4;
        out.extend_from_slice(&pixels[start..start + width * 4]);
    }
    Some(out)
}

/// Each texel's value channel, `max(r, g, b)`, in all three colour channels; alpha kept.
pub fn value_grey(pixels: &[u8]) -> Vec<u8> {
    pixels
        .as_chunks::<4>()
        .0
        .iter()
        .flat_map(|&[r, g, b, a]| {
            let value = r.max(g).max(b);
            [value, value, value, a]
        })
        .collect()
}

/// The vertex colour that turns the value-grey fill into `color`'s hue and saturation at the
/// texel's own value: `color` scaled so its largest component is 1, alpha kept.
pub fn tint(color: [f32; 4]) -> [f32; 4] {
    let peak = color[0].max(color[1]).max(color[2]);
    if peak <= 0.0 {
        return [0.0, 0.0, 0.0, color[3]];
    }
    [color[0] / peak, color[1] / peak, color[2] / peak, color[3]]
}

#[cfg(windows)]
pub use gpu::{base_and_fill, request_once};

#[cfg(windows)]
mod gpu {
    //! The lookup, the GPU copy and the hand-off. The copy and the BC7 decode follow
    //! `er-r3-view`'s `item_icon` (a private queue, a readback buffer, `bcdec_rs`), generalised to
    //! an unaligned rect wider than an icon.

    use std::ffi::c_void;
    use std::mem::ManuallyDrop;
    use std::sync::Mutex;

    use er_game_base::mem::{module_backing, safe_read_i32, safe_read_usize};
    use windows::Win32::Foundation::{CloseHandle, WAIT_FAILED, WAIT_OBJECT_0};
    use windows::Win32::Graphics::Direct3D12::{
        D3D12_BOX, D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_COMMAND_QUEUE_DESC,
        D3D12_COMMAND_QUEUE_FLAG_NONE, D3D12_CPU_PAGE_PROPERTY_UNKNOWN, D3D12_FENCE_FLAG_NONE,
        D3D12_HEAP_FLAG_NONE, D3D12_HEAP_PROPERTIES, D3D12_HEAP_TYPE_READBACK,
        D3D12_MEMORY_POOL_UNKNOWN, D3D12_PLACED_SUBRESOURCE_FOOTPRINT, D3D12_RANGE,
        D3D12_RESOURCE_BARRIER, D3D12_RESOURCE_BARRIER_0, D3D12_RESOURCE_BARRIER_FLAG_NONE,
        D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, D3D12_RESOURCE_DESC,
        D3D12_RESOURCE_DIMENSION_BUFFER, D3D12_RESOURCE_DIMENSION_TEXTURE2D,
        D3D12_RESOURCE_FLAG_NONE, D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_DEST,
        D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATES, D3D12_RESOURCE_TRANSITION_BARRIER,
        D3D12_SUBRESOURCE_FOOTPRINT, D3D12_TEXTURE_COPY_LOCATION, D3D12_TEXTURE_COPY_LOCATION_0,
        D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT, D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX,
        D3D12_TEXTURE_DATA_PITCH_ALIGNMENT, D3D12_TEXTURE_LAYOUT_ROW_MAJOR, ID3D12CommandAllocator,
        ID3D12CommandList, ID3D12CommandQueue, ID3D12Device, ID3D12Fence,
        ID3D12GraphicsCommandList, ID3D12Resource,
    };
    use windows::Win32::Graphics::Dxgi::Common::{
        DXGI_FORMAT, DXGI_FORMAT_BC7_TYPELESS, DXGI_FORMAT_BC7_UNORM, DXGI_FORMAT_BC7_UNORM_SRGB,
        DXGI_FORMAT_UNKNOWN, DXGI_SAMPLE_DESC,
    };
    use windows::Win32::System::Threading::{CreateEventW, WaitForSingleObject};
    use windows::core::Interface;

    use super::{BASE_IMAGE, BLOCK_BYTES, BLOCK_DIM, FILL_IMAGE};

    /// 1.16.2 rva of the Scaleform texture repository global (1.17.1 `0x3d86580`), translated for
    /// the running build by `er_game_base`, as `er-r3-view` does.
    const SCALEFORM_TEXTURE_REPOSITORY_GLOBAL_RVA: usize = 0x3d82510;
    /// 1.16.2 rva of `lookup(repo, out, wchar* name)` (1.17.1 `0xd65c00`).
    const SCALEFORM_TEXTURE_LOOKUP_RVA: u32 = 0xd63e50;
    /// `CS::ScaleformImageResource`: its `CSTextureImage`, and its symbol.
    const RESOURCE_IMAGE_OFFSET: usize = 0x18;
    const RESOURCE_SYMBOL_OFFSET: usize = 0x70;
    /// `CS::CSTextureImage` on 1.17.1: atlas size, rect, and the renderer's texture object whose
    /// `+0x70` is the `ID3D12Resource`.
    const IMAGE_ATLAS_W_OFFSET: usize = 0x2c;
    const IMAGE_ATLAS_H_OFFSET: usize = 0x30;
    const IMAGE_RECT_OFFSET: usize = 0x74;
    const IMAGE_HAL_OFFSET: usize = 0x10;
    const HAL_RESOURCE_OFFSET: usize = 0x70;
    /// Bound on the copy's fence wait.
    const FENCE_WAIT_MS: u32 = 2000;

    /// Where the art is: not asked for yet, being copied, given up on, or uploaded as the host's
    /// texture handles for the base and the recolourable fill.
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum ArtState {
        NotRequested,
        Pending,
        Failed,
        Ready { base: u32, fill: u32 },
    }

    static STATE: Mutex<ArtState> = Mutex::new(ArtState::NotRequested);

    fn set_state(state: ArtState) {
        *STATE.lock().unwrap_or_else(|e| e.into_inner()) = state;
    }

    /// The host's handles for the base and the fill, once uploaded.
    pub fn base_and_fill() -> Option<(u32, u32)> {
        match *STATE.lock().unwrap_or_else(|e| e.into_inner()) {
            ArtState::Ready { base, fill } => Some((base, fill)),
            _ => None,
        }
    }

    /// One image's place in its atlas.
    struct Located {
        resource: usize,
        atlas: (u32, u32),
        rect: [u32; 4],
    }

    /// Look both images up and start the copy, the first time this is called.
    ///
    /// # Safety
    ///
    /// On the game's main thread (the frame-begin task), where the repository is owned.
    pub unsafe fn request_once(log: fn(std::fmt::Arguments<'_>)) {
        {
            let mut state = STATE.lock().unwrap_or_else(|e| e.into_inner());
            if *state != ArtState::NotRequested {
                return;
            }
            *state = ArtState::Pending;
        }
        let located = unsafe { (locate(BASE_IMAGE), locate(FILL_IMAGE)) };
        let (base, fill) = match located {
            (Ok(base), Ok(fill)) => (base, fill),
            (base, fill) => {
                set_state(ArtState::Failed);
                for (name, result) in [(BASE_IMAGE, base), (FILL_IMAGE, fill)] {
                    match result {
                        Ok(located) => release(located.resource),
                        Err(why) => log(format_args!(
                            "art: {name}: {why}; the copies fall back to flat bars"
                        )),
                    }
                }
                return;
            }
        };
        log(format_args!(
            "art: {BASE_IMAGE} rect {:?}, {FILL_IMAGE} rect {:?}, atlas {:?}, same texture {}",
            base.rect,
            fill.rect,
            base.atlas,
            base.resource == fill.resource
        ));
        let spawned = std::thread::Builder::new()
            .name("er-target-bars-art".to_owned())
            .spawn(move || copy_and_hand_over(base, fill, log));
        if spawned.is_err() {
            set_state(ArtState::Failed);
            log(format_args!("art: could not spawn the copy thread"));
        }
    }

    /// Release the reference [`locate`] took.
    fn release(resource: usize) {
        // SAFETY: a reference `locate` took with `into_raw`; dropping it releases it.
        drop(unsafe { ID3D12Resource::from_raw(resource as *mut c_void) });
    }

    /// # Safety
    ///
    /// As [`request_once`].
    unsafe fn locate(name: &str) -> Result<Located, String> {
        let game_base = er_game_base::mem::game_module_base()?;
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
            return Err("texture repository global has no mapping for this build".to_owned());
        }
        let repo = unsafe { safe_read_usize(repo_global) }.unwrap_or(0);
        if repo == 0 {
            return Err("texture repository global is null".to_owned());
        }
        let vtable = unsafe { safe_read_usize(repo) }.unwrap_or(0);
        if !er_game_base::mem::vtable_in_game_image(vtable, game_base) {
            return Err(format!("texture repository 0x{repo:x} has no game vtable"));
        }
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
        // SAFETY: the vtable is in a d3d12 module and the image owns the object for as long as
        // the HUD lives; `cast` is a `QueryInterface`, which takes our own reference.
        let resource: ID3D12Resource = unsafe { ID3D12Resource::from_raw_borrowed(&raw_ptr) }
            .ok_or_else(|| "texture object pointer is null".to_owned())?
            .cast()
            .map_err(|e| format!("texture object is not an ID3D12Resource: {e}"))?;
        Ok(Located {
            resource: resource.into_raw() as usize,
            atlas,
            rect,
        })
    }

    /// The worker: copy the region covering both images, decode, crop, recolour the fill, and
    /// give both to the overlay host.
    fn copy_and_hand_over(base: Located, fill: Located, log: fn(std::fmt::Arguments<'_>)) {
        // SAFETY: the references `locate` took; dropping them at the end releases them.
        let base_res = unsafe { ID3D12Resource::from_raw(base.resource as *mut c_void) };
        let fill_res = unsafe { ID3D12Resource::from_raw(fill.resource as *mut c_void) };
        let result = (|| -> Result<(u32, u32), String> {
            let cut = |pixels: &[u8], located: &Located, region: [u32; 4]| {
                super::crop(pixels, region, located.rect)
                    .ok_or_else(|| format!("crop of {:?} out of {region:?} failed", located.rect))
            };
            let decode = |res: &ID3D12Resource, located: &Located, region: [u32; 4]| {
                let pixels = unsafe { copy_region(res, located.atlas, region) }?;
                cut(&pixels, located, region)
            };
            let (base_pixels, fill_pixels) = if base.resource == fill.resource {
                // One atlas holds both (measured): one copy, two crops.
                let region = super::union_region(base.rect, fill.rect, base.atlas)
                    .ok_or("the two rects do not fit their atlas")?;
                let pixels = unsafe { copy_region(&base_res, base.atlas, region) }?;
                (cut(&pixels, &base, region)?, cut(&pixels, &fill, region)?)
            } else {
                let base_region = super::block_region(base.rect, base.atlas)
                    .ok_or("the base rect does not fit its atlas")?;
                let fill_region = super::block_region(fill.rect, fill.atlas)
                    .ok_or("the fill rect does not fit its atlas")?;
                (
                    decode(&base_res, &base, base_region)?,
                    decode(&fill_res, &fill, fill_region)?,
                )
            };
            let size = |rect: [u32; 4]| (rect[2] - rect[0], rect[3] - rect[1]);
            let (bw, bh) = size(base.rect);
            let (fw, fh) = size(fill.rect);
            let base_handle =
                er_build_watermark_core::overlay_host::add_texture(&base_pixels, bw, bh)
                    .ok_or("the overlay host refused the base")?;
            let fill_handle = er_build_watermark_core::overlay_host::add_texture(
                &super::value_grey(&fill_pixels),
                fw,
                fh,
            )
            .ok_or("the overlay host refused the fill")?;
            Ok((base_handle, fill_handle))
        })();
        match result {
            Ok((base, fill)) => {
                set_state(ArtState::Ready { base, fill });
                log(format_args!(
                    "art: copied and handed to the overlay host, base handle {base}, fill handle \
                     {fill}"
                ));
            }
            Err(why) => {
                set_state(ArtState::Failed);
                log(format_args!(
                    "art: {why}; the copies fall back to flat bars"
                ));
            }
        }
    }

    fn is_bc7(format: DXGI_FORMAT) -> bool {
        format == DXGI_FORMAT_BC7_TYPELESS
            || format == DXGI_FORMAT_BC7_UNORM
            || format == DXGI_FORMAT_BC7_UNORM_SRGB
    }

    /// Copy the block-aligned `region` of `atlas_res` into a readback buffer on a queue of our
    /// own and decode it to tightly packed RGBA8.
    ///
    /// # Safety
    ///
    /// `atlas_res` must be a live resource we hold a reference to.
    unsafe fn copy_region(
        atlas_res: &ID3D12Resource,
        atlas: (u32, u32),
        region: [u32; 4],
    ) -> Result<Vec<u8>, String> {
        let desc = unsafe { atlas_res.GetDesc() };
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
        let [x0, y0, x1, y1] = region;
        let (width, height) = (x1 - x0, y1 - y0);
        if region.iter().any(|v| v % BLOCK_DIM != 0) || width == 0 || height == 0 {
            return Err(format!("region {region:?} is not block-aligned"));
        }
        let mut device: Option<ID3D12Device> = None;
        unsafe { atlas_res.GetDevice(&mut device) }.map_err(|e| format!("GetDevice: {e}"))?;
        let device = device.ok_or("GetDevice returned nothing")?;

        let row_bytes = width / BLOCK_DIM * BLOCK_BYTES;
        let pitch = row_bytes.div_ceil(D3D12_TEXTURE_DATA_PITCH_ALIGNMENT)
            * D3D12_TEXTURE_DATA_PITCH_ALIGNMENT;
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
        let list: ID3D12GraphicsCommandList = unsafe {
            device.CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, &allocator, None)
        }
        .map_err(|e| format!("command list: {e}"))?;
        let fence: ID3D12Fence = unsafe { device.CreateFence(0, D3D12_FENCE_FLAG_NONE) }
            .map_err(|e| format!("fence: {e}"))?;

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
        let source_box = D3D12_BOX {
            left: x0,
            top: y0,
            front: 0,
            right: x1,
            bottom: y1,
            back: 1,
        };
        unsafe {
            list.CopyTextureRegion(&dst, 0, 0, 0, &src, Some(&source_box));
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
                    // The copy may still be in flight; keep what it touches alive for the process
                    // rather than release it under the GPU.
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
            return Err("map returned no pointer".to_owned());
        }
        // SAFETY: `total` bytes mapped for reading, after the copy completed.
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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_measured_rects_get_block_aligned_regions_inside_the_atlas() {
        let atlas = (4096, 512);
        assert_eq!(
            block_region([0, 113, 2898, 145], atlas),
            Some([0, 112, 2900, 148])
        );
        assert_eq!(
            block_region([0, 29, 2926, 73], atlas),
            Some([0, 28, 2928, 76])
        );
        assert_eq!(
            union_region([0, 29, 2926, 73], [0, 113, 2898, 145], atlas),
            Some([0, 28, 2928, 148])
        );
        assert_eq!(block_region([0, 0, 0, 4], atlas), None);
        assert_eq!(block_region([0, 0, 4100, 4], atlas), None);
    }

    #[test]
    fn crop_takes_exactly_the_rect() {
        // A 4x4 region whose texel (x, y) is [x, y, 0, 255].
        let region = [0, 0, 4, 4];
        let pixels: Vec<u8> = (0..4)
            .flat_map(|y| (0..4).flat_map(move |x| [x as u8, y as u8, 0, 255]))
            .collect();
        let out = crop(&pixels, region, [1, 2, 3, 3]).unwrap();
        assert_eq!(out, vec![1, 2, 0, 255, 2, 2, 0, 255]);
        assert_eq!(crop(&pixels, region, [0, 0, 5, 1]), None);
        assert_eq!(crop(&pixels[4..], region, [0, 0, 1, 1]), None);
    }

    #[test]
    fn the_fill_keeps_its_brightness_and_alpha_and_loses_its_hue() {
        let red = [200, 30, 20, 180, 0, 0, 0, 0];
        assert_eq!(value_grey(&red), vec![200, 200, 200, 180, 0, 0, 0, 0]);
    }

    #[test]
    fn the_tint_keeps_hue_and_saturation_and_hands_value_to_the_texture() {
        let gold = tint([0.85, 0.70, 0.30, 1.0]);
        assert!((gold[0] - 1.0).abs() < 1e-6);
        assert!((gold[1] / gold[2] - 0.70 / 0.30).abs() < 1e-5);
        assert_eq!(tint([0.0, 0.0, 0.0, 0.5]), [0.0, 0.0, 0.0, 0.5]);
        // A texel of value 200 tinted green keeps value 200.
        let green = tint([0.25, 0.65, 0.25, 1.0]);
        assert!((200.0 * green[1] - 200.0).abs() < 1e-3);
    }
}
