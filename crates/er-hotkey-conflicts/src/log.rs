//! Fresh-per-process log file for this DLL, beside the game executable.

// Windows-only in practice; ungated so the host-testable modules it serves still compile on Linux.
#![cfg_attr(not(windows), allow(dead_code))]

use std::{
    io::Write,
    path::PathBuf,
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

const LOG_FILE_NAME: &str = "er-hotkey-conflicts.log";
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

/// Seconds since attach, for the report's own "observed for N" line.
pub(crate) fn elapsed_seconds() -> u64 {
    const MS_PER_SECOND: u128 = 1000;
    (elapsed_ms() / MS_PER_SECOND) as u64
}

fn log_path() -> PathBuf {
    PathBuf::from(LOG_FILE_NAME)
}

/// Anchor the `[+Nms]` stamps to DLL attach and rotate the previous run's log aside.
pub(crate) fn reset_log_file() {
    let _ = START_MS.set(now_ms());
    er_game_base::log::begin_fresh_run(&log_path());
}

/// Append one line. Never called from an input detour: this DLL's hot path writes to memory only,
/// because a file append inside `GetAsyncKeyState` would cost the game a disk write per key per
/// frame -- and a diagnostic tool that makes the thing it observes stutter is not passive.
pub(crate) fn conflict_log(args: std::fmt::Arguments<'_>) {
    let line = format!("[+{}ms] {args}\n", elapsed_ms());
    if let Some(mut file) = er_game_base::log::open_fresh_run_append(&log_path()) {
        let _ = file.write_all(line.as_bytes());
    }
}
