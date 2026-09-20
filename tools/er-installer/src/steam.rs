//! Where Steam put Elden Ring, asked of Steam rather than guessed.
//!
//! # Why guessing was not enough
//!
//! This used to be a list of six hard-coded roots -- `C:\Program Files (x86)\Steam`,
//! `C:\SteamLibrary`, and the same on `D:` and `E:`. A player who put their library on any other
//! drive, gave it any other name, or moved the game to a second library got "could not find
//! Elden Ring" and a list of six places it was never going to be. That is most players: Steam
//! offers a second library the first time a drive fills up, and names it after wherever it lands.
//!
//! # What Steam actually records
//!
//! Three files, each naming the next, none of them a guess:
//!
//! 1. The registry says where Steam itself is. `HKCU\Software\Valve\Steam\SteamPath` is written
//!    by the running client; `HKLM\SOFTWARE\WOW6432Node\Valve\Steam\InstallPath` by the
//!    installer. The first is a per-user value and wins.
//! 2. `<steam>/steamapps/libraryfolders.vdf` lists every library, including ones on other
//!    drives. Steam keeps a second copy at `<steam>/config/libraryfolders.vdf`; both are read
//!    because which one exists has varied across client versions.
//! 3. `<library>/steamapps/appmanifest_1245620.acf` is Elden Ring's own install record.
//!    `installdir` in it names the folder under `steamapps/common`, so the game is found by its
//!    app id rather than by matching the folder name `ELDEN RING`, which is a localised display
//!    name this tool has no business asserting.
//!
//! Every candidate is still confirmed by looking for `eldenring.exe` before it is accepted --
//! see [`crate::install::find_game`]. Steam's records say where the game was installed, not
//! whether it is still there.
//!
//! # No parser dependency
//!
//! [`vdf`] below is about forty lines because Valve's format is `"key" "value"` pairs and
//! braces, and that is all that has to be understood to read three known fields out of it. A
//! TOML or VDF crate would be a dependency in a binary whose product requirement is having none.

use std::path::{Path, PathBuf};

/// Elden Ring on Steam. The app id is the stable name for the game; the folder is not.
pub const APP_ID: &str = "1245620";

/// The subdirectory of the install that holds the executable.
const GAME_SUBDIR: &str = "Game";

/// What Steam names the folder itself, used only where the install record cannot be read.
const DEFAULT_INSTALL_DIR: &str = "ELDEN RING";

/// Every directory that might be the game's `Game` folder, best evidence first.
///
/// Nothing here touches the disk except to read Steam's own records, so a path that does not
/// exist costs a failed `is_file` in the caller and nothing else.
pub fn game_dirs() -> Vec<PathBuf> {
    game_dirs_in(&libraries())
}

/// The candidates one set of libraries yields, split from [`game_dirs`] so the rule can be
/// tested against a directory a test builds rather than against whatever Steam is on the
/// machine running the test.
fn game_dirs_in(libraries: &[PathBuf]) -> Vec<PathBuf> {
    let mut found = Vec::new();
    for library in libraries {
        let common = library.join("steamapps").join("common");
        // The install record names the folder. This is the answer when Steam has one.
        if let Some(install_dir) = app_install_dir(library, APP_ID) {
            found.push(common.join(&install_dir).join(GAME_SUBDIR));
        }
        // And the default name, for a library whose manifest is missing or unreadable -- a
        // game copied between machines by hand still lives under the folder Steam made.
        let default_name = common.join(DEFAULT_INSTALL_DIR).join(GAME_SUBDIR);
        if !found.contains(&default_name) {
            found.push(default_name);
        }
    }
    found
}

/// Every Steam library on this machine: each root itself, plus everything its
/// `libraryfolders.vdf` lists.
pub fn libraries() -> Vec<PathBuf> {
    let mut found: Vec<PathBuf> = Vec::new();
    for root in roots() {
        push_unique(&mut found, root.clone());
        for listed in listed_libraries(&root) {
            push_unique(&mut found, listed);
        }
    }
    found
}

