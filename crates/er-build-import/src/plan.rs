//! Turning a parsed build into concrete item grants.
//!
//! # The grant record
//!
//! The layout below is not invented here: it is the record the community's
//! long-standing `ItemGib` routine consumes, recovered from the planner's own
//! Cheat Engine exporter (`yF`), which emits per item
//! `dd <id+upgrade> <quantity> <reinforceLv:16><0:16> <weaponSkill>` and
//! terminates the array with `00000000 00000000 00000000 FFFFFFFF`.
//!
//! Three details are easy to get wrong and are pinned by tests:
//!
//! 1. The affinity is an **offset added into the item id** (`Occult` = +1200),
//!    not an index and not a separate field.
//! 2. The upgrade level goes in **both** places, exactly as the record above spells
//!    it: folded into the id (`<id+upgrade>`) AND passed as the separate 16-bit
//!    `reinforceLv`. The id is the half that is actually read back -- see
//!    [`armament_item_id`] for the measurement. `EquipParamWeapon::GetEntry`
//!    normalises to `(paramId / 100) * 100` because the last two digits are the
//!    LEVEL, not because they are noise; setting only `CSWepGaitemIns::reinforcement`
//!    yields a `+0` weapon whose `GetReinforcement` still answers 25.
//! 3. `weaponSkill` is the ash of war's **`EquipParamGem` row**, re-tagged with
//!    the game's gem category (see [`GEM_ITEM_CATEGORY`]). The planner's own
//!    database tags ashes with nibble 2, which is the planner's convention and
//!    not the game's, so the nibble is REPLACED rather than kept.

use crate::catalog::{Catalog, Entry, Kind};
use crate::model::{BuildDoc, Slot};

/// Sentinel meaning "leave this armament's default skill alone".
pub const NO_SKILL: u32 = 0xFFFF_FFFF;

/// The game's item-category nibble for `EquipParamGem`, i.e. for an ash of war.
///
/// Not a "flag", and not a spare bit: it is the same category tag every other item kind carries
/// (weapons 0, protectors 1, accessories 2, goods 4) and the engine dispatches on it.
/// `CS::CSGaitemImp::GetGaitemHandleByItemId` switches on `itemId >> 28` and sends 8 to
/// `GetGaItemHandleGem`; `CS::GaitemLookupResult::GetSwordArtsParamId` refuses any item id whose
/// `& 0xF000_0000` is not `0x8000_0000`, then looks the low 28 bits up in `EquipParamGem`.
///
/// So the 28 bits under this nibble MUST be a gem row, never a `SwordArtsParam` row. The two id
/// spaces are easy to swap -- most ashes sit at `gem = arts * 100` -- and swapping them is
/// SILENT: the value still passes every shape check and simply names a row that does not exist.
/// That swap is the bug that shipped: the runtime catalog resolved ash names to `SwordArtsParam`
/// rows, every name resolved, and no weapon came out carrying its ash.
pub const GEM_ITEM_CATEGORY: u32 = 0x8000_0000;

/// Affinity name -> the offset it adds to an armament's item id.
const INFUSIONS: &[(&str, u32)] = &[
    ("Standard", 0),
    ("Heavy", 100),
    ("Keen", 200),
    ("Quality", 300),
    ("Fire", 400),
    ("Flame Art", 500),
    ("Lightning", 600),
    ("Sacred", 700),
    ("Magic", 800),
    ("Cold", 900),
    ("Poison", 1000),
    ("Blood", 1100),
    ("Occult", 1200),
];

/// Somber-armament upgrade remap, replicated verbatim from the planner.
///
/// Applied only when a slot carries an explicit upgrade *and* the armament takes
/// Somber Smithing Stones. The planner's intent here is not documented and the
/// mapping is reproduced rather than reinterpreted; [`somber_remap`] returns
/// `None` past the table's end so an out-of-range level is reported instead of
/// silently wrapping.
const SOMBER_REMAP: &[u16] = &[0, 1, 4, 6, 9, 11, 14, 16, 19, 21, 24, 25];

/// Flasks the exporter deliberately never grants -- the character already owns
/// them and duplicating them corrupts the flask UI.
const NEVER_GRANT: &[&str] = &["Flask of Crimson Tears", "Flask of Cerulean Tears"];

/// One item to hand to the player.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Grant {
    /// Category-tagged item id with any affinity offset already applied.
    pub item_id: u32,
    /// How many to give.
    pub quantity: u32,
    /// Upgrade level, as its own field.
    pub reinforce_lv: u16,
    /// Ash of war as a gem item id ([`GEM_ITEM_CATEGORY`] `| EquipParamGem row`), or
    /// [`NO_SKILL`].
    pub weapon_skill: u32,
    /// What this grant is, for logs and for the user.
    pub label: String,
}

