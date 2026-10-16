//! The rule-break file beside the game: every break, one dated line each, kept across runs.
//!
//! Unlike `er-duel-rules.log`, which describes one run, this file is the record a player reads
//! back after a session, so it is appended and never rotated; each line carries its own date
//! (`er_duel_rules_core::feed::Entry::file_line`). `scripts/check-fresh-run-logs.py` exempts this
//! file, and only this file, for that reason.

// Windows-only in practice; ungated so the crate still compiles on Linux.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{io::Write, path::PathBuf};

use crate::log::rules_log;

const BREAKS_FILE_NAME: &str = "er-duel-rules-breaks.txt";

/// Append one line to the rule-break file beside the game.
pub(crate) fn record_break(line: &str) {
    let path = er_game_base::log::game_directory_path()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(BREAKS_FILE_NAME);
    match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        Ok(mut file) => {
            let _ = writeln!(file, "{line}");
        }
        Err(error) => rules_log(format_args!(
            "breaks: cannot append to {}: {error}",
            path.display()
        )),
    }
}
