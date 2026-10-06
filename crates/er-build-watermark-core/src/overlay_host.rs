//! Who owns the swapchain's imgui context, and how everybody else draws on it anyway.
//!
//! # The bug this exists to make impossible
//!
//! hudhook's install latch is a plain `static`, and statics are per DLL. Two of our modules each
//! calling `Hudhook::apply()` therefore both believe they are first, both hook `Present`, and one
//! of them silently loses every frame from then on. [`claim_owner`] was added to stop that, but
//! it only ever told the loser to give up -- it gave the loser nowhere to draw. So the module
//! with the interactive UI could lose the race to a module that draws six words of grey text, and
//! the user would simply find their panel gone.
//!
//! Measured 2026-08-25, live, on the user's own session: `er-build-watermark` logged
//! `first render display_width=3840 rows=14` while `er-net-effects` logged
//! `hudhook dx12 overlay installed` and then `hudhook_render_count = 0` -- installed, never
//! rendered, no error anywhere. The interactive bar had been invisible since #336 added the
//! watermark shell, because me3 loads `er_build_watermark.dll` before `er_net_effects.dll` and
//! alphabetical order is not a design.
//!
//! A prior fix had the watermark sleep six seconds to let a richer UI claim the context first.
//! That was removed for being a sleep used as synchronization -- correctly -- but the yield it
//! implemented was load-bearing and nothing replaced it. This does, without any sleep.
//!
//! # The shape
//!
//! Exactly one module hosts the render loop; every other module registers a draw callback and the
//! host calls it each frame. Load order stops mattering, because whoever gets there first hosts
//! and everyone else is a guest -- the outcome is the same either way.
//!
//! A guest finds the host by walking the loaded-module list and calling
//! [`REGISTER_EXPORT`] on each. Every shell that links this crate exports it; each
//! implementation registers only if that module is the host, so exactly one call answers `true`.
//!
//! # The ABI, and why the tag is not optional
//!
//! The `ui` pointer crosses a DLL boundary as `*const c_void` and is cast back to `&Ui` in the
//! guest. That is only sound while host and guest were built against the same imgui, so every
//! registration carries [`OVERLAY_ABI_TAG`] and a host refuses a tag it does not recognise. A
//! refused guest draws nothing, which is a missing panel; accepting a mismatched one would
//! reinterpret an imgui context through the wrong struct layout inside `Present`, which is a
//! crash in the renderer with no useful stack. Missing panel is the better failure, and it logs.
//!
//! # Fonts
//!
//! A guest cannot add a font to the host's atlas itself: hudhook builds and uploads the atlas once
//! after `initialize`, in the host's DLL. So a guest hands TrueType bytes to the host through
//! [`ADD_FONT_EXPORT`] ([`add_font`] finds it the same way registration does), the host copies
//! them, and on its next frame -- from hudhook's before-frame hook, wired in [`designate_host`] --
//! trial-builds them in a scratch atlas, adds them to the real one, and lets hudhook rebuild and
//! re-upload. From then on every [`OverlayFrame`] carries the resulting `ImFont*` per handle,
//! which a guest reads with [`frame_font`] and pushes with [`with_font`]. Until a font is built,
//! or if it never builds, both answer with the default font.
//!
//! # Textures
//!
//! Images take the same route. A guest hands RGBA8 pixels to [`ADD_TEXTURE_EXPORT`] through
//! [`add_texture`]; the host validates and copies them (`texture_request`), uploads them from the
//! same before-frame hook with hudhook's `RenderContext::load_texture`, and from then on every
//! [`OverlayFrame`] carries an [`OverlayTextureSet`] per uploaded handle, which a guest reads with
//! [`frame_texture`] and draws with `DrawList::add_image`. A guest cannot upload itself: the
//! texture heap whose descriptors imgui samples belongs to the host's render engine.

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use hudhook::imgui::Ui;

/// A guest's per-frame draw. Receives the host's `&Ui` erased to a pointer.
///
/// # Safety
///
/// The pointer is a live `&Ui` for the duration of the call and must not outlive it.
pub type OverlayDrawFn = unsafe extern "C" fn(frame: *const OverlayFrame);

