//! Print the shipped default text of the settings files whose content is computed in Rust.
//!
//! Output is one block per file, framed so a reader cannot be confused by the file content:
//!
//! ```text
//! <<<er-quit-menu.toml>>>
//! # Which rows this DLL adds to the System > Quit tab ...
//! <<<end>>>
//! ```
//!
//! `scripts/gen-installer-settings.py` consumes it. A frame line is used rather than a byte
//! count because these texts are edited by hand in the crates that own them, and a length that
//! has to be kept in step with the text is a second thing to get wrong.
//!
//! # Why this is a separate binary and not part of the generator
//!
//! The generator runs in every `check.sh`, and a gate that has to compile three crates before
//! it can answer is a gate people stop running. This writes a tracked file
//! (`tools/er-installer/config-defaults.txt`); the generator reads that, and a test compares the
//! tracked file against this binary's live output, so the slow half runs when the text changes
//! rather than on every check.

use std::io::{self, Write};

/// One computed settings file: the name it has in the game directory, and the crate call that
/// produces its shipped text.
struct Computed {
    file: &'static str,
    text: fn() -> String,
}

/// `er-quickload.toml`'s save-picker block, under the name the generator substitutes it by.
///
/// Not a settings file of its own: it is the `{picker_block}` placeholder inside the template
/// that `er-quickload/src/config.rs` holds. That template is a private item in a
/// `#[cfg(windows)]` module, so the generator scrapes the template and fills this hole with what
/// is printed here -- the same call the DLL makes.
const PICKER_BLOCK_NAME: &str = "er-quickload.toml#picker_block";

fn blocks() -> Vec<Computed> {
    vec![
        Computed {
            file: "er-quit-menu.toml",
            text: er_quit_menu_core::row_config::boilerplate_config,
        },
        Computed {
            file: "er-invasion-warp.toml",
            text: || er_invasion_warp_core::local_invasion_config::DEFAULT_CONFIG_TOML.to_owned(),
        },
        Computed {
            file: PICKER_BLOCK_NAME,
            // `None`: the shape written when the file does not exist yet, which is the one a
            // fresh install offers. The `Some` arm writes a remembered picker folder back into an
            // existing file and is not a default.
            text: || er_save_picker_core::boilerplate_picker_block(None),
        },
    ]
}

fn main() -> io::Result<()> {
    let mut out = io::stdout().lock();
    for block in blocks() {
        writeln!(out, "<<<{}>>>", block.file)?;
        let text = (block.text)();
        out.write_all(text.as_bytes())?;
        if !text.ends_with('\n') {
            writeln!(out)?;
        }
        writeln!(out, "<<<end>>>")?;
    }
    out.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The frame lines are only unambiguous while no settings file contains one. Checked here
    /// rather than assumed, because the texts are prose and somebody will eventually write an
    /// example in one.
    #[test]
    fn no_computed_text_contains_a_frame_line() {
        for block in blocks() {
            let text = (block.text)();
            for line in text.lines() {
                assert!(
                    !(line.starts_with("<<<") && line.ends_with(">>>")),
                    "{}: the line {line:?} would be read as a frame by the generator",
                    block.file
                );
            }
        }
    }

    /// A block that came back empty would reach the installer as a settings file with no
    /// settings in it, which reads as the mod having none.
    #[test]
    fn every_computed_text_carries_at_least_one_assignment() {
        for block in blocks() {
            let text = (block.text)();
            let assignments = text
                .lines()
                .map(str::trim)
                .filter(|line| !line.starts_with('#'))
                .filter(|line| line.contains('='))
                .count();
            assert!(
                assignments > 0,
                "{}: no assignment in {} bytes of text",
                block.file,
                text.len()
            );
        }
    }
}
