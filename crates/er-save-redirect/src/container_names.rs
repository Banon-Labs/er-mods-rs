//! Which save-container file names are in play for a given build of the game.
//!
//! Elden Ring writes exactly one container, `ER0000.sl2`. Seamless Co-op writes its own beside it
//! under an extension taken from `ersc_settings.ini`, where `.co2` is the shipped default and not
//! an invariant -- the key is documented as "any alphanumeric characters (limit = 120)". Every
//! hard-coded `.co2` in a save path is therefore a latent bug: a staged copy carrying a name the
//! runtime never asks for.
//!
//! Split out of `lib.rs` on 2026-09-14 under `scripts/check-rust-file-sizes.py`. It is a whole
//! concept with one input (the configured extension) and one output (a set of names), so it moves
//! as a unit with its tests and nothing else had to change.

/// The vanilla save container. Elden Ring itself writes only this one.
pub const VANILLA_SAVE_CONTAINER_NAME: &str = "ER0000.sl2";
/// The extension ERSC ships with in `ersc_settings.ini`. It is a default, never an invariant --
/// see [`parse_ersc_save_file_extension`].
pub const DEFAULT_SEAMLESS_SAVE_FILE_EXTENSION: &str = "co2";
/// ERSC's own documented ceiling for `save_file_extension` ("limit = 120").
pub const MAX_SAVE_FILE_EXTENSION_LEN: usize = 120;

