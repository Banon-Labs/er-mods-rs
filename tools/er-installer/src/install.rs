//! Finding the game, finding the DLLs, and putting the chosen ones where a profile can name them.
//!
//! Every path this module produces is absolute, because an ME3 profile's `[[natives]]` entries
//! are resolved by ME3 rather than relative to the profile, and a relative path here becomes a
//! mod that silently does not load.
//!
//! Nothing is guessed silently. Each discovery step reports which candidate it took, so a user
//! whose game is somewhere unusual sees that the tool looked in the wrong place instead of
//! finding out after a launch.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::catalog::Mod;

/// The DLLs baked into this executable, empty in a build that was not given any.
///
/// See `build.rs`: a release build embeds all of them so the download is one file, and a
/// development build embeds none so it compiles in under a second.
mod payload {
    include!(concat!(env!("OUT_DIR"), "/embedded.rs"));
}

/// The bytes of one mod's DLL, if this build carries it.
pub fn embedded(artifact: &str) -> Option<&'static [u8]> {
    payload::EMBEDDED
        .iter()
        .find(|(name, _)| *name == artifact)
        .map(|(_, bytes)| *bytes)
}

/// How many mods this executable can install without any other file.
pub fn embedded_count() -> usize {
    payload::EMBEDDED.len()
}

/// Which of `chosen` this build cannot supply from itself.
pub fn not_embedded(chosen: &[&'static Mod]) -> Vec<&'static Mod> {
    chosen
        .iter()
        .filter(|entry| embedded(entry.artifact).is_none())
        .copied()
        .collect()
}

/// What the game install looks like once it has been found.
#[derive(Debug)]
pub struct GameInstall {
    /// The `Game` directory -- the one holding `eldenring.exe`.
    pub game_dir: PathBuf,
    /// The game-installed Seamless Co-op DLL, when it is there. Referenced, never copied:
    /// this repo does not bundle, stage or redistribute that file.
    pub seamless: Option<PathBuf>,
}

/// The file whose presence proves a directory is the game directory. The protected launcher
/// `start_protected_game.exe` sits beside it and is deliberately not what is looked for.
const GAME_EXE: &str = "eldenring.exe";

const SEAMLESS_RELATIVE: &str = "SeamlessCoop/ersc.dll";

fn is_game_dir(candidate: &Path) -> bool {
    candidate.join(GAME_EXE).is_file()
}

/// Candidate game directories, most-specific first. `explicit` comes from `--game-dir`.
fn game_dir_candidates(explicit: Option<&Path>) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = explicit {
        candidates.push(path.to_path_buf());
        // A user pointing at the install root rather than the `Game` subdirectory is the most
        // common way to get this wrong, and it is unambiguous to correct.
        candidates.push(path.join("Game"));
    }
    if let Some(dir) = std::env::var_os("ME3_STEAM_DIR") {
        let dir = PathBuf::from(dir);
        candidates.push(dir.join("Game"));
        candidates.push(dir);
    }
    for root in steam_library_roots() {
        candidates.push(root.join("steamapps/common/ELDEN RING/Game"));
    }
    candidates
}

fn steam_library_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(home) = std::env::var_os("HOME") {
        let home = PathBuf::from(home);
        roots.push(home.join(".local/share/Steam"));
        roots.push(home.join(".steam/steam"));
    }
    for drive in ["C:", "D:", "E:"] {
        roots.push(PathBuf::from(format!(
            "{drive}\\Program Files (x86)\\Steam"
        )));
        roots.push(PathBuf::from(format!("{drive}\\SteamLibrary")));
    }
    roots
}

/// Find the game. Returns the candidates that were tried when none of them held the exe, so
/// the caller can say where it looked rather than only that it failed.
pub fn find_game(explicit: Option<&Path>) -> Result<GameInstall, Vec<PathBuf>> {
    let candidates = game_dir_candidates(explicit);
    for candidate in &candidates {
        if !is_game_dir(candidate) {
            continue;
        }
        let game_dir = candidate
            .canonicalize()
            .unwrap_or_else(|_| candidate.clone());
        let seamless = {
            let path = game_dir.join(SEAMLESS_RELATIVE);
            path.is_file().then_some(path)
        };
        return Ok(GameInstall { game_dir, seamless });
    }
    Err(candidates)
}

