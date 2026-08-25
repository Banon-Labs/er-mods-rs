//! The `?i=` document -- the planner's character object, written rather than read.
//!
//! [`crate::model`] is the mirror image of `er_build_import_core::model`: that one models the
//! *subset* a reader needs and ignores everything else, because an upstream field addition
//! must not fail a parse. A writer cannot afford the same laxity. The planner's
//! `importState` merges the incoming object into the live character with a helper that sets
//! any key **missing from the source** to `undefined`, so a key this crate omits is not
//! defaulted -- it is erased, and the planner then reads `undefined.slots` and dies. Every
//! key of the planner's own `makeDefault()` is therefore modelled here, defaulted here, and
//! always emitted.
//!
//! Two keys are omitted on purpose, and only these two: `computed` (the planner recomputes
//! it from scratch on its next save) and `activeEffects` (read with `?.`, so `undefined` is
//! a value it already handles). Both are large, both are derived, and neither survives a
//! round trip anyway.
//!
//! The defaults below are transcribed from the live bundle's
//! `static makeDefault(e=0,t=!1)`, not invented.

use serde::Serialize;
use std::collections::BTreeMap;

/// Planner schema version stamped into every document this crate writes.
///
/// The planner runs `migrateCharacter` over an imported document, and the migrations it
/// applies are chosen by this field. Stamping the version the site currently ships means
/// every migration is a no-op, which is the only state in which what we wrote is what the
/// planner ends up holding. Read out of the live bundle (`di = "3.12.2"`).
pub const PLANNER_VERSION: &str = "3.12.2";

/// Armament upgrade level `makeDefault` uses when it is given no rune level.
pub const DEFAULT_WEAPON_UPGRADE: u16 = 25;

/// Rune levels per armament upgrade level in `makeDefault`'s level-derived form,
/// `min(level / 5, 25)`. Not a game rule -- the planner's own guess at what a build of that
/// level would plausibly be carrying.
pub const RUNE_LEVELS_PER_UPGRADE: u16 = 5;

/// The physick flask has exactly two tear slots, and the planner always writes both
/// (`crystalTears: [null, null]`) rather than a shorter array.
pub const CRYSTAL_TEAR_SLOTS: usize = 2;

/// The name of the one set every category starts with.
pub const DEFAULT_SET_NAME: &str = "Default";

/// A whole planner character, as `?i=` carries it.
///
/// Field order is `makeDefault`'s order so the two can be diffed by eye; JSON object order
/// is not significant to the planner.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct BuildExportDoc {
    /// A link the author attached to the build. Not the share link -- free text.
    pub build_url: String,
    /// The author's notes.
    pub description: String,
    /// Server-side share id, i.e. the `?b=` value. Empty for a build that was never stored.
    pub id: String,
    /// Armaments. Named `inventory` upstream, and it is only ever weapons.
    pub inventory: SlotList,
    /// Talismans.
    pub talismans: SlotList,
    /// The build's display name.
    pub name: String,
    /// Rune level and the eight attributes.
    pub stats: Stats,
    /// Sorceries and incantations, in memorisation order.
    ///
    /// Deliberately [`SpellList`] and not [`SlotList`]: `makeDefault` gives spells
    /// `{slots: []}` with **no** `sorting` key, and emitting one would be inventing a field.
    pub spells: SpellList,
    /// Armour, one list per body part.
    pub protectors: Protectors,
    /// Consumables, ammunition, physick tears and flask allocation.
    pub items: Items,
    /// Starting class, or `null` when the author picked none.
    pub character_class: Option<String>,
    /// Equipped great rune, by name.
    ///
    /// The one key here that `makeDefault()` does **not** write, and it is still required.
    /// The stored payload carries it at the top level (verified on build `af97a9da874151`,
    /// where it is `null`), `er_build_import_core::model::BuildDoc` reads it, and the planner reads
    /// it as `this.character.greatRune && tools.get(...)` -- a guarded read, so absent is
    /// safe. Exporting without the key at all would silently drop the equipped rune on the
    /// way back into the game, which is why it is modelled; `null` would merely be noise,
    /// which is why it is skipped rather than written when unset.
    #[serde(rename = "greatRune", skip_serializing_if = "Option::is_none")]
    pub great_rune: Option<String>,
    /// Schema version; see [`PLANNER_VERSION`].
    pub version: String,
    /// Cloud account that stored the build, or `null` for a local one.
    pub author: Option<Author>,
    /// Upgrade level applied to any armament slot that does not override it.
    pub weapon_upgrade: u16,
    /// Whether the build two-hands its main armament.
    #[serde(rename = "is2h")]
    pub two_handing: bool,
    /// PvE mode, which changes which effects the planner considers active.
    #[serde(rename = "isPvE")]
    pub pve: bool,
    /// Which view each pane of the planner UI is showing.
    pub views: Views,
    /// Conditional-effect toggles.
    pub conditions: Conditions,
    /// Named equipment sets. Every category always has at least the active `Default`.
    pub sets: Sets,
    /// Free-text tags.
    pub tags: Vec<String>,
    /// User-authored effects. Shape is the planner's business, so it stays untyped.
    pub custom_effects: Vec<serde_json::Value>,
}

