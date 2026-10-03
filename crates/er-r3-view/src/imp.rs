use std::ffi::{CStr, c_void};
use std::sync::Mutex;
use std::sync::Once;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};
use std::time::Instant;

use er_build_watermark_core::overlay_host::{
    OverlayFrame, add_font, adopt_frame, frame_font, frame_texture, register_with_host_retrying,
};
use hudhook::imgui::Ui;

use crate::board;

const DLL_PROCESS_ATTACH: u32 = 1;
const DLL_MAIN_SUCCESS: i32 = 1;
const LOG_FILE_NAME: &str = "er-r3-view.log";

/// The step's bytes from its entry through the opcode of its tail jump. Identical on 1.16.2
/// (`0x1409970c0`) and 1.17.1 (`0x140998260`), and unique in each image; the `jb` displacement is
/// masked.
const STEP_PATTERN: [Option<u8>; 34] = {
    const fn b(v: u8) -> Option<u8> {
        Some(v)
    }
    [
        b(0x4c),
        b(0x8b),
        b(0x81),
        b(0x48),
        b(0x0b),
        b(0x00),
        b(0x00), // mov r8, [rcx+0xb48]
        b(0x48),
        b(0x81),
        b(0xc1),
        b(0xf8),
        b(0x08),
        b(0x00),
        b(0x00), // add rcx, 0x8f8
        b(0x49),
        b(0x83),
        b(0xf8),
        b(0x01), // cmp r8, 1
        b(0x72),
        None, // jb ret
        b(0x8b),
        b(0x01),
        b(0x33),
        b(0xd2),
        b(0xff),
        b(0xc0), // mov eax,[rcx]; xor edx,edx; inc eax
        b(0x48),
        b(0x98),
        b(0x49),
        b(0xf7),
        b(0xf0),
        b(0x89),
        b(0x11), // cdqe; div r8; mov [rcx],edx
        b(0xe9), // jmp apply
    ]
};
const STEP_TAIL_JUMP_OFFSET: usize = 33;

/// R3's enable predicate, `FUN(parts) -> bool` (1.16.2 `0x140995990`, 1.17.1 `0x140996b30`). The item
/// list registers R3 through `FUN_140745390(window, key, action, predicate)` with a predicate lambda
/// (`lambda_34d17bc8…`, 1.17 `FUN_1408f5040`) that calls this. It returns false unless
/// `0 <= mode < pane_count`, so in view 3 the game disables R3 itself and no press reaches the step:
/// measured, 45 presses at the pad and not one call to any R3 handler. Unique in both images.
const R3_ENABLED_PATTERN: [Option<u8>; 24] = {
    const fn b(v: u8) -> Option<u8> {
        Some(v)
    }
    [
        b(0x48),
        b(0x63),
        b(0x91),
        b(0xf8),
        b(0x08),
        b(0x00),
        b(0x00), // movsxd rdx, [rcx+0x8f8]
        b(0x85),
        b(0xd2),
        b(0x78),
        b(0x36), // test edx, edx; js
        b(0x4c),
        b(0x8b),
        b(0x89),
        b(0x48),
        b(0x0b),
        b(0x00),
        b(0x00), // mov r9, [rcx+0xb48]
        b(0x49),
        b(0x3b),
        b(0xd1),
        b(0x73),
        b(0x2a),
        b(0x8d), // cmp rdx, r9; jae
    ]
};
const PANE_COUNT_OFFSET: usize = 0xb48;
const MODE_OFFSET: usize = 0x8f8;
/// The step's `this` is the item list's `DetailStatusViewParts`; `+0x10` is the
/// `GaitemSelectDialog` that owns it (measured live with Frida on 23 presses, one pointer each).
const PARTS_OWNER_WINDOW_OFFSET: usize = 0x10;

/// `apply`'s pane slots: the array starts 8-aligned near `list+0x48` (the same arithmetic as
/// 1.17 `FUN_140975890`), and a slot's object has its "enter view" callback at `vtable+0x10`.
const PANE_SLOTS_OFFSET: usize = 0x48;
const PANE_CALLBACK_SLOT: usize = 0x10;

