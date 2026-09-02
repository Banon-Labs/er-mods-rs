//! Which Seamless Co-op build is installed, and whether this shim was measured against it.
//!
//! # Why this gate exists
//!
//! Every fixup in [`crate::fixups`] is a statement about what **one specific** `ersc.dll` build
//! searches the game image for. `scadutree_getter` does not merely add bytes: it rewrites the
//! entry of the game's `GetScadutreeBlessing` to the 7-byte `cmp byte [rcx+0xab5], 0` that
//! Seamless Co-op **v1.9.9** looks for -- an offset that belongs to ELDEN RING 1.16.2 and is
//! wrong for the 1.17 the game actually is. That rewrite is only ever correct as a favour to a
//! scanner that is looking for exactly those bytes.
//!
//! Nothing used to check that such a scanner was present. `ersc.dll` is third-party, the user
//! updates it whenever they like, and on 2026-09-02 v2.0.0 replaced v1.9.9 -- a build stamped
//! the same day, months after v1.9.9 and after ELDEN RING 1.17 shipped. On that install the shim
//! still rewrote the game function on behalf of a version no longer on disk.
//!
//! The failure mode of guessing here is the quiet one. Scadutree blessing is **session** state in
//! co-op: a guest reports its level to the host, and a wrong answer changes the other player's
//! damage numbers with nothing on screen to say so. A refusal costs a boot and names itself in a
//! log; a wrong rewrite costs someone else's session and says nothing.
//!
//! So the shim now acts only on a build it was measured against, and otherwise refuses with the
//! version it found written next to the version it knows.
//!
//! # Why the version comes from the FILE and not the loaded module
//!
//! The shim runs in its own `DllMain`, deliberately before `ersc.dll` is loaded -- the decoy has
//! to exist before ersc scans for it. There is therefore no ersc module to interrogate, and the
//! only evidence available at that moment is the file on disk. It is opened read-only and never
//! copied, moved, or staged (`AGENTS.md` forbids bundling it, and a version check has no reason
//! to want a copy).

/// The Seamless Co-op version this shim's fixups were measured against.
///
/// The two AOB shapes in [`crate::fixups`] were transcribed from **this** build's fatal-error box.
/// Bumping this constant is a claim that the new build searches for the same bytes, which is a
/// measurement, not a guess -- see the module docs on the cost of getting it wrong.
pub(crate) const MEASURED_VERSION: &str = "1.9.9";

/// The banner ersc builds into its own image, as UTF-16LE code units would spell it.
const BANNER_PREFIX: &str = "Seamless Co-op v";

/// How far into the file the banner is looked for.
///
/// It sits in `.rdata`, just under 2 MB in both known builds (v1.9.9 at file offset `0x1dcaf4`,
/// v2.0.0 at `0x1e19dc`). Reading a bounded prefix keeps this off the packer's VM section -- 11 MB
/// of it in v2.0.0 -- which matters because this runs under the loader lock in `DllMain`. A build
/// that moves the banner past this bound reports "not found" and the shim refuses, which is the
/// safe direction.
const SEARCH_LIMIT: usize = 8 << 20;

/// Longest banner accepted, in bytes of UTF-16LE, so a corrupt image cannot walk the file.
const MAX_BANNER_BYTES: usize = 128;

/// The `X.Y.Z` version out of an ersc image's banner, or `None` when it is not there.
///
/// The banner is UTF-16LE and NUL-terminated. Reading it as if it were ASCII -- by stripping the
/// interleaved NULs -- silently runs it into the string pooled directly after it: the observed
/// result on both known builds is `2.0.0 by YuiThis`, three characters of the next sentence
/// welded onto the author's name. Hence decoding to the terminator rather than pattern-matching
/// the raw bytes.
pub(crate) fn version_in_image(bytes: &[u8]) -> Option<String> {
    let prefix: Vec<u8> = BANNER_PREFIX
        .encode_utf16()
        .flat_map(u16::to_le_bytes)
        .collect();
    let haystack = &bytes[..bytes.len().min(SEARCH_LIMIT)];
    let start = haystack
        .windows(prefix.len())
        .position(|window| window == prefix)?;
    let body_start = start + prefix.len();
    let mut end = body_start;
    while end + 2 <= haystack.len()
        && end - body_start < MAX_BANNER_BYTES
        && haystack[end..end + 2] != [0, 0]
    {
        end += 2;
    }
    let units: Vec<u16> = haystack[body_start..end]
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| u16::from_le_bytes(*pair))
        .collect();
    let body = String::from_utf16(&units).ok()?;
    // "2.0.0 by Yui" -> "2.0.0". The author tail is not part of the identity.
    let version = body.split_whitespace().next()?.to_string();
    let mut parts = version.split('.');
    let looks_like_a_version = (0..3).all(|_| {
        parts
            .next()
            .is_some_and(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit()))
    }) && parts.next().is_none();
    looks_like_a_version.then_some(version)
}

/// Why the shim declined to identify the installed Seamless Co-op build.
///
/// Each variant names the thing that is missing rather than reporting a generic failure, because
/// this text is the only account anyone gets of why the game booted without the shim's writes.
#[cfg(windows)]
pub(crate) enum Unknown {
    /// No `ersc.dll` at any location this shim looks in.
    NotFound(String),
    /// The file is there but could not be read.
    Unreadable(String),
    /// The file was read but carries no recognisable version banner.
    NoBanner(String),
}

