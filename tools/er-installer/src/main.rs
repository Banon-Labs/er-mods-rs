//! Pick which of this workspace's mods to install, and write the ME3 profile that loads them.
//!
//! # Why this is a profile writer and not a build system
//!
//! Every feature here already ships as its own DLL, listed as its own `[[natives]]` entry. The
//! set of shipped shells is 31, which is 2^31 combinations -- so building a bespoke binary per
//! user is not a large job, it is an impossible one. Composing 31 prebuilt files is linear, and
//! it is what ME3 was built to do. Cargo features stay a build-time concern of this workspace
//! and never reach a player's vocabulary.
//!
//! # Why the conflicts matter more than the copying
//!
//! `scripts/me3-dll-conflicts.toml` records pairs that destroy each other in one process, each
//! with the run that measured it. The symptom is almost never a crash: it is one of the two
//! mods silently doing nothing, which reads as a feature bug. Nobody can be expected to know
//! that from a mod list, so the picker refuses the pair at the moment of ticking and says which
//! one to drop.
//!
//! # No dependencies, on either side
//!
//! The player installs nothing: one exe, the DLLs beside it. This crate depends on nothing but
//! `std`, and the two tables are compiled in by `scripts/gen-installer-catalog.py`, so there is
//! no data file beside the exe to lose and no parser inside it.

mod catalog;
mod console;
mod install;
mod picker;
mod selection;
mod steam;
mod tui;

use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use catalog::{CATALOG, CATEGORIES, Mod};
use selection::ProfileInputs;

const VERSION: &str = env!("CARGO_PKG_VERSION");

const USAGE: &str = "\
er-installer -- choose which Elden Ring mods to install, and write the me3 profile for them.

USAGE:
    er-installer [options]

With no options it finds the game, shows a numbered list, and installs what you tick.

OPTIONS:
    --game-dir <path>     The game's `Game` directory (the one holding eldenring.exe).
                          Found automatically through Steam's own records, and asked for
                          if that fails. Pass it to skip the search entirely.
    --dll-dir <path>      Where the mod DLLs are read from. Defaults to a `dlls` folder
                          beside this program, then to the program's own folder.
    --install-dir <path>  Where the chosen DLLs are copied. Defaults to <game-dir>/er-mods.
    --profile <path>      Profile to write. Defaults to <install-dir>/er-mods.me3.

    --no-seamless         Leave Seamless Co-op out of the profile even though it is
                          installed. Mods that need it will stay inert.
    --plain               Use the numbered-list picker instead of the full-screen one,
                          for a terminal the full-screen one does not suit.

    --select <names>      Comma-separated mods, by name or by label. Skips the picker.
    --defaults            Install the recommended set without the picker.
    --none                Write a profile that loads nothing.
    --list                Print every available mod and exit.
    --selfcheck           Report whether this executable carries every mod it offers, and
                          exit non-zero if it does not. No game needed.
    --dry-run             Print the profile that would be written; copy nothing.
    --version             Print the version and exit.
    -h, --help            Print this and exit.
";

#[derive(Default, Debug)]
struct Args {
    game_dir: Option<PathBuf>,
    dll_dir: Option<PathBuf>,
    install_dir: Option<PathBuf>,
    profile: Option<PathBuf>,
    select: Option<String>,
    no_seamless: bool,
    plain: bool,
    defaults: bool,
    none: bool,
    list: bool,
    selfcheck: bool,
    dry_run: bool,
    version: bool,
    help: bool,
}

#[derive(Debug, PartialEq, Eq)]
struct ArgError(String);

impl Args {
    fn parse<I: IntoIterator<Item = String>>(args: I) -> Result<Self, ArgError> {
        let mut parsed = Args::default();
        let mut iter = args.into_iter();
        while let Some(arg) = iter.next() {
            let mut value = || {
                iter.next()
                    .ok_or_else(|| ArgError(format!("{arg} needs a value after it")))
            };
            match arg.as_str() {
                "--game-dir" => parsed.game_dir = Some(PathBuf::from(value()?)),
                "--dll-dir" => parsed.dll_dir = Some(PathBuf::from(value()?)),
                "--install-dir" => parsed.install_dir = Some(PathBuf::from(value()?)),
                "--profile" => parsed.profile = Some(PathBuf::from(value()?)),
                "--select" => parsed.select = Some(value()?),
                "--no-seamless" => parsed.no_seamless = true,
                "--plain" => parsed.plain = true,
                "--defaults" => parsed.defaults = true,
                "--none" => parsed.none = true,
                "--list" => parsed.list = true,
                "--selfcheck" => parsed.selfcheck = true,
                "--dry-run" => parsed.dry_run = true,
                "--version" => parsed.version = true,
                "-h" | "--help" => parsed.help = true,
                other => {
                    return Err(ArgError(format!(
                        "unrecognised option {other:?}. Run with --help for the list."
                    )));
                }
            }
        }
        let exclusive = [parsed.select.is_some(), parsed.defaults, parsed.none];
        if exclusive.iter().filter(|set| **set).count() > 1 {
            return Err(ArgError(
                "--select, --defaults and --none each choose the whole set; pass one.".to_string(),
            ));
        }
        Ok(parsed)
    }
}

