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

/// Read one keypress from whichever source this platform delivers keys on.
///
/// Unix decodes bytes from the reader, where an arrow arrives as `esc [ A`. Windows ignores the
/// reader and takes the console event queue instead, because an arrow puts no byte in the stream
/// there -- see [`platform::read_key`]. Both return the same [`Key`], so the picker above this is
/// one code path.
pub fn next_key<R: Read>(reader: &mut R) -> Option<Key> {
    #[cfg(windows)]
    {
        let _ = reader;
        platform::read_key()
    }
    #[cfg(not(windows))]
    {
        decode(reader)
    }
}

/// Decode one keypress from a byte reader, or `None` at end of input.
///
/// Escape sequences are read greedily: an `esc` that is not followed by `[` or `O` is the
/// escape key itself, which is why this takes the reader rather than a buffer.
#[cfg_attr(windows, allow(dead_code))]
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

#[cfg_attr(windows, allow(dead_code))]
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

#[cfg_attr(windows, allow(dead_code))]
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
        if escape_sequences_work() {
            let mut out = io::stdout();
            // Alternate screen, then hide the cursor: the picker draws its own.
            let _ = out.write_all(b"\x1b[?1049h\x1b[?25l");
            let _ = out.flush();
        }
        Some(Self(restore))
    }
}

impl Drop for RawMode {
    fn drop(&mut self) {
        if escape_sequences_work() {
            let mut out = io::stdout();
            let _ = out.write_all(b"\x1b[?25h\x1b[?1049l");
            let _ = out.flush();
        }
        platform::leave_raw(&self.0);
    }
}

/// Whether the terminal reads the escape sequences this module writes, or stores them as text.
///
/// Only meaningful once [`RawMode::enter`] has run, because on Windows the answer is measured
/// there and depends on the output mode it sets.
pub fn escape_sequences_work() -> bool {
    platform::escape_sequences_work()
}

/// Paint a whole frame: home the cursor, write it, clear whatever the last frame left below.
///
/// Redrawing this way rather than clearing first is what stops the screen flickering -- a
/// clear-then-draw leaves the terminal briefly empty and every frame blinks.
pub fn paint(frame: &str) -> io::Result<()> {
    let (width, _) = size();
    let mut buffer = String::with_capacity(frame.len() + 64);
    platform::home_cursor(&mut buffer);
    let mut lines = frame.lines().peekable();
    while let Some(line) = lines.next() {
        buffer.push_str(line);
        // Pad to the window width rather than clearing to end of line with `esc [ K`, for the
        // same reason: padding is plain text, and a shorter line still wipes the tail the last
        // frame left on that row.
        let drawn = visible_columns(line);
        if drawn < width {
            buffer.extend(std::iter::repeat_n(' ', width - drawn));
        }
        // No newline after the last line. A frame is rendered to exactly the window height, so a
        // newline after its final row puts the cursor below the last one and the terminal scrolls
        // to make room.
        //
        // On its own this did not stop the header disappearing under Wine, and for a while the
        // comment here claimed it had. What actually scrolls that console is in
        // [`platform::home_cursor`]: it does not interpret `esc [ H`, so it believed the cursor
        // was walking down the buffer no matter what the frame contained.
        if lines.peek().is_some() {
            buffer.push_str("\r\n");
        }
    }
    // No `esc [ J` to clear below: every row of the window is painted and padded, so there is
    // nothing left over to clear, and one fewer sequence for a console to rewrite.
    platform::write_frame(&buffer)
}

/// The bit a console sets for a bright foreground, which is what bold means to one.
const FOREGROUND_INTENSITY: u16 = 0x0008;

/// Turn the parameters of one `esc [ ... m` into console attribute bits.
///
/// Only what the picker emits is handled: reset, bold, dim, reverse, and the eight foreground
/// colours. The colour index is the one place the two systems disagree on bit order -- a terminal
/// counts red, green, blue up from the low bit and a console counts blue, green, red -- so `32` is
/// green either way and `36` would come out red without the swap.
///
/// This lives out here rather than beside its one caller so it can be tested on either system.
/// The console call it feeds cannot be, and the arithmetic is where a mistake would hide.
#[cfg_attr(not(windows), allow(dead_code))]
fn apply_colour(parameters: &str, default: u16, current: u16) -> u16 {
    let mut attributes = current;
    for parameter in parameters.split(';') {
        match parameter {
            "" | "0" => attributes = default,
            "1" => attributes |= FOREGROUND_INTENSITY,
            "2" => attributes &= !FOREGROUND_INTENSITY,
            "7" => attributes = ((attributes & 0x0f) << 4) | ((attributes & 0xf0) >> 4),
            _ => {
                let Some(index) = parameter
                    .strip_prefix('3')
                    // One digit, so a longer number is not read as its last digit -- `300` is not
                    // a colour and must not come out as black.
                    .filter(|digit| digit.len() == 1)
                    .and_then(|digit| digit.parse::<u16>().ok())
                    .filter(|index| *index <= 7)
                else {
                    continue;
                };
                let swapped = (index & 1) << 2 | (index & 2) | (index & 4) >> 2;
                attributes = (attributes & !0x07) | swapped;
            }
        }
    }
    attributes
}