/// Everything a guest needs to draw into the host's imgui, handed over once per frame.
///
/// # Why the context and the allocators travel with the pointer
///
/// Dear ImGui keeps its current context in a plain global, and each DLL that links imgui gets its
/// own copy of that global. A guest handed only a `&Ui` therefore calls `ui.io()` against a NULL
/// `GImGui` and dies on the first dereference -- which is exactly what happened on 2026-08-25:
/// the guest logged that its render loop had initialised and then never logged the line four
/// statements later, drew nothing, and raised no crash anyone could see.
///
/// `imgui_context` is the host's `igGetCurrentContext()`, and the guest must install it with
/// `igSetCurrentContext` before touching `ui`. The allocator triple matters for the same reason
/// and is easier to miss: imgui allocates draw-list vertices internally, so a guest running on
/// its own allocator globals would allocate from one heap and hand the buffer to a host that
/// frees it on another.
#[repr(C)]
pub struct OverlayFrame {
    /// The host's live `&Ui`, erased. Valid only for the duration of the call.
    pub ui: *const c_void,
    /// The host's `ImGuiContext*`.
    pub imgui_context: *mut c_void,
    /// `ImGuiMemAllocFunc` as taken from the host.
    pub alloc_func: *mut c_void,
    /// `ImGuiMemFreeFunc` as taken from the host.
    pub free_func: *mut c_void,
    /// The allocator user-data the host was configured with.
    pub alloc_user_data: *mut c_void,
    /// One entry per font handle the host has finished building, `font_set_count` long. Null when
    /// there are none. Read it through [`frame_font`] rather than by hand.
    pub fonts: *const OverlayFontSet,
    /// Length of `fonts`.
    pub font_set_count: u32,
    /// `display_h / 1080`: the factor a guest scales its 1080p layout by.
    pub ui_scale: f32,
    /// One entry per texture handle the host has uploaded, `texture_count` long. Null when there
    /// are none. Read it through [`frame_texture`] rather than by hand.
    pub textures: *const OverlayTextureSet,
    /// Length of `textures`.
    pub texture_count: u32,
}

/// One uploaded [`add_texture`] handle: the imgui texture id to draw it with, and its size.
///
/// A handle appears here only once the host has uploaded it. Until then -- and forever, if the
/// upload failed -- [`frame_texture`] answers `None` and the guest draws nothing in its place.
#[repr(C)]
pub struct OverlayTextureSet {
    /// The handle [`add_texture`] returned.
    pub handle: u32,
    /// The value of the host's `imgui::TextureId`. An index into the host's texture heap, valid
    /// for the life of the host's render engine, which is the process.
    pub texture_id: usize,
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
}

/// The fonts behind one [`add_font`] handle, one `ImFont*` per requested size, in request order.
///
/// A handle appears here only once its fonts are in the atlas. Until then -- and forever, if the
/// host refused the bytes at build time -- [`frame_font`] answers `None` and the guest draws in
/// the default font.
#[repr(C)]
pub struct OverlayFontSet {
    /// The handle [`add_font`] returned.
    pub handle: u32,
    /// Length of `fonts`.
    pub count: u32,
    /// `ImFont*` per size. Valid for the life of the host's atlas, which is the process.
    pub fonts: *const *mut c_void,
}

/// Bumped whenever the imgui version behind this ABI changes. Host and guest must agree; see the
/// module docs for why a mismatch is refused rather than tolerated.
///
/// `0x0905` is hudhook 0.9.2 / imgui-sys 0.12 with the [`OverlayFrame`] handoff, including the
/// font sets, `ui_scale` and the texture sets appended to it. Each bump is a layout change a
/// guest built against the older tag would misread: `0x0902` to `0x0903` added the imgui context
/// and allocators, `0x0903` to `0x0904` the fonts, `0x0904` to `0x0905` the textures.
pub const OVERLAY_ABI_TAG: u32 = 0x0905;

/// The undecorated export every shell linking this crate must provide, so a guest can find the
/// host without knowing which module won.
pub const REGISTER_EXPORT: &[u8] = b"er_overlay_register_guest_v1\0";

/// The undecorated export a guest calls to hand the host TrueType bytes. Defined beside
/// [`REGISTER_EXPORT`] by [`export_overlay_host!`], and answered only by the host.
pub const ADD_FONT_EXPORT: &[u8] = b"er_overlay_add_font_v1\0";

/// Signature of [`ADD_FONT_EXPORT`]: bytes, byte count, sizes, size count; nonzero handle or `0`.
pub type AddFontFn = unsafe extern "C" fn(*const u8, usize, *const f32, u32) -> u32;

/// The undecorated export a guest calls to hand the host RGBA8 pixels. Defined beside
/// [`REGISTER_EXPORT`] by [`export_overlay_host!`], and answered only by the host.
pub const ADD_TEXTURE_EXPORT: &[u8] = b"er_overlay_add_texture_v1\0";

/// Signature of [`ADD_TEXTURE_EXPORT`]: pixels, byte count, width, height; nonzero handle or `0`.
pub type AddTextureFn = unsafe extern "C" fn(*const u8, usize, u32, u32) -> u32;

/// True in the one module that won the mutex, set the instant it wins.
///
/// Distinct from [`IS_CONFIRMED_HOST`] on purpose, and the distinction is the whole race. The
/// watermark claims from a spawned thread (hudhook's install takes locks that must not run under
/// the loader lock), so `apply()` finishes some unknown time after the claim. A guest that looks
/// for a host in that window would find nobody, claim the mutex itself, fail because the
/// watermark already holds it, and give up -- which is precisely the vanished-panel bug, merely
/// moved. Designation happens synchronously inside [`claim_owner`], so from the moment the mutex
/// is taken there is always exactly one module answering yes.
static IS_DESIGNATED_HOST: AtomicBool = AtomicBool::new(false);

/// True once that module's `Hudhook::apply()` actually returned `Ok`.
static IS_CONFIRMED_HOST: AtomicBool = AtomicBool::new(false);

