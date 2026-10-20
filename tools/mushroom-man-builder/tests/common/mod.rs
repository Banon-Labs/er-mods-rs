//! Where the corpus-gated tests find the two games' files, and how they fingerprint what they
//! build from them.
//!
//! No game bytes are versioned here. A test that needs Elden Ring's donor body or a Dark Souls
//! Remastered character reads them from the local machine and skips, out loud, when they are
//! absent; it commits only lengths and sha256 fingerprints. Every root is overridable:
//!
//! - `MMB_ER_CORPUS_ROOT`: an unpacked Elden Ring tree, default
//!   `$HOME/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713`.
//! - `MMB_DSR_DIR`: the Dark Souls Remastered install, default
//!   `$HOME/.local/share/Steam/steamapps/common/DARK SOULS REMASTERED`.
//! - `MMB_DS2_DIR`: Dark Souls II's `Game` folder, default
//!   `$HOME/.local/share/Steam/steamapps/common/Dark Souls II Scholar of the First Sin/Game`.

// PARITY: `mod common;` is compiled into every test binary, and not every binary uses every
// helper, so a helper is dead in whichever binaries skip it.
#![allow(dead_code)]

use std::{
    fs,
    path::{Path, PathBuf},
};

use sha2::{Digest, Sha256};

/// The installed 1.17.1 `FC_M_0000.flver`, and `FC_M_0000_L.flver`, which is the same file.
pub const DONOR_LEN: usize = 1_826_832;
pub const DONOR_SHA256: &str = "f32ee05469748ba5001836e83f24bb284efc373b8df46f777be776ccd86a4aa9";

fn home() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

fn env_dir(name: &str, default_under_home: &str) -> Option<PathBuf> {
    match std::env::var(name) {
        Ok(value) if !value.trim().is_empty() => Some(PathBuf::from(value)),
        _ => home().map(|home| home.join(default_under_home)),
    }
}

pub fn er_corpus_root() -> Option<PathBuf> {
    env_dir(
        "MMB_ER_CORPUS_ROOT",
        "er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713",
    )
}

pub fn dsr_dir() -> Option<PathBuf> {
    env_dir(
        "MMB_DSR_DIR",
        ".local/share/Steam/steamapps/common/DARK SOULS REMASTERED",
    )
}

/// `<binder dir>/<file>` under the corpus's `parts` folder. The sharded extraction puts some
/// binders directly under `sharded/parts` and the rest one `_chunk_NNNN` level down, so both
/// are looked at, and an unsharded `parts` beside them.
fn find_part_file(root: &Path, binder_dir: &str, file: &str) -> Option<PathBuf> {
    let mut parents = vec![root.join("sharded/parts"), root.join("parts")];
    if let Ok(entries) = fs::read_dir(root.join("sharded/parts")) {
        let mut chunks: Vec<PathBuf> = entries
            .filter_map(|entry| entry.ok().map(|entry| entry.path()))
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("_chunk_"))
            })
            .collect();
        chunks.sort();
        parents.extend(chunks);
    }
    parents
        .into_iter()
        .map(|parent| parent.join(binder_dir).join(file))
        .find(|path| path.is_file())
}

/// The Elden Ring donor body, checked against its fingerprint, or `None` (and a skip line) when
/// the corpus is absent. A donor with a different fingerprint fails the test: every golden
/// number here was measured against this one.
pub fn donor_or_skip(test: &str) -> Option<Vec<u8>> {
    let Some(root) = er_corpus_root() else {
        eprintln!("SKIP {test}: no HOME and no MMB_ER_CORPUS_ROOT");
        return None;
    };
    let Some(path) = find_part_file(&root, "fc_m_0000-partsbnd-dcx", "FC_M_0000.flver") else {
        eprintln!(
            "SKIP {test}: no fc_m_0000-partsbnd-dcx/FC_M_0000.flver under {} (set MMB_ER_CORPUS_ROOT)",
            root.display()
        );
        return None;
    };
    let donor = fs::read(&path).expect("read the donor FLVER");
    assert_eq!(donor.len(), DONOR_LEN, "{} drifted", path.display());
    assert_eq!(
        sha256_hex(&donor),
        DONOR_SHA256,
        "{} drifted",
        path.display()
    );
    Some(donor)
}