impl Default for BuildExportDoc {
    /// `makeDefault(0, false)`.
    fn default() -> Self {
        Self {
            build_url: String::new(),
            description: String::new(),
            id: String::new(),
            inventory: SlotList::default(),
            talismans: SlotList::default(),
            name: String::new(),
            stats: Stats::default(),
            spells: SpellList::default(),
            protectors: Protectors::default(),
            items: Items::default(),
            character_class: None,
            great_rune: None,
            version: PLANNER_VERSION.to_string(),
            author: None,
            weapon_upgrade: DEFAULT_WEAPON_UPGRADE,
            two_handing: false,
            pve: false,
            views: Views::default(),
            conditions: Conditions::default(),
            sets: Sets::default(),
            tags: Vec::new(),
            custom_effects: Vec::new(),
        }
    }
}

impl BuildExportDoc {
    /// `makeDefault(level, pve)` -- a default document for a character of a given rune level.
    ///
    /// Worth having as its own constructor rather than leaving callers to set the two fields:
    /// the level does not only land in `stats.rl`, it also *derives* `weaponUpgrade`, and a
    /// caller who set `rl` alone would silently ship every armament at +25.
    pub fn with_level(level: i64, pve: bool) -> Self {
        let mut doc = Self {
            weapon_upgrade: weapon_upgrade_for_level(level),
            pve,
            ..Self::default()
        };
        doc.stats.rune_level = level;
        doc
    }
}

/// The planner's `weaponUpgrade` derivation: `(level > 0 ? min(level / 5, 25) : 25) | 0`.
///
/// The `| 0` is a truncation, and JavaScript's `/` is real division, so this is a floor.
pub fn weapon_upgrade_for_level(level: i64) -> u16 {
    if level <= 0 {
        return DEFAULT_WEAPON_UPGRADE;
    }
    let derived = level / i64::from(RUNE_LEVELS_PER_UPGRADE);
    let capped = derived.min(i64::from(DEFAULT_WEAPON_UPGRADE));
    // `capped` is in `1..=DEFAULT_WEAPON_UPGRADE` here, so the narrowing cannot lose anything.
    capped as u16
}

/// Rune level and the eight attributes.
///
/// Every key is renamed because the planner's are abbreviations, and one of them is a trap:
/// see [`Stats::endurance`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
pub struct Stats {
    /// Arcane.
    #[serde(rename = "arc")]
    pub arcane: i64,
    /// Dexterity.
    #[serde(rename = "dex")]
    pub dexterity: i64,
    /// Faith.
    #[serde(rename = "fth")]
    pub faith: i64,
    /// Intelligence.
    #[serde(rename = "int")]
    pub intelligence: i64,
    /// Mind.
    #[serde(rename = "mnd")]
    pub mind: i64,
    /// Strength.
    #[serde(rename = "str")]
    pub strength: i64,
    /// Vigour.
    #[serde(rename = "vig")]
    pub vigor: i64,
    /// **Endurance**, despite the key.
    ///
    /// `vit` reads as Vitality -- a stat Elden Ring does not have, and a name Dark Souls used
    /// for the health stat, i.e. for what Elden Ring calls Vigour. So the intuitive misreading
    /// puts this value in the wrong slot *and* leaves endurance unset. The planner's key set is
    /// `{arc, dex, fth, int, mnd, str, vig, vit}`: eight keys for eight attributes, with no
    /// `end`, which only balances if `vit` is endurance. Verified against the game side in
    /// `er-build-import-runtime` (`stats[STAT_ENDURANCE] = want("vit")`) and recorded in bd
    /// `planner-vit-key-IS-endurance-verified-2026-08-22`.
    #[serde(rename = "vit")]
    pub endurance: i64,
    /// Rune level.
    #[serde(rename = "rl")]
    pub rune_level: i64,
}