/// The Seamless save-container extension ERSC is configured with, from its `ersc_settings.ini`.
///
/// `.co2` is only the shipped default. `ersc_settings.ini` says, in its own words: "Your save file
/// extension (in the vanilla game this is .sl2). Use any alphanumeric characters (limit = 120)" --
/// so the value replaces `sl2` and a user may set it to anything. Every hard-coded `.co2` in a save
/// path is therefore a latent version of the same bug this module exists to fix: the staged copy
/// carrying a name the runtime never asks for.
///
/// Returns None when the key is absent, outside `[SAVE]`, empty, over-long, or not plain ASCII
/// alphanumeric -- the last of which also keeps a config value from steering the staged filename
/// out of its directory.
pub fn parse_ersc_save_file_extension(ini: &str) -> Option<&str> {
    let mut in_save_section = false;
    for line in ini.lines() {
        let line = line.split(';').next().unwrap_or("").trim();
        if line.starts_with('[') {
            in_save_section = line.eq_ignore_ascii_case("[SAVE]");
            continue;
        }
        if !in_save_section {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if !key.trim().eq_ignore_ascii_case("save_file_extension") {
            continue;
        }
        let value = value.trim();
        let usable = (1..=MAX_SAVE_FILE_EXTENSION_LEN).contains(&value.len())
            && value.bytes().all(|b| b.is_ascii_alphanumeric());
        return usable.then_some(value);
    }
    None
}

/// The save container for one extension: `ER0000.<ext>`.
pub fn save_container_name_for_extension(extension: &str) -> String {
    format!("ER0000.{extension}")
}

/// Container names the active runtime will load, in priority order.
///
/// The mode lock is ASYMMETRIC: Seamless takes both containers preferring the co-op one, vanilla
/// takes only `.sl2` so an offline launch can never advance co-op progress. `seamless_name` is the
/// co-op container ERSC is configured with, not a fixed `ER0000.co2`.
pub fn active_save_container_names_for(seamless: bool, seamless_name: &str) -> Vec<String> {
    if seamless && !seamless_name.eq_ignore_ascii_case(VANILLA_SAVE_CONTAINER_NAME) {
        vec![
            seamless_name.to_owned(),
            VANILLA_SAVE_CONTAINER_NAME.to_owned(),
        ]
    } else {
        vec![VANILLA_SAVE_CONTAINER_NAME.to_owned()]
    }
}

/// Container names the boot default-save check may accept, in priority order.
///
/// **This is deliberately narrower than [`active_save_container_names_for`], and the difference is
/// the whole point.** That list answers "which containers might hold this run's save", and its
/// `.sl2` fallback under Seamless is correct wherever a redirect will normalise the name: a save
/// the user picks is staged under every container name
/// ([`staged_save_container_names_for`]), so picking a vanilla `.sl2` on a Seamless launch works
/// and must keep working (bd `er-effects-rs-h6sh` -- refusing it there softlocked the loading
/// screen on 2026-08-02).
///
/// The boot default-save check is the one place where that fallback is wrong, because it accepts a
/// container **with no redirect at all**. Whatever it accepts, the runtime then opens the container
/// it wants by name -- and under Seamless that is ERSC's container, not `.sl2`. So accepting a
/// `.sl2` there validates a file the runtime will never read.
///
/// Measured, run br-20260826-190532-55e2 (this is the bug this function exists to remove):
///
/// ```text
/// [+59ms]  save-override: Seamless save container resolved to 'ER0000.co2'
/// [+84ms]  save-override: default save '...\ER0000.co2' has ZERO readable character slots
///                         (native empty container); treating as no save
/// [+98ms]  save-override: DEFAULT-USER-SAVE -- ... default save '...\ER0000.sl2' with no redirect
/// ```
///
/// The live `.co2` was 28967888 bytes of all zeros (0% nonzero, valid BND4 header, no character
/// names); the `.sl2` beside it was 19% nonzero with all ten characters. The check rejected the
/// container the runtime opens, fell back to one it does not, and reported "there is a save" --
/// so `missing_save_selection_pending()` stayed false, the boot save-data `ShowProgressJob` was
/// never held (`show-progress: HOLD ...` = 0 occurrences, `PASS-THROUGH` = 6 from +14131ms), and
/// the title built its whole menu against an empty `ProfileSummary`. Everything after that -- the
/// disabled Continue row, the null `MENU_CONTINUE_ITEM`, the 65 s softlock -- was downstream of
/// this one line. See bd `seamless-boot-accepts-sl2-while-game-opens-blank-co2-2026-08-26`.
///
/// Returning "no usable save" instead is not a degradation: it arms the missing-save picker at
/// boot, which is the originally designed path and the one where every downstream stage works by
/// construction.
///
/// Vanilla is unchanged -- it only ever had `.sl2` -- so this narrows Seamless alone.
pub fn default_save_container_names_for(seamless: bool, seamless_name: &str) -> Vec<String> {
    vec![active_save_container_name_for(seamless, seamless_name)]
}

/// Does the container the boot default-save check accepted match the one the runtime will open?
///
/// The telemetry form of the invariant [`default_save_container_names_for`] enforces, exposed as
/// `oracle_boot_save_container_matches_runtime` so a mismatch is visible in RAM instead of costing
/// another run. `None` (no default save accepted) is not a mismatch: nothing was accepted, so
/// nothing disagrees -- that run arms the picker, which is the correct answer.
#[must_use]
pub fn boot_save_container_matches_runtime(accepted: Option<&str>, runtime_name: &str) -> bool {
    accepted.is_none_or(|name| name.eq_ignore_ascii_case(runtime_name))
}

/// The container name the active runtime writes to -- the preferred load candidate.
pub fn active_save_container_name_for(seamless: bool, seamless_name: &str) -> String {
    if seamless {
        seamless_name.to_owned()
    } else {
        VANILLA_SAVE_CONTAINER_NAME.to_owned()
    }
}

/// Every container name a staging pass writes from the configured source.
///
/// Both the vanilla container and ERSC's configured one, always -- the staged name is derived
/// neither from the source file's extension nor from the Seamless mode. Measured 2026-08-11:
/// staging runs inside the `CreateFileW` detour at DllMain+191ms, and me3 loads `ersc.dll` after
/// that, so the ERSC module latch still reads `seamless=false` there (`save-picker mode from ERSC
/// module latch seamless=false reason=active-default-save-file-name`) while the same run's
/// telemetry later reports `seamless_coop_loaded=true`. Naming the staged copy from that unsettled
/// latch put a Seamless run's save at `ER0000.sl2` while `own_load::drive` and the native writer
/// asked for the co-op container, and the two never met -- a silent soft lock at the boot cover.
///
/// Writing both names removes the time-of-check race outright: whichever container the runtime
/// resolves to once the mode has settled, it holds the configured source. Restamping the name is
/// byte-safe -- every flavor is the same 28 MB BND4 container.
pub fn staged_save_container_names_for(seamless_name: &str) -> Vec<String> {
    let mut names = vec![VANILLA_SAVE_CONTAINER_NAME.to_owned()];
    if !seamless_name.eq_ignore_ascii_case(VANILLA_SAVE_CONTAINER_NAME) {
        names.push(seamless_name.to_owned());
    }
    names
}

/// True when `file_name` is one of the containers this staging pass rewrites.
pub fn is_staged_save_container_name(file_name: &str, staged_names: &[&str]) -> bool {
    staged_names
        .iter()
        .any(|name| name.eq_ignore_ascii_case(file_name))
}

/// What happens to a file already sitting in a staged `<root>/<case>/<steamid>/` directory when a
/// new staging pass runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StagedEntryFate {
    /// A container this pass rewrites from the configured source, so its old bytes cannot survive.
    Rewritten,
    /// A save artifact left over from an earlier run that this pass does not rewrite: a `.bak`
    /// companion, a half-finished restore temp, a container under some other spelling. It does not
    /// correspond to the configured source and the game must never find it.
    StaleRemove,
    /// Not a save artifact (`GraphicsConfig.xml`, stray logs). Left alone.
    Keep,
}