/// 1.16.2 rva of `MenuWindowJob::Run`; `register_shared_hook` carries it to the running build.
const MENU_WINDOW_JOB_RUN_RVA_1162: usize = 0x7ad1c0;
const MENU_WINDOW_JOB_WINDOW_OFFSET: usize = 0x130;

/// The item list's own `SceneObjProxy` members that draw its left panel.
const LEFT_PANEL_PROXY_OFFSETS: [usize; 2] = [0x120, 0x230];
const VALUE_INTERFACE: usize = 0x18;
const VALUE_TYPE: usize = 0x20;
const VALUE_DATA: usize = 0x28;
const VALUE_TYPE_MASK: u8 = 0x8f;
const GET_DISPLAY_INFO_SLOT: usize = 0xd8;
const SET_DISPLAY_INFO_SLOT: usize = 0xe0;
const DISPLAY_INFO_BYTES: usize = 0xd8;
const DISPLAY_INFO_VARS_SET: usize = 0xd4;
/// `DisplayInfo.Alpha`, a percentage double: measured 100.0 at +0x28 on a visible proxy, after
/// X, Y, rotation and the two scales, which is Scaleform's own field order.
const DISPLAY_INFO_ALPHA: usize = 0x28;
const V_ALPHA: u16 = 0x20;

const SCENE_OBJ_PROXY: &str = ".?AVSceneObjProxy@CS@@";
const ITEM_LIST: &str = ".?AVGaitemSelectDialog@CS@@";
/// The item list not pumped for this long while view 3 is up means it closed.
const ITEM_LIST_GONE_MS: u128 = 1000;

static START: Once = Once::new();
static STEP_ORIG: AtomicUsize = AtomicUsize::new(0);
static R3_ENABLED_ORIG: AtomicUsize = AtomicUsize::new(0);
static RUN_ORIG: AtomicUsize = AtomicUsize::new(0);
static APPLY: AtomicUsize = AtomicUsize::new(0);
/// The running game image's base, for the icon lookup's measured addresses.
static GAME_BASE: AtomicUsize = AtomicUsize::new(0);
static SHOW_BOARD: AtomicBool = AtomicBool::new(false);
/// The host's handle for the game's menu font, 0 until it accepted one, and the screen scale it
/// was sized for; how many captured `font.gfx` have been tried.
static FONT_HANDLE: AtomicU32 = AtomicU32::new(0);
static FONT_KY_BITS: AtomicU32 = AtomicU32::new(0);
static FONT_TRIED: AtomicUsize = AtomicUsize::new(0);
static EPOCH: Mutex<Option<Instant>> = Mutex::new(None);
static ITEM_LIST_WINDOW: AtomicUsize = AtomicUsize::new(0);
static ITEM_LIST_AT_MS: AtomicUsize = AtomicUsize::new(0);

/// A faded left-panel proxy and the alpha to give back. Touched only on the menu thread.
struct Faded {
    window: usize,
    proxy: usize,
    alpha: f64,
}
static FADED: Mutex<Vec<Faded>> = Mutex::new(Vec::new());

fn log(args: std::fmt::Arguments<'_>) {
    let path = er_game_base::log::game_directory_path()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(LOG_FILE_NAME);
    er_game_base::log::append_line(&path, format_args!("er-r3-view: {args}"));
}

fn now_ms() -> u128 {
    let mut epoch = EPOCH.lock().unwrap_or_else(|e| e.into_inner());
    epoch.get_or_insert_with(Instant::now).elapsed().as_millis()
}

#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    _module: *mut c_void,
    reason: u32,
    _reserved: *mut c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        START.call_once(|| {
            er_game_base::panic_report::report_panics_to("er-r3-view", log);
            // Off the loader thread: hook installs and the overlay registration both take locks
            // and walk modules.
            if std::thread::Builder::new()
                .name("er-r3-view-install".to_string())
                .spawn(install)
                .is_err()
            {
                log(format_args!("could not spawn the install thread"));
            }
        });
    }
    DLL_MAIN_SUCCESS
}