/// A category's slot list plus how the planner is sorting it on screen.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct SlotList {
    /// The slots themselves.
    pub slots: Vec<Slot>,
    /// Display sort order. Cosmetic, but part of the key set, so it is always written.
    pub sorting: Sorting,
}

impl SlotList {
    /// A list holding exactly these slots, sorted the default way.
    pub fn new(slots: Vec<Slot>) -> Self {
        Self {
            slots,
            sorting: Sorting::default(),
        }
    }
}

/// Spells, which alone among the categories carry no `sorting` key.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct SpellList {
    /// Memorised spells, in memorisation order.
    pub slots: Vec<Slot>,
}

impl SpellList {
    /// A spell list holding exactly these slots.
    pub fn new(slots: Vec<Slot>) -> Self {
        Self { slots }
    }
}

/// How a category is sorted on screen.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Sorting {
    /// `"asc"` or `"desc"`.
    pub direction: String,
    /// `"acquisition"` and friends.
    pub method: String,
}

/// `makeDefault`'s sort direction.
pub const DEFAULT_SORT_DIRECTION: &str = "asc";
/// `makeDefault`'s sort method.
pub const DEFAULT_SORT_METHOD: &str = "acquisition";

impl Default for Sorting {
    fn default() -> Self {
        Self {
            direction: DEFAULT_SORT_DIRECTION.to_string(),
            method: DEFAULT_SORT_METHOD.to_string(),
        }
    }
}

/// One item in a build.
///
/// The optional fields are skipped when `None` rather than written as `null`, because the
/// planner distinguishes them: a *carried but unequipped* armament has no `equipIndex` key at
/// all, so `Some(i)` means equipped and `None` means carried. Writing `equipIndex: null`
/// would be a third state neither the planner nor `er-build-import-core` reads.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct Slot {
    /// The item's display name -- the only identifier the payload carries.
    pub name: String,
    /// Position within its list.
    pub order: i64,
    /// Per-slot upgrade level overriding [`BuildExportDoc::weapon_upgrade`].
    #[serde(skip_serializing_if = "Option::is_none")]
    pub upgrade: Option<u16>,
    /// Affinity, e.g. `"Occult"`. Absent means `"Standard"`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub infusion: Option<String>,
    /// Ash of war on this armament, if any.
    #[serde(rename = "weaponArt", skip_serializing_if = "Option::is_none")]
    pub weapon_art: Option<String>,
    /// Indices of the named sets this slot is equipped in. Observed as `[1]`, `[2]`, ...
    #[serde(rename = "equipSet", skip_serializing_if = "Option::is_none")]
    pub equip_set: Option<Vec<u32>>,
    /// Present when the slot is actually equipped, giving the equip position.
    #[serde(rename = "equipIndex", skip_serializing_if = "Option::is_none")]
    pub equip_index: Option<u32>,
}

impl Slot {
    /// A carried, unequipped item.
    pub fn carried(name: impl Into<String>, order: i64) -> Self {
        Self {
            name: name.into(),
            order,
            ..Self::default()
        }
    }

    /// Mark this slot equipped at `index`, in the set at the same index.
    ///
    /// Both keys are set together because the planner writes them together: `equipIndex`
    /// without a matching `equipSet` describes an item equipped in no set at all.
    pub fn equipped_at(mut self, index: u32) -> Self {
        self.equip_set = Some(vec![index]);
        self.equip_index = Some(index);
        self
    }

    /// Set the affinity.
    pub fn with_infusion(mut self, infusion: impl Into<String>) -> Self {
        self.infusion = Some(infusion.into());
        self
    }

    /// Set the ash of war.
    pub fn with_weapon_art(mut self, art: impl Into<String>) -> Self {
        self.weapon_art = Some(art.into());
        self
    }

    /// Override this slot's upgrade level.
    pub fn with_upgrade(mut self, upgrade: u16) -> Self {
        self.upgrade = Some(upgrade);
        self
    }

    /// Whether this slot is equipped rather than merely carried.
    pub fn is_equipped(&self) -> bool {
        self.equip_index.is_some()
    }
}

/// Armour, one list per body part.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct Protectors {
    /// Gauntlets.
    pub arms: SlotList,
    /// Chest armour.
    pub body: SlotList,
    /// Helms.
    pub head: SlotList,
    /// Greaves.
    pub legs: SlotList,
}

