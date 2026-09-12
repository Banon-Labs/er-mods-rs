//! Inline path completion for the destination browser's editable path bar.
//!
//! Typing `Z:\h` offers `Z:\home` -- the alphabetically first directory under `Z:\` whose name
//! starts with `h`, compared without regard to case. The offer is rendered behind the live text as
//! a dimmed run and accepted with Tab or Right; until then it is a suggestion and nothing else,
//! which is why every function here returns the *whole* line rather than just the tail. The caller
//! writes one string and never has to reason about where the typed part ends.
//!
//! # Why the suggestion keeps what was typed
//!
//! A match is found case-insensitively, so `Z:\h` can match a directory really named `Home`. The
//! suggestion is still `Z:\h` + `ome`, not `Z:\Home`: the ghost is drawn *under* the live field, so
//! the characters the player typed have to be the characters underneath them or the two runs show
//! as doubled glyphs. Windows paths resolve without regard to case, so the completed text opens the
//! same directory either way -- this is the same trade a case-insensitive shell completion makes.
//!
//! # Directories only
//!
//! The field chooses a folder to write into; the file name is the picker's own, not something the
//! player types here. Offering a file would complete to something `set_current_dir_from_text`
//! rejects, so files are skipped while listing.

use std::path::{Path, PathBuf};

/// Separators a typed path may use. The picker exposes Wine's `Z:` filesystem, so a player who
/// pastes a Linux-shaped path gets the same completion as one who types a Windows-shaped path.
const SEPARATORS: [char; 2] = ['\\', '/'];

/// Split typed text into the part naming a directory to list and the partial name being typed.
///
/// The parent keeps its trailing separator, because `Z:\` and `Z:` mean different things and only
/// the first is a directory. Text with no separator at all has no parent to list and returns
/// `None` rather than guessing at a relative directory.
pub fn split_typed_path(typed: &str) -> Option<(&str, &str)> {
    let index = typed.rfind(SEPARATORS)?;
    let (parent, rest) = typed.split_at(index + 1);
    Some((parent, rest))
}

/// The directory a parent fragment names, in the same terms `set_current_dir_from_text` accepts.
fn parent_directory(parent: &str) -> PathBuf {
    #[cfg(windows)]
    {
        // Matches `entered_directory_candidate`: a leading `/` is the Wine Z: filesystem written
        // the way a Linux user types it. Translating here and nowhere else keeps the completion
        // offering paths the commit path will actually accept.
        if parent.starts_with('/') {
            return PathBuf::from(format!("Z:{}", parent.replace('/', "\\")));
        }
    }
    // A bare drive root must keep its separator (`Z:\`); anything longer is trimmed so the
    // platform does not treat the trailing separator as an empty final component.
    let trimmed = parent.trim_end_matches(SEPARATORS);
    if trimmed.len() <= 2 {
        PathBuf::from(parent)
    } else {
        PathBuf::from(trimmed)
    }
}

/// Complete `typed` from `names`, the child directory names of its parent.
///
/// `None` when there is nothing to offer: no separator, nothing being typed yet, no case-insensitive
/// prefix match, or a match that adds no characters. That last one matters -- a suggestion equal to
/// the typed text would draw a ghost the player cannot tell from their own input.
pub fn suggestion_from_names<'a>(
    typed: &str,
    names: impl IntoIterator<Item = &'a str>,
) -> Option<String> {
    let (parent, partial) = split_typed_path(typed)?;
    if partial.is_empty() {
        return None;
    }
    let partial_folded = partial.to_lowercase();
    let best = names
        .into_iter()
        .filter(|name| name.to_lowercase().starts_with(&partial_folded))
        // Alphabetical without regard to case, with the raw name breaking ties so the choice is
        // stable when a directory differs from another only in case.
        .min_by(|left, right| {
            left.to_lowercase()
                .cmp(&right.to_lowercase())
                .then_with(|| left.cmp(right))
        })?;
    // Keep the typed characters and append only what the match adds. `partial` matched a prefix of
    // `best` case-insensitively, so the remainder starts at the same character count.
    let remainder: String = best.chars().skip(partial.chars().count()).collect();
    if remainder.is_empty() {
        return None;
    }
    Some(format!("{parent}{partial}{remainder}"))
}

/// Complete `typed` by listing its parent directory on this machine.
///
/// Errors are not reported: an unreadable or absent parent simply has nothing to offer, which is
/// also what a player half-way through typing a path should see.
pub fn suggestion_for(typed: &str) -> Option<String> {
    let (parent, partial) = split_typed_path(typed)?;
    if partial.is_empty() {
        return None;
    }
    let names = directory_child_names(&parent_directory(parent));
    suggestion_from_names(typed, names.iter().map(String::as_str))
}