/// Guests registered with this module. Only the host's copy is ever non-empty.
static GUESTS: Mutex<Vec<OverlayDrawFn>> = Mutex::new(Vec::new());

/// Guest draws dispatched, so "the host never rendered" and "the host rendered but the guest was
/// never registered" are different numbers instead of the same blank screen.
static GUEST_DISPATCHES: AtomicUsize = AtomicUsize::new(0);

/// Guests refused for an ABI tag this host does not speak.
static GUESTS_REFUSED: AtomicUsize = AtomicUsize::new(0);

/// Called by [`claim_owner`] the instant the mutex is won, before any install is attempted.
pub fn designate_host() {
    IS_DESIGNATED_HOST.store(true, Ordering::SeqCst);
    // Every host passes through here, whichever crate's render loop it installs, so this is the
    // one place the font hook can be wired without each host remembering to. The hook lives in
    // this module's own copy of hudhook, which is the copy that will run the render loop.
    let _ = hudhook::set_before_frame_hook(apply_pending);
}

/// hudhook's before-frame hook: fonts into the atlas, then pixels into textures.
fn apply_pending(ctx: &mut hudhook::imgui::Context, render: &mut dyn hudhook::RenderContext) {
    apply_pending_fonts(ctx);
    apply_pending_textures(render);
}

/// Confirm the render loop is really installed. Called after `apply()` returns `Ok`.
pub fn become_host() {
    IS_CONFIRMED_HOST.store(true, Ordering::SeqCst);
    designate_host();
}

/// Is this module the one that will host the render loop (installed or about to be)?
pub fn is_host() -> bool {
    IS_DESIGNATED_HOST.load(Ordering::SeqCst)
}

/// Has this module's render loop actually been installed?
pub fn is_confirmed_host() -> bool {
    IS_CONFIRMED_HOST.load(Ordering::SeqCst)
}

/// How many guest draws this host has dispatched.
pub fn guest_dispatches() -> usize {
    GUEST_DISPATCHES.load(Ordering::Relaxed)
}

/// How many guests were refused for an ABI mismatch.
pub fn guests_refused() -> usize {
    GUESTS_REFUSED.load(Ordering::Relaxed)
}

/// Guests currently registered with this module.
pub fn guest_count() -> usize {
    GUESTS.lock().map(|g| g.len()).unwrap_or(0)
}

/// Accept a guest, if this module is the host and speaks its ABI.
///
/// This is the body every shell's `er_overlay_register_guest_v1` export forwards to. Returning
/// `false` is the ordinary answer from every non-host module -- a guest calls this on each loaded
/// module in turn and exactly one says yes.
pub fn register_guest(abi_tag: u32, draw: OverlayDrawFn) -> bool {
    if !is_host() {
        return false;
    }
    if abi_tag != OVERLAY_ABI_TAG {
        GUESTS_REFUSED.fetch_add(1, Ordering::Relaxed);
        return false;
    }
    match GUESTS.lock() {
        Ok(mut guests) => {
            // A module that registers twice would draw twice, and the second draw would fight the
            // first for the same pointer state. Idempotent by identity.
            if !guests
                .iter()
                .any(|existing| std::ptr::fn_addr_eq(*existing, draw))
            {
                guests.push(draw);
            }
            true
        }
        Err(_) => false,
    }
}

/// Call every registered guest with the live frame. Host render loops call this once per frame.
pub fn dispatch_guests(ui: &Ui) {
    // Cloned out of the lock before calling: a guest's draw may do anything, and holding the
    // registry lock across foreign code inside `Present` is how a renderer deadlocks.
    let guests: Vec<OverlayDrawFn> = match GUESTS.lock() {
        Ok(guests) => guests.clone(),
        Err(_) => return,
    };
    if guests.is_empty() {
        return;
    }
    let mut alloc_func = None;
    let mut free_func = None;
    let mut alloc_user_data = std::ptr::null_mut();
    // SAFETY: three live out-params; imgui always has allocators set by the time it renders.
    unsafe {
        hudhook::imgui::sys::igGetAllocatorFunctions(
            &mut alloc_func,
            &mut free_func,
            &mut alloc_user_data,
        );
    }
    // Held for the whole dispatch: the frame points into it.
    let fonts = PUBLISHED_FONTS
        .lock()
        .ok()
        .and_then(|published| published.clone());
    let (font_sets, font_set_count) = fonts.as_ref().map_or((std::ptr::null(), 0), |fonts| {
        (fonts.sets.as_ptr(), fonts.sets.len() as u32)
    });
    let textures = PUBLISHED_TEXTURES
        .lock()
        .ok()
        .and_then(|published| published.clone());
    let (texture_sets, texture_count) = textures.as_ref().map_or((std::ptr::null(), 0), |sets| {
        (sets.as_ptr(), sets.len() as u32)
    });
    let frame = OverlayFrame {
        ui: std::ptr::from_ref(ui).cast::<c_void>(),
        // SAFETY: called from inside the host's own render, so a context is current.
        imgui_context: unsafe { hudhook::imgui::sys::igGetCurrentContext() }.cast::<c_void>(),
        alloc_func: alloc_func.map_or(std::ptr::null_mut(), |f| f as *mut c_void),
        free_func: free_func.map_or(std::ptr::null_mut(), |f| f as *mut c_void),
        alloc_user_data,
        fonts: font_sets,
        font_set_count,
        ui_scale: crate::font_request::ui_scale(ui.io().display_size[1]),
        textures: texture_sets,
        texture_count,
    };
    for guest in guests {
        // SAFETY: `frame` outlives the call, and the guest accepted OVERLAY_ABI_TAG at
        // registration, so it reads the layout this crate wrote.
        unsafe { guest(&raw const frame) };
    }
    drop(fonts);
    drop(textures);
    GUEST_DISPATCHES.fetch_add(1, Ordering::Relaxed);
}