fn install() {
    er_hook::set_hook_logger(log);
    er_game_base::game_build::set_address_logger(log);
    let Some((start, end)) = er_game_base::game_build::game_image_range() else {
        log(format_args!(
            "game image range unreadable; nothing installed"
        ));
        return;
    };
    let Some(step) = (unsafe { find_unique(start, end, &STEP_PATTERN) }) else {
        log(format_args!(
            "R3 view step not found exactly once in the image; nothing installed"
        ));
        return;
    };
    let jump = step + STEP_TAIL_JUMP_OFFSET;
    let rel = unsafe { ((jump + 1) as *const i32).read_unaligned() };
    let apply = (jump + 5).wrapping_add_signed(rel as isize);
    if !(start..end).contains(&apply) {
        log(format_args!(
            "step tail jump at 0x{jump:x} leaves the image (0x{apply:x}); nothing installed"
        ));
        return;
    }
    APPLY.store(apply, Ordering::SeqCst);
    GAME_BASE.store(start, Ordering::SeqCst);

    match unsafe { er_hook::register_union_hook_runtime_derived(step, step_hook, &STEP_ORIG) } {
        Ok(()) => log(format_args!("step hooked at 0x{step:x}, apply 0x{apply:x}")),
        Err(status) => {
            log(format_args!(
                "step hook at 0x{step:x} failed: {status:?}; nothing installed"
            ));
            return;
        }
    }
    match unsafe { find_unique(start, end, &R3_ENABLED_PATTERN) } {
        Some(predicate) => match unsafe {
            er_hook::register_union_hook_runtime_derived(
                predicate,
                r3_enabled_hook,
                &R3_ENABLED_ORIG,
            )
        } {
            Ok(()) => log(format_args!(
                "R3 enable predicate hooked at 0x{predicate:x}"
            )),
            Err(status) => log(format_args!(
                "R3 enable predicate hook at 0x{predicate:x} failed: {status:?}; R3 will not leave view 3"
            )),
        },
        None => log(format_args!(
            "R3 enable predicate not found exactly once; R3 will not leave view 3"
        )),
    }
    let run = start + MENU_WINDOW_JOB_RUN_RVA_1162;
    match unsafe { er_hook::register_shared_hook(run, run_hook, &RUN_ORIG) } {
        Ok(route) => log(format_args!(
            "MenuWindowJob::Run registered on the {route:?} union"
        )),
        Err(status) => log(format_args!(
            "MenuWindowJob::Run hook failed: {status:?}; view 3 will leave the left panel up"
        )),
    }
    match crate::menu_font::install(start) {
        Ok(route) => log(format_args!(
            "Scaleform file open registered on the {route} union, for the menu font"
        )),
        Err(status) => log(format_args!(
            "Scaleform file open hook failed: {status}; the board keeps the default font"
        )),
    }
    if register_with_host_retrying(guest_draw) {
        log(format_args!("drawing as a guest of the overlay host"));
    } else {
        log(format_args!(
            "no overlay host accepted a guest; load er-build-watermark in this profile to see the board"
        ));
    }
}

/// The one address in `[start, end)` matching `pattern`, or `None` for zero or several.
///
/// # Safety
///
/// `[start, end)` must be the mapped game image.
unsafe fn find_unique(start: usize, end: usize, pattern: &[Option<u8>]) -> Option<usize> {
    let image = unsafe { std::slice::from_raw_parts(start as *const u8, end - start) };
    let mut found = None;
    for (at, window) in image.windows(pattern.len()).enumerate() {
        if pattern
            .iter()
            .zip(window)
            .all(|(p, b)| p.is_none_or(|p| p == *b))
        {
            if found.is_some() {
                return None;
            }
            found = Some(start + at);
        }
    }
    found
}