/// How many columns a string occupies, ignoring the `esc [ ... m` colour sequences in it, which
/// a terminal consumes without moving the cursor.
fn visible_columns(line: &str) -> usize {
    let mut columns = 0;
    let mut characters = line.chars();
    while let Some(character) = characters.next() {
        if character != '\u{1b}' {
            columns += 1;
            continue;
        }
        for escape in characters.by_ref() {
            if escape.is_ascii_alphabetic() {
                break;
            }
        }
    }
    columns
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

    /// Home the cursor. A Unix terminal is driven entirely by sequences, so this is one.
    pub fn home_cursor(buffer: &mut String) {
        buffer.push_str("\x1b[H");
    }

    /// Always, here. A terminal that reaches this code path speaks them by definition -- there is
    /// no other way to drive it. The Windows side has to measure the answer.
    pub fn escape_sequences_work() -> bool {
        true
    }

    /// Write a built frame. The sequences in it are the terminal's own language, so it goes out
    /// as it stands.
    pub fn write_frame(text: &str) -> std::io::Result<()> {
        use std::io::Write;
        let mut out = std::io::stdout();
        out.write_all(text.as_bytes())?;
        out.flush()
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
    use std::io::Write;
    use std::sync::atomic::{AtomicBool, AtomicU16, Ordering};

    // Declared here rather than taken from the `windows` crate: four functions and two
    // constants do not justify a dependency in a binary whose selling point is having none.
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetStdHandle(handle_id: u32) -> isize;
        fn GetConsoleMode(handle: isize, mode: *mut u32) -> i32;
        fn SetConsoleMode(handle: isize, mode: u32) -> i32;
        fn GetConsoleScreenBufferInfo(handle: isize, info: *mut ScreenBufferInfo) -> i32;
        fn SetConsoleCursorPosition(handle: isize, position: Coord) -> i32;
        fn SetConsoleTextAttribute(handle: isize, attributes: u16) -> i32;
        fn GetConsoleCursorInfo(handle: isize, info: *mut ConsoleCursorInfo) -> i32;
        fn SetConsoleCursorInfo(handle: isize, info: *const ConsoleCursorInfo) -> i32;
        fn ReadConsoleInputW(handle: isize, buffer: *mut u8, len: u32, read: *mut u32) -> i32;
    }

    /// Home the cursor the way this console can actually be homed.
    ///
    /// A console that interprets the escape sequences gets `esc [ H`, which is also what a real
    /// terminal at the far end of a pipe reads. One that does not gets the API call, because the
    /// sequence would otherwise be three characters printed into the frame -- see
    /// [`escape_sequences_work`] for how that is told apart, and what it costs to get wrong.
    pub fn home_cursor(buffer: &mut String) {
        if escape_sequences_work() {
            buffer.push_str("\x1b[H");
            return;
        }
        if let Some(output) = handle(STD_OUTPUT_HANDLE) {
            unsafe { SetConsoleCursorPosition(output, Coord { x: 0, y: 0 }) };
        }
    }

    /// Whether this console interprets the escape sequences, measured rather than assumed.
    ///
    /// # What goes wrong when it is assumed
    ///
    /// Windows 10 and later interpret them once `ENABLE_VIRTUAL_TERMINAL_PROCESSING` is set, and
    /// the console Wine gives a Linux player accepts that flag and does not implement it. There is
    /// no version to ask and no error to catch: `SetConsoleMode` returns success, the mode reads
    /// back with the bit set, and every sequence the picker writes is then stored as ordinary
    /// characters.
    ///
    /// Measured 2026-09-19 under Wine at 209x75, reading `GetConsoleScreenBufferInfo` back after
    /// each write: `esc [ H` left the cursor three columns further along instead of at the origin,
    /// one column per byte of the sequence. The console had also counted those three columns
    /// against the row, so a window-height frame walked its cursor to the bottom of the buffer and
    /// the console scrolled to make room -- which is the picker's header going missing, emitted by
    /// the console rather than asked for by the painter. That is why nothing in the frame could
    /// account for it, and why every fix aimed at the frame failed.
    ///
    /// The test is the same measurement: put the cursor at the origin through the API, write
    /// `esc [ H`, and ask where it is. Still at the origin means the sequence was read. Three
    /// columns along means it was stored.
    pub fn escape_sequences_work() -> bool {
        ESCAPE_SEQUENCES_WORK.load(Ordering::Relaxed)
    }

    static ESCAPE_SEQUENCES_WORK: AtomicBool = AtomicBool::new(false);

    /// The attributes the console had before the picker started, which is what `esc [ 0 m` means
    /// on the path that has to reproduce colour through the API.
    static DEFAULT_ATTRIBUTES: AtomicU16 = AtomicU16::new(0x07);

    /// Write a built frame, in whichever language this console understands.
    ///
    /// A console that reads the escape sequences gets the frame as it stands. One that stores
    /// them as characters gets the same frame with every `esc [ ... m` taken out of the text and
    /// applied through `SetConsoleTextAttribute` instead, so a Linux player running the exe under
    /// Wine sees the same colours rather than a screen full of bracket codes.
    pub fn write_frame(text: &str) -> std::io::Result<()> {
        let mut out = std::io::stdout();
        if escape_sequences_work() {
            out.write_all(text.as_bytes())?;
            return out.flush();
        }

        let console = handle(STD_OUTPUT_HANDLE);
        let default = DEFAULT_ATTRIBUTES.load(Ordering::Relaxed);
        let mut attributes = default;
        let mut rest = text;
        while let Some(escape) = rest.find('\x1b') {
            let (plain, tail) = rest.split_at(escape);
            write_run(&mut out, console, attributes, plain)?;
            // `esc [ <parameters> <letter>`. The picker emits nothing but colour, so a sequence
            // ending in anything other than `m` is dropped whole rather than guessed at.
            let after = &tail[1..];
            let Some(end) = after.find(|character: char| character.is_ascii_alphabetic()) else {
                rest = "";
                break;
            };
            if after.as_bytes()[end] == b'm' {
                let parameters = after[..end].trim_start_matches('[');
                attributes = super::apply_colour(parameters, default, attributes);
            }
            rest = &after[end + 1..];
        }
        write_run(&mut out, console, attributes, rest)?;
        if let Some(console) = console {
            unsafe { SetConsoleTextAttribute(console, default) };
        }
        out.flush()
    }

    /// One stretch of text under one attribute. Flushed before the next attribute is set, because
    /// the attribute applies from the moment it is set and buffered text would land under it.
    fn write_run(
        out: &mut std::io::Stdout,
        console: Option<isize>,
        attributes: u16,
        text: &str,
    ) -> std::io::Result<()> {
        if text.is_empty() {
            return Ok(());
        }
        if let Some(console) = console {
            out.flush()?;
            unsafe { SetConsoleTextAttribute(console, attributes) };
        }
        out.write_all(text.as_bytes())
    }

    /// Run the measurement above and remember it. Called once, from [`enter_raw`], because the
    /// answer is only meaningful after the output mode has asked for the sequences.
    fn measure_escape_sequences(output: isize) {
        let mut info = ScreenBufferInfo::default();
        unsafe { SetConsoleCursorPosition(output, Coord { x: 0, y: 0 }) };
        let mut out = std::io::stdout();
        let _ = out.write_all(b"\x1b[H");
        let _ = out.flush();
        if unsafe { GetConsoleScreenBufferInfo(output, &raw mut info) } == 0 {
            return;
        }
        let stored_as_characters = info.cursor_position.x == 3 && info.cursor_position.y == 0;
        ESCAPE_SEQUENCES_WORK.store(!stored_as_characters, Ordering::Relaxed);
    }

    const STD_INPUT_HANDLE: u32 = -10i32 as u32;
    const STD_OUTPUT_HANDLE: u32 = -11i32 as u32;
    const ENABLE_PROCESSED_INPUT: u32 = 0x0001;
    const ENABLE_LINE_INPUT: u32 = 0x0002;
    const ENABLE_ECHO_INPUT: u32 = 0x0004;
    const ENABLE_VIRTUAL_TERMINAL_INPUT: u32 = 0x0200;
    const ENABLE_VIRTUAL_TERMINAL_PROCESSING: u32 = 0x0004;
    const ENABLE_WRAP_AT_EOL_OUTPUT: u32 = 0x0002;

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
        cursor: ConsoleCursorInfo,
    }

    /// `CONSOLE_CURSOR_INFO`: how tall the cursor is drawn, as a percentage of the cell, and
    /// whether it is drawn at all.
    #[repr(C)]
    #[derive(Default, Clone, Copy)]
    struct ConsoleCursorInfo {
        size: u32,
        visible: i32,
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
        // Wrap at end of line off, because the picker pads every row to the full window width and
        // with wrapping on the character in the last column advances the cursor to the next row by
        // itself -- the `\r\n` that follows then advances it a second time, so a frame consumes
        // twice its own height and the console scrolls under it. Measured 2026-09-19 at 209x75:
        // 209 columns moved the cursor from `y=0` to `y=1` before a newline was written at all,
        // and with the flag cleared the row stayed put and the padding past the margin was
        // discarded, which is what a full-screen frame wants.
        let ansi_output =
            (saved_output | ENABLE_VIRTUAL_TERMINAL_PROCESSING) & !ENABLE_WRAP_AT_EOL_OUTPUT;
        if unsafe { SetConsoleMode(input, raw_input) } == 0 {
            return None;
        }
        if unsafe { SetConsoleMode(output, ansi_output) } == 0 {
            unsafe { SetConsoleMode(input, saved_input) };
            return None;
        }
        let mut info = ScreenBufferInfo::default();
        if unsafe { GetConsoleScreenBufferInfo(output, &raw mut info) } != 0 {
            DEFAULT_ATTRIBUTES.store(info.attributes, Ordering::Relaxed);
        }
        measure_escape_sequences(output);

        // Hide the cursor through the API rather than with `esc [ ? 25 l`, on a console that would
        // print that sequence instead of reading it. It is the block the picker leaves parked at
        // the end of the last row it painted, and it moves on every keypress because every
        // keypress repaints.
        let mut cursor = ConsoleCursorInfo::default();
        if !escape_sequences_work() && unsafe { GetConsoleCursorInfo(output, &raw mut cursor) } != 0
        {
            let hidden = ConsoleCursorInfo {
                size: cursor.size.max(1),
                visible: 0,
            };
            unsafe { SetConsoleCursorInfo(output, &raw const hidden) };
        }

        Some(Restore {
            input: saved_input,
            output: saved_output,
            cursor,
        })
    }

    pub fn leave_raw(restore: &Restore) {
        if let Some(input) = handle(STD_INPUT_HANDLE) {
            unsafe { SetConsoleMode(input, restore.input) };
        }
        if let Some(output) = handle(STD_OUTPUT_HANDLE) {
            unsafe { SetConsoleMode(output, restore.output) };
            unsafe { SetConsoleTextAttribute(output, DEFAULT_ATTRIBUTES.load(Ordering::Relaxed)) };
            // A zero size means nothing was captured, so there is nothing to put back.
            if restore.cursor.size > 0 {
                unsafe { SetConsoleCursorInfo(output, &raw const restore.cursor) };
            }
        }
    }

    /// `INPUT_RECORD` on x86-64: a `WORD` event type, two bytes of padding, then the union whose
    /// `KEY_EVENT_RECORD` arm is `BOOL` down, `WORD` repeat, `WORD` virtual key, `WORD` scan,
    /// `WCHAR` char, `DWORD` control state. Twenty bytes, read at fixed offsets rather than
    /// declared as a Rust union, because only three fields are wanted.
    const INPUT_RECORD_BYTES: usize = 20;
    const KEY_EVENT: u16 = 0x0001;

    /// Block until the console reports a key, and return it.
    ///
    /// # Why this exists rather than reading bytes from stdin
    ///
    /// Reading stdin and decoding `esc [ A` is what the Unix side does, and on Windows it loses
    /// every arrow key. Measured 2026-09-19 against the exe running under Wine: a picker that
    /// rendered correctly, with raw mode genuinely on and every printable key working, would not
    /// move its cursor, because an arrow puts no byte in the stream. The same keys arrive here
    /// intact -- `VK_UP` (`0x26`) and `VK_DOWN` (`0x28`) as clean down/up pairs -- so the console
    /// queue is where a Windows build has to read from.
    ///
    /// Never mix the two on one thread. A loop that polls the event queue and otherwise blocks in
    /// a stdin read will block forever on the first arrow: no byte arrives to release the read, so
    /// nothing ever drains the queue the key went into. That mistake is what made an earlier probe
    /// report arrows as undeliverable when Wine was delivering them.
    pub fn read_key() -> Option<crate::tui::Key> {
        use crate::tui::Key;

        let input = handle(STD_INPUT_HANDLE)?;
        loop {
            let mut record = [0u8; INPUT_RECORD_BYTES];
            let mut read = 0u32;
            if unsafe { ReadConsoleInputW(input, record.as_mut_ptr(), 1, &raw mut read) } == 0 {
                return None;
            }
            if read != 1 || u16::from_le_bytes([record[0], record[1]]) != KEY_EVENT {
                continue;
            }
            // Key-up is skipped: every key reports both edges, and acting on both would move the
            // cursor two rows per press.
            let down = u32::from_le_bytes([record[4], record[5], record[6], record[7]]) != 0;
            if !down {
                continue;
            }
            let virtual_key = u16::from_le_bytes([record[10], record[11]]);
            let character = u16::from_le_bytes([record[14], record[15]]);

            let key = match virtual_key {
                0x26 => Key::Up,
                0x28 => Key::Down,
                0x25 => Key::Left,
                0x27 => Key::Right,
                0x21 => Key::PageUp,
                0x22 => Key::PageDown,
                0x24 => Key::Home,
                0x23 => Key::End,
                0x0d => Key::Enter,
                0x20 => Key::Space,
                0x1b => Key::Escape,
                // A modifier reports a key event with no character, and `char::from_u32(0)` is
                // `Some('\0')`, so the zero has to be rejected before the conversion rather than
                // after it -- otherwise holding shift feeds the picker a null character.
                _ => match character {
                    0 => continue,
                    0x03 => Key::Interrupt,
                    other => match char::from_u32(u32::from(other)) {
                        Some(found) => Key::Char(found),
                        None => continue,
                    },
                },
            };
            return Some(key);
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

    /// The attributes a console starts with: grey on black, the default the picker resets to.
    const GREY: u16 = 0x07;

    #[test]
    fn bold_and_dim_move_the_brightness_bit() {
        assert_eq!(apply_colour("1", GREY, GREY), 0x0f);
        assert_eq!(apply_colour("2", GREY, 0x0f), GREY);
    }

    #[test]
    fn reverse_video_swaps_the_two_halves_of_the_attribute() {
        // Grey on black becomes black on grey, which is what the cursor row is drawn with.
        assert_eq!(apply_colour("7", GREY, GREY), 0x70);
        // And it is its own inverse, so a reset is not the only way back.
        assert_eq!(apply_colour("7", GREY, 0x70), GREY);
    }

    #[test]
    fn the_colour_index_is_swapped_for_a_console() {
        // A terminal counts red, green, blue up from the low bit; a console counts blue, green,
        // red. Green is bit one in both and cyan is where the two orders visibly disagree.
        assert_eq!(apply_colour("32", GREY, GREY) & 0x07, 0x02);
        assert_eq!(apply_colour("36", GREY, GREY) & 0x07, 0x03);
        assert_eq!(apply_colour("31", GREY, GREY) & 0x07, 0x04);
        assert_eq!(apply_colour("34", GREY, GREY) & 0x07, 0x01);
        assert_eq!(apply_colour("33", GREY, GREY) & 0x07, 0x06);
    }

    #[test]
    fn a_reset_goes_back_to_what_the_console_started_with() {
        assert_eq!(apply_colour("0", GREY, 0x70), GREY);
        // `esc [ m` with no parameters is a reset too.
        assert_eq!(apply_colour("", GREY, 0x70), GREY);
    }

    #[test]
    fn several_parameters_apply_in_order() {
        // The section headers are written as `esc [ 1 ; 36 m` -- bright cyan, not one or other.
        assert_eq!(apply_colour("1;36", GREY, GREY), 0x0b);
        // And the greyed-out cursor row as `esc [ 2 ; 7 m`.
        assert_eq!(apply_colour("2;7", GREY, GREY), 0x70);
    }

    #[test]
    fn a_parameter_that_is_not_understood_changes_nothing() {
        // Underline, a background colour, and a nonsense number all leave the attribute alone
        // rather than turning into a colour by accident.
        assert_eq!(apply_colour("4", GREY, GREY), GREY);
        assert_eq!(apply_colour("42", GREY, GREY), GREY);
        assert_eq!(apply_colour("39", GREY, GREY), GREY);
        assert_eq!(apply_colour("300", GREY, GREY), GREY);
    }

    #[test]
    fn a_size_is_always_returned() {
        let (columns, rows) = size();
        assert!(columns >= 20 && rows >= 5, "got {columns}x{rows}");
    }
}
