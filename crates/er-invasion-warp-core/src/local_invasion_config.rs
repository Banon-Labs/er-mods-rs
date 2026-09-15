//! Loading [`LocalInvasionConfig`] from `er-invasion-warp.toml`, and RELOADING it while the game
//! runs.
//!
//! # Hot reload, and why it is content-polled rather than event-driven
//!
//! The user edits this file to change where they are hunting -- or which key marks a location --
//! mid-session, and expects the next match and the next keypress to follow the new rules. A watcher
//! API (ReadDirectoryChangesW, inotify) would be more elegant and is the wrong tool here: it needs
//! a thread and a handle owned by a DLL that can be unloaded, and it fires on events we do not care
//! about. Polling is not a compromise here; it is the cheaper correct thing.
//!
//! It polls the file's text (`er_hotkey_config::HotFile`), not its mtime, which is what it used to
//! do. mtime has one-second resolution on several filesystems -- including the kind a Wine prefix
//! sits on -- so two saves inside one second lost the second one, which reads as the edit not
//! working. And a `touch`, a re-save with no changes, or this DLL's own write-back of a mark all
//! moved mtime without changing anything, each of which was reported as a reload. That matters more
//! now than it did: a reload RESETS the key edge detectors, so a spurious one makes a key held at
//! that instant fire.
//!
//! # Parsing without a TOML dependency
//!
//! This crate is `no-std`-adjacent in spirit and is compiled into a DLL that already avoids
//! pulling weight it does not need. The schema is a handful of scalars and one string array, so it
//! is parsed directly. The parser is deliberately strict about unknown keys and bad values --
//! reporting them rather than ignoring them -- because a typo in a filter that cancels other
//! players' matches should be visible, not silently inert.
//!
//! # Composition
//!
//! [`parse_local_invasion_config`] takes the file's text and nothing else, so the same schema can
//! be read out of a shared TOML when this crate is used as a library alongside others: hand it the
//! `[local_invasion]` section's body and it behaves identically. [`SECTION_NAME`] is exported for
//! that purpose.

use crate::local_invasion::LocalInvasionConfig;

/// File name looked for next to the DLL.
pub const CONFIG_FILE_NAME: &str = "er-invasion-warp.toml";

/// Section name when this schema is embedded in a shared TOML rather than owning its own file.
pub const SECTION_NAME: &str = "local_invasion";

/// The default file written when none exists. Documented in place, because the person who needs
/// to understand these options is reading this file, not the source.
pub const DEFAULT_CONFIG_TOML: &str = r#"# er-invasion-warp-core -- local invasion filter.
#
# Seamless decides WHERE an invasion sends you, server-side, and tells the client. This filter
# reads that destination the moment it arrives -- before you have moved -- and cancels matches that
# are not where you want to be. Cancelling is safe: the session returns to idle and searching
# continues. Nothing here fakes an invasion or spoofs session state.
#
# EDITS TAKE EFFECT IMMEDIATELY. The file is re-read about once a second while the game runs; no
# restart. The log names what changed each time.

[local_invasion]

# Master switch. OFF by default -- this cancels real matches, so it has to be asked for.
enabled = false

# HUNT MODE -- ask Steam for ONE location instead of rejecting what it sends.
#
# The filter above DECLINES matches: it sees every host and cancels the ones you do not want, which
# always works but can take many tries. Hunt instead narrows the QUERY, so the answer arrives right
# the first time.
#
# The cost, and why this is separate and off by default: it filters on a key only this DLL
# publishes, so while hunt is on you will NOT see hosts who are not running it. Your friend needs
# it too. Leave it off to keep meeting everybody.
#
# A Steam filter tests ONE value and has no OR, so hunt uses the single marked location if you have
# marked exactly one, or the map you are standing in if you have marked none. Several marked
# locations cannot be expressed and hunt will say so and stay out of the way.
search_by_location = false

# Widen the query outward from where you are standing, one ring of map tiles at a time.
#
# 0 asks Steam for your exact tile only. 1 adds the eight tiles around it, 2 the ring beyond
# that, and 3 is the cap the ring builder enforces. Each query round asks for the next tile in
# turn, so the search moves instead of re-asking for a place nobody is in.
#
# This does nothing on its own. The widening runs inside the lobby-query detour that
# `steam_hooks` installs, and the tile it starts from is the one `hunt` picks -- with either of
# those off a radius is read, echoed back on the config line, and never acted on. The DLL says
# so in its log rather than leaving you to work it out.
search_radius = 0

# When the rings are spent, drop the filter and ask for everywhere.
#
# Off, the search keeps asking for the last tile rather than quietly reverting to an unfiltered
# query -- widening to a population you did not ask for is the thing hunt exists to avoid. On,
# it is the last rung of the ladder: everywhere, once nearby has been exhausted.
widen_to_anywhere = false

# Announce what the search is doing on the game's own message banner.
#
# The name is older than what it does. This build does not reject a connected invasion for being
# in the wrong place -- that cost you a connection to learn something the query could have asked
# for -- so there is no rejection to announce. What it announces now is arrival ("Invaded
# Limgrave") and, while search_radius is widening the search, which place is being asked for
# ("searching 3 of 9 nearby locations -- Stormhill").
#
# Only a CHANGE is announced. Seamless retries roughly every 20 seconds and the same line would
# otherwise be wallpaper within a minute; silence means nothing has moved since the last notice.
reject_notice = false

# ----------------------------------------------------------------------------------------------
# DIAGNOSTICS BELOW. These six install or withhold hooks so a crash can be attributed to one of
# them. They are not preferences, they are not shown in the F4 panel, and every one of them is a
# way to break the mod rather than to configure it. Leave them alone unless you are bisecting a
# crash.
# ----------------------------------------------------------------------------------------------

# Draw invasion pins on the world map.
#
# On by default -- the pins are the feature. Turning it off withholds the WorldMapViewModel
# constructor observer and the world-map GFx hook, and nothing else: the local-invasion filter,
# the warp keys and the lobby pool all still run. That makes the map path isolable on its own,
# which is what a crash that follows opening the map needs in order to be attributed.
map_pins = true

# Install the three Steam-matchmaking detours (location publishing, hunt mode, pool filter).
#
# On by default -- those features need them. These are the only detours this DLL installs at
# addresses it did not derive statically: each comes from a live ISteamMatchmaking vtable slot read
# at runtime. Turning it off withholds all three and nothing else.
steam_hooks = true

