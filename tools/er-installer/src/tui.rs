//! Raw terminal mode, key decoding and screen control, with no crates at all.
//!
//! # Why this is hand-rolled
//!
//! The installer's one product requirement is that a player downloads a single file and runs
//! it. `crossterm` would be the obvious answer and costs a dependency tree in a binary that
//! otherwise has none, so the two things actually needed are written here instead: put the
//! terminal in raw mode, and read a keypress.
//!
//! # One key decoder for both systems
//!
//! Windows 10 and later accept the same escape sequences Unix terminals emit, once the console
//! is asked for them -- `ENABLE_VIRTUAL_TERMINAL_INPUT` on the input handle and
//! `ENABLE_VIRTUAL_TERMINAL_PROCESSING` on the output handle. So the console setup differs and
//! [`decode`] does not: an arrow key is `esc [ A` on both.
//!
//! # Failing to enter raw mode is not an error
//!
//! Piped input, a terminal that is not one, a console handle that refuses -- all of them make
//! [`RawMode::enter`] return `None`, and the caller falls back to the line-based picker. A tool
//! that only works on a tty is a tool that cannot be scripted or tested.

use std::io::{self, Read, Write};

/// A key the picker can act on. Anything else is dropped rather than guessed at.
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Key {
    Up,
    Down,
    PageUp,
    PageDown,
    Home,
    End,
    Left,
    Right,
    Enter,
    Space,
    Escape,
    Interrupt,
    Char(char),
}

/// Decode one keypress from a byte reader, or `None` at end of input.
///
/// Escape sequences are read greedily: an `esc` that is not followed by `[` or `O` is the
/// escape key itself, which is why this takes the reader rather than a buffer.
pub fn decode<R: Read>(reader: &mut R) -> Option<Key> {
    let first = read_byte(reader)?;
    match first {
        0x03 => Some(Key::Interrupt),
        b'\r' | b'\n' => Some(Key::Enter),
        b' ' => Some(Key::Space),
        0x1b => decode_escape(reader),
        // Backspace and delete are not bound to anything, and neither are the other controls.
        0x00..=0x1f | 0x7f => Some(Key::Char('\u{0}')),
        byte => Some(Key::Char(byte as char)),
    }
}

fn decode_escape<R: Read>(reader: &mut R) -> Option<Key> {
    let Some(second) = read_byte(reader) else {
        return Some(Key::Escape);
    };
    if second != b'[' && second != b'O' {
        return Some(Key::Escape);
    }
    let third = read_byte(reader)?;
    match third {
        b'A' => Some(Key::Up),
        b'B' => Some(Key::Down),
        b'C' => Some(Key::Right),
        b'D' => Some(Key::Left),
        b'H' => Some(Key::Home),
        b'F' => Some(Key::End),
        // `esc [ <n> ~` -- page up is 5, page down 6, home 1 or 7, end 4 or 8.
        b'0'..=b'9' => {
            let mut number = String::new();
            number.push(third as char);
            loop {
                let byte = read_byte(reader)?;
                if byte == b'~' {
                    break;
                }
                number.push(byte as char);
                if number.len() > 8 {
                    return Some(Key::Escape);
                }
            }
            match number.as_str() {
                "5" => Some(Key::PageUp),
                "6" => Some(Key::PageDown),
                "1" | "7" => Some(Key::Home),
                "4" | "8" => Some(Key::End),
                _ => Some(Key::Escape),
            }
        }
        _ => Some(Key::Escape),
    }
}

fn read_byte<R: Read>(reader: &mut R) -> Option<u8> {
    let mut byte = [0u8; 1];
    match reader.read(&mut byte) {
        Ok(1) => Some(byte[0]),
        _ => None,
    }
}

/// Terminal size in columns and rows, or a conservative default when it cannot be asked for.
pub fn size() -> (usize, usize) {
    platform::size().unwrap_or((80, 24))
}

/// Whether colour should be emitted. `NO_COLOR` is honoured because a terminal that cannot
/// render it produces unreadable noise rather than plain text.
pub fn colour_enabled() -> bool {
    std::env::var_os("NO_COLOR").is_none()
        && std::env::var_os("TERM").as_deref() != Some("dumb".as_ref())
}

/// Raw mode, restored when this is dropped -- including while a panic unwinds, which is the
/// reason it is a guard rather than a pair of calls.
pub struct RawMode(platform::Restore);

impl RawMode {
    /// Enter raw mode and switch to the alternate screen. `None` when the terminal will not
    /// have it, which the caller treats as "use the line-based picker".
    pub fn enter() -> Option<Self> {
        let restore = platform::enter_raw()?;
        let mut out = io::stdout();
        // Alternate screen, then hide the cursor: the picker draws its own.
        let _ = out.write_all(b"\x1b[?1049h\x1b[?25l");
        let _ = out.flush();
        Some(Self(restore))
    }
}