impl Grant {
    /// Encode as the 16-byte `ItemGib` record.
    pub fn to_record(&self) -> [u8; 16] {
        let mut out = [0u8; 16];
        out[0..4].copy_from_slice(&self.item_id.to_le_bytes());
        out[4..8].copy_from_slice(&self.quantity.to_le_bytes());
        out[8..10].copy_from_slice(&self.reinforce_lv.to_le_bytes());
        // out[10..12] stays zero: the exporter emits an explicit zero half-word.
        out[12..16].copy_from_slice(&self.weapon_skill.to_le_bytes());
        out
    }
}

/// An item the catalog could not resolve.
///
/// Surfaced rather than dropped: a silently missing weapon is indistinguishable
/// from a broken importer, and this is exactly how the `Miséricorde` accent bug
/// was found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unresolved {
    /// Which catalog was searched.
    pub kind: Kind,
    /// The name as the build spelled it.
    pub name: String,
}

/// Everything needed to reproduce a build in game.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Plan {
    /// Items to grant, in build order.
    pub grants: Vec<Grant>,
    /// Spell param ids to memorise, in slot order.
    pub equip_spells: Vec<u32>,
    /// Names that resolved to nothing.
    pub unresolved: Vec<Unresolved>,
}

impl Plan {
    /// Whether every referenced item resolved.
    pub fn is_complete(&self) -> bool {
        self.unresolved.is_empty()
    }
}

/// The offset an affinity adds to an armament id, or `None` if unrecognised.
pub fn infusion_offset(infusion: Option<&str>) -> Option<u32> {
    let wanted = infusion.unwrap_or("Standard");
    INFUSIONS
        .iter()
        .find(|(name, _)| name.eq_ignore_ascii_case(wanted))
        .map(|(_, offset)| *offset)
}

/// Split an armament param id back into its base row and affinity name -- the inverse of
/// [`infusion_offset`], and the arithmetic the EXPORTER runs on every equipped weapon.
///
/// The affinity is an offset folded INTO the id (`Occult = +1200`), and the base is always a
/// multiple of [`ARMAMENT_ID_BLOCK`], which is what makes the split unambiguous. The reinforce
/// level is deliberately absent from both directions: it is a separate field, never part of the id.
///
/// `Standard` comes back as `None` rather than as the string, because that is how the planner
/// spells it -- a slot with no `infusion` key. Emitting the word would import identically and diff
/// against every hand-authored build.
///
/// ```
/// use er_build_import::plan::{infusion_offset, split_armament_id};
/// // Misericorde + Occult, the pair the importer builds as 1_070_000 + 1200.
/// assert_eq!(split_armament_id(1_071_200), (1_070_000, Some("Occult")));
/// assert_eq!(split_armament_id(1_070_000), (1_070_000, None));
/// assert_eq!(infusion_offset(Some("Occult")), Some(1200));
/// ```
pub fn split_armament_id(param_id: u32) -> (u32, Option<&'static str>) {
    let index = (param_id % ARMAMENT_ID_BLOCK / INFUSION_STEP) as usize;
    match INFUSIONS.get(index) {
        // Index 0 IS Standard, which the planner writes as an absent field.
        Some(_) if index == 0 => (param_id, None),
        Some((name, offset)) => (param_id - offset, Some(name)),
        // An offset past the table is not an affinity at all, so the id is taken whole rather than
        // having an invented amount subtracted from it.
        None => (param_id, None),
    }
}

/// The block an armament id's affinity offset occupies; the base row is always a multiple of it.
pub const ARMAMENT_ID_BLOCK: u32 = 10_000;
/// Step between consecutive affinities inside that block.
pub const INFUSION_STEP: u32 = 100;

/// Every affinity name the planner uses, in offset order. Exposed so the exporter can prove it
/// knows exactly the set the importer accepts, rather than keeping a second copy that can drift.
pub fn infusion_names() -> impl Iterator<Item = &'static str> {
    INFUSIONS.iter().map(|(name, _)| *name)
}