/// One font request as the host holds it.
enum FontSlotState {
    /// Accepted, copied, waiting for the next frame's rebuild.
    Pending { ttf: Vec<u8>, sizes: Vec<f32> },
    /// In the atlas. `ImFont*` per size, as integers so the registry stays `Send`.
    Built(Vec<usize>),
    /// The scratch-atlas trial build refused the bytes. The handle stays valid and resolves to
    /// nothing, so the guest falls back to the default font.
    Failed,
}

struct FontSlot {
    handle: u32,
    state: FontSlotState,
}

/// Every font request this host has accepted. Only the host's copy is ever non-empty.
static FONTS: Mutex<Vec<FontSlot>> = Mutex::new(Vec::new());

/// The built font sets in the layout [`OverlayFrame`] carries, replaced whole after each rebuild
/// so a frame can hold one snapshot without holding [`FONTS`].
static PUBLISHED_FONTS: Mutex<Option<Arc<PublishedFonts>>> = Mutex::new(None);

/// Font requests the host refused, at the export or at the trial build.
static FONTS_REFUSED: AtomicUsize = AtomicUsize::new(0);

/// Font sets this host has put in its atlas.
static FONTS_BUILT: AtomicUsize = AtomicUsize::new(0);

struct PublishedFonts {
    sets: Vec<OverlayFontSet>,
    // Owns the arrays `sets` points into. A `Box<[_]>` does not move its buffer when the outer
    // `Vec` does, so the pointers stay good for as long as this value lives.
    _storage: Vec<Box<[*mut c_void]>>,
}

// SAFETY: the raw pointers are `ImFont*` values owned by the host's atlas, which lives for the
// process and is only read through them; nothing here is mutated after construction.
unsafe impl Send for PublishedFonts {}
// SAFETY: as above, the value is immutable once built.
unsafe impl Sync for PublishedFonts {}

/// Font requests refused, at the export or at the trial build.
pub fn fonts_refused() -> usize {
    FONTS_REFUSED.load(Ordering::Relaxed)
}

/// Font sets built into the host's atlas.
pub fn fonts_built() -> usize {
    FONTS_BUILT.load(Ordering::Relaxed)
}