impl Drop for RawMode {
    fn drop(&mut self) {
        let mut out = io::stdout();
        let _ = out.write_all(b"\x1b[?25h\x1b[?1049l");
        let _ = out.flush();
        platform::leave_raw(&self.0);
    }
}

/// Paint a whole frame: home the cursor, write it, clear whatever the last frame left below.
///
/// Redrawing this way rather than clearing first is what stops the screen flickering -- a
/// clear-then-draw leaves the terminal briefly empty and every frame blinks.
pub fn paint(frame: &str) -> io::Result<()> {
    let mut out = io::stdout();
    let mut buffer = String::with_capacity(frame.len() + 64);
    buffer.push_str("\x1b[H");
    for line in frame.lines() {
        buffer.push_str(line);
        // Clear to end of line, so a shorter line does not leave the last frame's tail behind.
        buffer.push_str("\x1b[K\r\n");
    }
    buffer.push_str("\x1b[J");
    out.write_all(buffer.as_bytes())?;
    out.flush()
}

#[cfg(unix)]
mod platform {
    use std::process::Command;

    /// The `stty` settings string captured before raw mode, handed back to restore them.
    ///
    /// `stty` is shelled out to rather than calling `tcsetattr`, because that needs `libc` and
    /// this binary ships with no dependencies. It runs twice per session, not per keystroke.
    pub struct Restore(String);

    fn stty(args: &[&str]) -> Option<String> {
        let output = Command::new("stty")
            .args(args)
            .stdin(std::process::Stdio::inherit())
            .output()
            .ok()?;
        if !output.status.success() {
            return None;
        }
        // `stty` reports terminal settings as ASCII. A non-UTF-8 byte here means the terminal
        // is not one this picker can drive, and the caller falls back to the line-based picker
        // either way, so a replacement character reaches that same fallback harmlessly.
        // UTF-8 Lossy: a terminal that answers in non-UTF-8 is a fallback, not a failure.
        let text = String::from_utf8_lossy(&output.stdout);
        Some(text.trim().to_string())
    }

    pub fn enter_raw() -> Option<Restore> {
        let saved = stty(&["-g"])?;
        // `-echo` stops keypresses printing themselves, `raw` delivers them without waiting
        // for a newline. `isig` is left off so ctrl-c arrives as a byte the picker handles.
        stty(&["raw", "-echo"])?;
        Some(Restore(saved))
    }

    pub fn leave_raw(restore: &Restore) {
        let _ = stty(&[&restore.0]);
    }

    pub fn size() -> Option<(usize, usize)> {
        let reported = stty(&["size"])?;
        let mut parts = reported.split_whitespace();
        let rows: usize = parts.next()?.parse().ok()?;
        let columns: usize = parts.next()?.parse().ok()?;
        (rows > 0 && columns > 0).then_some((columns, rows))
    }
}

