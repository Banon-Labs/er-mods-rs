//! Where the games are, asked of Steam.
//!
//! The Steam half is `tools/er-installer/src/steam.rs`, compiled into this crate by path rather
//! than copied: the installer's reasons for reading the registry, every `libraryfolders.vdf`
//! and the app manifests -- rather than guessing drive letters -- apply unchanged to a second
//! game. A Dark Souls Remastered install is accepted when it holds `DarkSoulsRemastered.exe` and
//! a `chr` folder -- whether the chosen character's binder is in it is a separate question with
//! its own error -- a Dark Souls II one when its `Game` folder holds `DarkSoulsII.exe` and
//! `GameDataEbl.bhd`, and an Elden Ring one only when it holds `eldenring.exe` and its archives.

use std::path::{Path, PathBuf};

use crate::steam;

/// Dark Souls Remastered on Steam.
pub const DSR_APP_ID: &str = "570940";
const DSR_DEFAULT_DIR: &str = "DARK SOULS REMASTERED";
const DSR_EXE: &str = "DarkSoulsRemastered.exe";
const DSR_CHR_DIR: &str = "chr";
const ER_PROBES: [&str; 2] = ["eldenring.exe", "Data0.bhd"];
/// Dark Souls II: Scholar of the First Sin on Steam.
pub const DS2_APP_ID: &str = "335300";
const DS2_DEFAULT_DIR: &str = "Dark Souls II Scholar of the First Sin";
const DS2_GAME_DIR: &str = "Game";
const DS2_PROBES: [&str; 2] = ["DarkSoulsII.exe", "GameDataEbl.bhd"];

pub fn is_dsr_dir(dir: &Path) -> bool {
    dir.join(DSR_EXE).is_file() && dir.join(DSR_CHR_DIR).is_dir()
}

/// `chr/<id>.chrbnd.dcx` under a DSR install.
pub fn chrbnd_path(dsr_dir: &Path, id: &str) -> PathBuf {
    dsr_dir.join(DSR_CHR_DIR).join(format!("{id}.chrbnd.dcx"))
}

/// `chr/<id>.chrtpfbdt`: the texture data a character with split textures keeps beside its
/// binder, indexed by the `<id>.chrtpfbhd` inside it.
pub fn chrtpfbdt_path(dsr_dir: &Path, id: &str) -> PathBuf {
    dsr_dir.join(DSR_CHR_DIR).join(format!("{id}.chrtpfbdt"))
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

/// Dark Souls II: Scholar of the First Sin's `Game` folder: `DarkSoulsII.exe` and the archive
/// every install has.
pub fn is_ds2_game_dir(dir: &Path) -> bool {
    DS2_PROBES.iter().all(|probe| dir.join(probe).is_file())
}

/// The Dark Souls II `Game` folder Steam knows about.
pub fn find_ds2() -> Option<PathBuf> {
    steam::libraries()
        .into_iter()
        .flat_map(|library| {
            let common = library.join("steamapps").join("common");
            let named = steam::app_install_dir(&library, DS2_APP_ID).map(|dir| common.join(dir));
            [named, Some(common.join(DS2_DEFAULT_DIR))]
        })
        .flatten()
        .map(|install| install.join(DS2_GAME_DIR))
        .find(|dir| is_ds2_game_dir(dir))
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
    fn dark_souls_remastered_is_its_exe_and_a_chr_folder() {
        let dir = std::env::temp_dir().join(format!("mmb-not-dsr-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(DSR_EXE), b"x").unwrap();
        assert!(!is_dsr_dir(&dir));
        std::fs::create_dir_all(dir.join(DSR_CHR_DIR)).unwrap();
        assert!(is_dsr_dir(&dir));
        assert_eq!(
            chrbnd_path(&dir, "c2650"),
            dir.join("chr").join("c2650.chrbnd.dcx")
        );
        std::fs::remove_file(dir.join(DSR_EXE)).unwrap();
        assert!(!is_dsr_dir(&dir));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
