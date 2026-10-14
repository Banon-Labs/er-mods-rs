//! Where the two games are, asked of Steam.
//!
//! The Steam half is `tools/er-installer/src/steam.rs`, compiled into this crate by path rather
//! than copied: the installer's reasons for reading the registry, every `libraryfolders.vdf`
//! and the app manifests -- rather than guessing drive letters -- apply unchanged to a second
//! game. A Dark Souls Remastered install is accepted only when it holds c2280's binder, and an
//! Elden Ring one only when it holds `eldenring.exe` and its archives.

use std::path::{Path, PathBuf};

use crate::steam;

/// Dark Souls Remastered on Steam.
pub const DSR_APP_ID: &str = "570940";
const DSR_DEFAULT_DIR: &str = "DARK SOULS REMASTERED";
const DSR_PROBE: &str = "chr/c2280.chrbnd.dcx";
const ER_PROBES: [&str; 2] = ["eldenring.exe", "Data0.bhd"];

pub fn is_dsr_dir(dir: &Path) -> bool {
    dir.join(DSR_PROBE).is_file()
}

pub fn is_er_game_dir(dir: &Path) -> bool {
    ER_PROBES.iter().all(|probe| dir.join(probe).is_file())
}

/// Dark Souls Remastered folders Steam knows about, best evidence first.
pub fn dsr_candidates() -> Vec<PathBuf> {
    let mut found = Vec::new();
    for library in steam::libraries() {
        let common = library.join("steamapps").join("common");
        if let Some(install_dir) = steam::app_install_dir(&library, DSR_APP_ID) {
            found.push(common.join(install_dir));
        }
        let default = common.join(DSR_DEFAULT_DIR);
        if !found.contains(&default) {
            found.push(default);
        }
    }
    found
}

pub fn find_dsr() -> Option<PathBuf> {
    dsr_candidates().into_iter().find(|dir| is_dsr_dir(dir))
}

pub fn find_er() -> Option<PathBuf> {
    steam::game_dirs()
        .into_iter()
        .find(|dir| is_er_game_dir(dir))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_folder_without_c2280_is_not_dark_souls_remastered() {
        let dir = std::env::temp_dir().join(format!("mmb-not-dsr-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("chr")).unwrap();
        assert!(!is_dsr_dir(&dir));
        std::fs::write(dir.join(DSR_PROBE), b"x").unwrap();
        assert!(is_dsr_dir(&dir));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