# Install the two read-only observers on Seamless Co-op's own code (the menu `show` function and
# the lobby-key builder).
#
# On by default -- the `show` observer is how this DLL learns the Seamless menu object's address,
# and the lobby-key observer reports the one string that decides whether two Seamless players can
# see each other at all. Turning it off withholds those two and nothing else: the local-invasion
# filter still judges matches and the warp keys still work.
#
# OFF BY DEFAULT since 2026-09-04, because arming them kills the game in about 25 seconds. With
# them off the same configuration ran 251s and 110s with zero fault records; with them on and
# nothing else changed it died at 24.9s, with no input given at all. The mechanism is still
# unidentified, so this is a mitigation and not a fix -- turning it on is opting into a crash.
ersc_observers = false

# Which half of that pair to install, when ersc_observers is on. Both on by default, so the master
# switch alone behaves exactly as before. They exist because the master proved the PAIR is what
# crashes and these name which one: turn the master on and exactly one of these off.
ersc_show_observer = true
ersc_lobby_key_observer = true

# The third one, and the only one that sees the option-menu object when you invade with an ITEM --
# `show` runs only when Seamless's own menu is built, and the item path never builds it. Without
# this the filter judges matches correctly and then cannot cancel them, which is what run
# br-20260908-230004-d163 did 13 times in a row.
ersc_invade_observer = true

# ----------------------------------------------------------------------------------------------
# Back to ordinary settings.
# ----------------------------------------------------------------------------------------------

# Match ONLY other players running this DLL with this option turned on.
#
# Seamless finds worlds with a `lobby_key` that is a fingerprint of your game's params and Seamless
# build -- NOT your co-op password. This rewrites that key into a pool of our own, and because one
# value drives both the search and the advertisement, the separation is symmetric: vanilla players
# cannot see you and you cannot see them.
#
# It is ABSOLUTE, not a preference. While this is on, the entire vanilla population is invisible to
# you for hosting AND for invading -- you will only ever meet other people running this DLL with
# this same option on. Turn it on for a session with friends, not permanently.
only_players_with_this_mod = false

# THE TWO USEFUL COMBINATIONS, since these switches are independent:
#
#   search_by_location = true,  only_players_with_this_mod = false
#       aim the query at one place, and still meet everybody who is there
#   search_by_location = false, only_players_with_this_mod = true
#       invade anywhere as normal, but only ever meet other people running this DLL
#
# Both together works too: only DLL users, and only at the place you are standing.

# Locations you marked, and the two lists the in-game keys write to. Both WIDEN whatever `mode`
# allows -- a marked place is always accepted, in every mode -- so you can leave mode = "exact"
# and just collect places as you visit them.
#
#   mark_key         "invade here"      -> allowed_blocks   (and clears any exclusion)
#   unmark_key       "not here"         -> blocked_blocks   (and clears any mark)
#
# The world map colours its invasion pins by these two lists, and by nothing else: chosen is the
# brightest marker, excluded the dimmest, and anything in neither list keeps the middle one. That
# is a property of the LOCATION, so the map reads the same wherever you are standing.
#
# The keys rewrite this file, so marks survive a restart. Hand-edits are equally fine; the file is
# re-read about once a second while you play.
allowed_blocks = []

# The two keys, by NAME. Insert and Delete are the defaults, but a 60% keyboard has neither -- so
# name a key you actually have instead. Recognised:
#
#   Insert Delete Home End PageUp PageDown Backspace Tab Enter Escape Space
#   Left Up Right Down PrintScreen ScrollLock Pause CapsLock
#   F1..F24, any single letter or digit ("K", "7")
#   punctuation by symbol or name: - = [ ] \ ; ' , . / `  (Minus, Equals, LeftBracket,
#     RightBracket, Backslash, Semicolon, Quote, Comma, Period, Slash, Grave)
#   keypad: KP_0..KP_9, KP_Plus, KP_Minus, KP_Multiply, KP_Divide, KP_Period
#
# Case and spacing do not matter. A raw virtual-key code works too ("0x2d"). A name this file does
# not recognise is reported in the log and the previous key stays in force -- it is never silently
# ignored, because a key that does nothing is indistinguishable from a broken feature.
mark_key = "Insert"
unmark_key = "Delete"

# The switch for `enabled` above, by NAME, from the same list. Pressing it flips the setting
# and saves the file, so the state survives a restart and the file always says what you are
# actually playing in. The banner tells you which way it went.
enable_toggle_key = "F3"

# The in-game settings panel, by NAME, from the same list. It shows every key above that is a
# setting rather than a diagnostic, and writes each change straight back here -- this file stays
# the source of truth, and the panel re-reads it, so an edit you make by hand while the panel is
# open still wins.
#
# The file is REGENERATED from the shipped template on every save, so comments you add yourself
# do not survive a change made in game. Your values do.
settings_key = "F4"

# Locations you excluded. An exclusion is the strongest thing you can say about a place: it stops
# the search from asking for that location even when it is the one you marked.
blocked_blocks = []
"#;

/// A parse problem worth telling the user about.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConfigIssue {
    /// 1-based line number in the source text.
    pub line: usize,
    /// What was wrong.
    pub message: String,
}

/// Parse result: the config plus everything questionable about it.
///
/// Issues do not prevent a config from being returned. A single bad line should not disable a
/// filter the user asked for, but it must be visible -- so the good keys apply and the bad ones
/// are reported.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParsedConfig {
    /// The configuration, with defaults for anything absent.
    pub config: LocalInvasionConfig,
    /// Problems found while parsing.
    pub issues: Vec<ConfigIssue>,
}

/// Parse `er-invasion-warp.toml` text.
///
/// Accepts the schema at top level or inside a `[local_invasion]` section, so the same function
/// serves the standalone file and the embedded-in-a-shared-TOML case. Sections other than
/// `[local_invasion]` are skipped entirely rather than misread -- when this schema lives in a
/// shared file, another crate's `[section]` keys are none of our business, and warning about them
/// would make a correct shared config look broken.
#[must_use]
pub fn parse_local_invasion_config(text: &str) -> ParsedConfig {
    parse_local_invasion_config_with_fallback(text, &LocalInvasionConfig::default())
}