/// The step, over `count + 1` views.
///
/// # Safety
///
/// Installed by `er-hook` on the step's entry; the game calls it on its menu thread.
unsafe extern "system" fn step_hook(this: usize, _a: usize, _b: usize, _c: usize) -> usize {
    let count = unsafe { ((this + PANE_COUNT_OFFSET) as *const u64).read() } as i64;
    if count < 1 {
        return 0;
    }
    let list = this + MODE_OFFSET;
    let mode = ((unsafe { (list as *const i32).read() } as i64 + 1) % (count + 1)) as i32;
    unsafe { (list as *mut i32).write(mode) };
    let apply: unsafe extern "system" fn(usize, i32) =
        unsafe { std::mem::transmute(APPLY.load(Ordering::SeqCst)) };
    unsafe { apply(list, mode) };
    if i64::from(mode) == count {
        let owner = unsafe { ((this + PARTS_OWNER_WINDOW_OFFSET) as *const usize).read() };
        if unsafe { rtti_name(owner) } == Some(ITEM_LIST) {
            ITEM_LIST_WINDOW.store(owner, Ordering::Relaxed);
            ITEM_LIST_AT_MS.store(now_ms() as usize, Ordering::Relaxed);
        }
        unsafe { enter_view_zero_layout(list) };
        unsafe { fade_left_panel() };
        SHOW_BOARD.store(true, Ordering::Relaxed);
    } else {
        unsafe { restore_left_panel() };
        SHOW_BOARD.store(false, Ordering::Relaxed);
    }
    0
}

/// Run pane 0's own "enter view" callback, the game's layout with no right or center panel.
///
/// # Safety
///
/// `list` is the step's live pane list; on the menu thread.
unsafe fn enter_view_zero_layout(list: usize) {
    let slot = list + ((list.wrapping_add(8)).wrapping_neg() & 7) + PANE_SLOTS_OFFSET;
    let pane = unsafe { (slot as *const usize).read() };
    if pane == 0 {
        return;
    }
    let vtable = unsafe { (pane as *const usize).read() };
    let enter: unsafe extern "system" fn(usize, *const u8) =
        unsafe { std::mem::transmute(((vtable + PANE_CALLBACK_SLOT) as *const usize).read()) };
    let shown = 1u8;
    unsafe { enter(pane, &shown) };
}

/// R3 stays enabled in view 3; every other view asks the game.
///
/// # Safety
///
/// Installed by `er-hook` on the predicate's entry; the game calls it with a live `parts`.
unsafe extern "system" fn r3_enabled_hook(parts: usize, a: usize, b: usize, c: usize) -> usize {
    let count = unsafe { ((parts + PANE_COUNT_OFFSET) as *const u64).read() } as i64;
    let mode = i64::from(unsafe { ((parts + MODE_OFFSET) as *const i32).read() });
    if count >= 1 && mode == count {
        return 1;
    }
    let next: er_hook::UnionFn =
        unsafe { std::mem::transmute(R3_ENABLED_ORIG.load(Ordering::SeqCst)) };
    unsafe { next(parts, a, b, c) }
}

/// Note the item list's window, and give its left panel back if it closes while faded.
///
/// # Safety
///
/// Installed by `er-hook`; the game calls it on its menu thread with a live `MenuWindowJob`.
unsafe extern "system" fn run_hook(job: usize, a: usize, b: usize, c: usize) -> usize {
    let orig = RUN_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    // The slot may hold the next handler in the union's chain rather than the game trampoline.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    // Read before the call: with the read after it, this never once saw the item list while
    // Frida, reading at entry, counted it on every frame.
    let window = if job == 0 {
        0
    } else {
        unsafe { ((job + MENU_WINDOW_JOB_WINDOW_OFFSET) as *const usize).read() }
    };
    let is_item_list = unsafe { rtti_name(window) } == Some(ITEM_LIST);
    let ret = unsafe { next(job, a, b, c) };
    if is_item_list {
        if ITEM_LIST_WINDOW.swap(window, Ordering::Relaxed) != window {
            log(format_args!("Run names the item list window 0x{window:x}"));
        }
        ITEM_LIST_AT_MS.store(now_ms() as usize, Ordering::Relaxed);
        // The menu thread, inside the item list's own job: the one place the icon lookup, which
        // inserts into the texture repository's map on a miss, may run. Once per process.
        let base = GAME_BASE.load(Ordering::Relaxed);
        if base != 0 {
            unsafe { crate::item_icon::resolve_once(base, board::MISERICORDE.icon_id, log) };
        }
    }
    let faded_any = !FADED.lock().unwrap_or_else(|e| e.into_inner()).is_empty();
    if faded_any && now_ms() - ITEM_LIST_AT_MS.load(Ordering::Relaxed) as u128 > ITEM_LIST_GONE_MS {
        unsafe { restore_left_panel() };
        SHOW_BOARD.store(false, Ordering::Relaxed);
    }
    ret
}

