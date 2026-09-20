//! Where does the cursor end up after a frame is painted?
//!
//! Written 2026-09-19 against one unexplained symptom: the installer's picker renders correctly
//! under Wine and its top rows are scrolled off the window anyway. The frame is provably the right
//! height, the painter homes to `esc [ H` and writes no newline after the last row, and the header
//! is still gone -- so the row advance that scrolls it comes from somewhere the source does not
//! obviously say.
//!
//! This asks the console instead of arguing. `GetConsoleScreenBufferInfo` reports the cursor after
//! every step, so each write's effect on the row is a measured number rather than an assumption:
//!
//! * writing a line exactly as wide as the window -- if `ENABLE_WRAP_AT_EOL_OUTPUT` is honoured,
//!   the character in the last column moves the cursor to the next row on its own, and the `\r\n`
//!   that follows moves it a second time;
//! * writing one column short of the window, as the control;
//! * painting a whole window-height frame the way `tui::paint` does, then reading both the cursor
//!   and the window top, which is the scroll itself if it has moved.
//!
//! Run it under Wine in the terminal the picker misbehaves in. It prints a table and exits.

#[cfg(windows)]
fn main() {
    probe::run();
}

#[cfg(not(windows))]
fn main() {
    eprintln!("this probe measures the Windows console; build it for x86_64-pc-windows-msvc");
}

#[cfg(windows)]
mod probe {
    use std::io::Write;

    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    const ENABLE_PROCESSED_OUTPUT: u32 = 0x0001;
    const ENABLE_WRAP_AT_EOL_OUTPUT: u32 = 0x0002;
    const ENABLE_VIRTUAL_TERMINAL_PROCESSING: u32 = 0x0004;

    #[repr(C)]
    #[derive(Default, Clone, Copy)]
    struct Coord {
        x: i16,
        y: i16,
    }

    #[repr(C)]
    #[derive(Default, Clone, Copy)]
    struct SmallRect {
        left: i16,
        top: i16,
        right: i16,
        bottom: i16,
    }

    #[repr(C)]
    #[derive(Default, Clone, Copy)]
    struct ScreenBufferInfo {
        size: Coord,
        cursor_position: Coord,
        attributes: u16,
        window: SmallRect,
        maximum_window_size: Coord,
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetStdHandle(id: u32) -> isize;
        fn GetConsoleMode(handle: isize, mode: *mut u32) -> i32;
        fn SetConsoleMode(handle: isize, mode: u32) -> i32;
        fn GetConsoleScreenBufferInfo(handle: isize, info: *mut ScreenBufferInfo) -> i32;
        fn SetConsoleCursorPosition(handle: isize, position: Coord) -> i32;
    }

