//! What does this console actually deliver when a key is pressed?
//!
//! Written 2026-09-19 to settle one question with a measurement instead of an argument: running
//! the installer's Windows exe under Wine, the arrow keys move nothing. The picker reads bytes
//! from stdin and relies on `ENABLE_VIRTUAL_TERMINAL_INPUT` to turn an arrow into `esc [ A`.
//! Wine accepts that console mode and reports success, and the bytes never arrive.
//!
//! So the design choice -- keep arrows and make them work, or drop them for keys that already do
//! -- turns on whether the Windows-native input path sees the key that the byte path misses.
//! This probe runs both at once and prints whichever answers.
//!
//! Run it under Wine in a terminal, press keys, press `q` to quit.

#[cfg(windows)]
fn main() {
    windows_probe::run();
}

#[cfg(not(windows))]
fn main() {
    eprintln!("this probe measures the Windows console path; build it for x86_64-pc-windows-msvc");
}

#[cfg(windows)]
mod windows_probe {
    use std::io::{Read, Write};
    use std::sync::{Mutex, OnceLock};

    /// The file every line also goes to, named by `ER_KEY_PROBE_LOG`, opened once and truncated.
    ///
    /// One handle for the process, so the file holds this run and not a pile of them. Flushed per
    /// line, because under `wineconsole` the process draws into its own window -- stdout cannot be
    /// teed, and a result that only exists on screen is lost the moment the window closes, which
    /// is exactly how the first run of this probe was thrown away.
    fn log_file() -> Option<&'static Mutex<std::fs::File>> {
        static FILE: OnceLock<Option<Mutex<std::fs::File>>> = OnceLock::new();
        FILE.get_or_init(|| {
            let path = std::env::var_os("ER_KEY_PROBE_LOG")?;
            std::fs::File::create(path).ok().map(Mutex::new)
        })
        .as_ref()
    }

    fn log(line: &str) {
        println!("{line}");
        let Some(file) = log_file() else {
            return;
        };
        if let Ok(mut file) = file.lock() {
            let _ = writeln!(file, "{line}");
            let _ = file.flush();
        }
    }

    const STD_INPUT_HANDLE: u32 = -10i32 as u32;
    const KEY_EVENT: u16 = 0x0001;
    const ENABLE_PROCESSED_INPUT: u32 = 0x0001;
    const ENABLE_LINE_INPUT: u32 = 0x0002;
    const ENABLE_ECHO_INPUT: u32 = 0x0004;
    const ENABLE_VIRTUAL_TERMINAL_INPUT: u32 = 0x0200;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetStdHandle(id: u32) -> isize;
        fn GetConsoleMode(handle: isize, mode: *mut u32) -> i32;
        fn SetConsoleMode(handle: isize, mode: u32) -> i32;
        fn GetNumberOfConsoleInputEvents(handle: isize, count: *mut u32) -> i32;
        fn ReadConsoleInputW(handle: isize, buffer: *mut u8, len: u32, read: *mut u32) -> i32;
        fn GetLastError() -> u32;
    }

    /// `INPUT_RECORD` on x86-64: a `WORD` event type, two bytes of padding, then the union. The
    /// `KEY_EVENT_RECORD` arm is `BOOL` down, `WORD` repeat, `WORD` virtual key, `WORD` scan,
    /// `WCHAR` char, `DWORD` control state -- so the whole record is 20 bytes and the fields this
    /// probe wants sit at fixed offsets. Read as raw bytes rather than declared as a Rust union,
    /// because the only question here is which bytes arrive.
    const INPUT_RECORD_BYTES: usize = 20;

    pub fn run() {
        let input = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
        log(&format!("stdin handle: {input:#x}"));

        let mut saved = 0u32;
        let got_mode = unsafe { GetConsoleMode(input, &raw mut saved) } != 0;
        log(&format!("GetConsoleMode: {got_mode}, mode {saved:#06x}"));
        if !got_mode {
            let error = unsafe { GetLastError() };
            log(&format!(
                "  no console mode -- this is not a console handle, err {error}"
            ));
        }

        // `--no-vt` leaves `ENABLE_VIRTUAL_TERMINAL_INPUT` clear. With it set, Wine delivered
        // nothing at all for an arrow -- no key event and no bytes -- so the open question is
        // whether the classic console path reports the key that the terminal path drops.
        let want_vt = !std::env::args().any(|arg| arg == "--no-vt");
        let mut raw = saved & !(ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT);
        if want_vt {
            raw |= ENABLE_VIRTUAL_TERMINAL_INPUT;
        } else {
            raw &= !ENABLE_VIRTUAL_TERMINAL_INPUT;
        }
        let set_ok = unsafe { SetConsoleMode(input, raw) } != 0;
        log(&format!(
            "SetConsoleMode(raw, virtual terminal input {want_vt}): {set_ok}"
        ));

        let mut readback = 0u32;
        if unsafe { GetConsoleMode(input, &raw mut readback) } != 0 {
            log(&format!("mode read back: {readback:#06x}"));
            let kept = readback & ENABLE_VIRTUAL_TERMINAL_INPUT != 0;
            log(&format!("  virtual terminal input actually set: {kept}"));
        }

        log(&format!(
            "console size reported to the picker: {:?}",
            er_installer_size()
        ));
        log("");
        log("press keys; arrows are the ones in question. q quits.");
        log("  'console' lines come from ReadConsoleInputW, 'bytes' from reading stdin.");
        log("");

        // `--events` reads the console queue with a blocking `ReadConsoleInputW` and never touches
        // stdin.
        //
        // The mixed loop below cannot see an arrow even if Wine delivers one perfectly, and that
        // is a defect in this probe rather than a fact about Wine. When no byte is pending it
        // blocks in `stdin().read()`; an arrow that arrives as a console `KEY_EVENT` puts nothing
        // in the byte stream, so the read never returns, the loop never comes back round, and
        // nothing ever drains the event queue. A printable key returns from the read and lets the
        // next iteration poll -- which is exactly the pattern observed: `z` reported, arrows
        // silent. Read one queue or the other, never both.
        if std::env::args().any(|arg| arg == "--events") {
            log("reading the console event queue only, with a blocking ReadConsoleInputW");
            loop {
                let mut record = [0u8; INPUT_RECORD_BYTES];
                let mut read = 0u32;
                if unsafe { ReadConsoleInputW(input, record.as_mut_ptr(), 1, &raw mut read) } == 0 {
                    let error = unsafe { GetLastError() };
                    log(&format!("ReadConsoleInputW failed, err {error}"));
                    break;
                }
                if read != 1 {
                    continue;
                }
                let event_type = u16::from_le_bytes([record[0], record[1]]);
                if event_type != KEY_EVENT {
                    log(&format!("console: non-key event type {event_type:#06x}"));
                    continue;
                }
                let down = u32::from_le_bytes([record[4], record[5], record[6], record[7]]);
                let vk = u16::from_le_bytes([record[10], record[11]]);
                let ch = u16::from_le_bytes([record[14], record[15]]);
                log(&format!(
                    "console: down={} vk={vk:#04x} char={ch:#06x} {}",
                    down != 0,
                    name_of(vk)
                ));
                if down != 0 && (vk == 0x51 || ch == u16::from(b'q')) {
                    break;
                }
            }
            unsafe { SetConsoleMode(input, saved) };
            log(&format!("restored mode {saved:#06x}"));
            return;
        }

        loop {
            let mut pending = 0u32;
            let have_events =
                unsafe { GetNumberOfConsoleInputEvents(input, &raw mut pending) } != 0;
            if have_events && pending > 0 {
                let mut record = [0u8; INPUT_RECORD_BYTES];
                let mut read = 0u32;
                let ok =
                    unsafe { ReadConsoleInputW(input, record.as_mut_ptr(), 1, &raw mut read) } != 0;
                if ok && read == 1 {
                    let event_type = u16::from_le_bytes([record[0], record[1]]);
                    if event_type == KEY_EVENT {
                        let down = u32::from_le_bytes([record[4], record[5], record[6], record[7]]);
                        let vk = u16::from_le_bytes([record[10], record[11]]);
                        let ch = u16::from_le_bytes([record[14], record[15]]);
                        log(&format!(
                            "console: down={} vk={vk:#04x} char={ch:#06x} {}",
                            down != 0,
                            name_of(vk)
                        ));
                        if down != 0 && (vk == 0x51 || ch == u16::from(b'q')) {
                            break;
                        }
                    } else {
                        log(&format!("console: non-key event type {event_type:#06x}"));
                    }
                    continue;
                }
            }

            let mut byte = [0u8; 1];
            match std::io::stdin().read(&mut byte) {
                Ok(0) => {
                    log("bytes: end of input");
                    break;
                }
                Ok(_) => {
                    log(&format!("bytes: {:#04x} {:?}", byte[0], byte[0] as char));
                    if byte[0] == b'q' {
                        break;
                    }
                }
                Err(error) => {
                    log(&format!("bytes: read failed: {error}"));
                    break;
                }
            }
        }

        unsafe { SetConsoleMode(input, saved) };
        log(&format!("restored mode {saved:#06x}"));
    }

    /// The console window size, read the same way `tui::size` reads it: the window rectangle out
    /// of `GetConsoleScreenBufferInfo`, not the buffer, since a console buffer is routinely
    /// thousands of rows tall and padding a frame to that height scrolls the screen away.
    fn er_installer_size() -> Option<(usize, usize)> {
        const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;

        #[repr(C)]
        #[derive(Default)]
        struct Coord {
            x: i16,
            y: i16,
        }
        #[repr(C)]
        #[derive(Default)]
        struct SmallRect {
            left: i16,
            top: i16,
            right: i16,
            bottom: i16,
        }
        #[repr(C)]
        #[derive(Default)]
        struct ScreenBufferInfo {
            size: Coord,
            cursor_position: Coord,
            attributes: u16,
            window: SmallRect,
            maximum_window_size: Coord,
        }

        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn GetConsoleScreenBufferInfo(handle: isize, info: *mut ScreenBufferInfo) -> i32;
        }

        let output = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
        let mut info = ScreenBufferInfo::default();
        if unsafe { GetConsoleScreenBufferInfo(output, &raw mut info) } == 0 {
            return None;
        }
        log(&format!(
            "  buffer {}x{}, window l{} t{} r{} b{}",
            info.size.x,
            info.size.y,
            info.window.left,
            info.window.top,
            info.window.right,
            info.window.bottom
        ));
        let columns = (info.window.right - info.window.left + 1).max(1) as usize;
        let rows = (info.window.bottom - info.window.top + 1).max(1) as usize;
        Some((columns, rows))
    }

    fn name_of(vk: u16) -> &'static str {
        match vk {
            0x25 => "VK_LEFT",
            0x26 => "VK_UP",
            0x27 => "VK_RIGHT",
            0x28 => "VK_DOWN",
            0x21 => "VK_PRIOR (page up)",
            0x22 => "VK_NEXT (page down)",
            0x0d => "VK_RETURN",
            0x20 => "VK_SPACE",
            0x1b => "VK_ESCAPE",
            _ => "",
        }
    }
}