/// The RTTI name of the polymorphic object at `object`, when its vtable is inside the game image.
///
/// # Safety
///
/// `object` must be null or readable for eight bytes.
unsafe fn rtti_name(object: usize) -> Option<&'static str> {
    if object == 0 {
        return None;
    }
    let vtable = unsafe { (object as *const usize).read() };
    if !er_game_base::game_build::game_image_range().is_some_and(|(s, e)| (s..e).contains(&vtable))
    {
        return None;
    }
    // x64 MSVC: `vtable[-1]` is the complete-object locator; signature 1 holds image-relative
    // RVAs, `+0x0c` the type descriptor and `+0x14` the locator's own RVA, so the image base is
    // the difference. The decorated name starts 0x10 into the type descriptor.
    let col = unsafe { ((vtable - 8) as *const usize).read() };
    if unsafe { (col as *const u32).read() } != 1 {
        return None;
    }
    let image = col - unsafe { ((col + 0x14) as *const u32).read() } as usize;
    let td = image + unsafe { ((col + 0x0c) as *const u32).read() } as usize;
    unsafe { CStr::from_ptr((td + 0x10) as *const std::ffi::c_char) }
        .to_str()
        .ok()
}

/// The GFx `(ObjectInterface*, data)` behind a proxy, if it is one and holds a display object.
///
/// # Safety
///
/// `proxy` must point into a live menu window.
unsafe fn gfx_value(proxy: usize) -> Option<(usize, usize)> {
    if unsafe { rtti_name(proxy) } != Some(SCENE_OBJ_PROXY) {
        return None;
    }
    let vtable = unsafe { (proxy as *const usize).read() };
    let get_value: unsafe extern "system" fn(usize) -> usize =
        unsafe { std::mem::transmute((vtable as *const usize).read()) };
    let value = unsafe { get_value(proxy) };
    if value == 0 || unsafe { ((value + VALUE_TYPE) as *const u8).read() } & VALUE_TYPE_MASK == 0 {
        return None;
    }
    let iface = unsafe { ((value + VALUE_INTERFACE) as *const usize).read() };
    if iface == 0 {
        return None;
    }
    Some((iface, unsafe {
        ((value + VALUE_DATA) as *const usize).read()
    }))
}

type DisplayInfoFn = unsafe extern "system" fn(usize, usize, *mut u8);

/// # Safety
///
/// `iface` must be a live `GFx::Value::ObjectInterface`.
unsafe fn display_info_fn(iface: usize, slot: usize) -> DisplayInfoFn {
    let vtable = unsafe { (iface as *const usize).read() };
    unsafe { std::mem::transmute(((vtable + slot) as *const usize).read()) }
}

/// # Safety
///
/// As [`gfx_value`], and on the menu thread.
unsafe fn alpha(iface: usize, data: usize) -> f64 {
    let mut info = [0u8; DISPLAY_INFO_BYTES];
    unsafe { display_info_fn(iface, GET_DISPLAY_INFO_SLOT)(iface, data, info.as_mut_ptr()) };
    let mut raw = [0u8; 8];
    raw.copy_from_slice(&info[DISPLAY_INFO_ALPHA..DISPLAY_INFO_ALPHA + 8]);
    f64::from_le_bytes(raw)
}

/// # Safety
///
/// As [`gfx_value`], and on the menu thread.
unsafe fn set_alpha(iface: usize, data: usize, value: f64) {
    let mut info = [0u8; DISPLAY_INFO_BYTES];
    info[DISPLAY_INFO_VARS_SET..DISPLAY_INFO_VARS_SET + 2].copy_from_slice(&V_ALPHA.to_le_bytes());
    info[DISPLAY_INFO_ALPHA..DISPLAY_INFO_ALPHA + 8].copy_from_slice(&value.to_le_bytes());
    unsafe { display_info_fn(iface, SET_DISPLAY_INFO_SLOT)(iface, data, info.as_mut_ptr()) };
}