/// One key binding, keeping `fallback` when the value cannot be read.
///
/// Surfaced as an issue rather than silently accepted: a player who mistypes their key otherwise
/// presses it, gets nothing, and has no way to tell that apart from the feature being broken. And
/// `fallback` rather than the built-in default because on a reload the value already in force is
/// the one that was working -- resetting a typo to F7 would move a key the player had deliberately
/// moved away from F7, which is the collision this option exists to escape.
fn key_setting(
    name: &str,
    value: &str,
    fallback: crate::keybind::VirtualKey,
    line_no: usize,
    issues: &mut Vec<ConfigIssue>,
) -> crate::keybind::VirtualKey {
    match crate::keybind::parse_key(&unquote(value)) {
        Ok(key) => key,
        Err(error) => {
            issues.push(ConfigIssue {
                line: line_no,
                message: format!(
                    "{name}: {error} -- keeping {}",
                    crate::keybind::key_name(fallback)
                ),
            });
            fallback
        }
    }
}

/// Parse, keeping `fallback`'s key BINDINGS for any key line that does not parse.
///
/// Every other setting still falls back to its own built-in default when absent or malformed,
/// which is the behaviour this file has always had. Key bindings are the exception because they
/// are the setting a reload is most likely to be editing, and the one where falling back to the
/// shipped default is actively wrong -- see [`key_setting`].
#[must_use]
pub fn parse_local_invasion_config_with_fallback(
    text: &str,
    fallback: &LocalInvasionConfig,
) -> ParsedConfig {
    let mut config = LocalInvasionConfig::default();
    let mut issues = Vec::new();
    // None = top level (ours), Some(true) = inside [local_invasion], Some(false) = someone else's.
    let mut in_our_section: Option<bool> = None;

    for (index, raw_line) in text.lines().enumerate() {
        let line_no = index + 1;
        let line = strip_comment(raw_line).trim();
        if line.is_empty() {
            continue;
        }
        if let Some(section) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            in_our_section = Some(section.trim() == SECTION_NAME);
            continue;
        }
        if in_our_section == Some(false) {
            continue; // another crate's section in a shared file
        }
        let Some((key, value)) = line.split_once('=') else {
            issues.push(ConfigIssue {
                line: line_no,
                message: format!("not a key = value line: {line:?}"),
            });
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        match key {
            "enabled" => match parse_bool(value) {
                Some(v) => config.enabled = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("enabled must be true or false, got {value:?}"),
                }),
            },
            "only_players_with_this_mod" => match parse_bool(value) {
                Some(v) => config.dll_users_only = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!(
                        "only_players_with_this_mod must be true or false, got {value:?}"
                    ),
                }),
            },
            "steam_hooks" => match parse_bool(value) {
                Some(v) => config.steam_hooks = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("steam_hooks must be true or false, got {value:?}"),
                }),
            },
            "map_pins" => match parse_bool(value) {
                Some(v) => config.map_pins = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("map_pins must be true or false, got {value:?}"),
                }),
            },
            "ersc_observers" => match parse_bool(value) {
                Some(v) => config.ersc_observers = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("ersc_observers must be true or false, got {value:?}"),
                }),
            },
            "ersc_invade_observer" => match parse_bool(value) {
                Some(v) => config.ersc_invade_observer = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("ersc_invade_observer must be true or false, got {value:?}"),
                }),
            },
            "ersc_show_observer" => match parse_bool(value) {
                Some(v) => config.ersc_show_observer = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("ersc_show_observer must be true or false, got {value:?}"),
                }),
            },
            "ersc_lobby_key_observer" => match parse_bool(value) {
                Some(v) => config.ersc_lobby_key_observer = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!(
                        "ersc_lobby_key_observer must be true or false, got {value:?}"
                    ),
                }),
            },
            "reject_notice" => match parse_bool(value) {
                Some(v) => config.reject_notice = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("reject_notice must be true or false, got {value:?}"),
                }),
            },
            "search_by_location" => match parse_bool(value) {
                Some(v) => config.hunt = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("search_by_location must be true or false, got {value:?}"),
                }),
            },
            "search_radius" => match unquote(value).parse::<u8>() {
                Ok(v) if usize::from(v) <= usize::from(crate::search_ring::MAX_RADIUS) => {
                    config.prefilter_radius = v;
                }
                Ok(v) => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!(
                        "search_radius {v} is past the {} the ring is capped at -- keeping {}",
                        crate::search_ring::MAX_RADIUS,
                        config.prefilter_radius
                    ),
                }),
                Err(_) => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("search_radius must be a whole number, got {value:?}"),
                }),
            },
            "widen_to_anywhere" => match parse_bool(value) {
                Some(v) => config.search_everywhere_when_exhausted = v,
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!("widen_to_anywhere must be true or false, got {value:?}"),
                }),
            },
            "mark_key" => {
                config.mark_key =
                    key_setting("mark_key", value, fallback.mark_key, line_no, &mut issues);
            }
            "unmark_key" => {
                config.unmark_key = key_setting(
                    "unmark_key",
                    value,
                    fallback.unmark_key,
                    line_no,
                    &mut issues,
                );
            }
            "enable_toggle_key" => {
                config.enable_toggle_key = key_setting(
                    "enable_toggle_key",
                    value,
                    fallback.enable_toggle_key,
                    line_no,
                    &mut issues,
                );
            }
            "settings_key" => {
                config.settings_key = key_setting(
                    "settings_key",
                    value,
                    fallback.settings_key,
                    line_no,
                    &mut issues,
                );
            }
            "blocked_blocks" => match parse_block_array(value) {
                Some(v) => config.blocked_blocks = v.into_iter().collect(),
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!(
                        "blocked_blocks must be an array of block ids (0x... or decimal), got \
                         {value:?}"
                    ),
                }),
            },
            "allowed_blocks" => match parse_block_array(value) {
                Some(v) => config.allowed_blocks = v.into_iter().collect(),
                None => issues.push(ConfigIssue {
                    line: line_no,
                    message: format!(
                        "allowed_blocks must be an array of block ids (0x... or decimal), got \
                         {value:?}"
                    ),
                }),
            },
            other => issues.push(ConfigIssue {
                line: line_no,
                message: format!("unknown key {other:?} -- ignored"),
            }),
        }
    }

    ParsedConfig { config, issues }
}

/// Strip a `#` comment, respecting quotes so a `#` inside a place name survives.
fn strip_comment(line: &str) -> &str {
    let bytes = line.as_bytes();
    let mut in_quotes = false;
    for (index, byte) in bytes.iter().enumerate() {
        match byte {
            b'"' => in_quotes = !in_quotes,
            b'#' if !in_quotes => return &line[..index],
            _ => {}
        }
    }
    line
}

fn unquote(value: &str) -> String {
    value.trim().trim_matches('"').to_owned()
}

