//! Shared schemas for effect metadata and user-provided effect catalogs.
//!
//! Runtime selector catalogs are external JSONC files in `effect-catalogs/*.jsonc`
//! next to `eldenring.exe`. The runtime still accepts legacy `*.json` catalogs.
//! `data/effects.json` remains a host-side curated list used by validation/generation
//! tooling, not a runtime built-in catalog.

use std::collections::BTreeMap;

use serde::Deserialize;

/// Host-side curated effect list used by validation/generation tooling.
pub const EMBEDDED_EFFECTS_JSON: &str = include_str!("../../../data/effects.json");

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EffectsFile {
    pub calls: Vec<EffectCallSpec>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EffectHotkeysFile {
    pub hotkeys: Vec<EffectHotkeySpec>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EffectHotkeySpec {
    pub name: Option<String>,
    pub key: String,
    pub effect_id: i32,
    pub count: u32,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EffectMasterCatalog {
    pub schema_version: u32,
    pub kind: String,
    pub source: EffectMasterCatalogSource,
    pub field_index: BTreeMap<String, EffectMasterField>,
    pub effects: Vec<EffectMasterEntry>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EffectMasterCatalogSource {
    pub param: String,
    pub binder_version: String,
    pub row_count: usize,
    pub regulation_file: String,
    pub paramdef_file: String,
    pub names_file: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EffectMasterField {
    pub r#type: String,
    pub display_name: Option<String>,
    pub tags: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct EffectMasterEntry {
    pub id: i32,
    pub name: String,
    pub row_name: Option<String>,
    pub community_name: Option<String>,
    pub curated_name: Option<String>,
    pub vfx: Vec<i32>,
    pub tags: Vec<String>,
    pub fields: BTreeMap<String, serde_json::Value>,
    /// The game's own "who may receive this effect" flags: `effectTarget*` and `vowType*`.
    ///
    /// Separate from `fields` and defaulted here because the generator keeps these even when
    /// they equal the paramdef default, which `fields` drops. It is `#[serde(default)]` so a
    /// catalog generated before 2026-09-20 still parses -- `deny_unknown_fields` on this
    /// struct means the reverse is not true, and a missing arm here rejects the whole file.
    #[serde(default)]
    pub applicability: BTreeMap<String, serde_json::Value>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct EffectCallSpec {
    /// Which kind of runtime call this entry maps to. Adding a new kind means
    /// extending this enum and the matching dispatch in the DLL's
    /// `EffectCallKind`; existing data files stay valid.
    pub kind: EffectKindSpec,
    pub id: i32,
    pub name: String,
    /// Whether the call starts selected in the overlay.
    pub enabled: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EffectKindSpec {
    SpEffect,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum EffectIdCatalogEntry {
    Id(i32),
    Object(EffectIdCatalogObjectEntry),
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct EffectIdCatalogObjectEntry {
    id: i32,
    #[serde(default)]
    label: Option<String>,
    #[serde(default)]
    comment: Option<String>,
}

pub fn parse_effects_json(json: &str) -> Result<EffectsFile, serde_json::Error> {
    serde_json::from_str(&normalize_jsonc(json))
}

pub fn parse_effect_id_catalog_json(json: &str) -> Result<Vec<i32>, serde_json::Error> {
    let entries: Vec<EffectIdCatalogEntry> = serde_json::from_str(&normalize_jsonc(json))?;
    Ok(entries
        .into_iter()
        .map(|entry| match entry {
            EffectIdCatalogEntry::Id(id) => id,
            EffectIdCatalogEntry::Object(entry) => {
                let _ = (entry.label, entry.comment);
                entry.id
            }
        })
        .collect())
}

pub fn parse_effect_hotkeys_json(json: &str) -> Result<EffectHotkeysFile, serde_json::Error> {
    serde_json::from_str(&normalize_jsonc(json))
}

pub fn parse_effect_master_catalog_json(
    json: &str,
) -> Result<EffectMasterCatalog, serde_json::Error> {
    serde_json::from_str(&normalize_jsonc(json))
}

fn normalize_jsonc(json: &str) -> String {
    strip_trailing_commas(&strip_jsonc_comments(json))
}

fn strip_jsonc_comments(json: &str) -> String {
    let mut output = String::with_capacity(json.len());
    let mut chars = json.chars().peekable();
    let mut in_string = false;
    let mut escaped = false;

    while let Some(ch) = chars.next() {
        if in_string {
            output.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }

        match ch {
            '"' => {
                in_string = true;
                output.push(ch);
            }
            '/' if chars.peek() == Some(&'/') => {
                chars.next();
                output.push(' ');
                for comment_char in chars.by_ref() {
                    if comment_char == '\n' || comment_char == '\r' {
                        output.push(comment_char);
                        break;
                    }
                }
            }
            '/' if chars.peek() == Some(&'*') => {
                chars.next();
                output.push(' ');
                let mut previous = '\0';
                for comment_char in chars.by_ref() {
                    if comment_char == '\n' || comment_char == '\r' {
                        output.push(comment_char);
                    }
                    if previous == '*' && comment_char == '/' {
                        break;
                    }
                    previous = comment_char;
                }
            }
            _ => output.push(ch),
        }
    }

    output
}

fn strip_trailing_commas(json: &str) -> String {
    let chars = json.chars().collect::<Vec<_>>();
    let mut output = String::with_capacity(json.len());
    let mut in_string = false;
    let mut escaped = false;
    let mut index = 0;

    while index < chars.len() {
        let ch = chars[index];
        if in_string {
            output.push(ch);
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == '"' {
                in_string = false;
            }
            index += 1;
            continue;
        }

        match ch {
            '"' => {
                in_string = true;
                output.push(ch);
            }
            ',' => {
                let mut next = index + 1;
                while next < chars.len() && chars[next].is_whitespace() {
                    next += 1;
                }
                if next >= chars.len() || !matches!(chars[next], ']' | '}') {
                    output.push(ch);
                }
            }
            _ => output.push(ch),
        }
        index += 1;
    }

    output
}

/// Parses the compile-time embedded copy of `data/effects.json`.
pub fn embedded_effects() -> Result<EffectsFile, serde_json::Error> {
    parse_effects_json(EMBEDDED_EFFECTS_JSON)
}

/// The effects this mod may put on a player while a peer is in the session.
///
/// An allowlist rather than a blocklist, and the direction is the design. There are 11354
/// rows in `SpEffectParam` and only 810 fit to travel; a blocklist would pass everything it
/// had not heard of, which on the day the game adds rows is all of them. This way an
/// unrecognised id is refused, which is the answer that cannot hurt somebody else's game.
///
/// Embedded rather than read from the game directory because it is not a user setting: a
/// player who can delete the file could lift the restriction, and a file that can go missing
/// turns the gate into a silent no-op on exactly the machines where nobody is watching. Ids
/// only, no names or fields, so embedding it costs nine kilobytes rather than fourteen
/// megabytes.
///
/// Regenerate with `scripts/generate-pvp-allowed.py` after the catalog generator; the rules
/// that decide membership live there and in
/// `scripts/generate-effect-discriminator-catalogs.py`, and a hand edit here is reverted by
/// the next regeneration without anyone being told.
pub const EMBEDDED_PVP_ALLOWED_JSON: &str = include_str!("../../../data/pvp-allowed-effects.json");

/// Provenance of the regulation the table was derived from.
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PvpAllowedSource {
    pub param: String,
    pub binder_version: String,
    pub regulation_file: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PvpAllowedTable {
    pub schema_version: u32,
    pub kind: String,
    pub source: PvpAllowedSource,
    /// The tags that removed an otherwise cosmetic row from the list. Carried so a build can
    /// report what the generator was applying, rather than only how many rows survived it.
    pub disqualifying_tags: Vec<String>,
    /// Sorted, unique ids. Sortedness is what lets the runtime binary-search it, and a test
    /// below asserts it here rather than leaving the runtime to assume it.
    pub effects: Vec<i32>,
}

pub fn parse_pvp_allowed_json(json: &str) -> Result<PvpAllowedTable, serde_json::Error> {
    serde_json::from_str(&normalize_jsonc(json))
}

/// Parses the compile-time embedded copy of `data/pvp-allowed-effects.json`.
pub fn embedded_pvp_allowed() -> Result<PvpAllowedTable, serde_json::Error> {
    parse_pvp_allowed_json(EMBEDDED_PVP_ALLOWED_JSON)
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXPECTED_BUILT_IN_CALL_COUNT: usize = 594;
    #[test]
    fn embedded_effects_file_is_valid() {
        let effects = embedded_effects().expect("data/effects.json must parse");

        assert_eq!(effects.calls.len(), EXPECTED_BUILT_IN_CALL_COUNT);
        let player_all_black = effects
            .calls
            .iter()
            .find(|call| call.name == "Player all black")
            .expect("Player all black built-in call");
        assert_eq!(player_all_black.kind, EffectKindSpec::SpEffect);
        assert!(!player_all_black.enabled);
        assert!(effects.calls.iter().all(|call| !call.enabled));
    }

    #[test]
    fn embedded_ids_are_unique() {
        let effects = embedded_effects().expect("data/effects.json must parse");
        let mut ids: Vec<i32> = effects.calls.iter().map(|call| call.id).collect();
        ids.sort_unstable();
        ids.dedup();

        assert_eq!(ids.len(), effects.calls.len(), "duplicate effect IDs");
    }

    #[test]
    fn parses_effect_master_catalog_schema() {
        let master = parse_effect_master_catalog_json(
            r#"{
                "schema_version": 1,
                "kind": "sp_effect_master_catalog",
                "source": {
                    "param": "SpEffectParam",
                    "binder_version": "7",
                    "row_count": 1,
                    "regulation_file": "regulation.bin",
                    "paramdef_file": "SpEffect.xml",
                    "names_file": "SpEffectParam.txt"
                },
                "field_index": {
                    "sightSearchEnemyRate": {
                        "type": "f32",
                        "display_name": "Sight Search Enemy Rate",
                        "tags": ["ai.perception"]
                    }
                },
                "effects": [
                    {
                        "id": 20004380,
                        "name": "Stealth",
                        "row_name": null,
                        "community_name": null,
                        "curated_name": null,
                        "vfx": [],
                        "tags": ["ai.perception.zero"],
                        "fields": {"sightSearchEnemyRate": 0}
                    }
                ]
            }"#,
        )
        .expect("effect master catalog schema must parse");

        assert_eq!(master.schema_version, 1);
        assert_eq!(master.kind, "sp_effect_master_catalog");
        assert_eq!(master.source.param, "SpEffectParam");
        assert_eq!(master.effects.len(), 1);
        assert_eq!(master.effects[0].id, 20004380);
        assert_eq!(
            master.effects[0].fields.get("sightSearchEnemyRate"),
            Some(&serde_json::json!(0))
        );
        assert!(
            master.effects[0]
                .tags
                .iter()
                .any(|tag| tag == "ai.perception.zero")
        );
    }

    #[test]
    fn user_effect_catalogs_are_plain_id_lists() {
        let ids = parse_effect_id_catalog_json("[18570, 20004380]").expect("plain ID list parses");
        assert_eq!(ids, vec![18570, 20004380]);
    }

    #[test]
    fn user_effect_catalogs_accept_jsonc_comments_and_trailing_commas() {
        let ids = parse_effect_id_catalog_json(
            r#"[
                8355,      // Deathblight network test
                20010719,  // VFX 20050101
            ]"#,
        )
        .expect("JSONC ID list parses");
        assert_eq!(ids, vec![8355, 20010719]);
    }

    #[test]
    fn user_effect_catalogs_accept_object_entries_with_human_notes() {
        let ids = parse_effect_id_catalog_json(
            r#"[
                { "id": 8355, "label": "Deathblight", "comment": "network test" },
                20010719,
            ]"#,
        )
        .expect("mixed JSONC ID/object list parses");
        assert_eq!(ids, vec![8355, 20010719]);
    }

    #[test]
    fn jsonc_comment_markers_inside_strings_are_preserved() {
        let parsed = parse_effect_id_catalog_json(
            r#"[
                { "id": 8355, "label": "not // a comment", "comment": "not /* a comment */" }
            ]"#,
        )
        .expect("comment markers inside strings parse");
        assert_eq!(parsed, vec![8355]);
    }

    #[test]
    fn parses_effect_hotkeys_file() {
        let parsed = parse_effect_hotkeys_json(
            r#"{
                "hotkeys": [
                    {
                        "name": "deathblight self test",
                        "key": "numpad_multiply",
                        "effect_id": 8355,
                        "count": 1
                    }
                ]
            }"#,
        )
        .expect("effect hotkeys file must parse");

        assert_eq!(parsed.hotkeys.len(), 1);
        assert_eq!(parsed.hotkeys[0].key, "numpad_multiply");
        assert_eq!(parsed.hotkeys[0].effect_id, 8355);
        assert_eq!(parsed.hotkeys[0].count, 1);
    }

    #[test]
    fn rejects_unknown_hotkey_fields() {
        assert!(
            parse_effect_hotkeys_json(
                r#"{"hotkeys":[{"key":"numpad_multiply","effect_id":8355,"count":1,"extra":true}]}"#
            )
            .is_err()
        );
    }

    #[test]
    fn embedded_pvp_allowlist_parses_and_is_sorted() {
        let table = embedded_pvp_allowed().expect("embedded pvp allowlist must parse");

        assert_eq!(table.kind, "pvp_allowed_effects");
        assert_eq!(table.schema_version, 1);
        assert!(
            !table.effects.is_empty(),
            "an empty allowlist refuses every effect in multiplayer while still parsing -- \
             legal, fail-closed, and almost certainly a broken generator run"
        );
        assert!(
            table.effects.len() < 2000,
            "the allowlist is a small cosmetic subset of 11354 rows; a large one means the \
             visuals-only predicate stopped discriminating"
        );
        let mut sorted = table.effects.clone();
        sorted.sort_unstable();
        sorted.dedup();
        assert_eq!(
            table.effects, sorted,
            "ids must be sorted and unique for a binary search"
        );
        assert!(
            !table.disqualifying_tags.is_empty(),
            "a build that records no disqualifying tags cannot say what it filtered"
        );
    }

    #[test]
    fn pvp_allowlist_rejects_unknown_fields() {
        assert!(
            parse_pvp_allowed_json(
                r#"{"schema_version":1,"kind":"pvp_allowed_effects","source":{"param":"p",
                    "binder_version":"1","regulation_file":"r"},"disqualifying_tags":[],
                    "effects":[1],"extra":true}"#
            )
            .is_err()
        );
    }

    #[test]
    fn master_entry_keeps_applicability_and_tolerates_its_absence() {
        let with = parse_effect_master_catalog_json(
            r#"{"schema_version":1,"kind":"k","source":{"param":"p","binder_version":"b",
                "row_count":1,"regulation_file":"r","paramdef_file":"d","names_file":"n"},
                "field_index":{},"effects":[{"id":7,"name":"","row_name":null,
                "community_name":null,"curated_name":null,"vfx":[],"tags":["pvp.status_icon"],
                "fields":{},"applicability":{"effectTargetPlayer":0}}]}"#,
        )
        .expect("catalog with applicability must parse");
        assert_eq!(with.effects[0].applicability.len(), 1);

        let without = parse_effect_master_catalog_json(
            r#"{"schema_version":1,"kind":"k","source":{"param":"p","binder_version":"b",
                "row_count":1,"regulation_file":"r","paramdef_file":"d","names_file":"n"},
                "field_index":{},"effects":[{"id":7,"name":"","row_name":null,
                "community_name":null,"curated_name":null,"vfx":[],"tags":[],"fields":{}}]}"#,
        )
        .expect("a catalog generated before applicability existed must still parse");
        assert!(without.effects[0].applicability.is_empty());
    }

    #[test]
    fn rejects_unknown_kinds() {
        const UNKNOWN_KIND_JSON: &str =
            r#"{ "calls": [ { "kind": "warp", "id": 1, "name": "x", "enabled": true } ] }"#;

        assert!(parse_effects_json(UNKNOWN_KIND_JSON).is_err());
    }
}