/// # Safety
///
/// On the menu thread.
unsafe fn fade_left_panel() {
    let window = ITEM_LIST_WINDOW.load(Ordering::Relaxed);
    if unsafe { rtti_name(window) } != Some(ITEM_LIST) {
        log(format_args!(
            "view 3: item list window unknown; left panel left up"
        ));
        return;
    }
    let mut faded = FADED.lock().unwrap_or_else(|e| e.into_inner());
    for offset in LEFT_PANEL_PROXY_OFFSETS {
        let proxy = window + offset;
        let Some((iface, data)) = (unsafe { gfx_value(proxy) }) else {
            continue;
        };
        let was = unsafe { alpha(iface, data) };
        if was <= 0.0 {
            continue;
        }
        unsafe { set_alpha(iface, data, 0.0) };
        faded.push(Faded {
            window,
            proxy,
            alpha: was,
        });
    }
    log(format_args!(
        "view 3: right and center panels via view 0's layout, left panel faded ({})",
        faded.len()
    ));
}

/// # Safety
///
/// On the menu thread.
unsafe fn restore_left_panel() {
    let mut faded = FADED.lock().unwrap_or_else(|e| e.into_inner());
    if faded.is_empty() {
        return;
    }
    let mut shown = 0;
    for f in faded.iter() {
        // The menu windows outlive a menu close, so the same object still carrying the item list's
        // class is the window that was faded.
        if unsafe { rtti_name(f.window) } != Some(ITEM_LIST) {
            continue;
        }
        if let Some((iface, data)) = unsafe { gfx_value(f.proxy) } {
            unsafe { set_alpha(iface, data, f.alpha) };
            shown += 1;
        }
    }
    log(format_args!("left panel back: {shown} of {}", faded.len()));
    faded.clear();
}

/// # Safety
///
/// `frame` is the pointer the overlay host just passed, live for this call.
unsafe extern "C" fn guest_draw(frame: *const OverlayFrame) {
    let Some(ui) = (unsafe { adopt_frame(frame) }) else {
        return;
    };
    request_menu_font(ui);
    if SHOW_BOARD.load(Ordering::Relaxed) {
        let handle = FONT_HANDLE.load(Ordering::Relaxed);
        let fonts = (handle != 0).then(|| board::Fonts {
            faces: std::array::from_fn(|i| unsafe { frame_font(frame, handle, i) }),
            ky: f32::from_bits(FONT_KY_BITS.load(Ordering::Relaxed)),
        });
        let art = board::BoardArt {
            icon: crate::item_icon::handle().and_then(|h| unsafe { frame_texture(frame, h) }),
        };
        board::draw(ui, &board::MISERICORDE, fonts.as_ref(), &art);
    }
}

/// Hand the host the game's menu font once its `font.gfx` has been captured: converted to
/// TrueType in memory, at every board size for this screen. Tried again only when another
/// `font.gfx` arrives, so a refusal costs one log line, not one per frame.
fn request_menu_font(ui: &Ui) {
    if FONT_HANDLE.load(Ordering::Relaxed) != 0 {
        return;
    }
    let seen = crate::menu_font::captured_count();
    if seen == FONT_TRIED.swap(seen, Ordering::Relaxed) {
        return;
    }
    let ky = ui.io().display_size[1] / board::FRAME_H;
    for gfx in crate::menu_font::captured() {
        let Ok(ttf) = er_gfx::ttf::menu_font_ttf(&gfx) else {
            continue;
        };
        let sizes = board::SIZES.map(|px| px * ky * board::LINE_PER_EM);
        match add_font(&ttf, &sizes) {
            Some(handle) => {
                FONT_KY_BITS.store(ky.to_bits(), Ordering::Relaxed);
                FONT_HANDLE.store(handle, Ordering::Relaxed);
                log(format_args!(
                    "menu font handed to the overlay host: {} bytes of TrueType, handle {handle}",
                    ttf.len()
                ));
            }
            None => log(format_args!(
                "the overlay host refused the menu font; the board keeps the default face"
            )),
        }
        return;
    }
    log(format_args!(
        "{seen} font.gfx captured, none holds MenuFont_01; the board keeps the default face"
    ));
}