/// `<binder dir>/<file>` out of the corpus's parts, or `None` (and a skip line) when it is absent.
/// Unlike the donor it is not fingerprinted here; the test that reads it says what it holds it to.
pub fn part_or_skip(test: &str, binder_dir: &str, file: &str) -> Option<Vec<u8>> {
    let Some(root) = er_corpus_root() else {
        eprintln!("SKIP {test}: no HOME and no MMB_ER_CORPUS_ROOT");
        return None;
    };
    let Some(path) = find_part_file(&root, binder_dir, file) else {
        eprintln!(
            "SKIP {test}: no {binder_dir}/{file} under {} (set MMB_ER_CORPUS_ROOT)",
            root.display()
        );
        return None;
    };
    Some(fs::read(&path).expect("read a corpus part file"))
}

/// The Dark Souls Remastered install when it holds character `id` (`chr/<id>.chrbnd.dcx`), or
/// `None` (and a skip line).
pub fn dsr_with_or_skip(test: &str, id: &str) -> Option<PathBuf> {
    let Some(dsr) = dsr_dir() else {
        eprintln!("SKIP {test}: no HOME and no MMB_DSR_DIR");
        return None;
    };
    if !dsr.join("chr").join(format!("{id}.chrbnd.dcx")).is_file() {
        eprintln!(
            "SKIP {test}: no chr/{id}.chrbnd.dcx under {} (set MMB_DSR_DIR)",
            dsr.display()
        );
        return None;
    }
    Some(dsr)
}

/// Dark Souls II's `Game` folder: `MMB_DS2_DIR`, default
/// `$HOME/.local/share/Steam/steamapps/common/Dark Souls II Scholar of the First Sin/Game`.
pub fn ds2_dir() -> Option<PathBuf> {
    env_dir(
        "MMB_DS2_DIR",
        ".local/share/Steam/steamapps/common/Dark Souls II Scholar of the First Sin/Game",
    )
}

/// Dark Souls II's archives, opened, or `None` (and a skip line) when the install is absent.
pub fn ds2_archives_or_skip(test: &str) -> Option<mushroom_man_builder::archive::ArchiveSet> {
    let Some(dir) = ds2_dir() else {
        eprintln!("SKIP {test}: no HOME and no MMB_DS2_DIR");
        return None;
    };
    if !dir.join("GameDataEbl.bhd").is_file() {
        eprintln!(
            "SKIP {test}: no GameDataEbl.bhd under {} (set MMB_DS2_DIR)",
            dir.display()
        );
        return None;
    }
    Some(
        mushroom_man_builder::archive::ArchiveSet::open_ds2(&dir)
            .expect("open Dark Souls II's archives"),
    )
}

/// Dark Souls III's `Game` folder: `MMB_DS3_DIR`, default
/// `$HOME/.local/share/Steam/steamapps/common/DARK SOULS III/Game`.
pub fn ds3_dir() -> Option<PathBuf> {
    env_dir(
        "MMB_DS3_DIR",
        ".local/share/Steam/steamapps/common/DARK SOULS III/Game",
    )
}

/// Dark Souls III's archives, opened, or `None` (and a skip line) when the install is absent.
pub fn ds3_archives_or_skip(test: &str) -> Option<mushroom_man_builder::archive::ArchiveSet> {
    let Some(dir) = ds3_dir() else {
        eprintln!("SKIP {test}: no HOME and no MMB_DS3_DIR");
        return None;
    };
    if !dir.join("Data1.bhd").is_file() {
        eprintln!(
            "SKIP {test}: no Data1.bhd under {} (set MMB_DS3_DIR)",
            dir.display()
        );
        return None;
    }
    Some(
        mushroom_man_builder::archive::ArchiveSet::open_ds3(&dir)
            .expect("open Dark Souls III's archives"),
    )
}

/// c2280's FLVER and TPF out of the Dark Souls Remastered install, or `None` (and a skip line).
pub fn c2280_or_skip(test: &str) -> Option<(Vec<u8>, Vec<u8>)> {
    let dsr = dsr_with_or_skip(test, "c2280")?;
    Some(
        mushroom_man_builder::package::read_c2280(&dsr).expect("read c2280 out of the DSR install"),
    )
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// `(length, sha256)` of `bytes`, the form every fingerprint here is recorded in.
pub fn fingerprint(bytes: &[u8]) -> (usize, String) {
    (bytes.len(), sha256_hex(bytes))
}