/// Resolve a non-interactive `--select` list into mods, naming anything that did not resolve.
fn resolve_selection(list: &str) -> Result<Vec<&'static Mod>, String> {
    let mut chosen = Vec::new();
    let mut unknown = Vec::new();
    for name in list.split(',').map(str::trim).filter(|n| !n.is_empty()) {
        match selection::resolve(name) {
            Some(entry) => chosen.push(entry),
            None => unknown.push(name.to_string()),
        }
    }
    if !unknown.is_empty() {
        return Err(format!(
            "not a mod in this package: {}. Run --list to see the names.",
            unknown.join(", ")
        ));
    }
    Ok(selection::in_display_order(&chosen))
}

fn print_list() {
    println!(
        "er-installer {VERSION} -- {} mods available.\n",
        CATALOG.len()
    );
    for (key, title) in CATEGORIES {
        let section: Vec<&Mod> = CATALOG.iter().filter(|e| e.category == *key).collect();
        if section.is_empty() {
            continue;
        }
        println!("{}", title.to_uppercase());
        for entry in section {
            let tick = if entry.default_on { "recommended" } else { "" };
            println!("  {:<34} {}", entry.package, tick);
            println!("    {}", entry.label);
            println!("    {}", entry.blurb);
            if entry.needs_seamless {
                println!("    Needs Seamless Co-op to do anything.");
            }
            if let Some(caution) = entry.caution {
                println!("    Never installed unless you ask for it: {caution}.");
            }
            if let Some(config) = entry.config {
                println!("    Settings: {config} in the game folder.");
            }
        }
        println!();
    }

    println!("PAIRS THAT CANNOT BE INSTALLED TOGETHER");
    println!("The picker refuses these as you tick them; this is the whole list up front.\n");
    for conflict in catalog::CONFLICTS {
        let a = selection::by_package(conflict.a).map_or(conflict.a, |e| e.label);
        let b = selection::by_package(conflict.b).map_or(conflict.b, |e| e.label);
        println!("  {a}");
        println!("  {b}");
        // The `kind` is the failure mode as recorded in scripts/me3-dll-conflicts.toml. It is
        // printed so someone reporting a problem, or reading that file, has the same word for it.
        println!("    {} [{}]\n", conflict.explanation, conflict.kind);
    }
}

/// Does this executable carry every mod it offers?
///
/// A release build answers yes and needs nothing else on the machine. This exists so that
/// question is asked of the binary that will actually be uploaded, by the packager, rather
/// than inferred from the build command having been run with the right environment.
fn selfcheck() -> ExitCode {
    let all: Vec<&'static Mod> = CATALOG.iter().collect();
    let outstanding = install::not_embedded(&all);
    if outstanding.is_empty() {
        println!(
            "er-installer {VERSION}: self-contained -- all {} mods are built in, \
             no other files needed.",
            CATALOG.len()
        );
        return ExitCode::SUCCESS;
    }
    eprintln!(
        "er-installer {VERSION}: carries {} of {} mods. Not shippable on its own.\n\
         Missing:",
        install::embedded_count(),
        CATALOG.len()
    );
    for entry in outstanding {
        eprintln!("  {} ({})", entry.artifact, entry.label);
    }
    eprintln!(
        "\nBuild with the payload:\n  scripts/er-build-dlls.sh --all\n  \
         ER_INSTALLER_EMBED_DIR=target/x86_64-pc-windows-msvc/release \\\n    \
         cargo build --release -p er-installer"
    );
    ExitCode::FAILURE
}

