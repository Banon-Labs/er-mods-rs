//! Hotkey text -> DirectInput scancodes.
//!
//! Elden Ring reads the keyboard through `IDirectInputDevice8::GetDeviceState`, which fills a
//! 256-byte DIK table indexed by *scancode*, not by virtual key. So a hotkey has to be expressed
//! in DIK codes or it can never be matched against what the game actually reads.

// Windows-only in practice; kept portable so the table and the parser are covered by `cargo test`
// on the host instead of being trusted from memory.
#![cfg_attr(not(windows), allow(dead_code))]

/// A key is held when the high bit of its DIK byte is set.
pub(crate) const DIK_DOWN_BIT: u8 = 0x80;

pub(crate) const DIK_LCONTROL: usize = 0x1d;
pub(crate) const DIK_RCONTROL: usize = 0x9d;
pub(crate) const DIK_LMENU: usize = 0x38;
pub(crate) const DIK_RMENU: usize = 0xb8;
pub(crate) const DIK_LSHIFT: usize = 0x2a;
pub(crate) const DIK_RSHIFT: usize = 0x36;

pub(crate) const MODIFIER_CTRL: u8 = 1 << 0;
pub(crate) const MODIFIER_ALT: u8 = 1 << 1;
pub(crate) const MODIFIER_SHIFT: u8 = 1 << 2;

/// A parsed hotkey: zero or more modifiers plus exactly one trigger key.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Hotkey {
    pub(crate) modifiers: u8,
    pub(crate) key: usize,
}

/// DIK scancode for a key name, from `dinput.h`.
///
/// Only the keys a hotkey is plausibly bound to are listed; an unknown name is an error rather
/// than a silent no-op, so a typo in the config is reported instead of disabling the feature.
fn dik_for_name(name: &str) -> Option<usize> {
    let code = match name {
        "escape" | "esc" => 0x01,
        "1" => 0x02,
        "2" => 0x03,
        "3" => 0x04,
        "4" => 0x05,
        "5" => 0x06,
        "6" => 0x07,
        "7" => 0x08,
        "8" => 0x09,
        "9" => 0x0a,
        "0" => 0x0b,
        "minus" | "-" => 0x0c,
        "equals" | "=" => 0x0d,
        "backspace" => 0x0e,
        "tab" => 0x0f,
        "q" => 0x10,
        "w" => 0x11,
        "e" => 0x12,
        "r" => 0x13,
        "t" => 0x14,
        "y" => 0x15,
        "u" => 0x16,
        "i" => 0x17,
        "o" => 0x18,
        "p" => 0x19,
        "lbracket" | "[" => 0x1a,
        "rbracket" | "]" => 0x1b,
        "enter" | "return" => 0x1c,
        "a" => 0x1e,
        "s" => 0x1f,
        "d" => 0x20,
        "f" => 0x21,
        "g" => 0x22,
        "h" => 0x23,
        "j" => 0x24,
        "k" => 0x25,
        "l" => 0x26,
        "semicolon" | ";" => 0x27,
        "apostrophe" | "quote" | "'" => 0x28,
        "grave" | "backtick" | "`" => 0x29,
        "backslash" | "\\" => 0x2b,
        "z" => 0x2c,
        "x" => 0x2d,
        "c" => 0x2e,
        "v" => 0x2f,
        "b" => 0x30,
        "n" => 0x31,
        "m" => 0x32,
        "comma" | "," => 0x33,
        "period" | "." => 0x34,
        "slash" | "/" => 0x35,
        "numpad_multiply" | "numpad_*" => 0x37,
        "space" => 0x39,
        "capslock" => 0x3a,
        "f1" => 0x3b,
        "f2" => 0x3c,
        "f3" => 0x3d,
        "f4" => 0x3e,
        "f5" => 0x3f,
        "f6" => 0x40,
        "f7" => 0x41,
        "f8" => 0x42,
        "f9" => 0x43,
        "f10" => 0x44,
        "numlock" => 0x45,
        "scrolllock" => 0x46,
        "numpad7" => 0x47,
        "numpad8" => 0x48,
        "numpad9" => 0x49,
        "numpad_subtract" | "numpad_minus" | "numpad_-" => 0x4a,
        "numpad4" => 0x4b,
        "numpad5" => 0x4c,
        "numpad6" => 0x4d,
        "numpad_add" | "numpad_plus" | "numpad_+" => 0x4e,
        "numpad1" => 0x4f,
        "numpad2" => 0x50,
        "numpad3" => 0x51,
        "numpad0" => 0x52,
        "numpad_decimal" | "numpad_." => 0x53,
        "f11" => 0x57,
        "f12" => 0x58,
        "numpad_enter" => 0x9c,
        "numpad_divide" | "numpad_/" => 0xb5,
        "home" => 0xc7,
        "up" => 0xc8,
        "pageup" => 0xc9,
        "left" => 0xcb,
        "right" => 0xcd,
        "end" => 0xcf,
        "down" => 0xd0,
        "pagedown" => 0xd1,
        "insert" => 0xd2,
        "delete" => 0xd3,
        _ => return None,
    };
    Some(code)
}