/// Consumables, ammunition, physick tears and flask allocation.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Items {
    /// Arrows and bolts, keyed by name. Shape is the planner's business, so it stays untyped.
    pub ammo: BTreeMap<String, serde_json::Value>,
    /// Consumable and crafting items.
    pub tools: SlotList,
    /// Physick tears; entries are `null` when a slot is left empty. Always
    /// [`CRYSTAL_TEAR_SLOTS`] long.
    #[serde(rename = "crystalTears")]
    pub crystal_tears: Vec<Option<String>>,
    /// Flask allocation.
    pub flasks: Flasks,
}

impl Default for Items {
    fn default() -> Self {
        Self {
            ammo: BTreeMap::new(),
            tools: SlotList::default(),
            crystal_tears: vec![None; CRYSTAL_TEAR_SLOTS],
            flasks: Flasks::default(),
        }
    }
}

/// Flask allocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Flasks {
    /// Flasks allocated to Crimson Tears.
    pub crimson: u32,
    /// Flasks allocated to Cerulean Tears.
    pub cerulean: u32,
    /// Total flasks, i.e. Golden Seeds spent.
    pub total: u32,
    /// Flask potency, i.e. Sacred Tears drunk.
    pub level: u32,
}

impl Default for Flasks {
    /// `makeDefault`'s allocation: ten crimson, four cerulean, twelve sacred tears.
    fn default() -> Self {
        Self {
            crimson: 10,
            cerulean: 4,
            total: 14,
            level: 12,
        }
    }
}

/// Which view each pane of the planner UI is showing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Views {
    /// The overview pane. `makeDefault` picks between `"build-planner"` and `"swappa"`
    /// behind a feature flag; the former is the current default.
    pub overview: String,
}

/// `makeDefault`'s overview view.
pub const DEFAULT_OVERVIEW_VIEW: &str = "build-planner";

impl Default for Views {
    fn default() -> Self {
        Self {
            overview: DEFAULT_OVERVIEW_VIEW.to_string(),
        }
    }
}

/// Conditional-effect toggles.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct Conditions {
    /// Effects the author has forced on. Shape is the planner's business.
    #[serde(rename = "forEffects")]
    pub for_effects: Vec<serde_json::Value>,
    /// Whether physick tear effects count as active.
    #[serde(rename = "crystalTears")]
    pub crystal_tears: bool,
}

/// Named equipment sets, one list per category.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Sets {
    /// Armament sets.
    pub weapons: Vec<SetEntry>,
    /// Talisman sets.
    pub talismans: Vec<SetEntry>,
    /// Armour sets.
    pub protectors: Vec<SetEntry>,
}

impl Default for Sets {
    fn default() -> Self {
        Self {
            weapons: vec![SetEntry::default()],
            talismans: vec![SetEntry::default()],
            protectors: vec![SetEntry::default()],
        }
    }
}

/// One named equipment set.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct SetEntry {
    /// The set's display name.
    pub name: String,
    /// Whether this is the set currently being edited.
    pub active: bool,
}

impl Default for SetEntry {
    fn default() -> Self {
        Self {
            name: DEFAULT_SET_NAME.to_string(),
            active: true,
        }
    }
}