fn report_conflicts(chosen: &[&'static Mod]) -> bool {
    let clashes = selection::conflicts_within(chosen);
    let redundant = selection::redundancies_within(chosen);
    if clashes.is_empty() && redundant.is_empty() {
        return false;
    }
    eprintln!("That set cannot be installed as it stands:\n");
    for conflict in clashes {
        let a = selection::by_package(conflict.a).map_or(conflict.a, |e| e.label);
        let b = selection::by_package(conflict.b).map_or(conflict.b, |e| e.label);
        eprintln!("  {a} and {b}: {}.", conflict.explanation);
    }
    // Reported beside the conflicts rather than in a section of its own: from where the user
    // stands both are "pick one of these two", and the difference in mechanism is ours.
    for (entry, host) in redundant {
        let host_label = selection::by_package(host).map_or(host, |e| e.label);
        eprintln!(
            "  {host_label} already does what {} does, so pick one of them -- loading both \
             puts two copies of one feature in the game.",
            entry.label
        );
    }
    eprintln!("\nDrop one of each pair and try again.");
    true
}

/// How many of the searched directories to name before the list stops being read.
///
/// Steam's records can produce a long list on a machine with several libraries, and a wall of
/// paths is the same as no message at all. The first few are the ones a player recognises.
const MAX_PATHS_LISTED: usize = 8;

fn describe_search(tried: &[PathBuf]) -> String {
    let mut message = String::from("Could not find Elden Ring. Looked in:\n");
    for path in tried.iter().take(MAX_PATHS_LISTED) {
        message.push_str(&format!("  {}\n", path.display()));
    }
    if tried.len() > MAX_PATHS_LISTED {
        message.push_str(&format!(
            "  ...and {} more\n",
            tried.len() - MAX_PATHS_LISTED
        ));
    }
    message
}

/// Find the game, asking the player where it is when the search comes up empty.
///
/// `Ok(None)` means they chose to leave rather than answer, which is not a failure and must not
/// exit non-zero.
///
/// The prompt is skipped in two cases, both of which would make it a bug rather than a help.
/// An explicit `--game-dir` that did not resolve is a mistake in a command, and the right
/// answer is to say so and stop rather than to start a conversation. Input that is not a
/// keyboard belongs to a script, and a question asked of a script either hangs it or is
/// answered by whatever the pipe held next.
fn locate_game(explicit: Option<&Path>) -> Result<Option<install::GameInstall>, String> {
    let tried = match install::find_game(explicit) {
        Ok(found) => return Ok(Some(found)),
        Err(tried) => tried,
    };
    let searched = describe_search(&tried);
    if explicit.is_some() {
        return Err(format!(
            "{searched}\nThat is not a folder holding {}. Check the path and try again.",
            install::GAME_EXE
        ));
    }
    if !console::input_is_interactive() {
        return Err(format!(
            "{searched}\nPass the folder holding {} with --game-dir <path>.",
            install::GAME_EXE
        ));
    }
    print!("{searched}");
    ask_for_game_dir()
}

/// Ask for the game directory until one of the answers is a game directory.
///
/// Both console ways out are offered and both are honoured: ctrl-c is handled by Windows
/// itself, and ctrl-z followed by enter closes standard input, which arrives here as the end of
/// the iterator. Typing the word works too, because someone who does not know the key sequence
/// should not be trapped in a loop over their own installer.
fn ask_for_game_dir() -> Result<Option<install::GameInstall>, String> {
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();
    println!(
        "\nType or paste the folder that holds {}, then press enter.\n\
         In Explorer, right-click that folder and choose \"Copy as path\" -- the quotes it \
         adds are fine.\n\
         To close without installing: press ctrl-c, or type quit and press enter.",
        install::GAME_EXE
    );
    loop {
        print!("\nElden Ring folder: ");
        io::stdout()
            .flush()
            .map_err(|err| format!("writing to the screen: {err}"))?;

        let Some(line) = lines.next() else {
            // End of input: ctrl-z and enter, or a closed pipe. Either way, leave quietly.
            println!();
            return Ok(None);
        };
        let line = line.map_err(|err| format!("reading what you typed: {err}"))?;
        if matches!(
            line.trim().to_ascii_lowercase().as_str(),
            "q" | "quit" | "exit"
        ) {
            return Ok(None);
        }
        let Some(candidate) = install::clean_pasted_path(&line) else {
            println!("\nNothing typed. Paste the folder, or type quit to close.");
            continue;
        };
        match install::game_at(&candidate) {
            Some(found) => return Ok(Some(found)),
            None => println!(
                "\n{} does not hold {}, and neither does a Game folder inside it.\n\
                 It is the folder Elden Ring is installed in, usually ending in \
                 \\steamapps\\common\\ELDEN RING\\Game.",
                candidate.display(),
                install::GAME_EXE
            ),
        }
    }
}

fn run() -> Result<ExitCode, String> {
    let args = Args::parse(std::env::args().skip(1)).map_err(|err| err.0)?;

    if args.help {
        print!("{USAGE}");
        return Ok(ExitCode::SUCCESS);
    }
    if args.version {
        println!("er-installer {VERSION}");
        return Ok(ExitCode::SUCCESS);
    }
    if args.list {
        print_list();
        return Ok(ExitCode::SUCCESS);
    }
    if args.selfcheck {
        return Ok(selfcheck());
    }

    let Some(game) = locate_game(args.game_dir.as_deref())? else {
        println!("Nothing installed.");
        return Ok(ExitCode::SUCCESS);
    };
    println!("Elden Ring: {}", game.game_dir.display());
    match (&game.seamless, args.no_seamless) {
        (Some(path), false) => println!("Seamless Co-op: {}", path.display()),
        (Some(_), true) => println!("Seamless Co-op: installed, but left out by --no-seamless"),
        (None, _) => println!("Seamless Co-op: not installed (mods needing it will stay inert)"),
    }

    let chosen = if args.none {
        Vec::new()
    } else if args.defaults {
        selection::default_selection()
    } else if let Some(list) = &args.select {
        resolve_selection(list)?
    } else {
        let mut state = picker::Picker::new();
        let driven = if args.plain {
            picker::run_plain(&mut state)
        } else {
            picker::run(&mut state)
        };
        match driven.map_err(|err| format!("reading input: {err}"))? {
            Some(chosen) => chosen,
            None => {
                println!("Nothing installed.");
                return Ok(ExitCode::SUCCESS);
            }
        }
    };

    if report_conflicts(&chosen) {
        return Ok(ExitCode::FAILURE);
    }

    let install_dir = args
        .install_dir
        .unwrap_or_else(|| game.game_dir.join("er-mods"));
    let profile_path = args
        .profile
        .unwrap_or_else(|| install_dir.join("er-mods.me3"));

    // An empty selection needs no mod files at all, which is what makes "install nothing" work
    // from a bare download. Anything this build carries needs none either: a directory is
    // looked for only to cover what is left, which in a release build is nothing.
    let natives = if chosen.is_empty() {
        Vec::new()
    } else {
        let outstanding = install::not_embedded(&chosen);
        let source = if outstanding.is_empty() {
            None
        } else {
            let found = install::find_dll_source(args.dll_dir.as_deref()).ok_or_else(|| {
                let names: Vec<&str> = outstanding.iter().map(|entry| entry.label).collect();
                format!(
                    "This installer does not carry {}, and no folder of mod files was found.\n\
                     Put them in a `dlls` folder beside this program, or pass --dll-dir <path>.",
                    names.join(", ")
                )
            })?;
            let missing = install::missing_artifacts(&outstanding, &found);
            if !missing.is_empty() {
                let mut message = format!("These files are not in {}:\n", found.display());
                for item in missing {
                    message.push_str(&format!("  {} ({})\n", item.artifact, item.label));
                }
                message.push_str("\nNothing was installed.");
                return Err(message);
            }
            Some(found)
        };

        if args.dry_run {
            chosen
                .iter()
                .map(|entry| {
                    (
                        *entry,
                        install::display_path(&install_dir.join(entry.artifact)),
                    )
                })
                .collect()
        } else {
            install::install_artifacts(&chosen, source.as_deref(), &install_dir)
                .map_err(|err| format!("installing the mod files: {err}"))?
        }
    };

    let seamless = if args.no_seamless {
        None
    } else {
        game.seamless.as_deref().map(install::display_path)
    };
    let profile = selection::render_profile(&ProfileInputs {
        natives: &natives,
        seamless: seamless.as_deref(),
        installer_version: VERSION,
    })
    .map_err(|bad| {
        format!(
            "This path cannot be written into an me3 profile: {}\n\
             me3 quotes paths in a form with no escapes, so an apostrophe or a line break in \
             one cannot be represented. Install somewhere without one, using --install-dir.",
            bad.0
        )
    })?;

    if args.dry_run {
        println!(
            "\n--- {} (not written) ---\n{profile}",
            profile_path.display()
        );
        return Ok(ExitCode::SUCCESS);
    }

    let written = install::write_profile(&profile_path, &profile)
        .map_err(|err| format!("writing {}: {err}", profile_path.display()))?;

    println!(
        "\nInstalled {} mod(s) to {}",
        natives.len(),
        install_dir.display()
    );
    println!("Profile: {}", written.display());
    let configs: Vec<&str> = chosen.iter().filter_map(|entry| entry.config).collect();
    if !configs.is_empty() {
        println!(
            "Settings files to look at in the game folder: {}",
            configs.join(", ")
        );
    }

    // Files an earlier install left behind that this profile does not list. Reported, never
    // deleted: they belong to the player's game directory, they do not load, and a tool that
    // quietly removes files there has substituted its tidiness for their intent.
    let orphans = install::orphaned_artifacts(&chosen, &install_dir);
    if !orphans.is_empty() {
        println!(
            "\n{} file(s) from an earlier install are still in that folder and are NOT in this \
             profile, so they will not load: {}. Delete them if you want the folder to match.",
            orphans.len(),
            orphans.join(", ")
        );
    }
    println!(
        "\nLaunch it with:\n  {}",
        install::launch_command(&game.game_dir, &written)
    );
    // The mods are in place either way -- me3 is what loads them, and it is a separate
    // download. Saying so here is the difference between "install me3 and run that command"
    // and a command that answers "not recognized", which reads as this tool having failed.
    if !install::me3_on_path() {
        println!(
            "\nme3 is not installed yet, or not on your PATH, so that command will not run \
             as it stands.\nGet it from https://github.com/garyttierney/me3 -- the mods above \
             are already in place and do not need installing again."
        );
    }
    Ok(ExitCode::SUCCESS)
}

fn main() -> ExitCode {
    let code = match run() {
        Ok(code) => code,
        Err(message) => {
            eprintln!("{message}");
            ExitCode::FAILURE
        }
    };
    // Last thing before the process ends, and after the error above, because on a
    // double-clicked run the console dies with the process and takes every line of this with
    // it. Does nothing when a shell is attached -- see `console::launched_from_explorer`.
    console::wait_before_the_window_closes();
    code
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(items: &[&str]) -> Result<Args, ArgError> {
        Args::parse(items.iter().map(|s| (*s).to_string()))
    }

    #[test]
    fn options_with_values_are_parsed() {
        let parsed = args(&["--game-dir", "/games/er", "--select", "er-quickload"]).unwrap();
        assert_eq!(parsed.game_dir, Some(PathBuf::from("/games/er")));
        assert_eq!(parsed.select.as_deref(), Some("er-quickload"));
    }

    #[test]
    fn an_option_missing_its_value_is_an_error_not_a_silent_default() {
        let error = args(&["--game-dir"]).unwrap_err();
        assert!(error.0.contains("needs a value"), "{}", error.0);
    }

    #[test]
    fn an_unknown_option_is_refused() {
        assert!(args(&["--turbo"]).is_err());
    }

    #[test]
    fn the_three_whole_set_options_are_mutually_exclusive() {
        assert!(args(&["--defaults", "--none"]).is_err());
        assert!(args(&["--select", "er-quickload", "--defaults"]).is_err());
        assert!(args(&["--defaults"]).is_ok());
    }

    #[test]
    fn a_selection_resolves_by_package_or_label() {
        let product = selection::by_package("er-quickload").unwrap();
        let chosen = resolve_selection(&format!("er-armament-icons, {}", product.label)).unwrap();
        let packages: Vec<_> = chosen.iter().map(|entry| entry.package).collect();
        assert!(packages.contains(&"er-quickload"));
        assert!(packages.contains(&"er-armament-icons"));
    }

    #[test]
    fn an_unknown_name_in_a_selection_is_named_back() {
        let error = resolve_selection("er-quickload,er-nonsense").unwrap_err();
        assert!(error.contains("er-nonsense"), "{error}");
    }

    #[test]
    fn an_empty_selection_string_is_an_empty_set_rather_than_an_error() {
        assert!(resolve_selection("").unwrap().is_empty());
    }

    #[test]
    fn a_conflicting_selection_is_reported() {
        let chosen = resolve_selection("er-quickload,er-loading-portrait").unwrap();
        assert!(
            report_conflicts(&chosen),
            "the known conflict was not caught"
        );
    }

    #[test]
    fn the_recommended_set_passes_the_same_check() {
        assert!(!report_conflicts(&selection::default_selection()));
    }
}
