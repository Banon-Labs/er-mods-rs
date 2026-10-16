//! Fresh-per-process log file for this DLL, beside the game executable.

// Windows-only in practice; ungated so the crate still compiles on Linux.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{
    io::Write,
    path::PathBuf,
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

const LOG_FILE_NAME: &str = "er-duel-rules.log";
/// Every rule break, one line each, kept across runs: the overlay lists only the newest.
const BREAKS_FILE_NAME: &str = "er-duel-rules-breaks.txt";
static START_MS: OnceLock<u128> = OnceLock::new();

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn elapsed_ms() -> u128 {
    let start = *START_MS.get_or_init(now_ms);
    now_ms().saturating_sub(start)
}

fn log_path() -> PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| PathBuf::from("."))
        .join(LOG_FILE_NAME)
}

/// Anchor the `[+Nms]` stamps to DLL attach and rotate the previous run's log aside.
pub(crate) fn reset_log_file() {
    let _ = START_MS.set(now_ms());
    er_game_base::log::begin_fresh_run(&log_path());
}

/// Append one line to the rule-break file beside the game. It is never rotated, so every session's
/// breaks stay in it, each line carrying its own date.
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

pub(crate) fn rules_log(args: std::fmt::Arguments<'_>) {
    let line = format!("[+{}ms] er-duel-rules: {args}\n", elapsed_ms());
    if let Some(mut file) = er_game_base::log::open_fresh_run_append(&log_path()) {
        let _ = file.write_all(line.as_bytes());
    }
}