/// Does this directory hold any of the DLLs this installer knows how to install?
///
/// Being a directory is not enough, and getting that wrong is not theoretical: run the Linux
/// build straight out of `target/release` and the exe's own directory is the first candidate
/// that exists, so discovery settled there and then reported all nine chosen mods missing --
/// naming the wrong directory in the error, which is the shape of bug that sends someone
/// looking for files that were never supposed to be there.
fn holds_mod_dlls(dir: &Path) -> bool {
    dir.is_dir()
        && crate::catalog::CATALOG
            .iter()
            .any(|entry| dir.join(entry.artifact).is_file())
}

/// Where the DLLs to install are read from: alongside the installer in a release download, or
/// out of a build tree when this is run from the repo.
///
/// An explicit `--dll-dir` wins unconditionally, even when it holds nothing, so the error names
/// the directory the user chose rather than quietly searching somewhere else.
pub fn find_dll_source(explicit: Option<&Path>) -> Option<PathBuf> {
    if let Some(path) = explicit {
        return Some(path.to_path_buf());
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        // The release zip layout: the installer at the top, the DLLs in a folder beside it.
        candidates.push(dir.join("dlls"));
        candidates.push(dir.to_path_buf());
        // Running the Linux build out of `target/release`, where the cross-compiled DLLs are
        // one directory over.
        if let Some(target) = dir.parent() {
            candidates.push(target.join("x86_64-pc-windows-msvc/release"));
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("target/x86_64-pc-windows-msvc/release"));
        candidates.push(cwd.join("dlls"));
    }
    candidates.into_iter().find(|dir| holds_mod_dlls(dir))
}

/// A mod that was selected but whose DLL is not in the source directory.
pub struct MissingArtifact {
    pub label: &'static str,
    pub artifact: &'static str,
}