fn parse_bool(value: &str) -> Option<bool> {
    match value.trim() {
        "true" => Some(true),
        "false" => Some(false),
        _ => None,
    }
}

fn array_body(value: &str) -> Option<&str> {
    value.trim().strip_prefix('[')?.strip_suffix(']')
}

fn parse_block_array(value: &str) -> Option<Vec<u32>> {
    let body = array_body(value)?;
    if body.trim().is_empty() {
        return Some(Vec::new());
    }
    let mut out = Vec::new();
    for item in body.split(',') {
        let item = item.trim();
        if item.is_empty() {
            continue;
        }
        let parsed = item.strip_prefix("0x").map_or_else(
            || item.parse::<u32>().ok(),
            |hex| u32::from_str_radix(hex, 16).ok(),
        )?;
        out.push(parsed);
    }
    Some(out)
}

/// Render a config back to TOML.
///
/// This exists because the in-game keys edit the config, and an edit that only lived in memory
/// would evaporate on the next reload -- the file is the source of truth, so a mark has to be
/// written into it. The output is the documented default file with the four value lines replaced,
/// so a user who marks a place still gets the comments explaining what they just did.
#[must_use]
pub fn render_local_invasion_config(config: &LocalInvasionConfig) -> String {
    let mut out = String::new();
    for line in DEFAULT_CONFIG_TOML.lines() {
        let key = line.split_once('=').map(|(k, _)| k.trim()).unwrap_or("");
        match key {
            "enabled" => out.push_str(&format!("enabled = {}\n", config.enabled)),
            "search_by_location" => {
                out.push_str(&format!("search_by_location = {}\n", config.hunt));
            }
            // These two were missing, and the default arm below copies the shipped file'S line
            // verbatim -- so every write silently reset them to `false`, on disk and in memory
            // (`save` adopts the re-parsed round-trip). Marking a location with Insert was enough
            // to switch the player's own banner off and drop them out of the DLL-users pool, with
            // nothing said. Any key the writer does not name is a key the writer destroys.
            "reject_notice" => {
                out.push_str(&format!("reject_notice = {}\n", config.reject_notice));
            }
            "map_pins" => {
                out.push_str(&format!("map_pins = {}\n", config.map_pins));
            }
            "steam_hooks" => {
                out.push_str(&format!("steam_hooks = {}\n", config.steam_hooks));
            }
            "search_radius" => {
                out.push_str(&format!("search_radius = {}\n", config.prefilter_radius));
            }
            "widen_to_anywhere" => {
                out.push_str(&format!(
                    "widen_to_anywhere = {}\n",
                    config.search_everywhere_when_exhausted
                ));
            }
            "ersc_observers" => {
                out.push_str(&format!("ersc_observers = {}\n", config.ersc_observers));
            }
            "ersc_show_observer" => {
                out.push_str(&format!(
                    "ersc_show_observer = {}\n",
                    config.ersc_show_observer
                ));
            }
            // Named here for the reason two keys above it were not: an unnamed key is copied from
            // the shipped template verbatim on every save, so leaving it out would silently reset
            // it to the template's value whenever the player marks a location.
            "ersc_invade_observer" => {
                out.push_str(&format!(
                    "ersc_invade_observer = {}\n",
                    config.ersc_invade_observer
                ));
            }
            "ersc_lobby_key_observer" => {
                out.push_str(&format!(
                    "ersc_lobby_key_observer = {}\n",
                    config.ersc_lobby_key_observer
                ));
            }
            "only_players_with_this_mod" => {
                out.push_str(&format!(
                    "only_players_with_this_mod = {}\n",
                    config.dll_users_only
                ));
            }
            "mark_key" => out.push_str(&format!(
                "mark_key = \"{}\"\n",
                crate::keybind::key_name(config.mark_key)
            )),
            "unmark_key" => out.push_str(&format!(
                "unmark_key = \"{}\"\n",
                crate::keybind::key_name(config.unmark_key)
            )),
            "enable_toggle_key" => out.push_str(&format!(
                "enable_toggle_key = \"{}\"\n",
                crate::keybind::key_name(config.enable_toggle_key)
            )),
            "settings_key" => out.push_str(&format!(
                "settings_key = \"{}\"\n",
                crate::keybind::key_name(config.settings_key)
            )),
            "blocked_blocks" => {
                let blocks = config
                    .blocked_blocks
                    .iter()
                    .map(|block| format!("{block:#010x}"))
                    .collect::<Vec<_>>()
                    .join(", ");
                out.push_str(&format!("blocked_blocks = [{blocks}]\n"));
            }
            "allowed_blocks" => {
                let blocks = config
                    .allowed_blocks
                    .iter()
                    .map(|block| format!("{block:#010x}"))
                    .collect::<Vec<_>>()
                    .join(", ");
                out.push_str(&format!("allowed_blocks = [{blocks}]\n"));
            }
            _ => {
                out.push_str(line);
                out.push('\n');
            }
        }
    }
    out
}

/// Tracks the config file so edits during play take effect on the next match.
///
/// # Why the file's text and not its mtime
///
/// This used to hold the last modification time. Two things went wrong with that, and the second
/// is the one that matters here:
///
/// * mtime has one-second resolution on several filesystems, including the kind a Wine prefix
///   tends to sit on. Two saves inside one second and the second one is invisible -- which reads
///   as "changing the key did nothing", indistinguishable from a broken feature.
/// * `touch`, a re-save with no edits, and this DLL's own write-back of a mark all move mtime
///   without changing anything, and every one of those was reported as a reload. A reload RESETS
///   the key edge detectors, so a key held at that instant fires again.
///
/// `er_hotkey_config::HotFile` compares the text and throttles itself to roughly one read a
/// second, which is cheaper than the per-match `stat` this replaced and cannot miss an edit.
///
/// `reload_if_changed` returns `Some` only when a reload actually happened, so the caller can log
/// the transition once instead of every time it asks.
#[derive(Debug)]
pub struct HotConfig {
    config: LocalInvasionConfig,
    file: Option<er_hotkey_config::HotFile>,
    poll_interval_ms: u64,
}

impl Default for HotConfig {
    fn default() -> Self {
        Self::with_poll_interval_ms(er_hotkey_config::reload::DEFAULT_POLL_INTERVAL_MS)
    }
}