/// The libraries `root`'s own records name, which is how a second drive is found.
fn listed_libraries(root: &Path) -> Vec<PathBuf> {
    let mut found = Vec::new();
    for relative in ["steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"] {
        let Ok(text) = std::fs::read_to_string(root.join(relative)) else {
            continue;
        };
        for path in vdf::values(&text, "path") {
            for candidate in reachable_spellings(&path) {
                push_unique(&mut found, candidate);
            }
        }
    }
    found
}

/// The spellings of one recorded library path this build can actually open.
///
/// A Linux Steam records POSIX paths, and the shipped executable is a Windows binary a player
/// runs under Proton or Wine. `/mnt/games/SteamLibrary` is not a path the Windows file APIs
/// accept, but Wine maps the whole filesystem onto `Z:` by default -- measured through
/// `WINEHOMEDIR`, which arrives as `\??\Z:\home\<user>` on this machine -- so the same library
/// is reachable as `Z:\mnt\games\SteamLibrary`. Both are offered; the one that does not exist
/// costs a failed lookup.
fn reachable_spellings(recorded: &str) -> Vec<PathBuf> {
    #[cfg(windows)]
    if recorded.starts_with('/') {
        return vec![
            PathBuf::from(recorded),
            PathBuf::from(format!("Z:{}", recorded.replace('/', "\\"))),
        ];
    }
    vec![PathBuf::from(recorded)]
}

/// `installdir` out of one library's install record for `app_id`, when there is one.
pub fn app_install_dir(library: &Path, app_id: &str) -> Option<String> {
    let manifest = library
        .join("steamapps")
        .join(format!("appmanifest_{app_id}.acf"));
    let text = std::fs::read_to_string(manifest).ok()?;
    vdf::values(&text, "installdir")
        .into_iter()
        .find(|value| !value.is_empty())
}

/// Every plausible Steam root, best evidence first: what the registry says, then what the
/// player's home directory says, then the handful of default install locations.
pub fn roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for path in registry_roots() {
        push_unique(&mut roots, path);
    }
    if let Some(home) = player_home() {
        push_unique(&mut roots, home.join(".local/share/Steam"));
        push_unique(&mut roots, home.join(".steam/steam"));
    }
    // Last, and only as a fallback: a machine whose registry could not be read still usually
    // has Steam where its installer puts it.
    for drive in ["C:", "D:", "E:"] {
        push_unique(
            &mut roots,
            PathBuf::from(format!("{drive}\\Program Files (x86)\\Steam")),
        );
        push_unique(&mut roots, PathBuf::from(format!("{drive}\\SteamLibrary")));
    }
    roots
}

fn push_unique(into: &mut Vec<PathBuf>, path: PathBuf) {
    if !into.contains(&path) {
        into.push(path);
    }
}

/// The player's real home directory, on whichever system is underneath.
///
/// `HOME` answers on a native Linux build and is absent from the Windows environment, so the
/// shipped exe -- which a Linux player runs under Proton or Wine -- used to fall through to the
/// drive letters alone and report six `C:`/`D:`/`E:` paths it had tried. Autodetect could
/// therefore never succeed for a Proton player, whose library is only reachable on `Z:`, and
/// every one of them had to discover `--game-dir` from a failure message.
///
/// `WINEHOMEDIR` is how Wine spells that home to the Windows side, and it is an NT object path:
/// measured on this machine as `\??\Z:\home\banon`. Stripping the `\??\` prefix leaves a path
/// the Windows file APIs accept.
fn player_home() -> Option<PathBuf> {
    if let Some(home) = std::env::var_os("HOME") {
        return Some(PathBuf::from(home));
    }
    wine_home_dir(std::env::var("WINEHOMEDIR").ok().as_deref())
}

