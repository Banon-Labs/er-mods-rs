//! The spoken clip played after the voice chat key flips the game's Voice Chat setting.
//!
//! The clips are the ones `ds2-voice-chat` ships in ds2-mods-rs, so both games say it in the same
//! voice: "Voice chat on" / "Voice chat off", rendered offline with Piper (`en_US-lessac-medium`),
//! 16 kHz 16-bit mono, and compiled into the DLL so there is no speech engine and no file to go
//! missing. English is the one language so far; `announce = ""` silences it.

/// The language the clip is spoken in, or none.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Announce {
    Silent,
    English,
}

/// The language spoken when the file does not name one.
pub const DEFAULT_ANNOUNCE: Announce = Announce::English;

impl Announce {
    /// `"en"`, or `""` for none. `None` for a value it does not know, so the caller keeps the
    /// language already in force.
    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "" | "off" | "none" => Some(Self::Silent),
            "en" | "english" => Some(Self::English),
            _ => None,
        }
    }

    /// The WAV for a voice chat state, or `None` when silent.
    pub const fn clip(self, on: bool) -> Option<&'static [u8]> {
        match (self, on) {
            (Self::Silent, _) => None,
            (Self::English, true) => Some(include_bytes!("../assets/en-on.wav")),
            (Self::English, false) => Some(include_bytes!("../assets/en-off.wav")),
        }
    }

    /// For an atomic: the DLL's key poller and its config reader are on different threads.
    pub const fn as_u8(self) -> u8 {
        match self {
            Self::Silent => 0,
            Self::English => 1,
        }
    }

    pub const fn from_u8(value: u8) -> Self {
        match value {
            1 => Self::English,
            _ => Self::Silent,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_language_has_two_distinct_riff_wave_clips() {
        let on = Announce::English.clip(true).expect("on clip");
        let off = Announce::English.clip(false).expect("off clip");
        assert_ne!(on, off);
        for clip in [on, off] {
            assert_eq!(&clip[0..4], b"RIFF");
            assert_eq!(&clip[8..12], b"WAVE");
        }
        assert_eq!(Announce::Silent.clip(true), None);
        assert_eq!(Announce::Silent.clip(false), None);
    }

    #[test]
    fn parses_languages_and_keeps_unknown_out() {
        assert_eq!(Announce::parse("en"), Some(Announce::English));
        assert_eq!(Announce::parse(" EN "), Some(Announce::English));
        assert_eq!(Announce::parse(""), Some(Announce::Silent));
        assert_eq!(Announce::parse("pl"), None);
    }

    #[test]
    fn the_atomic_round_trips() {
        for a in [Announce::Silent, Announce::English] {
            assert_eq!(Announce::from_u8(a.as_u8()), a);
        }
    }
}