/// The cloud account a stored build belongs to.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Author {
    /// Account uuid.
    pub id: String,
    /// Display name.
    pub name: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every key `makeDefault` writes, transcribed from the live bundle. `computed` and
    /// `activeEffects` are absent from that list because `makeDefault` does not write them
    /// either -- they are populated later, by `populateComputedValues`. Nor is `greatRune`,
    /// which the planner only writes once a rune is equipped; see
    /// [`super::BuildExportDoc::great_rune`].
    const MAKE_DEFAULT_KEYS: &[&str] = &[
        "buildUrl",
        "description",
        "id",
        "inventory",
        "talismans",
        "name",
        "stats",
        "spells",
        "protectors",
        "items",
        "characterClass",
        "version",
        "author",
        "weaponUpgrade",
        "is2h",
        "isPvE",
        "views",
        "conditions",
        "sets",
        "tags",
        "customEffects",
    ];

    fn as_object(doc: &BuildExportDoc) -> serde_json::Map<String, serde_json::Value> {
        match serde_json::to_value(doc).expect("a plain data document always serialises") {
            serde_json::Value::Object(map) => map,
            other => panic!("document serialised to {other:?}, not an object"),
        }
    }

    #[test]
    fn default_document_carries_exactly_the_planner_default_key_set() {
        let map = as_object(&BuildExportDoc::default());
        let mut got: Vec<&str> = map.keys().map(String::as_str).collect();
        let mut want = MAKE_DEFAULT_KEYS.to_vec();
        got.sort_unstable();
        want.sort_unstable();
        assert_eq!(got, want);
    }

    #[test]
    fn great_rune_is_absent_by_default_and_present_once_set() {
        // Absent, not null: the planner's read is guarded, and `makeDefault` omits the key.
        assert!(!as_object(&BuildExportDoc::default()).contains_key("greatRune"));

        let doc = BuildExportDoc {
            great_rune: Some("Great Rune of the Unborn".to_string()),
            ..BuildExportDoc::default()
        };
        assert_eq!(as_object(&doc)["greatRune"], "Great Rune of the Unborn");
    }

    #[test]
    fn derived_values_are_never_written() {
        let map = as_object(&BuildExportDoc::default());
        assert!(!map.contains_key("computed"));
        assert!(!map.contains_key("activeEffects"));
    }

    #[test]
    fn spells_carry_no_sorting_key() {
        let map = as_object(&BuildExportDoc::default());
        let spells = map["spells"].as_object().expect("spells is an object");
        assert_eq!(spells.keys().collect::<Vec<_>>(), vec!["slots"]);
    }

    #[test]
    fn every_other_category_carries_sorting() {
        let map = as_object(&BuildExportDoc::default());
        for key in ["inventory", "talismans"] {
            assert!(map[key].get("sorting").is_some(), "{key} lost its sorting");
        }
        for part in ["arms", "body", "head", "legs"] {
            assert!(map["protectors"][part].get("sorting").is_some());
        }
        assert!(map["items"]["tools"].get("sorting").is_some());
    }

    #[test]
    fn stat_keys_are_the_planner_abbreviations() {
        let map = as_object(&BuildExportDoc::default());
        let mut keys: Vec<&str> = map["stats"]
            .as_object()
            .expect("stats is an object")
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            vec!["arc", "dex", "fth", "int", "mnd", "rl", "str", "vig", "vit"]
        );
    }

    #[test]
    fn endurance_is_written_to_vit() {
        let doc = BuildExportDoc {
            stats: Stats {
                endurance: 40,
                vigor: 60,
                ..Stats::default()
            },
            ..BuildExportDoc::default()
        };
        let map = as_object(&doc);
        assert_eq!(map["stats"]["vit"], 40);
        assert_eq!(map["stats"]["vig"], 60);
    }

    #[test]
    fn unset_slot_options_are_absent_rather_than_null() {
        let doc = BuildExportDoc {
            inventory: SlotList::new(vec![Slot::carried("Dryleaf Arts", 0)]),
            ..BuildExportDoc::default()
        };
        let map = as_object(&doc);
        let slot = map["inventory"]["slots"][0]
            .as_object()
            .expect("a slot is an object");
        assert_eq!(slot.keys().collect::<Vec<_>>(), vec!["name", "order"]);
    }

    #[test]
    fn equipping_a_slot_writes_both_keys() {
        let slot = Slot::carried("Silver Tear Mask", 1).equipped_at(1);
        assert!(slot.is_equipped());
        let value = serde_json::to_value(&slot).expect("a slot always serialises");
        assert_eq!(value["equipIndex"], 1);
        assert_eq!(value["equipSet"], serde_json::json!([1]));
    }

    #[test]
    fn weapon_upgrade_follows_the_planner_derivation() {
        assert_eq!(weapon_upgrade_for_level(0), DEFAULT_WEAPON_UPGRADE);
        assert_eq!(weapon_upgrade_for_level(-5), DEFAULT_WEAPON_UPGRADE);
        assert_eq!(weapon_upgrade_for_level(1), 0);
        assert_eq!(weapon_upgrade_for_level(5), 1);
        assert_eq!(weapon_upgrade_for_level(9), 1);
        assert_eq!(weapon_upgrade_for_level(60), 12);
        assert_eq!(weapon_upgrade_for_level(125), 25);
        assert_eq!(weapon_upgrade_for_level(713), DEFAULT_WEAPON_UPGRADE);
    }

    #[test]
    fn with_level_sets_the_level_and_the_derived_upgrade() {
        let doc = BuildExportDoc::with_level(60, true);
        assert_eq!(doc.stats.rune_level, 60);
        assert_eq!(doc.weapon_upgrade, 12);
        assert!(doc.pve);
    }

    #[test]
    fn physick_always_has_two_tear_slots() {
        let map = as_object(&BuildExportDoc::default());
        assert_eq!(
            map["items"]["crystalTears"],
            serde_json::json!([null, null]),
        );
    }
}