/// The path half of [`player_home`], split out so it can be tested without an environment.
fn wine_home_dir(raw: Option<&str>) -> Option<PathBuf> {
    let raw = raw?.trim();
    let stripped = raw.strip_prefix(r"\??\").unwrap_or(raw);
    (!stripped.is_empty()).then(|| PathBuf::from(stripped))
}

#[cfg(windows)]
fn registry_roots() -> Vec<PathBuf> {
    registry::steam_roots()
}

/// Nothing, on a system with no registry. The Linux build exists to run this crate's tests and
/// to be driven from the repo, and both reach the game through `HOME` or `--game-dir`.
#[cfg(not(windows))]
fn registry_roots() -> Vec<PathBuf> {
    Vec::new()
}

/// Reading two string values out of the Windows registry, with no crate to do it.
///
/// `winreg` and the `windows` crate both answer this in one line and both cost a dependency
/// tree in a binary that ships with none. `RegGetValueW` is one import and it does the whole
/// job: it opens the key, checks the type, and null-terminates the string, so there is no
/// handle to close and no partial read to get wrong.
#[cfg(windows)]
mod registry {
    use std::path::PathBuf;

    // `HKEY_CURRENT_USER` is defined as `(HKEY)(ULONG_PTR)(LONG)0x80000001`, so the cast chain
    // matters: the constant is narrowed to a signed 32-bit value and then widened, which
    // sign-extends it. Writing `0x8000_0001` directly gives a different handle on a 64-bit
    // build and every lookup fails.
    const HKEY_CURRENT_USER: isize = 0x8000_0001u32 as i32 as isize;
    const HKEY_LOCAL_MACHINE: isize = 0x8000_0002u32 as i32 as isize;

    /// `RRF_RT_REG_SZ`: refuse anything that is not a plain string, rather than reading a
    /// `REG_DWORD` as though its bytes were a path.
    const RRF_RT_REG_SZ: u32 = 0x0000_0002;

    const ERROR_SUCCESS: i32 = 0;
    const ERROR_MORE_DATA: i32 = 234;

    /// Room for a long path without a first call that only asks how much room is needed.
    /// `ERROR_MORE_DATA` is still handled below, so this is a size, not a limit.
    const INITIAL_CHARS: usize = 520;

    #[link(name = "advapi32")]
    unsafe extern "system" {
        fn RegGetValueW(
            key: isize,
            sub_key: *const u16,
            value: *const u16,
            flags: u32,
            kind: *mut u32,
            data: *mut u16,
            size: *mut u32,
        ) -> i32;
    }

    /// Where the registry says Steam is, most authoritative first.
    ///
    /// The per-user value is written by the client each time it starts, so it follows a Steam
    /// that has been moved. The machine-wide one is written once by the installer, which is why
    /// it is the fallback rather than the answer.
    pub fn steam_roots() -> Vec<PathBuf> {
        let lookups: [(isize, &str, &str); 3] = [
            (HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (
                HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Valve\Steam",
                "InstallPath",
            ),
            (HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ];
        let mut found = Vec::new();
        for (key, sub_key, value) in lookups {
            let Some(text) = string_value(key, sub_key, value) else {
                continue;
            };
            let trimmed = text.trim();
            if trimmed.is_empty() {
                continue;
            }
            // `SteamPath` is recorded with forward slashes, as `c:/program files (x86)/steam`.
            // The file APIs take either, but this path is printed back to a player when the
            // search fails, and a mixed-separator path reads as a bug in the tool.
            let path = PathBuf::from(trimmed.replace('/', "\\"));
            if !found.contains(&path) {
                found.push(path);
            }
        }
        found
    }

    fn string_value(key: isize, sub_key: &str, value: &str) -> Option<String> {
        let sub_key = wide(sub_key);
        let value = wide(value);
        let mut buffer = vec![0u16; INITIAL_CHARS];
        let mut bytes = (buffer.len() * size_of::<u16>()) as u32;
        let mut status = unsafe {
            RegGetValueW(
                key,
                sub_key.as_ptr(),
                value.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                buffer.as_mut_ptr(),
                &raw mut bytes,
            )
        };
        if status == ERROR_MORE_DATA {
            buffer = vec![0u16; bytes as usize / size_of::<u16>() + 1];
            bytes = (buffer.len() * size_of::<u16>()) as u32;
            status = unsafe {
                RegGetValueW(
                    key,
                    sub_key.as_ptr(),
                    value.as_ptr(),
                    RRF_RT_REG_SZ,
                    std::ptr::null_mut(),
                    buffer.as_mut_ptr(),
                    &raw mut bytes,
                )
            };
        }
        if status != ERROR_SUCCESS {
            return None;
        }
        // `bytes` comes back as the size written, terminator included. Trusting it rather than
        // scanning for the terminator keeps a value the registry stored unterminated from
        // running off the end of what was written.
        let chars = (bytes as usize / size_of::<u16>()).min(buffer.len());
        let text = &buffer[..chars];
        let text = match text.iter().position(|unit| *unit == 0) {
            Some(end) => &text[..end],
            None => text,
        };
        // Not `from_utf16_lossy`: a registry value that is not valid UTF-16 is not a path this
        // tool can open, and a replacement character would turn that into a candidate that
        // silently names the wrong directory.
        String::from_utf16(text).ok()
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }
}

/// Just enough of Valve's key-value format to read a named field out of it.
///
/// The format is quoted strings in pairs, nested in braces:
///
/// ```text
/// "libraryfolders"
/// {
///     "0"
///     {
///         "path"      "D:\\SteamLibrary"
///         "label"     ""
///     }
/// }
/// ```
///
/// A string followed by another string is a key and its value; a string followed by `{` is a
/// section name. Tracking which of the two a string is, rather than scanning for the token
/// after every occurrence of the word, is what stops a value that happens to read `path` from
/// being taken for a key.
mod vdf {
    /// Every value whose key is `name`, in file order, with escapes resolved.
    ///
    /// Keys are matched without regard to case, which is how Valve's own reader treats them.
    pub fn values(text: &str, name: &str) -> Vec<String> {
        let mut found = Vec::new();
        let mut pending: Option<String> = None;
        let mut characters = text.chars().peekable();
        while let Some(character) = characters.next() {
            match character {
                '"' => {
                    let token = read_string(&mut characters);
                    match pending.take() {
                        Some(key) if key.eq_ignore_ascii_case(name) => found.push(token),
                        Some(_) => {}
                        None => pending = Some(token),
                    }
                }
                // A brace ends whatever the last string was: a section name before `{`, and
                // nothing at all before `}`.
                '{' | '}' => pending = None,
                // Valve writes `//` comments into some of these files.
                '/' if characters.peek() == Some(&'/') => {
                    for skipped in characters.by_ref() {
                        if skipped == '\n' {
                            break;
                        }
                    }
                }
                _ => {}
            }
        }
        found
    }

    /// Read one quoted string, the opening quote already consumed.
    ///
    /// Steam escapes backslashes, so a Windows library is recorded as `D:\\SteamLibrary` and
    /// comes back as `D:\SteamLibrary`. An escape that is not one of the four Valve emits keeps
    /// its backslash, so a file written by something that did not escape at all still yields a
    /// usable path rather than one with characters eaten out of it.
    fn read_string(characters: &mut std::iter::Peekable<std::str::Chars<'_>>) -> String {
        let mut text = String::new();
        while let Some(character) = characters.next() {
            match character {
                '"' => break,
                '\\' => match characters.next() {
                    Some('\\') => text.push('\\'),
                    Some('"') => text.push('"'),
                    Some('n') => text.push('\n'),
                    Some('t') => text.push('\t'),
                    Some(other) => {
                        text.push('\\');
                        text.push(other);
                    }
                    None => break,
                },
                other => text.push(other),
            }
        }
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// The shape Steam writes on Linux, copied from a real `libraryfolders.vdf` on this
    /// machine: two libraries, the second on another filesystem, with the app id lists that
    /// make the file long enough for a naive scan to go wrong in.
    const LINUX_LIBRARIES: &str = r#"
"libraryfolders"
{
	"0"
	{
		"path"		"/home/player/.local/share/Steam"
		"label"		""
		"apps"
		{
			"570"		"76687030983"
			"1245620"		"72118304140"
		}
	}
	"1"
	{
		"path"		"/mnt/games/SteamLibrary"
		"label"		""
		"apps"
		{
			"381210"		"0"
		}
	}
}
"#;

    /// The same file as Steam writes it on Windows, where every backslash in a path is doubled.
    const WINDOWS_LIBRARIES: &str = r#"
"libraryfolders"
{
	"0"
	{
		"path"		"C:\\Program Files (x86)\\Steam"
		"label"		""
	}
	"1"
	{
		"path"		"D:\\SteamLibrary"
		"label"		""
	}
}
"#;

    const MANIFEST: &str = r#"
"AppState"
{
	"appid"		"1245620"
	"name"		"ELDEN RING"
	"installdir"		"ELDEN RING"
	"InstallScripts"
	{
		"1245621"		"Game\\EasyAntiCheat\\install_script.vdf"
	}
}
"#;

    fn temp_dir(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("er-installer-steam-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn every_library_path_is_read_in_file_order() {
        assert_eq!(
            vdf::values(LINUX_LIBRARIES, "path"),
            vec!["/home/player/.local/share/Steam", "/mnt/games/SteamLibrary"]
        );
    }

    #[test]
    fn a_doubled_backslash_comes_back_as_one() {
        assert_eq!(
            vdf::values(WINDOWS_LIBRARIES, "path"),
            vec![r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"]
        );
    }

    #[test]
    fn a_key_is_matched_without_regard_to_case() {
        assert_eq!(vdf::values(MANIFEST, "InstallDir"), vec!["ELDEN RING"]);
        assert_eq!(vdf::values(MANIFEST, "installdir"), vec!["ELDEN RING"]);
    }

    #[test]
    fn a_value_that_reads_like_the_key_is_not_taken_for_one() {
        // The trap a scan for the word would fall into: `path` here is a label's value, and
        // the only real path is the one after the key.
        let text = r#""libraryfolders" { "0" { "label" "path" "path" "D:\\Games" } }"#;
        assert_eq!(vdf::values(text, "path"), vec![r"D:\Games"]);
    }

    #[test]
    fn a_nested_section_does_not_leak_a_key_across_its_braces() {
        // `path` opens a section rather than naming a value. Reading the next string anyway
        // would report `sub` as a library.
        let text = r#""root" { "path" { "sub" "value" } "path" "/real" }"#;
        assert_eq!(vdf::values(text, "path"), vec!["/real"]);
    }

    #[test]
    fn comments_and_missing_keys_produce_nothing_rather_than_noise() {
        assert!(vdf::values("// \"path\" \"/commented/out\"\n", "path").is_empty());
        assert!(vdf::values(LINUX_LIBRARIES, "installdir").is_empty());
        assert!(vdf::values("", "path").is_empty());
    }

    #[test]
    fn an_unterminated_string_ends_the_file_rather_than_hanging() {
        assert_eq!(vdf::values(r#""path" "/half"#, "path"), vec!["/half"]);
        assert!(vdf::values(r#""path"#, "path").is_empty());
    }

    #[test]
    fn the_install_record_names_the_folder_under_common() {
        let library = temp_dir("manifest");
        fs::create_dir_all(library.join("steamapps")).unwrap();
        fs::write(
            library.join("steamapps/appmanifest_1245620.acf"),
            MANIFEST.as_bytes(),
        )
        .unwrap();

        assert_eq!(
            app_install_dir(&library, APP_ID),
            Some("ELDEN RING".to_string())
        );
        // A library without the game says nothing rather than guessing a folder name.
        assert_eq!(app_install_dir(&library, "440"), None);
        fs::remove_dir_all(&library).unwrap();
    }

    /// The case the old hard-coded search could not reach: a library whose game folder is not
    /// called `ELDEN RING`. Steam writes `installdir` from the depot, and a game restored from
    /// a backup, moved by hand, or installed by a client in another language does not
    /// necessarily match the English name this tool used to assume.
    #[test]
    fn the_folder_the_install_record_names_is_tried_before_the_default_one() {
        let library = temp_dir("renamed");
        fs::create_dir_all(library.join("steamapps")).unwrap();
        fs::write(
            library.join("steamapps/appmanifest_1245620.acf"),
            "\"AppState\"\n{\n\t\"appid\"\t\t\"1245620\"\n\t\"installdir\"\t\t\"Elden Ring GOTY\"\n}\n",
        )
        .unwrap();

        let candidates = game_dirs_in(std::slice::from_ref(&library));
        assert_eq!(
            candidates[0],
            library.join("steamapps/common/Elden Ring GOTY/Game"),
            "got {candidates:?}"
        );
        // The default name is still offered behind it, so a manifest that has gone missing
        // does not take the usual install down with it.
        assert!(
            candidates.contains(&library.join("steamapps/common/ELDEN RING/Game")),
            "got {candidates:?}"
        );
        fs::remove_dir_all(&library).unwrap();
    }

    #[test]
    fn a_library_with_no_install_record_still_offers_the_usual_folder() {
        let library = temp_dir("norecord");
        let candidates = game_dirs_in(std::slice::from_ref(&library));
        assert_eq!(
            candidates,
            vec![library.join("steamapps/common/ELDEN RING/Game")]
        );
        fs::remove_dir_all(&library).unwrap();
    }

    #[test]
    fn a_second_library_is_found_through_the_roots_own_records() {
        let root = temp_dir("libraries");
        let other = root.join("second-library");
        fs::create_dir_all(root.join("steamapps")).unwrap();
        fs::create_dir_all(&other).unwrap();
        fs::write(
            root.join("steamapps/libraryfolders.vdf"),
            format!(
                "\"libraryfolders\"\n{{\n\t\"0\"\n\t{{\n\t\t\"path\"\t\t\"{}\"\n\t}}\n}}\n",
                other.display()
            ),
        )
        .unwrap();

        let listed = listed_libraries(&root);
        assert!(listed.contains(&other), "got {listed:?}");
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_root_with_no_records_at_all_is_not_an_error() {
        let root = temp_dir("bare");
        assert!(listed_libraries(&root).is_empty());
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_same_library_listed_twice_is_offered_once() {
        // Steam keeps the file in two places, and they normally agree. Both are read, so the
        // duplicate has to be dropped or every later message lists each library twice.
        let root = temp_dir("duplicate");
        let other = root.join("shared");
        fs::create_dir_all(root.join("steamapps")).unwrap();
        fs::create_dir_all(root.join("config")).unwrap();
        fs::create_dir_all(&other).unwrap();
        let text = format!(
            "\"libraryfolders\"\n{{\n\t\"0\"\n\t{{\n\t\t\"path\"\t\t\"{}\"\n\t}}\n}}\n",
            other.display()
        );
        fs::write(root.join("steamapps/libraryfolders.vdf"), &text).unwrap();
        fs::write(root.join("config/libraryfolders.vdf"), &text).unwrap();

        let listed = listed_libraries(&root);
        assert_eq!(
            listed.iter().filter(|path| **path == other).count(),
            1,
            "got {listed:?}"
        );
        fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn a_recorded_posix_path_is_taken_as_it_stands_on_this_build() {
        let spellings = reachable_spellings("/mnt/games/SteamLibrary");
        assert_eq!(spellings[0], PathBuf::from("/mnt/games/SteamLibrary"));
        // The Wine spelling is offered as well on a Windows build, and the first entry is the
        // recorded one either way.
        assert_eq!(spellings.len(), if cfg!(windows) { 2 } else { 1 });
    }

    #[test]
    fn the_default_roots_are_offered_once_each() {
        let roots = roots();
        let mut sorted = roots.clone();
        sorted.sort();
        let before = sorted.len();
        sorted.dedup();
        assert_eq!(before, sorted.len(), "a root was offered twice: {roots:?}");
        assert!(!roots.is_empty());
    }

    /// The exact string Wine put in `WINEHOMEDIR`, measured on this machine 2026-09-19 with
    /// `wine cmd /c set`. The prefix is what made the shipped exe unable to find a Proton
    /// player's library: `HOME` is not in the Windows environment, so this is the only witness
    /// of the real home, and it arrives as an NT object path rather than a usable one.
    #[test]
    fn wine_spells_the_linux_home_as_an_nt_object_path() {
        assert_eq!(
            wine_home_dir(Some(r"\??\Z:\home\banon")),
            Some(PathBuf::from(r"Z:\home\banon"))
        );
    }

    #[test]
    fn a_home_without_the_nt_prefix_is_taken_as_it_stands() {
        assert_eq!(
            wine_home_dir(Some(r"Z:\home\banon")),
            Some(PathBuf::from(r"Z:\home\banon"))
        );
    }

    #[test]
    fn an_absent_or_empty_wine_home_is_not_a_root() {
        assert_eq!(wine_home_dir(None), None);
        assert_eq!(wine_home_dir(Some("")), None);
        assert_eq!(wine_home_dir(Some("   ")), None);
        // The prefix alone names no directory, and joining Steam paths onto it would put two
        // candidates in the error message that could never have held the game.
        assert_eq!(wine_home_dir(Some(r"\??\")), None);
    }
}