/// Classify one existing staged directory entry against the containers this pass rewrites.
///
/// Nothing here consults mtimes. A staged file is current because this run wrote it from the
/// configured source, and stale otherwise -- the 2026-08-11 soft lock served a `.co2` written
/// 33 minutes earlier from a different source, and every timestamp involved looked plausible.
pub fn staged_entry_fate(file_name: &str, staged_names: &[&str]) -> StagedEntryFate {
    if is_staged_save_container_name(file_name, staged_names) {
        return StagedEntryFate::Rewritten;
    }
    let lower = file_name.to_ascii_lowercase();
    let save_artifact = lower.contains("er0000")
        || lower.ends_with(".sl2")
        || lower.ends_with(".co2")
        || lower.ends_with(".bak");
    if save_artifact {
        StagedEntryFate::StaleRemove
    } else {
        StagedEntryFate::Keep
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The Seamless container comes from ERSC's own config, not from a hard-coded `.co2`.
    #[test]
    fn reads_the_seamless_container_extension_out_of_ersc_settings() {
        // Verbatim shape of the shipped `ersc_settings.ini`, comment and all.
        let shipped = "[PASSWORD]\n\ncooppassword =seamless\n\n[SAVE]\n\n;Your save file extension (in the vanilla game this is .sl2). Use any alphanumeric characters (limit = 120)\nsave_file_extension = co2\n\n[LANGUAGE]\n\nmod_language_override =\n";
        assert_eq!(parse_ersc_save_file_extension(shipped), Some("co2"));
        assert_eq!(
            save_container_name_for_extension(parse_ersc_save_file_extension(shipped).unwrap()),
            "ER0000.co2"
        );

        // A user-chosen extension must flow through -- the case a hard-coded `.co2` breaks.
        assert_eq!(
            parse_ersc_save_file_extension("[SAVE]\nsave_file_extension = coop2\n"),
            Some("coop2")
        );
        // The key only counts inside `[SAVE]`.
        assert_eq!(
            parse_ersc_save_file_extension("[GAMEPLAY]\nsave_file_extension = nope\n"),
            None
        );
        // Absent, blank, commented out, or over-long -> no usable value.
        assert_eq!(parse_ersc_save_file_extension("[SAVE]\n"), None);
        assert_eq!(
            parse_ersc_save_file_extension("[SAVE]\nsave_file_extension =\n"),
            None
        );
        assert_eq!(
            parse_ersc_save_file_extension("[SAVE]\n;save_file_extension = co2\n"),
            None
        );
        assert_eq!(
            parse_ersc_save_file_extension(&format!(
                "[SAVE]\nsave_file_extension = {}\n",
                "a".repeat(MAX_SAVE_FILE_EXTENSION_LEN + 1)
            )),
            None
        );
        // A filename is built from this, so anything that could leave the directory is refused.
        for hostile in ["../../evil", "co2/x", r"co2\x", "co 2", "co.2"] {
            assert_eq!(
                parse_ersc_save_file_extension(&format!(
                    "[SAVE]\nsave_file_extension = {hostile}\n"
                )),
                None,
                "a non-alphanumeric extension must not reach a staged filename: {hostile}"
            );
        }
    }

    /// The boot default-save check accepts only the container the runtime opens.
    ///
    /// Regression for run br-20260826-190532-55e2: under Seamless the check accepted `ER0000.sl2`
    /// after the configured `ER0000.co2` read as characterless, and reported default-user-save
    /// "with no redirect" -- validating a file ersc.dll never opens. Everything downstream (the
    /// save-check hold never engaging, the menu building against an empty ProfileSummary, the
    /// disabled Continue row, the softlock) followed from that.
    #[test]
    fn boot_default_save_check_accepts_only_the_container_the_runtime_opens() {
        for extension in [DEFAULT_SEAMLESS_SAVE_FILE_EXTENSION, "coop2"] {
            let seamless_name = save_container_name_for_extension(extension);

            // Seamless: exactly one candidate, and it is ERSC's container. No `.sl2` fallback --
            // that is the fallback that made the boot answer unfalsifiable.
            assert_eq!(
                default_save_container_names_for(true, &seamless_name),
                vec![seamless_name.clone()],
                "seamless boot check must not fall back past ERSC's container (ext={extension})"
            );
            assert!(
                !default_save_container_names_for(true, &seamless_name)
                    .iter()
                    .any(|name| name.eq_ignore_ascii_case(VANILLA_SAVE_CONTAINER_NAME)),
                "the `.sl2` fallback is the bug (ext={extension})"
            );

            // Vanilla is untouched: it only ever had `.sl2`.
            assert_eq!(
                default_save_container_names_for(false, &seamless_name),
                vec![VANILLA_SAVE_CONTAINER_NAME.to_owned()]
            );

            // Whatever the boot check accepts is what the runtime writes/opens, both modes.
            for seamless in [false, true] {
                let accepted = default_save_container_names_for(seamless, &seamless_name);
                let runtime = active_save_container_name_for(seamless, &seamless_name);
                assert_eq!(accepted, vec![runtime.clone()]);
                assert!(boot_save_container_matches_runtime(
                    Some(&accepted[0]),
                    &runtime
                ));
            }
        }

        // An ERSC configured with `sl2` is the vanilla container -- one name, no duplicate.
        assert_eq!(
            default_save_container_names_for(true, VANILLA_SAVE_CONTAINER_NAME),
            vec![VANILLA_SAVE_CONTAINER_NAME.to_owned()]
        );

        // The picked-save path keeps its `.sl2` fallback: staging rewrites the name, so a picked
        // vanilla container on a Seamless launch still loads (bd er-effects-rs-h6sh).
        assert_eq!(
            active_save_container_names_for(true, "ER0000.co2"),
            vec!["ER0000.co2", "ER0000.sl2"]
        );
    }

    /// The exact 2026-08-26 mismatch, as the oracle now reports it.
    #[test]
    fn boot_container_mismatch_is_visible_to_telemetry() {
        // What the run did: accepted `.sl2` while the runtime opened `.co2`.
        assert!(!boot_save_container_matches_runtime(
            Some("ER0000.sl2"),
            "ER0000.co2"
        ));
        // What it must do now: accept the runtime's own container, or accept nothing and arm the
        // picker. Neither is a mismatch.
        assert!(boot_save_container_matches_runtime(
            Some("ER0000.co2"),
            "ER0000.co2"
        ));
        assert!(boot_save_container_matches_runtime(None, "ER0000.co2"));
        // Wine paths are case-insensitive; the oracle must not fire on case alone.
        assert!(boot_save_container_matches_runtime(
            Some("er0000.CO2"),
            "ER0000.co2"
        ));
        // A vanilla launch accepting `.sl2` is correct, not a mismatch.
        assert!(boot_save_container_matches_runtime(
            Some("ER0000.sl2"),
            "ER0000.sl2"
        ));
    }

    /// The naming rule. Whatever container the runtime resolves to once the Seamless mode has
    /// settled, staging must already have written the configured source under that name -- for the
    /// default co-op extension and for a custom one, and never varying with the source file's
    /// extension (the shape the 2026-08-11 soft lock was misdiagnosed as).
    #[test]
    fn staged_container_names_cover_every_mode_for_any_configured_extension() {
        for extension in [DEFAULT_SEAMLESS_SAVE_FILE_EXTENSION, "coop2", "sl2"] {
            let seamless_name = save_container_name_for_extension(extension);
            let staged = staged_save_container_names_for(&seamless_name);
            let staged_refs: Vec<&str> = staged.iter().map(String::as_str).collect();
            for seamless in [false, true] {
                assert!(
                    is_staged_save_container_name(
                        &active_save_container_name_for(seamless, &seamless_name),
                        &staged_refs
                    ),
                    "staging must write the container the runtime writes (ext={extension} seamless={seamless})"
                );
                for name in active_save_container_names_for(seamless, &seamless_name) {
                    assert!(
                        is_staged_save_container_name(&name, &staged_refs),
                        "staging must write every container the runtime may load: {name} (ext={extension})"
                    );
                }
            }
            // Vanilla is locked to `.sl2` whatever ERSC is configured with.
            assert_eq!(
                active_save_container_names_for(false, &seamless_name),
                vec!["ER0000.sl2"]
            );
        }

        // `.co2` is a default, not an invariant: a custom extension names a different container.
        assert_eq!(
            active_save_container_names_for(true, "ER0000.coop2"),
            vec!["ER0000.coop2", "ER0000.sl2"]
        );
        assert_eq!(
            staged_save_container_names_for("ER0000.coop2"),
            vec!["ER0000.sl2", "ER0000.coop2"]
        );
        // An ERSC configured with `sl2` is the vanilla container -- one name, never duplicated.
        assert_eq!(
            staged_save_container_names_for("ER0000.sl2"),
            vec!["ER0000.sl2"]
        );
        assert_eq!(
            active_save_container_names_for(true, "ER0000.sl2"),
            vec!["ER0000.sl2"]
        );

        let default_staged = staged_save_container_names_for("ER0000.co2");
        let default_refs: Vec<&str> = default_staged.iter().map(String::as_str).collect();
        assert!(is_staged_save_container_name("er0000.CO2", &default_refs));
        assert!(!is_staged_save_container_name(
            "ER0000.sl2.bak",
            &default_refs
        ));
        assert!(!is_staged_save_container_name("ER0001.sl2", &default_refs));
        // The staged set never depends on the source file's extension: it is the same set whether
        // the configured save was picked as a `.sl2`, a `.co2`, or anything else.
        assert_eq!(default_staged, vec!["ER0000.sl2", "ER0000.co2"]);
    }

    /// The staleness check. A container from an earlier run that this pass does not rewrite is
    /// removed, so it can never be served in place of the configured source.
    #[test]
    fn staged_entry_fate_removes_leftovers_and_keeps_non_save_files() {
        let staged = ["ER0000.sl2", "ER0000.co2"];
        assert_eq!(
            staged_entry_fate("ER0000.sl2", &staged),
            StagedEntryFate::Rewritten
        );
        assert_eq!(
            staged_entry_fate("er0000.co2", &staged),
            StagedEntryFate::Rewritten
        );

        // The 2026-08-11 leftovers: a `.bak` companion and a restore temp from earlier sessions.
        assert_eq!(
            staged_entry_fate("ER0000.co2.bak", &staged),
            StagedEntryFate::StaleRemove
        );
        assert_eq!(
            staged_entry_fate("ER0000.sl2.bak", &staged),
            StagedEntryFate::StaleRemove
        );
        assert_eq!(
            staged_entry_fate("er0000.sl2.er-save-dest-restore.tmp", &staged),
            StagedEntryFate::StaleRemove
        );
        assert_eq!(
            staged_entry_fate("ER0001.sl2", &staged),
            StagedEntryFate::StaleRemove
        );

        // A container left by a previous ERSC extension is exactly what must not survive...
        assert_eq!(
            staged_entry_fate("ER0000.coop2", &staged),
            StagedEntryFate::StaleRemove
        );
        // ...and it is kept once ERSC is configured that way.
        assert_eq!(
            staged_entry_fate("ER0000.coop2", &["ER0000.sl2", "ER0000.coop2"]),
            StagedEntryFate::Rewritten
        );

        assert_eq!(
            staged_entry_fate("GraphicsConfig.xml", &staged),
            StagedEntryFate::Keep
        );
        assert_eq!(
            staged_entry_fate("er-quickload-autoload-debug.log", &staged),
            StagedEntryFate::Keep
        );
    }
}