impl HotConfig {
    /// A watcher that reads the file at most once every `poll_interval_ms`.
    ///
    /// The default is about a second, which is below the threshold at which a person editing a
    /// file would call it "not working" and far above the frame rate. Tests pass `0` so a decision
    /// can be exercised without waiting for a clock.
    #[must_use]
    pub fn with_poll_interval_ms(poll_interval_ms: u64) -> Self {
        Self {
            config: LocalInvasionConfig::default(),
            file: None,
            poll_interval_ms,
        }
    }
}

/// What a reload produced.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReloadOutcome {
    /// The configuration now in force.
    pub config: LocalInvasionConfig,
    /// Issues from this parse.
    pub issues: Vec<ConfigIssue>,
    /// True when the file went missing and defaults were restored.
    pub reverted_to_defaults: bool,
}

impl HotConfig {
    /// The configuration currently in force.
    #[must_use]
    pub fn current(&self) -> &LocalInvasionConfig {
        &self.config
    }

    /// Write a config to disk and adopt it, returning whether it read back identically.
    ///
    /// This is what the in-game mark keys call. It re-reads and re-parses what it just wrote
    /// instead of trusting the write, because the failure it guards against is silent: a renderer
    /// that emits something the parser drops would lose the user's mark while the key press looked
    /// like it worked. A `false` return means exactly that happened and is worth logging loudly.
    ///
    /// The watcher adopts the text we just wrote, so our own write is not re-reported as somebody
    /// editing the file -- which would otherwise reset the key edge detectors on every mark press.
    pub fn save(
        &mut self,
        path: &std::path::Path,
        config: &LocalInvasionConfig,
    ) -> std::io::Result<bool> {
        let rendered = render_local_invasion_config(config);
        std::fs::write(path, &rendered)?;
        let read_back = std::fs::read_to_string(path)?;
        self.watcher(path).adopt(read_back.clone());
        let round_tripped = parse_local_invasion_config_with_fallback(&read_back, config).config;
        let matched = round_tripped == *config;
        self.config = round_tripped;
        Ok(matched)
    }

    /// The file watcher, created on first use so `HotConfig::default()` needs no path.
    ///
    /// The path is fixed for the life of the process; a caller that somehow passes a different one
    /// gets a fresh watcher rather than a stale comparison against the old file's text.
    fn watcher(&mut self, path: &std::path::Path) -> &mut er_hotkey_config::HotFile {
        if self.file.as_ref().is_none_or(|hot| hot.path() != path) {
            self.file = Some(er_hotkey_config::HotFile::with_interval(
                path,
                self.poll_interval_ms,
            ));
        }
        // Just assigned above when it was absent.
        self.file.as_mut().expect("the watcher was just created")
    }