/// The item id an armament instance must carry to READ AS `+level` in the player's hands.
///
/// The upgrade level is part of the id, in its last two digits: the character's own weapons come
/// back from `GaitemInsLookupResult::GetItemId` as `12531125` (base `12530000` + Blood `1100` +
/// `25`), and `CSGaitemImp::GetGaItemHandleWeapon` stores whatever id it is handed verbatim
/// through `CSGaitemIns::SetItemIdWithWeaponCategory`. A weapon minted from a bare
/// `base + affinity` id is therefore a `+0` weapon, whatever is written to its `reinforcement`
/// field afterwards.
///
/// THE MISREADING THIS EXISTS TO CORRECT. `EquipParamWeapon::GetEntry` normalises its argument to
/// `(paramId / 100) * 100`, and this module used to conclude from that "the game throws the level
/// away, so it can only reach the game as `CSWepGaitemIns::reinforcement`". The normalisation is
/// there for the opposite reason: the last two digits are the LEVEL, so the stat row is found by
/// stripping them, and the level is read back off the same id by a different consumer. Dropping it
/// shipped 30 armaments at +0 with `GetReinforcement` cheerfully reporting 25 -- the field was set,
/// nothing read it. The reference exporter this module is ported from sets BOTH halves, and its
/// record layout says so in the first line of the module doc: `dd <id+upgrade> ... <reinforceLv>`.
///
/// `level` must already be clamped to a level this armament HAS (see the runtime's
/// `ReinforceLevels::clamp`); a somber armament asked for +25 would otherwise name a row that does
/// not exist.
///
/// ```
/// use er_build_import::plan::armament_item_id;
/// // Great Stars + Blood, +25.
/// assert_eq!(armament_item_id(12181200, 25), 12181225);
/// // A somber armament, clamped to +10 by the caller.
/// assert_eq!(armament_item_id(11500000, 10), 11500010);
/// // +0 leaves the id exactly as the affinity left it.
/// assert_eq!(armament_item_id(2020100, 0), 2020100);
/// ```
#[must_use]
pub fn armament_item_id(base_with_affinity: u32, level: u16) -> u32 {
    base_with_affinity + u32::from(level)
}

/// Remap a requested upgrade level for a somber armament.
pub fn somber_remap(level: u16) -> Option<u16> {
    SOMBER_REMAP.get(usize::from(level)).copied()
}

impl Entry {
    /// The bare param row id, with the category nibble stripped.
    pub fn param_id(self) -> u32 {
        self.full_item_id & 0x0FFF_FFFF
    }
}

/// Compute the grants and spell list for `doc` using `catalog`.
pub fn plan(doc: &BuildDoc, catalog: &dyn Catalog) -> Plan {
    let mut out = Plan::default();

    // ARMAMENTS ARE GRANTED IN PAYLOAD ORDER -- the order the build lists them, which is the order
    // the player sees in their inventory and the only order they can check against the planner page.
    //
    // This USED to be two passes, worn-in-the-active-set first and everything else after, and that
    // reordering was load-bearing while the equip resolved a copy through
    // `EquipInventoryData::GetItemInventoryIdx`: several copies of one armament differing only by
    // ash share an item id (the ash lives on the gaitem instance), and
    // `InventoryItemsData::InsertItemIntoLookupMap` keeps the LOWEST index for a repeated id, so the
    // game always answered with the earliest-granted copy. Granting the worn one first was the only
    // way to make that answer right.
    //
    // It is obsolete now: the equip carries each mint's `GaItemHandle` forward and asks
    // `GetItemIndexByGaitemHandle` (0x14024c460), which names ONE instance and does not care where
    // in the inventory it sits. The reorder bought nothing after that and cost the user the thing
    // they can actually see -- reported 2026-08-23 against build 94252a868b4f2a, where the two worn
    // armaments were granted at positions 1 and 2 while the payload puts them at `order` 2 and 8.
    for slot in &doc.inventory.slots {
        plan_weapon(doc, catalog, slot, &mut out);
    }
    for slot in &doc.talismans.slots {
        push_simple(catalog, Kind::Talisman, slot, 1, &mut out);
    }
    for slot in &doc.spells.slots {
        match catalog.lookup(Kind::Spell, &slot.name) {
            Some(found) => {
                out.equip_spells.push(found.param_id());
                out.grants.push(Grant {
                    item_id: found.full_item_id,
                    quantity: 1,
                    reinforce_lv: 0,
                    weapon_skill: NO_SKILL,
                    label: slot.name.clone(),
                });
            }
            None => out.unresolved.push(Unresolved {
                kind: Kind::Spell,
                name: slot.name.clone(),
            }),
        }
    }
    // BTreeMap iteration keeps body parts in a stable order across runs.
    for part in doc.protectors.values() {
        for slot in &part.slots {
            push_simple(catalog, Kind::Protector, slot, 1, &mut out);
        }
    }
    for slot in &doc.items.tools.slots {
        if NEVER_GRANT
            .iter()
            .any(|skip| skip.eq_ignore_ascii_case(&slot.name))
        {
            continue;
        }
        let quantity = catalog
            .lookup(Kind::Tool, &slot.name)
            .and_then(|found| found.max_stored)
            .unwrap_or(1);
        push_simple(catalog, Kind::Tool, slot, quantity, &mut out);
    }
    // The great rune is a goods item like any other and has to be in the inventory before it
    // can be equipped; nothing else in the payload implies it.
    if let Some(rune) = doc.great_rune.as_deref() {
        let slot = Slot {
            name: rune.to_owned(),
            ..Slot::default()
        };
        push_simple(catalog, Kind::GreatRune, &slot, 1, &mut out);
    }
    for tear in doc.items.crystal_tears.iter().flatten() {
        let slot = Slot {
            name: tear.clone(),
            ..Slot::default()
        };
        push_simple(catalog, Kind::Tool, &slot, 1, &mut out);
    }

    out
}