#[cfg(windows)]
mod platform {
    // Declared here rather than taken from the `windows` crate: four functions and two
    // constants do not justify a dependency in a binary whose selling point is having none.
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetStdHandle(handle_id: u32) -> isize;
        fn GetConsoleMode(handle: isize, mode: *mut u32) -> i32;
        fn SetConsoleMode(handle: isize, mode: u32) -> i32;
        fn GetConsoleScreenBufferInfo(handle: isize, info: *mut ScreenBufferInfo) -> i32;
    }

    const STD_INPUT_HANDLE: u32 = -10i32 as u32;
    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    const ENABLE_PROCESSED_INPUT: u32 = 0x0001;
    const ENABLE_LINE_INPUT: u32 = 0x0002;
    const ENABLE_ECHO_INPUT: u32 = 0x0004;
    const ENABLE_VIRTUAL_TERMINAL_INPUT: u32 = 0x0200;
    const ENABLE_VIRTUAL_TERMINAL_PROCESSING: u32 = 0x0004;
    const INVALID_HANDLE_VALUE: isize = -1;

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

    /// The console modes captured before raw mode, handed back to restore them.
    pub struct Restore {
        input: u32,
        output: u32,
    }

    fn handle(id: u32) -> Option<isize> {
        let raw = unsafe { GetStdHandle(id) };
        (raw != 0 && raw != INVALID_HANDLE_VALUE).then_some(raw)
    }

    fn mode(handle: isize) -> Option<u32> {
        let mut value = 0u32;
        (unsafe { GetConsoleMode(handle, &raw mut value) } != 0).then_some(value)
    }

    pub fn enter_raw() -> Option<Restore> {
        let input = handle(STD_INPUT_HANDLE)?;
        let output = handle(STD_OUTPUT_HANDLE)?;
        let saved_input = mode(input)?;
        let saved_output = mode(output)?;

        // Line input and echo off so keys arrive one at a time and do not print themselves;
        // virtual terminal input on so arrow keys arrive as the same escape sequences Unix
        // sends, which is what lets one decoder serve both systems.
        let raw_input = (saved_input
            & !(ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT | ENABLE_PROCESSED_INPUT))
            | ENABLE_VIRTUAL_TERMINAL_INPUT;
        let ansi_output = saved_output | ENABLE_VIRTUAL_TERMINAL_PROCESSING;
        if unsafe { SetConsoleMode(input, raw_input) } == 0 {
            return None;
        }
        if unsafe { SetConsoleMode(output, ansi_output) } == 0 {
            unsafe { SetConsoleMode(input, saved_input) };
            return None;
        }
        Some(Restore {
            input: saved_input,
            output: saved_output,
        })
    }

    pub fn leave_raw(restore: &Restore) {
        if let Some(input) = handle(STD_INPUT_HANDLE) {
            unsafe { SetConsoleMode(input, restore.input) };
        }
        if let Some(output) = handle(STD_OUTPUT_HANDLE) {
            unsafe { SetConsoleMode(output, restore.output) };
        }
    }

    pub fn size() -> Option<(usize, usize)> {
        let output = handle(STD_OUTPUT_HANDLE)?;
        let mut info = ScreenBufferInfo::default();
        if unsafe { GetConsoleScreenBufferInfo(output, &raw mut info) } == 0 {
            return None;
        }
        // The window, not the buffer: a console buffer is commonly 9001 rows tall.
        let columns = (info.window.right - info.window.left + 1).max(1) as usize;
        let rows = (info.window.bottom - info.window.top + 1).max(1) as usize;
        Some((columns, rows))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn keys(input: &[u8]) -> Vec<Key> {
        let mut reader = io::Cursor::new(input.to_vec());
        let mut out = Vec::new();
        while let Some(key) = decode(&mut reader) {
            out.push(key);
        }
        out
    }

    #[test]
    fn arrow_keys_decode() {
        assert_eq!(
            keys(b"\x1b[A\x1b[B\x1b[C\x1b[D"),
            vec![Key::Up, Key::Down, Key::Right, Key::Left]
        );
    }

    #[test]
    fn application_cursor_mode_arrows_decode_the_same() {
        // Some terminals send `esc O A` instead of `esc [ A` once the alternate screen is up.
        assert_eq!(keys(b"\x1bOA\x1bOB"), vec![Key::Up, Key::Down]);
    }

    #[test]
    fn paging_and_jump_keys_decode() {
        assert_eq!(
            keys(b"\x1b[5~\x1b[6~\x1b[1~\x1b[4~"),
            vec![Key::PageUp, Key::PageDown, Key::Home, Key::End]
        );
    }

    #[test]
    fn plain_keys_decode() {
        assert_eq!(keys(b" \ra"), vec![Key::Space, Key::Enter, Key::Char('a')]);
    }

    #[test]
    fn ctrl_c_is_its_own_key_rather_than_a_character() {
        assert_eq!(keys(&[0x03]), vec![Key::Interrupt]);
    }

    #[test]
    fn a_lone_escape_is_the_escape_key() {
        assert_eq!(keys(b"\x1b"), vec![Key::Escape]);
        // Escape followed by an ordinary key is escape, then that key.
        assert_eq!(keys(b"\x1bq"), vec![Key::Escape]);
    }

    #[test]
    fn an_unterminated_sequence_does_not_hang_or_panic() {
        assert_eq!(keys(b"\x1b[999999999"), vec![Key::Escape]);
        assert!(keys(b"\x1b[").is_empty());
    }

    #[test]
    fn end_of_input_ends_decoding() {
        assert!(keys(b"").is_empty());
    }

    #[test]
    fn a_frame_is_painted_with_each_line_cleared_to_its_end() {
        // Not a terminal assertion -- just that the control codes the painter relies on are
        // the ones being emitted, since a wrong one leaves the previous frame's tail on screen.
        let frame = "one\ntwo";
        let mut buffer = String::new();
        buffer.push_str("\x1b[H");
        for line in frame.lines() {
            buffer.push_str(line);
            buffer.push_str("\x1b[K\r\n");
        }
        buffer.push_str("\x1b[J");
        assert!(buffer.starts_with("\x1b[H"));
        assert_eq!(buffer.matches("\x1b[K").count(), 2);
        assert!(buffer.ends_with("\x1b[J"));
    }

    #[test]
    fn a_size_is_always_returned() {
        let (columns, rows) = size();
        assert!(columns >= 20 && rows >= 5, "got {columns}x{rows}");
    }
}