    /// Everything printed is also appended to the file named by `ER_PAINT_PROBE_LOG`, because the
    /// probe's own output is on the screen it is busy scrolling.
    fn log(line: &str) {
        let Some(path) = std::env::var_os("ER_PAINT_PROBE_LOG") else {
            return;
        };
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            let _ = writeln!(file, "{line}");
            let _ = file.flush();
        }
    }

    fn info(handle: isize) -> ScreenBufferInfo {
        let mut info = ScreenBufferInfo::default();
        unsafe { GetConsoleScreenBufferInfo(handle, &raw mut info) };
        info
    }

    /// The cursor and window top, as one line, tagged with what was written just before it.
    fn mark(handle: isize, what: &str) {
        let info = info(handle);
        log(&format!(
            "{what:<34} cursor y={:<4} x={:<4} window top={:<4} bottom={:<4} buffer h={}",
            info.cursor_position.y,
            info.cursor_position.x,
            info.window.top,
            info.window.bottom,
            info.size.y
        ));
    }

    fn emit(text: &str) {
        let mut out = std::io::stdout();
        let _ = out.write_all(text.as_bytes());
        let _ = out.flush();
    }

    pub fn run() {
        let handle = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
        let mut saved = 0u32;
        let has_mode = unsafe { GetConsoleMode(handle, &raw mut saved) } != 0;

        let start = info(handle);
        let width = (start.window.right - start.window.left + 1).max(1) as usize;
        let height = (start.window.bottom - start.window.top + 1).max(1) as usize;

        log("");
        log("---- paint probe ----");
        log(&format!(
            "console mode readable: {has_mode}, mode {saved:#06x}"
        ));
        log(&format!(
            "  processed output {}, wrap at end of line {}, virtual terminal {}",
            saved & ENABLE_PROCESSED_OUTPUT != 0,
            saved & ENABLE_WRAP_AT_EOL_OUTPUT != 0,
            saved & ENABLE_VIRTUAL_TERMINAL_PROCESSING != 0
        ));
        log(&format!(
            "window {width}x{height}, buffer {}x{}, window top {} bottom {}",
            start.size.x, start.size.y, start.window.top, start.window.bottom
        ));
        log("");

        // Ask for the same output mode the picker asks for, so the measurement describes the
        // picker's console rather than a default one.
        if has_mode {
            unsafe { SetConsoleMode(handle, saved | ENABLE_VIRTUAL_TERMINAL_PROCESSING) };
        }

        // Step one: a line exactly as wide as the window, written at a row with room below it.
        emit("\x1b[H");
        mark(handle, "after esc [ H");
        emit(&"a".repeat(width));
        mark(handle, &format!("after {width} columns"));
        emit("\r\n");
        mark(handle, "after the \\r\\n that follows");

        // Step two: the control, one column short.
        emit("\x1b[H");
        emit(&"b".repeat(width - 1));
        mark(handle, &format!("after {} columns", width - 1));
        emit("\r\n");
        mark(handle, "after the \\r\\n that follows");

        // Step three: a whole frame the way the picker paints one -- every row padded to the full
        // width, a `\r\n` between rows and none after the last.
        emit("\x1b[H");
        mark(handle, "frame: after esc [ H");
        for row in 0..height {
            let label = format!("row {row}");
            emit(&label);
            emit(&" ".repeat(width - label.len()));
            if row + 1 < height {
                emit("\r\n");
            }
        }
        mark(handle, &format!("frame: after {height} padded rows"));

        // And again, because one repaint is not a drift. If the window top climbs per frame, two
        // frames name the rate.
        emit("\x1b[H");
        for row in 0..height {
            let label = format!("again {row}");
            emit(&label);
            emit(&" ".repeat(width - label.len()));
            if row + 1 < height {
                emit("\r\n");
            }
        }
        mark(handle, "frame: after a second paint");

        // Step four: the same frame with wrap at end of line turned off, which is the one lever
        // that would stop a full-width row advancing the cursor by itself.
        if has_mode {
            let no_wrap = (saved | ENABLE_VIRTUAL_TERMINAL_PROCESSING) & !ENABLE_WRAP_AT_EOL_OUTPUT;
            let set = unsafe { SetConsoleMode(handle, no_wrap) } != 0;
            let mut back = 0u32;
            unsafe { GetConsoleMode(handle, &raw mut back) };
            log(&format!(
                "wrap at end of line cleared: {set}, mode now {back:#06x}, wrap bit {}",
                back & ENABLE_WRAP_AT_EOL_OUTPUT != 0
            ));
            emit("\x1b[H");
            for row in 0..height {
                let label = format!("nowrap {row}");
                emit(&label);
                emit(&" ".repeat(width - label.len()));
                if row + 1 < height {
                    emit("\r\n");
                }
            }
            mark(handle, "frame: no wrap, after one paint");
            emit("\x1b[H");
            for row in 0..height {
                let label = format!("nowrap2 {row}");
                emit(&label);
                emit(&" ".repeat(width - label.len()));
                if row + 1 < height {
                    emit("\r\n");
                }
            }
            mark(handle, "frame: no wrap, after a second paint");
            unsafe { SetConsoleMode(handle, saved) };
        }

        // Step five: home both cursors, and keep a full-width row from advancing on its own.
        //
        // `esc [ H` is what the terminal at the far end of the pipe reads, and the console
        // underneath does not interpret it -- the steps above counted its three bytes as three
        // printable characters. `SetConsoleCursorPosition` is the other half, and on its own it
        // moved only the console's own idea of where the cursor is. Both together, with wrap at
        // end of line cleared, is the combination the picker has never tried.
        if has_mode {
            let no_wrap = (saved | ENABLE_VIRTUAL_TERMINAL_PROCESSING) & !ENABLE_WRAP_AT_EOL_OUTPUT;
            unsafe { SetConsoleMode(handle, no_wrap) };
            for pass in 0..3 {
                emit("\x1b[H");
                unsafe { SetConsoleCursorPosition(handle, Coord { x: 0, y: 0 }) };
                mark(handle, &format!("both: pass {pass}, after homing"));
                for row in 0..height {
                    let label = format!("both {pass} row {row}");
                    emit(&label);
                    emit(&" ".repeat(width - label.len()));
                    if row + 1 < height {
                        emit("\r\n");
                    }
                }
                mark(handle, &format!("both: pass {pass}, after the paint"));
            }
            unsafe { SetConsoleMode(handle, saved) };
        }

        log("---- end ----");
    }
}