    /// Re-read the file if its text changed since the last look.
    ///
    /// Returns `Some` only on an actual change. A missing file reverts to defaults -- which means
    /// the filter switches off, because `enabled` defaults to false. Deleting the config is
    /// therefore a safe way to stop filtering mid-session, and it fails in the direction that
    /// stops cancelling other people's matches.
    ///
    /// Cheap to call often: the read itself is throttled to roughly once a second inside the
    /// watcher, so a per-frame caller pays one integer comparison in the steady state.
    pub fn reload_if_changed(&mut self, path: &std::path::Path) -> Option<ReloadOutcome> {
        // The key bindings in force are the fallback for a key line that does not parse: on a
        // reload, "what was working" is a better answer than "what shipped".
        let fallback = self.config.clone();
        match self.watcher(path).poll()? {
            er_hotkey_config::FileChange::Text(text) => {
                let parsed = parse_local_invasion_config_with_fallback(&text, &fallback);
                self.config = parsed.config.clone();
                Some(ReloadOutcome {
                    config: parsed.config,
                    issues: parsed.issues,
                    reverted_to_defaults: false,
                })
            }
            er_hotkey_config::FileChange::Missing => {
                self.config = LocalInvasionConfig::default();
                Some(ReloadOutcome {
                    config: LocalInvasionConfig::default(),
                    issues: Vec::new(),
                    reverted_to_defaults: true,
                })
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The names before the 2026-09-15 rename are gone, and must stay gone.
    ///
    /// No alias was kept, deliberately (user directive, same day): two spellings for one setting
    /// is the ambiguity the rename exists to remove, and an alias kept "for now" is how both
    /// survive for years. A file still using the old words gets defaults and a complaint about
    /// each line, which is loud and fixable -- unlike an alias, which is silent and permanent.
    #[test]
    fn the_names_before_the_rename_are_refused() {
        let old = "\
enabled = true\n\
hunt = true\n\
prefilter_radius = 2\n\
search_everywhere_when_exhausted = true\n\
dll_users_only = true\n";
        let parsed = parse_local_invasion_config(old);
        let complaints = parsed.issues.len();
        assert_eq!(
            complaints, 4,
            "each retired name should be reported as unknown, got {:?}",
            parsed.issues
        );
        assert!(
            !parsed.config.hunt,
            "the retired `hunt` must not still set anything"
        );
        assert_eq!(parsed.config.prefilter_radius, 0);
        assert!(!parsed.config.search_everywhere_when_exhausted);
        assert!(!parsed.config.dll_users_only);
    }

    /// The new names parse too, and to the same fields.
    #[test]
    fn the_names_after_the_rename_reach_the_same_fields() {
        let new = "\
enabled = true\n\
search_by_location = true\n\
search_radius = 3\n\
widen_to_anywhere = true\n\
only_players_with_this_mod = true\n";
        let parsed = parse_local_invasion_config(new);
        let (config, issues) = (parsed.config, parsed.issues);
        assert!(
            issues.is_empty(),
            "the new spellings should parse cleanly, got {issues:?}"
        );
        assert!(config.hunt);
        assert_eq!(config.prefilter_radius, 3);
        assert!(config.search_everywhere_when_exhausted);
        assert!(config.dll_users_only);
    }

    /// Every key the writer can emit must exist in the shipped template.
    ///
    /// The writer walks `DEFAULT_CONFIG_TOML` line by line and emits a value for each key it
    /// recognises. A key with a `match` arm but no template line is therefore never visited: the
    /// arm is dead, the value is dropped on every save, and the config round-trips back to its
    /// default. Nothing errors -- `save` reports that the file "did not read back as what was
    /// written" and the setting simply refuses to move.
    ///
    /// That is exactly what `prefilter_radius` did on 2026-09-15. Clicking it in the settings
    /// panel logged a successful write six times in a row while the value stayed at 0, because
    /// the arm had been added and the template line had not.
    #[test]
    fn every_writable_key_has_a_line_in_the_shipped_template() {
        let source = include_str!("local_invasion_config.rs");
        // The arms of the writer's dispatch, read out of this file rather than listed by hand --
        // a hand-kept list is the same failure one level up.
        let writer = source
            .split_once("for line in DEFAULT_CONFIG_TOML.lines()")
            .expect("the writer's loop should still be here")
            .1;
        let writer = writer.split_once("\n}").map_or(writer, |(body, _)| body);
        let mut missing = Vec::new();
        for chunk in writer.split("\n            \"").skip(1) {
            let Some((key, _)) = chunk.split_once('"') else {
                continue;
            };
            if key.is_empty() || key.contains(' ') {
                continue;
            }
            let has_line = DEFAULT_CONFIG_TOML
                .lines()
                .any(|line| line.split_once('=').is_some_and(|(k, _)| k.trim() == key));
            if !has_line {
                missing.push(key.to_owned());
            }
        }
        assert!(
            missing.is_empty(),
            "these keys can be written but have no line in the shipped template, so every save \
             drops them and the setting cannot be changed: {missing:?}"
        );
    }

    /// Every file that tells a user where to put the config must name the file the DLL opens.
    ///
    /// # What it cost to get this wrong
    ///
    /// Four user-facing files spelled the name after the crate directory rather than after
    /// [`CONFIG_FILE_NAME`] -- the same name with `-core` inserted before the extension. A user
    /// following the setup guide saves the file where nothing reads it, the DLL writes its own
    /// default alongside, and the symptom is "the mod ignores its own config": no error, no log
    /// line, nothing to search for. The wrong spelling is also the plausible one, because it is
    /// what the crate directory is called, so it comes back every time somebody writes a new doc
    /// from memory.
    ///
    /// The needle is assembled here rather than written out, so this file does not contain the
    /// string it forbids.
    #[test]
    fn every_user_facing_file_names_the_config_the_dll_actually_opens() {
        let wrong = CONFIG_FILE_NAME.replace(".toml", "-core.toml");
        for (name, text) in [
            (
                "local_invasion_config.rs",
                include_str!("local_invasion_config.rs"),
            ),
            (
                "docs/invasion-warp-second-player-setup.md",
                include_str!("../../../docs/invasion-warp-second-player-setup.md"),
            ),
            (
                "docs/er-invasion-warp.dll-pool-test.toml",
                include_str!("../../../docs/er-invasion-warp.dll-pool-test.toml"),
            ),
            (
                "docs/er-invasion-warp.invader-example.toml",
                include_str!("../../../docs/er-invasion-warp.invader-example.toml"),
            ),
        ] {
            assert!(
                !text.contains(&wrong),
                "{name} names the config {wrong:?}; the DLL opens {CONFIG_FILE_NAME:?}, so a user \
                 following it saves the file where nothing reads it"
            );
        }
    }

    #[test]
    fn every_boolean_survives_the_writer_including_the_two_it_used_to_eat() {
        // The bug this pins. `reject_notice` and `dll_users_only` had no arm in the writer, so the
        // default arm copied the shipped file's `= false` over them. Pressing Insert to mark a
        // location therefore switched the player's banner off and dropped them out of the DLL-users
        // pool, on disk and in memory, silently. The old round-trip test could not see it because
        // its fixture left both at their `false` defaults -- a fixture that only exercises defaults
        // cannot detect a writer that emits defaults.
        let config = LocalInvasionConfig {
            enabled: true,
            hunt: true,
            reject_notice: true,
            dll_users_only: true,
            ..Default::default()
        };
        let rendered = render_local_invasion_config(&config);
        let parsed = parse_local_invasion_config(&rendered);
        assert_eq!(parsed.issues, Vec::new(), "our own output must parse clean");
        assert!(
            parsed.config.reject_notice,
            "reject_notice was eaten: {rendered}"
        );
        assert!(
            parsed.config.dll_users_only,
            "dll_users_only was eaten: {rendered}"
        );
        assert_eq!(parsed.config, config);
    }

    #[test]
    fn the_writer_names_every_key_the_shipped_file_declares() {
        // Structural, so the next field added cannot repeat this. Any assignment in the shipped
        // default file that the writer does not match on is a setting the writer overwrites with
        // the shipped literal the moment anything calls it.
        let source = include_str!("local_invasion_config.rs");
        let writer = source
            .split_once("fn render_local_invasion_config")
            .expect("the writer is in this file")
            .1;
        let writer = writer.split_once("\n}").expect("the writer ends").0;
        for line in DEFAULT_CONFIG_TOML.lines() {
            let Some((key, _)) = line.split_once('=') else {
                continue;
            };
            let key = key.trim();
            if key.is_empty() || key.starts_with('#') || key.starts_with('[') {
                continue;
            }
            assert!(
                writer.contains(&format!("\"{key}\" =>")),
                "the shipped config declares `{key}` but the writer has no arm for it, so every \
                 write resets it to the shipped default"
            );
        }
    }

    #[test]
    fn block_ids_read_back_in_hex_or_decimal() {
        let parsed = parse_local_invasion_config(
            "[local_invasion]\nallowed_blocks = [0x3c353800, 251658240]\n",
        );
        assert_eq!(parsed.issues, Vec::new());
        assert_eq!(
            parsed.config.allowed_blocks,
            [0x3c35_3800, 0x0f00_0000].into_iter().collect()
        );
    }

    #[test]
    fn a_marked_block_survives_a_save_and_reload_cycle() {
        // Process-SCOPED, like every other temp path in this file. It was a fixed shared name
        // until 2026-08-31, and two copies of this binary running at once -- routine here: the
        // host `cargo test` and the wine `cargo xwin test` run the same tests, and several agents
        // run check.sh concurrently -- collided on the one path. Reproduced by running eight
        // copies under wine: two failed, one at the `save` below with
        // `Os { code: 2, kind: NotFound }` (a sibling unlinking the file mid-`File::create`) and
        // one at the `reload_if_changed` assertion (a sibling's write read, correctly, as a
        // foreign edit). Neither is a product defect: nothing but this DLL writes the real
        // config, and a genuinely foreign edit should be reported. It was test cross-talk.
        let dir = std::env::temp_dir().join(format!(
            "er-invasion-warp-config-save-test-{}",
            std::process::id()
        ));
        // Expected, not discarded. Swallowing this error is what turned "the directory could not
        // be created" into a bare `NotFound` at the write eight lines down, which reads as a bug
        // in `save`.
        std::fs::create_dir_all(&dir).expect("create the test's own temp directory");
        let path = dir.join("er-invasion-warp.toml");
        let _ = std::fs::remove_file(&path);
        // Interval 0 so the poll below actually reads. With the shipped ~1s throttle it would
        // return None without looking, and this test would pass without proving anything.
        let mut hot = HotConfig::with_poll_interval_ms(0);
        let mut config = LocalInvasionConfig {
            enabled: true,
            ..Default::default()
        };
        config.mark_block(0x3c35_3800);
        assert!(hot.save(&path, &config).expect("write"), "round trip");
        assert_eq!(
            hot.current().allowed_blocks,
            [0x3c35_3800].into_iter().collect()
        );
        // Our own write must not read as somebody else editing the file. If it did, every mark
        // press would also reset the key edge detectors -- and a key held at that instant fires.
        assert_eq!(hot.reload_if_changed(&path), None);
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir(&dir);
    }

    /// Every temp path this file's tests use must be process-SCOPED.
    ///
    /// Structural, because the fix to a shared path is invisible from a green run: the collision
    /// only shows when two copies of the binary happen to overlap, so a reintroduced fixed name
    /// passes locally, passes in the next run, and then fails once in somebody else's suite with
    /// an error that names `save` rather than the test. Measured 2026-08-31, eight concurrent wine
    /// copies of `a_marked_block_survives_a_save_and_reload_cycle`: 2 of 8 failed, in two
    /// different places. The other four temp-path tests here were already process-scoped; this
    /// asserts the property instead of leaving it to whoever writes the sixth.
    #[test]
    fn every_temp_path_in_this_file_is_process_scoped() {
        let source = include_str!("local_invasion_config.rs");
        // Spelled in two halves so this test's own source does not match the pattern it scans for
        // and fail on itself.
        let needle = concat!("env::temp_", "dir().join(");
        let mut sites = 0;
        for (index, _) in source.match_indices(needle) {
            // Enough of what follows the `.join(` to see the whole name expression, whether it is
            // written on one line or wrapped over three.
            let name = &source[index + needle.len()..source.len().min(index + needle.len() + 160)];
            sites += 1;
            assert!(
                name.contains("std::process::id()"),
                "a test temp path is shared across processes, so two concurrent copies of this \
                 binary collide on it: {}",
                &name[..name.len().min(90)]
            );
        }
        // The property is trivially true of zero sites, so the floor exists to catch the scan
        // silently ceasing to match the code it constrains. It was four for months and is three
        // since 2026-09-15, when the tests covering the deleted match-time filter went with it.
        assert!(
            sites >= 3,
            "found only {sites} temp-path sites; the scan stopped matching the code it is \
             supposed to constrain, so a shared path would now pass unexamined"
        );
    }

    /// The switch key survives a write, and the switch itself survives being written by a mark.
    ///
    /// Both halves are the same hazard the `reject_notice` / `map_pins` comment in the writer
    /// records: a key the writer does not name is a key the writer destroys, and every in-game
    /// keypress rewrites this file. A dropped `enable_toggle_key` would silently return the
    /// player to F3 after they rebound it; a dropped `enabled` would switch the filter back on
    /// the first time they marked a location.
    #[test]
    fn the_toggle_key_and_the_switch_both_survive_a_write_and_reparse() {
        let config = LocalInvasionConfig {
            enabled: false,
            enable_toggle_key: crate::keybind::parse_key("KP_Plus").expect("KP_Plus is a key"),
            ..Default::default()
        };
        let rendered = render_local_invasion_config(&config);
        assert!(
            rendered.contains("enable_toggle_key = \"KP_Plus\""),
            "{rendered}"
        );
        assert!(rendered.contains("enabled = false"), "{rendered}");
        let parsed = parse_local_invasion_config(&rendered);
        assert!(parsed.issues.is_empty(), "{:?}", parsed.issues);
        assert_eq!(parsed.config.enable_toggle_key, config.enable_toggle_key);
        assert!(!parsed.config.enabled);
    }

    /// The shipped default is F3, and the shipped file says so -- a default the template does not
    /// carry is a key the writer never emits, so the setting would be undiscoverable.
    #[test]
    fn the_shipped_config_names_the_toggle_key_and_it_is_f3() {
        assert_eq!(
            LocalInvasionConfig::default().enable_toggle_key,
            crate::keybind::VK_F3
        );
        assert!(
            DEFAULT_CONFIG_TOML.contains("enable_toggle_key = \"F3\""),
            "the shipped template must carry the key, or render_local_invasion_config never \
             writes it"
        );
    }

    /// A 60% keyboard names a key it has; that must survive the writer, since the in-game keys
    /// rewrite this file and would otherwise erase the player's choice on the first mark.
    #[test]
    fn a_renamed_key_survives_a_write_and_reparse() {
        let config = LocalInvasionConfig {
            mark_key: crate::keybind::parse_key("F7").expect("F7 is a key"),
            unmark_key: crate::keybind::parse_key("]").expect("] is a key"),
            ..Default::default()
        };
        let rendered = render_local_invasion_config(&config);
        assert!(rendered.contains("mark_key = \"F7\""), "{rendered}");
        // Rendered as the SYMBOL: it is the first name in the table and is what a player typing a
        // config sees printed on the key itself.
        assert!(rendered.contains("unmark_key = \"]\""), "{rendered}");
        let parsed = parse_local_invasion_config(&rendered);
        assert!(parsed.issues.is_empty(), "{:?}", parsed.issues);
        assert_eq!(parsed.config.mark_key, config.mark_key);
        assert_eq!(parsed.config.unmark_key, config.unmark_key);
    }

    /// A typo must be reported and must leave the previous key in force -- never silently swallowed
    /// and never a crash.
    #[test]
    fn an_unknown_key_name_is_reported_and_leaves_the_default_in_force() {
        let parsed = parse_local_invasion_config(
            "[local_invasion]\nmark_key = \"Winkey\"\nunmark_key = \"Delete\"\n",
        );
        assert_eq!(parsed.config.mark_key, crate::keybind::VK_INSERT);
        assert_eq!(parsed.config.unmark_key, crate::keybind::VK_DELETE);
        assert_eq!(parsed.issues.len(), 1, "{:?}", parsed.issues);
        assert!(
            parsed.issues[0].message.contains("Winkey"),
            "{:?}",
            parsed.issues
        );
    }

    /// The shipped file must name the defaults, or the writer has no line to replace and a marked
    /// location would silently drop the key settings.
    #[test]
    fn the_shipped_file_names_both_keys() {
        let parsed = parse_local_invasion_config(DEFAULT_CONFIG_TOML);
        assert!(parsed.issues.is_empty(), "{:?}", parsed.issues);
        assert_eq!(parsed.config.mark_key, crate::keybind::VK_INSERT);
        assert_eq!(parsed.config.unmark_key, crate::keybind::VK_DELETE);
        assert!(
            DEFAULT_CONFIG_TOML.contains("mark_key = "),
            "no mark_key line to rewrite"
        );
        assert!(
            DEFAULT_CONFIG_TOML.contains("unmark_key = "),
            "no unmark_key line to rewrite"
        );
    }

    #[test]
    fn another_crates_section_in_a_shared_toml_is_skipped_not_warned_about() {
        let parsed = parse_local_invasion_config(
            r#"
            [some_other_crate]
            unrelated_key = 42
            another = "value"

            [local_invasion]
            enabled = true
            "#,
        );
        assert_eq!(
            parsed.issues,
            Vec::new(),
            "sharing a TOML must not make a correct config look broken"
        );
        assert!(parsed.config.enabled);
    }

    #[test]
    fn an_unknown_key_is_surfaced_rather_than_silently_dropped() {
        let parsed = parse_local_invasion_config("enabbled = true\n");
        assert!(!parsed.config.enabled);
        assert_eq!(parsed.issues.len(), 1);
        assert!(parsed.issues[0].message.contains("unknown key"));
    }

    /// Two edits inside one mtime tick, both of which must land.
    ///
    /// This is the case the old mtime watcher got wrong. Several filesystems -- including the kind
    /// a Wine prefix sits on -- stamp mtime to a whole second, so a player who saves their config
    /// twice quickly had the second save silently ignored, which reads as the key change not
    /// working. No sleeping here on purpose: the point is that the watcher never needed the clock.
    #[test]
    fn two_edits_inside_one_mtime_tick_are_both_picked_up() {
        let dir =
            std::env::temp_dir().join(format!("er-invasion-warp-fast-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("er-invasion-warp.toml");
        std::fs::write(&path, "[local_invasion]\nmark_key = \"Insert\"\n").unwrap();

        let mut hot = HotConfig::with_poll_interval_ms(0);
        assert_eq!(
            hot.reload_if_changed(&path)
                .expect("first look loads")
                .config
                .mark_key,
            crate::keybind::VK_INSERT
        );
        std::fs::write(&path, "[local_invasion]\nmark_key = \"]\"\n").unwrap();
        assert_eq!(
            hot.reload_if_changed(&path)
                .expect("the second edit must land too")
                .config
                .mark_key,
            crate::keybind::parse_key("]").expect("] is a key")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The FALLBACK rule. A typo on a reload keeps the key that was working -- not the shipped
    /// default, and not nothing.
    ///
    /// Falling back to the shipped default would be actively wrong. The reason the rule exists is
    /// a player on a keyboard with no Insert key: a typo on the line they added to fix that would
    /// drag them back onto a key they cannot press, and the log would say the config loaded fine.
    #[test]
    fn a_malformed_key_on_reload_keeps_the_previous_value_not_the_shipped_default() {
        let dir =
            std::env::temp_dir().join(format!("er-invasion-warp-typo-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("er-invasion-warp.toml");
        let chosen = crate::keybind::parse_key("]").expect("] is a key");
        std::fs::write(&path, "[local_invasion]\nmark_key = \"]\"\n").unwrap();

        let mut hot = HotConfig::with_poll_interval_ms(0);
        assert_eq!(
            hot.reload_if_changed(&path)
                .expect("first look loads")
                .config
                .mark_key,
            chosen
        );

        std::fs::write(&path, "[local_invasion]\nmark_key = \"Winkey\"\n").unwrap();
        let outcome = hot
            .reload_if_changed(&path)
            .expect("the edit is a change even though it does not parse");
        assert_eq!(
            outcome.config.mark_key, chosen,
            "a typo must not drag the binding back to the shipped default"
        );
        assert_eq!(hot.current().mark_key, chosen);
        assert_eq!(outcome.issues.len(), 1, "{:?}", outcome.issues);
        let message = &outcome.issues[0].message;
        assert!(message.contains("Winkey"), "{message}");
        assert!(message.contains("keeping ]"), "{message}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The keys that remain are readable by name and survive the writer -- the mark keys rewrite
    /// this file, so a key the writer dropped would be erased on the first mark press.
    ///
    /// It covered the three warp keys until 2026-09-15, when they were removed with the feature.
    /// The writer bug it guards against was never specific to those keys:
    /// `render_local_invasion_config` walks the shipped template and copies verbatim any line it
    /// has no arm for, so a key with a parse arm and no writer arm is reset on every save.
    #[test]
    fn the_keys_are_configurable_and_survive_the_writer() {
        let parsed = parse_local_invasion_config(
            "[local_invasion]\nmark_key = \"]\"\nunmark_key = \"K\"\n\
             enable_toggle_key = \"KP_Plus\"\n",
        );
        assert_eq!(parsed.issues, Vec::new(), "{:?}", parsed.issues);
        assert_eq!(parsed.config.mark_key, 0xdd);
        assert_eq!(parsed.config.unmark_key, 0x4b);
        assert_eq!(parsed.config.enable_toggle_key, 0x6b);

        let rendered = render_local_invasion_config(&parsed.config);
        assert!(rendered.contains("mark_key = \"]\""), "{rendered}");
        assert!(rendered.contains("unmark_key = \"K\""), "{rendered}");
        assert!(
            rendered.contains("enable_toggle_key = \"KP_Plus\""),
            "{rendered}"
        );
        assert_eq!(parse_local_invasion_config(&rendered).config, parsed.config);
    }

    /// The shipped file must name every key it binds, at the historical defaults, so an existing
    /// player's muscle memory keeps working.
    #[test]
    fn the_shipped_file_names_the_keys_at_their_historical_defaults() {
        let parsed = parse_local_invasion_config(DEFAULT_CONFIG_TOML);
        assert_eq!(parsed.issues, Vec::new(), "{:?}", parsed.issues);
        assert_eq!(parsed.config.mark_key, crate::keybind::VK_INSERT);
        assert_eq!(parsed.config.unmark_key, crate::keybind::VK_DELETE);
        assert_eq!(parsed.config.enable_toggle_key, crate::keybind::VK_F3);
        assert_eq!(parsed.config.settings_key, crate::keybind::VK_F4);
    }
}