/// Check every selected mod's DLL exists before copying any of them, so a missing file is a
/// refusal with nothing half-installed rather than a profile naming a path that is not there.
pub fn missing_artifacts(chosen: &[&'static Mod], source: &Path) -> Vec<MissingArtifact> {
    chosen
        .iter()
        .filter(|entry| !source.join(entry.artifact).is_file())
        .map(|entry| MissingArtifact {
            label: entry.label,
            artifact: entry.artifact,
        })
        .collect()
}

/// Write each chosen DLL into `dest`, returning the absolute path each one now lives at, in
/// the order given. Existing files are overwritten: reinstalling is how a user updates.
///
/// A mod baked into this executable is written from there; `source` supplies anything that is
/// not, and is `None` in a fully self-contained build. Preferring the embedded copy means a
/// player who happens to have an unrelated `dlls` folder beside the installer still gets the
/// versions this build was released with.
pub fn install_artifacts(
    chosen: &[&'static Mod],
    source: Option<&Path>,
    dest: &Path,
) -> io::Result<Vec<(&'static Mod, String)>> {
    fs::create_dir_all(dest)?;
    let dest = dest.canonicalize().unwrap_or_else(|_| dest.to_path_buf());
    let mut installed = Vec::with_capacity(chosen.len());
    for entry in chosen {
        let to = dest.join(entry.artifact);
        let context = |err: io::Error, from: &str| {
            io::Error::new(
                err.kind(),
                format!("writing {} from {from}: {err}", to.display()),
            )
        };
        match embedded(entry.artifact) {
            Some(bytes) => fs::write(&to, bytes).map_err(|err| context(err, "this installer"))?,
            None => {
                let source = source.ok_or_else(|| {
                    io::Error::other(format!(
                        "{} is not built into this installer and no folder of mod files was \
                         found. Pass --dll-dir <path>.",
                        entry.artifact
                    ))
                })?;
                let from = source.join(entry.artifact);
                fs::copy(&from, &to).map_err(|err| context(err, &from.display().to_string()))?;
            }
        }
        installed.push((*entry, display_path(&to)));
    }
    Ok(installed)
}

/// Render a path for an ME3 profile. Windows verbatim prefixes (`\\?\`) come back from
/// `canonicalize` and ME3 does not want them, so they are stripped here rather than written
/// into a profile a user may later read or edit.
pub fn display_path(path: &Path) -> String {
    let rendered = path.display().to_string();
    rendered
        .strip_prefix(r"\\?\")
        .map_or(rendered.clone(), str::to_string)
}

/// Write the profile, creating its directory. Returns the absolute path written.
pub fn write_profile(path: &Path, contents: &str) -> io::Result<PathBuf> {
    if let Some(parent) = path.parent()
        && !parent.as_os_str().is_empty()
    {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, contents)?;
    Ok(path.canonicalize().unwrap_or_else(|_| path.to_path_buf()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = env::temp_dir().join(format!("er-installer-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn a_directory_holding_the_game_exe_is_the_game_directory() {
        let dir = temp_dir("gamedir");
        assert!(!is_game_dir(&dir));
        fs::write(dir.join(GAME_EXE), b"stub").unwrap();
        assert!(is_game_dir(&dir));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn pointing_at_the_install_root_still_finds_the_game_subdirectory() {
        let root = temp_dir("installroot");
        let game = root.join("Game");
        fs::create_dir_all(&game).unwrap();
        fs::write(game.join(GAME_EXE), b"stub").unwrap();

        let found = find_game(Some(&root)).expect("the Game subdirectory should be found");
        assert!(found.game_dir.ends_with("Game"));
        assert!(found.seamless.is_none());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn seamless_is_detected_where_the_game_installed_it() {
        let game = temp_dir("seamless");
        fs::write(game.join(GAME_EXE), b"stub").unwrap();
        fs::create_dir_all(game.join("SeamlessCoop")).unwrap();
        fs::write(game.join(SEAMLESS_RELATIVE), b"stub").unwrap();

        let found = find_game(Some(&game)).unwrap();
        assert!(found.seamless.is_some(), "installed ersc.dll was not found");
        fs::remove_dir_all(&game).unwrap();
    }

    /// Asserted against the candidate list rather than against `find_game`, because on a
    /// machine that actually has the game the fallbacks find it -- which is the behaviour
    /// wanted, and makes a "nothing was found" assertion untestable here.
    #[test]
    fn the_candidate_list_looks_past_the_directory_it_was_given() {
        let explicit = PathBuf::from("/nowhere/in/particular");
        let candidates = game_dir_candidates(Some(&explicit));
        assert!(
            candidates.contains(&explicit),
            "the explicit path is tried first"
        );
        assert!(
            candidates.contains(&explicit.join("Game")),
            "pointing at an install root should also try its Game subdirectory"
        );
        assert!(
            candidates.len() > 2,
            "no fallback locations were offered: {candidates:?}"
        );
    }

    #[test]
    fn a_directory_with_no_game_in_it_is_not_taken_as_the_game() {
        let empty = temp_dir("nogame");
        assert!(!is_game_dir(&empty));
        assert!(!is_game_dir(&empty.join("Game")));
        fs::remove_dir_all(&empty).unwrap();
    }

    #[test]
    fn an_empty_directory_is_not_taken_as_the_dll_source() {
        let empty = temp_dir("emptysource");
        assert!(
            !holds_mod_dlls(&empty),
            "an empty directory should be skipped"
        );
        let product = crate::selection::by_package("er-quickload").unwrap();
        fs::write(empty.join(product.artifact), b"stub").unwrap();
        assert!(
            holds_mod_dlls(&empty),
            "a directory with a mod DLL should be taken"
        );
        fs::remove_dir_all(&empty).unwrap();
    }

    #[test]
    fn an_explicit_dll_dir_wins_even_when_it_holds_nothing() {
        let empty = temp_dir("explicitsource");
        let found = find_dll_source(Some(&empty)).expect("an explicit directory is always used");
        assert_eq!(
            found, empty,
            "the error must name the directory the user chose"
        );
        fs::remove_dir_all(&empty).unwrap();
    }

    #[test]
    fn a_missing_dll_is_named_before_anything_is_copied() {
        let source = temp_dir("artifacts");
        let product = crate::selection::by_package("er-quickload").unwrap();
        let icons = crate::selection::by_package("er-armament-icons").unwrap();
        fs::write(source.join(product.artifact), b"stub").unwrap();

        let missing = missing_artifacts(&[product, icons], &source);
        assert_eq!(missing.len(), 1);
        assert_eq!(missing[0].artifact, icons.artifact);
        fs::remove_dir_all(&source).unwrap();
    }

    #[test]
    fn copying_puts_each_dll_at_an_absolute_path_the_profile_can_name() {
        let source = temp_dir("copy-src");
        let dest = temp_dir("copy-dst");
        let product = crate::selection::by_package("er-quickload").unwrap();
        fs::write(source.join(product.artifact), b"stub").unwrap();

        let installed = install_artifacts(&[product], Some(&source), &dest).unwrap();
        assert_eq!(installed.len(), 1);
        let (_, path) = &installed[0];
        assert!(Path::new(path).is_absolute(), "{path} is not absolute");
        assert!(path.ends_with(product.artifact));
        assert!(
            !path.starts_with(r"\\?\"),
            "verbatim prefix leaked into {path}"
        );

        fs::remove_dir_all(&source).unwrap();
        fs::remove_dir_all(&dest).unwrap();
    }

    #[test]
    fn reinstalling_over_an_existing_file_succeeds() {
        let source = temp_dir("recopy-src");
        let dest = temp_dir("recopy-dst");
        let product = crate::selection::by_package("er-quickload").unwrap();
        fs::write(source.join(product.artifact), b"first").unwrap();
        install_artifacts(&[product], Some(&source), &dest).unwrap();
        let first = fs::read(dest.join(product.artifact)).unwrap();
        fs::write(source.join(product.artifact), b"second").unwrap();
        install_artifacts(&[product], Some(&source), &dest).unwrap();
        let second = fs::read(dest.join(product.artifact)).unwrap();

        if embedded(product.artifact).is_some() {
            // A build carrying its own payload writes that, and the directory is ignored --
            // which is the point: a stray `dlls` folder must not override a release.
            assert_eq!(first, second);
            assert_eq!(second, embedded(product.artifact).unwrap());
        } else {
            assert_eq!(second, b"second");
        }

        fs::remove_dir_all(&source).unwrap();
        fs::remove_dir_all(&dest).unwrap();
    }

    #[test]
    fn a_build_with_a_payload_needs_no_directory_at_all() {
        let dest = temp_dir("embedded-dst");
        let product = crate::selection::by_package("er-quickload").unwrap();

        match embedded(product.artifact) {
            Some(bytes) => {
                let installed = install_artifacts(&[product], None, &dest).unwrap();
                assert_eq!(installed.len(), 1);
                assert_eq!(fs::read(dest.join(product.artifact)).unwrap(), bytes);
            }
            None => {
                // A development build carries nothing, and must say so rather than write a
                // truncated or empty DLL into someone's game directory.
                let refusal = install_artifacts(&[product], None, &dest).unwrap_err();
                assert!(
                    refusal.to_string().contains("--dll-dir"),
                    "refusal should say how to fix it: {refusal}"
                );
            }
        }
        fs::remove_dir_all(&dest).unwrap();
    }

    #[test]
    fn the_payload_is_all_or_nothing_never_a_partial_set() {
        // A build that carries some mods but not others would offer all of them and install
        // only some, which is the failure `--selfcheck` exists to make impossible to ship.
        let count = embedded_count();
        assert!(
            count == 0 || count == crate::catalog::CATALOG.len(),
            "this build carries {count} of {} mods",
            crate::catalog::CATALOG.len()
        );
        let all: Vec<&'static Mod> = crate::catalog::CATALOG.iter().collect();
        assert_eq!(
            not_embedded(&all).len(),
            crate::catalog::CATALOG.len() - count
        );
    }

    #[test]
    fn writing_a_profile_creates_the_directory_it_needs() {
        let root = temp_dir("profile");
        let path = root.join("nested/er-mods.me3");
        let written = write_profile(&path, "profileVersion = \"v1\"\n").unwrap();
        assert!(written.is_file());
        fs::remove_dir_all(&root).unwrap();
    }
}