/// Parse `"ctrl+alt+c"` into modifiers plus a trigger key.
///
/// Modifiers are side-agnostic: `ctrl` matches either control key, because a hotkey that only
/// answered to the left one would look broken to anyone who reaches for the right.
pub(crate) fn parse_hotkey(raw: &str) -> Result<Hotkey, String> {
    let mut modifiers = 0u8;
    let mut key = None;
    for part in raw.split('+') {
        let part = part.trim().to_ascii_lowercase();
        if part.is_empty() {
            continue;
        }
        match part.as_str() {
            "ctrl" | "control" => modifiers |= MODIFIER_CTRL,
            "alt" => modifiers |= MODIFIER_ALT,
            "shift" => modifiers |= MODIFIER_SHIFT,
            name => {
                let code =
                    dik_for_name(name).ok_or_else(|| format!("unknown key {name:?} in {raw:?}"))?;
                if key.replace(code).is_some() {
                    return Err(format!("more than one non-modifier key in {raw:?}"));
                }
            }
        }
    }
    let key = key.ok_or_else(|| format!("no trigger key in {raw:?}"))?;
    Ok(Hotkey { modifiers, key })
}

/// Is `offset`'s key held down in a DIK table of `size` bytes?
pub(crate) fn dik_key_down(size: u32, data: *const u8, offset: usize) -> bool {
    !data.is_null() && (size as usize) > offset && unsafe { *data.add(offset) } & DIK_DOWN_BIT != 0
}

/// Are every modifier in `modifiers` and the trigger key all held in this DIK table?
pub(crate) fn hotkey_is_down(hotkey: Hotkey, size: u32, data: *const u8) -> bool {
    let modifier_held = |left: usize, right: usize| {
        dik_key_down(size, data, left) || dik_key_down(size, data, right)
    };
    if hotkey.modifiers & MODIFIER_CTRL != 0 && !modifier_held(DIK_LCONTROL, DIK_RCONTROL) {
        return false;
    }
    if hotkey.modifiers & MODIFIER_ALT != 0 && !modifier_held(DIK_LMENU, DIK_RMENU) {
        return false;
    }
    if hotkey.modifiers & MODIFIER_SHIFT != 0 && !modifier_held(DIK_LSHIFT, DIK_RSHIFT) {
        return false;
    }
    dik_key_down(size, data, hotkey.key)
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEYBOARD_STATE_BYTES: u32 = 256;

    fn state_with(down: &[usize]) -> [u8; 256] {
        let mut table = [0u8; 256];
        for offset in down {
            table[*offset] = DIK_DOWN_BIT;
        }
        table
    }

    #[test]
    fn parses_a_modifier_combination() {
        let parsed = parse_hotkey("ctrl+alt+c").expect("ctrl+alt+c parses");
        assert_eq!(parsed.modifiers, MODIFIER_CTRL | MODIFIER_ALT);
        assert_eq!(parsed.key, 0x2e);
    }

    #[test]
    fn parsing_is_case_and_space_insensitive() {
        assert_eq!(
            parse_hotkey(" Ctrl + ALT + C ").expect("spaced parse"),
            parse_hotkey("ctrl+alt+c").expect("plain parse")
        );
    }

    #[test]
    fn a_bare_key_needs_no_modifier() {
        let parsed = parse_hotkey("f9").expect("f9 parses");
        assert_eq!(parsed.modifiers, 0);
        assert_eq!(parsed.key, 0x43);
    }

    #[test]
    fn unknown_and_incomplete_hotkeys_are_errors() {
        assert!(parse_hotkey("ctrl+alt+nonsense").is_err());
        assert!(parse_hotkey("ctrl+alt").is_err());
        assert!(parse_hotkey("ctrl+a+b").is_err());
    }

    #[test]
    fn combination_matches_only_when_every_key_is_held() {
        let hotkey = parse_hotkey("ctrl+alt+c").expect("parse");
        let full = state_with(&[DIK_LCONTROL, DIK_LMENU, 0x2e]);
        assert!(hotkey_is_down(hotkey, KEYBOARD_STATE_BYTES, full.as_ptr()));

        let no_alt = state_with(&[DIK_LCONTROL, 0x2e]);
        assert!(!hotkey_is_down(
            hotkey,
            KEYBOARD_STATE_BYTES,
            no_alt.as_ptr()
        ));

        let no_trigger = state_with(&[DIK_LCONTROL, DIK_LMENU]);
        assert!(!hotkey_is_down(
            hotkey,
            KEYBOARD_STATE_BYTES,
            no_trigger.as_ptr()
        ));
    }

    #[test]
    fn either_side_of_a_modifier_counts() {
        let hotkey = parse_hotkey("ctrl+alt+c").expect("parse");
        let right_hand = state_with(&[DIK_RCONTROL, DIK_RMENU, 0x2e]);
        assert!(hotkey_is_down(
            hotkey,
            KEYBOARD_STATE_BYTES,
            right_hand.as_ptr()
        ));
    }

    #[test]
    fn a_short_buffer_is_never_a_press() {
        let hotkey = parse_hotkey("insert").expect("parse");
        let mouse_sized = [0xffu8; 16];
        assert!(!hotkey_is_down(hotkey, 16, mouse_sized.as_ptr()));
    }
}
