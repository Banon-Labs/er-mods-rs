//! The game's menu font, copied out of the Scaleform file open of `font:/<locale>/font.gfx`.
//!
//! The game ships its menu face (Agmena, exported as `MenuFont_01`) as `DefineFont3` outlines in a
//! `.gfx` movie, not as a TrueType file, and only inside the encrypted archives. So the bytes are
//! taken from the game's own `MemoryFile` the moment it opens them, the way
//! `er-loading-portrait-core`'s `capture_menu_font_gfx` does, and never touch the disk.
//!
//! The file-open prologue (`TITLE_SCALEFORM_FILE_OPEN_RVA`) is shared with the product,
//! er-armament-icons and others, so this registers through the hook union. The handler only reads
//! the opened file after the chain returns it.

use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use er_game_base::mem::{safe_read_i32, safe_read_u8, safe_read_usize};

/// `MemoryFile`: its vtable, and where its payload pointer and length sit.
const MEMORY_FILE_DATA_OFFSET: usize = 0x18;
const MEMORY_FILE_LEN_OFFSET: usize = 0x20;
const MAX_GFX_BYTES: i32 = 64 * 1024 * 1024;
const URL_MAX: usize = 512;

static FILE_OPEN_ORIG: AtomicUsize = AtomicUsize::new(0);
static GAME_BASE: AtomicUsize = AtomicUsize::new(0);
/// Every `font.gfx` the game opened, in order; one locale opens a body and a map face.
static CAPTURED: Mutex<Vec<Vec<u8>>> = Mutex::new(Vec::new());

/// Registers the file-open observer. Returns the union route, or the hook error, for the log.
pub fn install(base: usize) -> Result<String, String> {
    GAME_BASE.store(base, Ordering::SeqCst);
    let target = base + er_game_base::rva::TITLE_SCALEFORM_FILE_OPEN_RVA;
    match unsafe {
        er_hook::register_shared_hook(target, menu_font_file_open_hook, &FILE_OPEN_ORIG)
    } {
        Ok(route) => Ok(format!("{route:?}")),
        Err(status) => Err(format!("{status:?}")),
    }
}

/// The `font.gfx` payloads captured so far.
pub fn captured() -> Vec<Vec<u8>> {
    CAPTURED.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

/// How many `font.gfx` payloads have been captured, without copying them.
pub fn captured_count() -> usize {
    CAPTURED.lock().unwrap_or_else(|e| e.into_inner()).len()
}

/// # Safety
///
/// Installed by `er-hook` on the Scaleform loader's file open; the game calls it with its loader,
/// a NUL-terminated URL and the open flags.
unsafe extern "system" fn menu_font_file_open_hook(
    loader: usize,
    url: usize,
    flags: usize,
    c: usize,
) -> usize {
    let orig = FILE_OPEN_ORIG.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    // The slot holds the game trampoline or the next union handler; both take the union shape.
    let next: er_hook::UnionFn = unsafe { std::mem::transmute(orig) };
    let file = unsafe { next(loader, url, flags, c) };
    if unsafe { url_contains(url, b"font.gfx") } {
        unsafe { capture(file) };
    }
    file
}

/// # Safety
///
/// `file` is the object the file open just returned, alive for this call.
unsafe fn capture(file: usize) {
    let base = GAME_BASE.load(Ordering::SeqCst);
    let Some(vtable) = (unsafe { safe_read_usize(file) }) else {
        return;
    };
    let want = er_game_base::mem::game_data_addr(
        base,
        er_game_base::rva::SCALEFORM_MEMORY_FILE_VTABLE_RVA,
        "SCALEFORM_MEMORY_FILE_VTABLE_RVA",
    );
    if vtable != want {
        return;
    }
    let data = unsafe { safe_read_usize(file + MEMORY_FILE_DATA_OFFSET) }.unwrap_or(0);
    let len = unsafe { safe_read_i32(file + MEMORY_FILE_LEN_OFFSET) }.unwrap_or(0);
    if data == 0 || !(8..=MAX_GFX_BYTES).contains(&len) {
        return;
    }
    // Both ends through the guarded reader before the one bulk copy.
    if unsafe { safe_read_u8(data) } != Some(b'G')
        || unsafe { safe_read_u8(data + len as usize - 1) }.is_none()
    {
        return;
    }
    let bytes = unsafe { std::slice::from_raw_parts(data as *const u8, len as usize) }.to_vec();
    if bytes.starts_with(b"GFX") {
        CAPTURED
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .push(bytes);
    }
}

/// # Safety
///
/// `url` is null or a NUL-terminated string; at most `URL_MAX` bytes are read, each guarded.
unsafe fn url_contains(url: usize, needle: &[u8]) -> bool {
    if url == 0 {
        return false;
    }
    let mut buf = [0u8; URL_MAX];
    let mut n = 0;
    while n < URL_MAX {
        match unsafe { safe_read_u8(url + n) } {
            Some(0) | None => break,
            Some(b) => {
                buf[n] = b;
                n += 1;
            }
        }
    }
    buf[..n].windows(needle.len()).any(|w| w == needle)
}