/// Child directory names of `dir`, unsorted. Files are skipped -- see the module doc.
///
/// The kind comes from stat'ing the target (`Path::is_dir`), never from the dirent's `file_type`.
/// `SavePickerModel::refresh` learned this first and says why: under Wine the symlinked and
/// btrfs-subvolume directories at the `Z:\` root -- `/usr`, `/bin`, `/home` -- come back as
/// non-directory reparse points, so `file_type` drops them and only plain directories like `/etc`
/// and `/var` survive. Typing `Z:\h` offered nothing on run br-20260912-215501-61b6 for exactly
/// that reason: the listing the player could see included `home/` and this one did not.
///
/// Dot-prefixed entries are hidden here too, so the completion cannot offer a folder the browser
/// refuses to show.
fn directory_child_names(dir: &Path) -> Vec<String> {
    let Ok(read) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    read.flatten()
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            (!name.starts_with('.') && entry.path().is_dir()).then_some(name)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_partial_name_completes_to_the_first_match_without_regard_to_case() {
        // `help` and `Home` both match; folded to lowercase `help` sorts first, and the offer is
        // built from the typed `h` plus that match's remaining characters.
        assert_eq!(
            suggestion_from_names("Z:\\h", ["Windows", "Home", "help", "Games"]),
            Some("Z:\\help".to_owned())
        );
    }

    #[test]
    fn the_typed_characters_survive_verbatim_so_the_ghost_can_sit_under_them() {
        // `Home` matched, but the offer keeps the lowercase `h` the player actually typed.
        let offer = suggestion_from_names("Z:\\h", ["Home"]).expect("a match");
        assert!(offer.starts_with("Z:\\h"), "{offer}");
        assert_eq!(offer, "Z:\\home");
    }

    #[test]
    fn ties_are_broken_alphabetically_without_regard_to_case() {
        assert_eq!(
            suggestion_from_names("Z:\\", ["b", "a"]),
            None,
            "nothing typed yet is nothing to complete"
        );
        // `ANVIL` sorts before `apples` and `Apricot` once case is folded. The typed `a` stays as
        // typed and the remainder keeps the directory's real casing, so the player can read the
        // true name of the part they have not typed yet.
        assert_eq!(
            suggestion_from_names("Z:\\a", ["apples", "Apricot", "ANVIL"]),
            Some("Z:\\aNVIL".to_owned())
        );
    }

    #[test]
    fn a_deeper_parent_is_completed_the_same_way() {
        assert_eq!(
            suggestion_from_names("Z:\\Games\\ste", ["Steam", "Stellaris"]),
            Some("Z:\\Games\\steam".to_owned())
        );
    }

    #[test]
    fn a_forward_slash_path_is_split_on_its_own_separator() {
        assert_eq!(
            suggestion_from_names("/home/ba", ["banon", "backup"]),
            Some("/home/backup".to_owned())
        );
    }

    #[test]
    fn an_exact_name_offers_nothing_because_the_ghost_would_be_invisible() {
        assert_eq!(suggestion_from_names("Z:\\Home", ["Home"]), None);
    }

    #[test]
    fn text_with_no_separator_has_no_parent_to_list() {
        assert_eq!(split_typed_path("home"), None);
        assert_eq!(suggestion_from_names("home", ["home"]), None);
    }

    #[test]
    fn no_prefix_match_offers_nothing() {
        assert_eq!(suggestion_from_names("Z:\\q", ["Home", "Games"]), None);
    }

    #[test]
    fn the_parent_keeps_the_separator_that_makes_a_drive_root_a_directory() {
        assert_eq!(split_typed_path("Z:\\h"), Some(("Z:\\", "h")));
        assert_eq!(parent_directory("Z:\\"), PathBuf::from("Z:\\"));
        assert_eq!(parent_directory("Z:\\Games\\"), PathBuf::from("Z:\\Games"));
    }

    #[test]
    fn a_symlinked_directory_is_offered_because_the_target_is_stat_ed() {
        // The `Z:\` root under Wine is full of these. A dirent-`file_type` listing reports them as
        // non-directory reparse points and drops them, which is how `Z:\h` came back with nothing
        // while `home/` was visible in the browser one row below.
        let root = crate::picker_scratch_dir("autocomplete-symlink");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("real/hangar")).expect("create the real directory");
        #[cfg(unix)]
        std::os::unix::fs::symlink(root.join("real/hangar"), root.join("harbour"))
            .expect("create the symlinked directory");
        std::fs::create_dir_all(root.join(".hidden")).expect("create a hidden directory");

        let names = directory_child_names(&root);
        assert!(names.contains(&"real".to_owned()), "{names:?}");
        #[cfg(unix)]
        assert!(
            names.contains(&"harbour".to_owned()),
            "a symlinked directory must be offered: {names:?}"
        );
        assert!(
            !names.iter().any(|name| name.starts_with('.')),
            "hidden entries are not offered: {names:?}"
        );

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_real_directory_is_completed_from_the_filesystem() {
        let root = crate::picker_scratch_dir("autocomplete-listing");
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("Harvest")).expect("create the match");
        std::fs::create_dir_all(root.join("hangar")).expect("create the earlier match");
        std::fs::create_dir_all(root.join("Zebra")).expect("create a non-match");
        std::fs::write(root.join("hfile.txt"), b"x").expect("create a file that must be skipped");

        let typed = format!("{}/h", root.display());
        let offer = suggestion_for(&typed).expect("a completion");
        assert_eq!(offer, format!("{}/hangar", root.display()));

        let _ = std::fs::remove_dir_all(&root);
    }
}