#[cfg(windows)]
impl core::fmt::Display for Unknown {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::NotFound(where_looked) => write!(
                f,
                "no ersc.dll found ({where_looked}); nothing here is for a game without Seamless \
                 Co-op, so the game image was left alone"
            ),
            Self::Unreadable(why) => write!(f, "ersc.dll could not be read: {why}"),
            Self::NoBanner(path) => write!(
                f,
                "{path} carries no \"{BANNER_PREFIX}X.Y.Z\" banner in its first {} bytes, so the \
                 installed Seamless Co-op version cannot be identified",
                SEARCH_LIMIT
            ),
        }
    }
}

/// The Seamless Co-op version installed beside the game, or why it could not be identified.
#[cfg(windows)]
pub(crate) fn installed_version() -> Result<String, Unknown> {
    use std::io::Read;

    let Some(game_dir) = er_game_base::log::game_directory_path() else {
        return Err(Unknown::NotFound(
            "the game directory could not be resolved".to_string(),
        ));
    };
    // The two locations a me3 profile references. `SeamlessCoop/ersc.dll` is what
    // `scripts/er-gen-me3-profile.py` emits and what `AGENTS.md` documents; the bare sibling is
    // the older hand-placed layout. Deliberately NOT `_SeamlessCoop/` -- that is the backup the
    // Seamless launcher leaves behind when it updates itself, so reading it would report the
    // version that was just REPLACED.
    let candidates = [
        game_dir.join("SeamlessCoop").join("ersc.dll"),
        game_dir.join("ersc.dll"),
    ];
    let Some(path) = candidates.iter().find(|path| path.is_file()) else {
        return Err(Unknown::NotFound(format!(
            "looked in {}",
            candidates
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join(" and ")
        )));
    };
    let mut file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(why) => return Err(Unknown::Unreadable(format!("{}: {why}", path.display()))),
    };
    let mut bytes = Vec::new();
    if let Err(why) = file
        .by_ref()
        .take(SEARCH_LIMIT as u64)
        .read_to_end(&mut bytes)
    {
        return Err(Unknown::Unreadable(format!("{}: {why}", path.display())));
    }
    version_in_image(&bytes).ok_or_else(|| Unknown::NoBanner(path.display().to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build the banner the way ersc does: UTF-16LE, NUL-terminated, with another string
    /// pooled immediately after it. The trailing string is the whole point -- it is what a
    /// NUL-stripping "ASCII" search welds onto the version.
    fn image_with(banner: &str) -> Vec<u8> {
        let mut bytes = vec![0x11u8; 64];
        let full = format!("{BANNER_PREFIX}{banner}");
        bytes.extend(full.encode_utf16().flat_map(u16::to_le_bytes));
        bytes.extend([0, 0]);
        bytes.extend(
            "This mod uses a separate"
                .encode_utf16()
                .flat_map(u16::to_le_bytes),
        );
        bytes
    }

    #[test]
    fn reads_the_version_and_stops_at_the_terminator() {
        assert_eq!(
            version_in_image(&image_with("1.9.9 by Yui")).as_deref(),
            Some("1.9.9")
        );
        assert_eq!(
            version_in_image(&image_with("2.0.0 by Yui")).as_deref(),
            Some("2.0.0")
        );
    }

    #[test]
    fn the_measured_version_is_the_one_the_fixups_were_transcribed_from() {
        assert_eq!(
            version_in_image(&image_with("1.9.9 by Yui")).as_deref(),
            Some(MEASURED_VERSION),
        );
        assert_ne!(
            version_in_image(&image_with("2.0.0 by Yui")).as_deref(),
            Some(MEASURED_VERSION),
        );
    }

    #[test]
    fn a_banner_without_an_author_still_reads() {
        assert_eq!(
            version_in_image(&image_with("1.9.9")).as_deref(),
            Some("1.9.9")
        );
    }

    #[test]
    fn an_image_without_the_banner_is_not_identified() {
        assert_eq!(version_in_image(&vec![0x11u8; 4096]), None);
    }

    #[test]
    fn a_banner_that_is_not_three_numbers_is_rejected_rather_than_guessed() {
        assert_eq!(version_in_image(&image_with("beta by Yui")), None);
        assert_eq!(version_in_image(&image_with("1.9 by Yui")), None);
        assert_eq!(version_in_image(&image_with("1.9.9.1 by Yui")), None);
    }

    /// An unterminated banner must stop rather than walk the buffer, and must NOT be read as a
    /// version: what follows the digits is unknown bytes, and "1.9.9" salvaged out of them would
    /// be a guess. Returning `None` refuses, which is the direction that costs a boot instead of
    /// a co-op session.
    #[test]
    fn an_unterminated_banner_is_bounded_and_not_identified() {
        let mut bytes = Vec::new();
        let full = format!("{BANNER_PREFIX}1.9.9");
        bytes.extend(full.encode_utf16().flat_map(u16::to_le_bytes));
        bytes.extend(core::iter::repeat_n(0x41u8, 512));
        assert_eq!(version_in_image(&bytes), None);
    }

    /// The bound is on the banner, not on the file: a banner that terminates normally is still
    /// read when plenty of bytes follow it.
    #[test]
    fn a_terminated_banner_is_read_however_much_follows_it() {
        let mut bytes = image_with("1.9.9 by Yui");
        bytes.extend(core::iter::repeat_n(0x41u8, 4096));
        assert_eq!(version_in_image(&bytes).as_deref(), Some("1.9.9"));
    }
}