/// Accept a guest's font, if this module is the host. The body of every shell's
/// `er_overlay_add_font_v1` export.
///
/// Copies the bytes and the sizes, so the caller's buffers need only live for the call. Returns
/// the new handle, or `0` from a module that is not the host and for any refused request.
///
/// # Safety
///
/// `ttf` must be readable for `len` bytes and `sizes_px` for `n` floats, or be null.
pub unsafe fn add_font_v1(ttf: *const u8, len: usize, sizes_px: *const f32, n: u32) -> u32 {
    if !is_host() {
        return 0;
    }
    if ttf.is_null() || sizes_px.is_null() || len == 0 || n == 0 {
        FONTS_REFUSED.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    // Bounded before the slices are formed, so a wild length is refused rather than read.
    if len > crate::font_request::MAX_FONT_BYTES
        || n as usize > crate::font_request::MAX_SIZES_PER_FONT
    {
        FONTS_REFUSED.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    // SAFETY: the caller's contract, with both lengths already bounded.
    let (ttf, sizes) = unsafe {
        (
            std::slice::from_raw_parts(ttf, len),
            std::slice::from_raw_parts(sizes_px, n as usize),
        )
    };
    if crate::font_request::validate(ttf, sizes).is_err() {
        FONTS_REFUSED.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    let Ok(mut fonts) = FONTS.lock() else {
        return 0;
    };
    if fonts.len() >= crate::font_request::MAX_FONT_REQUESTS {
        FONTS_REFUSED.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    let handle = fonts.len() as u32 + 1;
    fonts.push(FontSlot {
        handle,
        state: FontSlotState::Pending {
            ttf: ttf.to_vec(),
            sizes: sizes.to_vec(),
        },
    });
    handle
}

/// Build one font into a throwaway atlas and report whether imgui could.
///
/// `ImFontAtlas::Build` answers `false` when `stbtt_InitFont` cannot open the bytes, and if that
/// happened to the host's own atlas the next `NewFrame` would assert -- an abort inside `Present`.
/// The scratch atlas takes that failure instead. One size is enough: the font either opens or it
/// does not, independent of size.
fn trial_build(ttf: &[u8], size_px: f32) -> bool {
    use hudhook::imgui::sys;
    let Ok(byte_count) = i32::try_from(ttf.len()) else {
        return false;
    };
    // SAFETY: a fresh atlas owned by this function. The copy is allocated with imgui's own
    // allocator because the atlas takes ownership of it (`FontDataOwnedByAtlas` defaults to
    // true) and frees it on destruction.
    unsafe {
        let atlas = sys::ImFontAtlas_ImFontAtlas();
        if atlas.is_null() {
            return false;
        }
        let copy = sys::igMemAlloc(ttf.len());
        if copy.is_null() {
            sys::ImFontAtlas_destroy(atlas);
            return false;
        }
        std::ptr::copy_nonoverlapping(ttf.as_ptr(), copy.cast::<u8>(), ttf.len());
        let font = sys::ImFontAtlas_AddFontFromMemoryTTF(
            atlas,
            copy,
            byte_count,
            size_px,
            std::ptr::null(),
            crate::font_request::GLYPH_RANGES.as_ptr(),
        );
        let built = !font.is_null() && sys::ImFontAtlas_Build(atlas);
        sys::ImFontAtlas_destroy(atlas);
        built
    }
}

/// hudhook's before-frame hook: move every pending font into the atlas.
///
/// Runs on the render thread between frames, the one point where the atlas is unlocked. Adding a
/// font clears the atlas's built flag, and hudhook rebuilds and re-uploads it right after this
/// returns. `try_lock` because this runs inside `Present`: a guest holding the registry for the
/// length of a copy delays its font by one frame, never the game.
fn apply_pending_fonts(ctx: &mut hudhook::imgui::Context) {
    use hudhook::imgui::{FontConfig, FontGlyphRanges, FontSource};

    let Ok(mut fonts) = FONTS.try_lock() else {
        return;
    };
    if !fonts
        .iter()
        .any(|slot| matches!(slot.state, FontSlotState::Pending { .. }))
    {
        return;
    }
    for slot in fonts.iter_mut() {
        let FontSlotState::Pending { ttf, sizes } =
            std::mem::replace(&mut slot.state, FontSlotState::Failed)
        else {
            continue;
        };
        let smallest = sizes.iter().copied().fold(f32::MAX, f32::min);
        if !trial_build(&ttf, smallest) {
            FONTS_REFUSED.fetch_add(1, Ordering::Relaxed);
            continue;
        }
        let atlas = ctx.fonts();
        let pointers = sizes
            .iter()
            .map(|&size_pixels| {
                let id = atlas.add_font(&[FontSource::TtfData {
                    data: &ttf,
                    size_pixels,
                    config: Some(FontConfig {
                        size_pixels,
                        glyph_ranges: FontGlyphRanges::from_slice(
                            &crate::font_request::GLYPH_RANGES,
                        ),
                        ..FontConfig::default()
                    }),
                }]);
                atlas
                    .get_font(id)
                    .map_or(0, |font| std::ptr::from_ref(font) as usize)
            })
            .collect();
        slot.state = FontSlotState::Built(pointers);
        FONTS_BUILT.fetch_add(1, Ordering::Relaxed);
    }
    let mut storage = Vec::new();
    let mut sets = Vec::new();
    for slot in fonts.iter() {
        let FontSlotState::Built(pointers) = &slot.state else {
            continue;
        };
        let array: Box<[*mut c_void]> = pointers
            .iter()
            .map(|&pointer| pointer as *mut c_void)
            .collect();
        sets.push(OverlayFontSet {
            handle: slot.handle,
            count: array.len() as u32,
            fonts: array.as_ptr(),
        });
        storage.push(array);
    }
    if let Ok(mut published) = PUBLISHED_FONTS.lock() {
        *published = Some(Arc::new(PublishedFonts {
            sets,
            _storage: storage,
        }));
    }
}

/// One texture request as the host holds it.
enum TextureSlotState {
    /// Accepted, copied, waiting for the next frame's upload.
    Pending(Vec<u8>),
    /// Uploaded; the value of the host's `TextureId`.
    Uploaded(usize),
    /// The upload failed. The handle stays valid and resolves to nothing.
    Failed,
}

struct TextureSlot {
    handle: u32,
    width: u32,
    height: u32,
    state: TextureSlotState,
}

/// Every texture request this host has accepted. Only the host's copy is ever non-empty.
static TEXTURES: Mutex<Vec<TextureSlot>> = Mutex::new(Vec::new());

/// The uploaded textures in the layout [`OverlayFrame`] carries, replaced whole after each upload
/// so a frame can hold one snapshot without holding [`TEXTURES`].
static PUBLISHED_TEXTURES: Mutex<Option<Arc<Vec<OverlayTextureSet>>>> = Mutex::new(None);

/// Texture requests the host refused, at the export or at the upload.
static TEXTURES_REFUSED: AtomicUsize = AtomicUsize::new(0);

/// Textures this host has uploaded.
static TEXTURES_UPLOADED: AtomicUsize = AtomicUsize::new(0);

/// Texture requests refused, at the export or at the upload.
pub fn textures_refused() -> usize {
    TEXTURES_REFUSED.load(Ordering::Relaxed)
}

/// Textures uploaded by this host.
pub fn textures_uploaded() -> usize {
    TEXTURES_UPLOADED.load(Ordering::Relaxed)
}

/// Accept a guest's RGBA8 image, if this module is the host. The body of every shell's
/// `er_overlay_add_texture_v1` export.
///
/// Copies the pixels, so the caller's buffer need only live for the call. Returns the new handle,
/// or `0` from a module that is not the host and for any refused request.
///
/// # Safety
///
/// `rgba` must be readable for `len` bytes, or be null.
pub unsafe fn add_texture_v1(rgba: *const u8, len: usize, width: u32, height: u32) -> u32 {
    if !is_host() {
        return 0;
    }
    // Validated before the slice is formed, so a wild length is refused rather than read.
    if rgba.is_null() || crate::texture_request::validate(len, width, height).is_err() {
        TEXTURES_REFUSED.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    // SAFETY: the caller's contract, with the length bounded by the validation above.
    let pixels = unsafe { std::slice::from_raw_parts(rgba, len) };
    let Ok(mut textures) = TEXTURES.lock() else {
        return 0;
    };
    // Every slot keeps its size after its pixels are uploaded, so the bytes so far are its sum.
    let bytes: usize = textures
        .iter()
        .map(|t| t.width as usize * t.height as usize * crate::texture_request::BYTES_PER_PIXEL)
        .sum();
    if textures.len() >= crate::texture_request::MAX_TEXTURE_REQUESTS
        || bytes + len > crate::texture_request::MAX_TEXTURE_BYTES
    {
        TEXTURES_REFUSED.fetch_add(1, Ordering::Relaxed);
        return 0;
    }
    let handle = textures.len() as u32 + 1;
    textures.push(TextureSlot {
        handle,
        width,
        height,
        state: TextureSlotState::Pending(pixels.to_vec()),
    });
    handle
}

/// The second half of hudhook's before-frame hook: upload every pending texture.
///
/// Runs on the render thread between frames. hudhook's D3D12 `load_texture` records the copy on
/// its own queue and waits for it, so the texture is resident before the frame that first samples
/// it. `try_lock` for the same reason as the fonts: a guest holding the registry delays its image
/// by one frame, never the game.
fn apply_pending_textures(render: &mut dyn hudhook::RenderContext) {
    let Ok(mut textures) = TEXTURES.try_lock() else {
        return;
    };
    if !textures
        .iter()
        .any(|slot| matches!(slot.state, TextureSlotState::Pending(_)))
    {
        return;
    }
    for slot in textures.iter_mut() {
        let TextureSlotState::Pending(pixels) =
            std::mem::replace(&mut slot.state, TextureSlotState::Failed)
        else {
            continue;
        };
        match render.load_texture(&pixels, slot.width, slot.height) {
            Ok(id) => {
                slot.state = TextureSlotState::Uploaded(id.id());
                TEXTURES_UPLOADED.fetch_add(1, Ordering::Relaxed);
            }
            Err(_) => {
                TEXTURES_REFUSED.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
    let sets: Vec<OverlayTextureSet> = textures
        .iter()
        .filter_map(|slot| match slot.state {
            TextureSlotState::Uploaded(texture_id) => Some(OverlayTextureSet {
                handle: slot.handle,
                texture_id,
                width: slot.width,
                height: slot.height,
            }),
            _ => None,
        })
        .collect();
    if let Ok(mut published) = PUBLISHED_TEXTURES.lock() {
        *published = Some(Arc::new(sets));
    }
}

/// Hand RGBA8 pixels (`width * height * 4` bytes, rows top to bottom) to whichever loaded module
/// hosts the overlay.
///
/// Returns the handle to pass to [`frame_texture`], or `None` when no module hosts an overlay yet
/// or the host refused the request. The image is drawable from the first frame after the host's
/// next upload; until then [`frame_texture`] answers `None`.
#[cfg(windows)]
pub fn add_texture(rgba: &[u8], width: u32, height: u32) -> Option<u32> {
    for module in er_game_base::build_id::loaded_module_handles() {
        // SAFETY: a handle straight out of the loader's own module list, and a NUL-terminated
        // export name. A module without the export answers None.
        let Some(symbol) =
            (unsafe { er_game_base::build_id::module_export(module, ADD_TEXTURE_EXPORT) })
        else {
            continue;
        };
        // SAFETY: this export is defined only by `export_overlay_host!` in this crate, so any
        // module answering to the name has our signature.
        let add: AddTextureFn = unsafe { std::mem::transmute(symbol) };
        // SAFETY: the slice is live for the call, and the host copies it before returning.
        let handle = unsafe { add(rgba.as_ptr(), rgba.len(), width, height) };
        if handle != 0 {
            return Some(handle);
        }
    }
    None
}

/// The imgui texture id and pixel size for `handle`, if the host has uploaded it.
///
/// # Safety
///
/// `frame` must be the pointer the host just passed to the guest's draw, or null.
pub unsafe fn frame_texture(
    frame: *const OverlayFrame,
    handle: u32,
) -> Option<(hudhook::imgui::TextureId, [f32; 2])> {
    if frame.is_null() || handle == 0 {
        return None;
    }
    // SAFETY: the caller's contract.
    let frame = unsafe { &*frame };
    if frame.textures.is_null() || frame.texture_count == 0 {
        return None;
    }
    // SAFETY: the host wrote `texture_count` entries at `textures`, alive for this call.
    let sets = unsafe { std::slice::from_raw_parts(frame.textures, frame.texture_count as usize) };
    let set = sets.iter().find(|set| set.handle == handle)?;
    Some((
        hudhook::imgui::TextureId::new(set.texture_id),
        [set.width as f32, set.height as f32],
    ))
}

/// Hand TrueType bytes to whichever loaded module hosts the overlay, at any time.
///
/// Returns the handle to pass to [`frame_font`], or `None` when no module hosts an overlay yet or
/// the host refused the request. A guest normally calls this once, after its registration
/// succeeded. The font is usable from the first frame after the host's next rebuild; until then
/// [`frame_font`] answers `None`, which [`with_font`] treats as "use the default font".
#[cfg(windows)]
pub fn add_font(ttf: &[u8], sizes_px: &[f32]) -> Option<u32> {
    let n = u32::try_from(sizes_px.len()).ok()?;
    for module in er_game_base::build_id::loaded_module_handles() {
        // SAFETY: a handle straight out of the loader's own module list, and a NUL-terminated
        // export name. A module without the export answers None.
        let Some(symbol) =
            (unsafe { er_game_base::build_id::module_export(module, ADD_FONT_EXPORT) })
        else {
            continue;
        };
        // SAFETY: this export is defined only by `export_overlay_host!` in this crate, so any
        // module answering to the name has our signature.
        let add: AddFontFn = unsafe { std::mem::transmute(symbol) };
        // SAFETY: both slices are live for the call, and the host copies them before returning.
        let handle = unsafe { add(ttf.as_ptr(), ttf.len(), sizes_px.as_ptr(), n) };
        if handle != 0 {
            return Some(handle);
        }
    }
    None
}

#[cfg(not(windows))]
pub fn add_font(_ttf: &[u8], _sizes_px: &[f32]) -> Option<u32> {
    None
}

/// The `ImFont*` for `handle` at `size_index` (an index into the sizes passed to [`add_font`]),
/// if the host has built it.
///
/// # Safety
///
/// `frame` must be the pointer the host just passed to the guest's draw, or null.
pub unsafe fn frame_font(
    frame: *const OverlayFrame,
    handle: u32,
    size_index: usize,
) -> Option<*mut hudhook::imgui::sys::ImFont> {
    if frame.is_null() || handle == 0 {
        return None;
    }
    // SAFETY: the caller's contract.
    let frame = unsafe { &*frame };
    if frame.fonts.is_null() || frame.font_set_count == 0 {
        return None;
    }
    // SAFETY: the host wrote `font_set_count` entries at `fonts`, alive for this call.
    let sets = unsafe { std::slice::from_raw_parts(frame.fonts, frame.font_set_count as usize) };
    let set = sets.iter().find(|set| set.handle == handle)?;
    if set.fonts.is_null() || size_index >= set.count as usize {
        return None;
    }
    // SAFETY: `size_index` is inside the `count` pointers the host wrote.
    let font = unsafe { *set.fonts.add(size_index) };
    (!font.is_null()).then_some(font.cast())
}

/// Run `draw` with `font` pushed, or in the current font when `font` is `None`.
///
/// imgui-rs cannot name a font from another DLL's atlas -- its `FontId` is constructible only
/// inside that crate -- so this pushes the raw pointer [`frame_font`] returned. The `&Ui` is the
/// proof that a frame is open, which `igPushFont` requires.
pub fn with_font<R>(
    _ui: &Ui,
    font: Option<*mut hudhook::imgui::sys::ImFont>,
    draw: impl FnOnce() -> R,
) -> R {
    let Some(font) = font.filter(|font| !font.is_null()) else {
        return draw();
    };
    // SAFETY: a frame is open (we hold its `&Ui`), and `font` came from the host's atlas through
    // `frame_font`; atlas fonts are never freed, so the pointer is live.
    unsafe { hudhook::imgui::sys::igPushFont(font) };
    let result = draw();
    // SAFETY: pops exactly the push above.
    unsafe { hudhook::imgui::sys::igPopFont() };
    result
}

/// Register `draw` with whichever loaded module hosts the overlay.
///
/// Walks the process's module list and offers the guest to each in turn; exactly one -- the host
/// -- accepts. Returns whether a host took it. A `false` return means no module in this process
/// hosts an overlay yet, which is the caller's cue to host it itself.
#[cfg(windows)]
pub fn register_with_host(draw: OverlayDrawFn) -> bool {
    type RegisterFn = unsafe extern "C" fn(u32, OverlayDrawFn) -> bool;

    for module in er_game_base::build_id::loaded_module_handles() {
        // SAFETY: a handle straight out of the loader's own module list, and a NUL-terminated
        // export name. A module without the export answers None.
        let Some(symbol) =
            (unsafe { er_game_base::build_id::module_export(module, REGISTER_EXPORT) })
        else {
            continue;
        };
        // SAFETY: this export is defined only by `export_overlay_host!` in this crate, so any
        // module answering to the name has our signature.
        let register: RegisterFn = unsafe { std::mem::transmute(symbol) };
        // SAFETY: FFI into a sibling module of this workspace; it only records the pointer.
        if unsafe { register(OVERLAY_ABI_TAG, draw) } {
            return true;
        }
    }
    false
}

#[cfg(not(windows))]
pub fn register_with_host(_draw: OverlayDrawFn) -> bool {
    false
}

/// Register `draw` with a host that is known to exist, waiting out its designation.
///
/// Call this only after [`crate::OverlayClaim::LostToAnotherModule`], which proves some other
/// module created the ownership mutex. That module calls [`designate_host`] a handful of
/// instructions after the `CreateMutexW` this caller observed -- so the answer is almost always
/// yes on the first attempt, but "almost always" is exactly the gap that a single try loses to,
/// and losing it means a permanently blank overlay.
///
/// Bounded by [`er_game_base::wait::poll_until`], which spins in user space and reaches no
/// wineserver, so a host that somehow never designates itself costs a bounded spin on this
/// module's own install thread rather than the process.
#[cfg(windows)]
pub fn register_with_host_retrying(draw: OverlayDrawFn) -> bool {
    er_game_base::wait::poll_until(|| register_with_host(draw).then_some(())).is_some()
}

#[cfg(not(windows))]
pub fn register_with_host_retrying(_draw: OverlayDrawFn) -> bool {
    false
}

/// Adopt the host's imgui context and allocators, then hand back its `&Ui`.
///
/// Every guest draw calls this first. Skipping it is not a subtle degradation: imgui's context is
/// a per-DLL global, so the guest's copy is NULL and the first `ui.io()` faults.
///
/// # Safety
///
/// `frame` must be the pointer the host just passed, and the returned reference must not outlive
/// the call.
#[cfg(windows)]
pub unsafe fn adopt_frame<'a>(frame: *const OverlayFrame) -> Option<&'a Ui> {
    if frame.is_null() {
        return None;
    }
    // SAFETY: the host passes a live `OverlayFrame` for the duration of the call.
    let frame = unsafe { &*frame };
    if frame.ui.is_null() || frame.imgui_context.is_null() {
        return None;
    }
    // SAFETY: adopting the host's context and allocator globals into this module's copies, which
    // is the documented way to drive imgui from more than one DLL.
    unsafe {
        hudhook::imgui::sys::igSetCurrentContext(frame.imgui_context.cast());
        if !frame.alloc_func.is_null() && !frame.free_func.is_null() {
            hudhook::imgui::sys::igSetAllocatorFunctions(
                Some(std::mem::transmute::<
                    *mut c_void,
                    unsafe extern "C" fn(usize, *mut c_void) -> *mut c_void,
                >(frame.alloc_func)),
                Some(std::mem::transmute::<
                    *mut c_void,
                    unsafe extern "C" fn(*mut c_void, *mut c_void),
                >(frame.free_func)),
                frame.alloc_user_data,
            );
        }
        Some(&*(frame.ui.cast::<Ui>()))
    }
}

/// Define this module's `er_overlay_register_guest_v1` export.
///
/// Every shell that links this crate must invoke this once, or it becomes a host that no guest
/// can find -- the exact silent failure this module exists to remove.
#[macro_export]
macro_rules! export_overlay_host {
    () => {
        /// # Safety
        ///
        /// Called across a DLL boundary by [`er_build_watermark_core::overlay_host`].
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn er_overlay_register_guest_v1(
            abi_tag: u32,
            draw: $crate::overlay_host::OverlayDrawFn,
        ) -> bool {
            $crate::overlay_host::register_guest(abi_tag, draw)
        }

        /// # Safety
        ///
        /// Called across a DLL boundary by [`er_build_watermark_core::overlay_host::add_font`];
        /// `ttf` is readable for `len` bytes and `sizes_px` for `n` floats.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn er_overlay_add_font_v1(
            ttf: *const u8,
            len: usize,
            sizes_px: *const f32,
            n: u32,
        ) -> u32 {
            // SAFETY: forwarded from the caller's contract.
            unsafe { $crate::overlay_host::add_font_v1(ttf, len, sizes_px, n) }
        }

        /// # Safety
        ///
        /// Called across a DLL boundary by
        /// [`er_build_watermark_core::overlay_host::add_texture`]; `rgba` is readable for `len`
        /// bytes.
        #[unsafe(no_mangle)]
        pub unsafe extern "C" fn er_overlay_add_texture_v1(
            rgba: *const u8,
            len: usize,
            width: u32,
            height: u32,
        ) -> u32 {
            // SAFETY: forwarded from the caller's contract.
            unsafe { $crate::overlay_host::add_texture_v1(rgba, len, width, height) }
        }
    };
}
