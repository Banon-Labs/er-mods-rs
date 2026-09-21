//! The hand-marked SpEffect list -- a research instrument, not a product gate.
//!
//! # What this is for
//!
//! Some effects should never be something this DLL puts on another player in a Seamless session:
//! an effect that grants HP on a timer, one that hands out stat points, one that changes rune
//! gain. Which ones those are is not yet a rule anyone can write, because nobody has looked at
//! the set. This file is how the set gets collected: the selector's mark key toggles whichever
//! effect the cursor is on into it, so a pass through a catalog produces a list instead of a
//! memory.
//!
//! The list is the input to the rule, not the rule. Once it is big enough, the fields of the
//! marked rows get compared against the unmarked ones (`scripts/er-net-effects-mark-patterns.py`)
//! and what separates them becomes a predicate over
//! `er-net-effect-master-catalog.json` -- the shape `duration_filter` already uses for
//! `effectEndurance == -1`. Nothing in the DLL reads this file back as a restriction, and nothing
//! should: a hand-built list of raw ids cannot cover a catalog added after it was built.
//!
//! # Why it is written as a catalog
//!
//! The file is a `.jsonc` id array, byte-identical in shape to the files in
//! `er-net-effect-catalogs`. Copy it in there and the marked set becomes a catalog of its own,
//! so the review pass -- scrolling only what was marked, to throw out the mistakes -- uses the
//! same selector that made the marks. It is written to the game directory rather than into the
//! catalog directory on purpose: that directory is watched and rebuilt on change, and rebuilding
//! the catalog list under a cursor that is mid-scroll moves the thing being marked.

// Windows-only in practice; portable so the file format is asserted by `cargo test` on the host.
#![cfg_attr(not(windows), allow(dead_code))]

/// The comment block at the top of the written file.
///
/// Written every time, so a file a player opens explains itself without them having to find this
/// source. The trailing newline is part of it -- the ids follow directly.
pub(crate) const FILE_HEADER: &str = "\
// er-net-effects: SpEffect ids marked by hand while scrolling the selector.
//
// Written by the mark key (selector_mark_key in er-net-effects.toml). Pressing it on an effect
// that is already here takes it back out, so a mis-press costs one more press.
//
// This is a research list, not a setting. The DLL never reads it back and nothing is blocked by
// being in it. It exists to be looked at: what the marked rows have in common becomes a rule
// over the master catalog's fields, and the rule is what ships.
//
// The format is a catalog. Copy this file into er-net-effect-catalogs/ to scroll only what you
// marked.
";

/// Read the ids out of a marked file.
///
/// Tolerant in the same way the catalog reader is, and for the same reason -- this is a file a
/// player may hand-edit. Comments, brackets, commas and blank lines are all noise; anything that
/// parses as an integer is an id, and a line that does not is skipped rather than voiding the
/// rest. Order is kept, because the order marks were made in is evidence about the pass that
/// made them, and duplicates are dropped.
pub(crate) fn parse(text: &str) -> Vec<i32> {
    let mut ids = Vec::new();
    for line in text.lines() {
        let code = line.split("//").next().unwrap_or_default();
        for token in code.split([',', '[', ']', ' ', '\t']) {
            let token = token.trim();
            if token.is_empty() {
                continue;
            }
            if let Ok(id) = token.parse::<i32>()
                && !ids.contains(&id)
            {
                ids.push(id);
            }
        }
    }
    ids
}

/// Render the marked set as the `.jsonc` catalog written to disk.
///
/// Each id carries its name as a trailing comment, because an id alone is unreadable and the
/// whole point of the file is that a human looks at it later. A name is only a comment, so a
/// name the parser would choke on cannot corrupt the list -- but a `//` or a newline inside one
/// would end the comment early and swallow the rest of the line, so both are flattened.
pub(crate) fn render<'a>(entries: impl IntoIterator<Item = (i32, &'a str)>) -> String {
    let mut out = String::from(FILE_HEADER);
    out.push_str("[\n");
    for (id, name) in entries {
        let name = comment_safe(name);
        if name.is_empty() {
            out.push_str(&format!("  {id},\n"));
        } else {
            out.push_str(&format!("  {id}, // {name}\n"));
        }
    }
    out.push_str("]\n");
    out
}

/// Flatten a name into something that cannot end its own comment or start a new line.
fn comment_safe(name: &str) -> String {
    name.replace(['\r', '\n'], " ").replace("//", "/ /")
}

/// Add the id, or take it back out if it is already there. Reports whether it is marked now.
pub(crate) fn toggle(ids: &mut Vec<i32>, id: i32) -> bool {
    if let Some(position) = ids.iter().position(|existing| *existing == id) {
        ids.remove(position);
        false
    } else {
        ids.push(id);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// What the DLL writes, the DLL must read back unchanged -- the file survives a relaunch only
    /// if this holds.
    #[test]
    fn a_written_file_reads_back_as_the_same_ids() {
        let entries = [
            (475, "Blessing of the Erdtree - HP Regen"),
            (2, ""),
            (-1, "x"),
        ];
        let text = render(entries);
        assert_eq!(parse(&text), vec![475, 2, -1]);
    }

    /// The rendered file must also be a catalog, or the review pass cannot load it.
    #[test]
    fn the_rendered_file_is_a_catalog_the_selector_can_load() {
        let text = render([(475, "regen"), (829, "frost")]);
        assert!(text.contains("[\n"), "{text}");
        assert!(text.contains("  475, // regen\n"), "{text}");
        assert!(text.trim_end().ends_with(']'), "{text}");
    }

    /// A name is a comment and may say anything, including the two characters that start one.
    /// Letting a name end its own comment would put the rest of the line -- nothing today, but a
    /// field away from it -- outside the comment.
    #[test]
    fn a_name_cannot_end_its_own_comment() {
        let text = render([(7, "a // b\nc")]);
        assert_eq!(text.lines().filter(|line| line.contains('7')).count(), 1);
        assert_eq!(parse(&text), vec![7]);
    }

    /// Hand-editing is expected, so the reader takes the shapes a hand produces.
    #[test]
    fn a_hand_edited_file_still_reads() {
        let text = "// notes\n[\n 1, 2,\n\n 3 // three\n]\n";
        assert_eq!(parse(text), vec![1, 2, 3]);
    }

    /// An id inside a comment is a note about an effect, not a mark on it. Reading one back would
    /// resurrect an entry the player deliberately commented out.
    #[test]
    fn an_id_in_a_comment_is_not_a_mark() {
        assert_eq!(parse("[\n 1,\n // 999 -- ruled out\n]\n"), vec![1]);
    }

    #[test]
    fn marking_twice_unmarks() {
        let mut ids = vec![1, 2];
        assert!(toggle(&mut ids, 3));
        assert_eq!(ids, vec![1, 2, 3]);
        assert!(!toggle(&mut ids, 2));
        assert_eq!(ids, vec![1, 3]);
    }

    /// Order is the order the marks were made in -- a re-mark goes to the end rather than back to
    /// where it was, because it is a later decision than the entries before it.
    #[test]
    fn a_remark_lands_at_the_end() {
        let mut ids = vec![1, 2, 3];
        toggle(&mut ids, 1);
        toggle(&mut ids, 1);
        assert_eq!(ids, vec![2, 3, 1]);
    }
}