/// Grant an armament, applying affinity, upgrade level and ash of war.
fn plan_weapon(doc: &BuildDoc, catalog: &dyn Catalog, slot: &Slot, out: &mut Plan) {
    let Some(found) = catalog.lookup(Kind::Weapon, &slot.name) else {
        out.unresolved.push(Unresolved {
            kind: Kind::Weapon,
            name: slot.name.clone(),
        });
        return;
    };
    let Some(offset) = infusion_offset(slot.infusion.as_deref()) else {
        out.unresolved.push(Unresolved {
            kind: Kind::Weapon,
            name: format!("{} (unknown affinity {:?})", slot.name, slot.infusion),
        });
        return;
    };

    let requested = slot.upgrade.unwrap_or(doc.weapon_upgrade);
    let reinforce_lv = if slot.upgrade.is_some() && found.somber {
        match somber_remap(requested) {
            Some(mapped) => mapped,
            None => {
                out.unresolved.push(Unresolved {
                    kind: Kind::Weapon,
                    name: format!("{} (somber upgrade {requested} out of range)", slot.name),
                });
                return;
            }
        }
    } else {
        requested
    };

    let mut weapon_skill = NO_SKILL;
    if let Some(art) = slot.weapon_art.as_deref()
        && !art.eq_ignore_ascii_case("No Skill")
    {
        match catalog.lookup(Kind::AshOfWar, art) {
            Some(ash) => weapon_skill = GEM_ITEM_CATEGORY | ash.param_id(),
            None => {
                out.unresolved.push(Unresolved {
                    kind: Kind::AshOfWar,
                    name: art.to_owned(),
                });
            }
        }
    }

    out.grants.push(Grant {
        item_id: found.full_item_id + offset,
        quantity: 1,
        reinforce_lv,
        weapon_skill,
        label: slot.name.clone(),
    });
}

/// Grant a non-armament item, which carries no affinity, upgrade or skill.
fn push_simple(catalog: &dyn Catalog, kind: Kind, slot: &Slot, quantity: u32, out: &mut Plan) {
    match catalog.lookup(kind, &slot.name) {
        Some(found) => out.grants.push(Grant {
            item_id: found.full_item_id,
            quantity,
            reinforce_lv: 0,
            weapon_skill: NO_SKILL,
            label: slot.name.clone(),
        }),
        None => out.unresolved.push(Unresolved {
            kind,
            name: slot.name.clone(),
        }),
    }
}

/// The ash of war the build wants on one EQUIPPED armament slot.
///
/// Exists so the importer can check its own work. A grant that "succeeded" and an equip that
/// "succeeded" still say nothing about whether the weapon in the player's hand carries the right
/// skill -- there are three native hops between the two -- so the runtime reads the arts id back
/// out of the equipped slot and needs to know what it should have found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArmamentSkill {
    /// `ChrAsmSlot` the armament is worn in.
    pub slot: i32,
    /// The armament's display name.
    pub weapon: String,
    /// The ash's display name, or `None` when the build asked for no skill.
    pub art: Option<String>,
    /// What [`Grant::weapon_skill`] carries for this armament.
    pub weapon_skill: u32,
}

/// What every equipped armament in `doc` should be holding, for the post-import read-back.
///
/// Slots the build leaves empty are absent; an armament whose ash the catalog could not resolve
/// is still listed, with [`NO_SKILL`], so the read-back reports it rather than skipping it.
pub fn equipped_armament_skills(doc: &BuildDoc, catalog: &dyn Catalog) -> Vec<ArmamentSkill> {
    let mut out = Vec::new();
    for slot in &doc.inventory.slots {
        let Some(index) = slot.equip_index else {
            continue;
        };
        let Some(chr_asm_slot) = crate::equip::armament_slot(index) else {
            continue;
        };
        let art = slot
            .weapon_art
            .as_deref()
            .filter(|art| !art.eq_ignore_ascii_case("No Skill"));
        let weapon_skill = art
            .and_then(|art| catalog.lookup(Kind::AshOfWar, art))
            .map_or(NO_SKILL, |ash| GEM_ITEM_CATEGORY | ash.param_id());
        out.push(ArmamentSkill {
            slot: chr_asm_slot,
            weapon: slot.name.clone(),
            art: art.map(str::to_owned),
            weapon_skill,
        });
    }
    out
}
