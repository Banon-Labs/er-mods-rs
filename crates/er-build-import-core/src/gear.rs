//! The gear a build wears, as the param ids a character's `ChrAsm` holds.
//!
//! # Who this is for
//!
//! The importer dresses the player through the game's own equip calls, item by item, out of an
//! inventory it has just granted. A character a lab tool spawns has no such inventory: it is
//! dressed by writing param ids straight into its `ChrAsm` and minting a gaitem for each hand
//! (`scripts/frida/spawn-npc.js`). What that needs is one id per slot, and this module answers it
//! from the same [`equip_plan`] and the same armament arithmetic -- [`armament_grant`],
//! [`game_level`], [`armament_item_id`] -- the importer uses, so a spawned character and a player
//! imported from the same link wear the same talisman in slot 2 and carry the armament at the same
//! level.
//!
//! # Every slot gets a value
//!
//! A slot the build leaves empty is not left out. It gets the value the engine itself writes for an
//! empty position: the Unarmed fist in a hand and the slot's bare piece for armour
//! ([`UNARMED_ITEM_ID`], [`EMPTY_PROTECTOR_ITEM_IDS`]), and [`EMPTY_TALISMAN_ID`] for a talisman. A
//! caller layering this over a character's own gear has to overwrite every slot, or the character
//! keeps whatever its own param row gave it wherever the build has nothing.
//!
//! # The catalog
//!
//! Names still have to become ids, and on the host there is no message repository to ask. A host
//! tool reads the tables the runtime asks the game for -- the item-name message files and the
//! regulation's param rows -- and hands them over as [`CatalogRow`]s. [`catalog_from_rows`] turns
//! them into the [`MapCatalog`] every lookup in this crate goes through, so name folding, kind
//! scoping and collision handling stay here rather than being written a second time by the tool.

use serde::{Deserialize, Serialize};

use crate::catalog::{Catalog, Entry, Kind, MapCatalog};
use crate::equip::{
    CHR_ASM_SLOT_ACCESSORY_1, CHR_ASM_SLOT_PROTECTOR_HEAD, Capacity, EquipRef, armament_slot,
    equip_plan,
};
use crate::model::{BuildDoc, Items, Slot, SlotList};
use crate::plan::{NO_SKILL, armament_grant, armament_item_id, game_level};
use crate::sweep::{EMPTY_PROTECTOR_ITEM_IDS, ITEM_ID_ROW_MASK, UNARMED_ITEM_ID};

/// What an empty talisman slot holds. The engine has no bare piece for an accessory, so the slot
/// reads `-1`, which is also what `er-build-import-runtime` expects a vacated talisman to read back
/// as.
pub const EMPTY_TALISMAN_ID: i64 = -1;

/// `spawn-npc.js`'s names for the four armour slots, in [`crate::equip::PROTECTOR_PARTS`] order:
/// the planner's `head`, `body`, `arms` and `legs`.
pub const PROTECTOR_SLOT_NAMES: [&str; 4] = ["head", "chest", "hands", "legs"];

/// `spawn-npc.js`'s names for the four talisman slots, `Accessory1..4`.
pub const TALISMAN_SLOT_NAMES: [&str; 4] = ["talisman1", "talisman2", "talisman3", "talisman4"];

/// `spawn-npc.js`'s name for an armament `ChrAsmSlot`. The slots interleave the hands
/// (`0 = WeaponLeft1`, `1 = WeaponRight1`, ...), which [`armament_slot`] already maps the planner's
/// positions onto, so the name is read off the slot rather than off the planner position.
fn armament_slot_name(chr_asm_slot: i32) -> Option<&'static str> {
    match chr_asm_slot {
        0 => Some("left1"),
        1 => Some("right1"),
        2 => Some("left2"),
        3 => Some("right2"),
        4 => Some("left3"),
        5 => Some("right3"),
        _ => None,
    }
}

/// One row of a host-built item catalog: a param row of `kind`'s table and the name the game's
/// message files give it.
///
/// Rows are inserted in the order given, and [`MapCatalog::insert`] keeps the first row under a
/// name, so a tool should list each kind's rows in ascending row id -- the order the runtime
/// enumerates the live param table in -- for a name shared by several rows to resolve the same way
/// on both sides.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct CatalogRow {
    /// Which table the row is from.
    pub kind: Kind,
    /// The bare param row id; [`Kind::item_id`] adds the category nibble.
    pub row: u32,
    /// The row's display name.
    pub name: String,
    /// Whether the armament upgrades with Somber Smithing Stones, measured as its highest
    /// reinforce row being +10. Read only for [`Kind::Weapon`]; absent reads as `false`.
    #[serde(default)]
    pub somber: bool,
}

/// Build the catalog every lookup goes through from a host tool's rows.
///
/// ```
/// use er_build_import_core::catalog::{Catalog, Kind};
/// use er_build_import_core::gear::{CatalogRow, catalog_from_rows};
/// let rows: Vec<CatalogRow> = serde_json::from_str(
///     r#"[{"kind": "ash_of_war", "row": 65200, "name": "Beast's Roar"}]"#,
/// )
/// .expect("rows parse");
/// let catalog = catalog_from_rows(&rows);
/// let ash = catalog.lookup(Kind::AshOfWar, "beast's roar").expect("folded lookup");
/// assert_eq!(ash.full_item_id, 0x8000_0000 | 65_200);
/// ```
pub fn catalog_from_rows(rows: &[CatalogRow]) -> MapCatalog {
    let mut catalog = MapCatalog::new();
    for row in rows {
        catalog.insert(
            row.kind,
            &row.name,
            Entry {
                full_item_id: row.kind.item_id(row.row),
                // Nothing here is granted, so neither hold limit nor pot group is asked.
                max_stored: None,
                somber: row.somber,
                pot_group: None,
            },
        );
    }
    catalog
}

/// One `ChrAsm` slot and what the build puts in it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WornPiece {
    /// `spawn-npc.js`'s name for the slot, e.g. `right1`, `chest`, `talisman3`.
    pub slot: &'static str,
    /// The engine's `ChrAsmSlot` number for it.
    pub chr_asm_slot: i32,
    /// The param id the slot holds. An armament's carries its affinity and level; an empty slot's
    /// is the engine's empty value (see the module docs).
    pub id: i64,
    /// The ash of war an armament carries, as its `EquipParamGem` row, or `None` for none.
    pub gem: Option<u32>,
    /// The item's name as the build spells it, or `None` when the slot is empty.
    pub name: Option<String>,
    /// The ash of war's name as the build spells it, when [`Self::gem`] is set.
    pub ash: Option<String>,
}

/// Something the build asks for that did not make it onto the character, and why.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Problem {
    /// The item as the build names it.
    pub name: String,
    /// Plain-language reason.
    pub reason: String,
}

/// Every gear slot, in a fixed order, and everything that could not be placed.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct WornGear {
    /// `right1..3`, `left1..3`, `head`, `chest`, `hands`, `legs`, `talisman1..4`, always all
    /// fourteen.
    pub slots: Vec<WornPiece>,
    /// Items the build wears that did not resolve or lost their position to another row.
    pub unresolved: Vec<Problem>,
}

impl WornGear {
    /// The slot named `name`, e.g. `"talisman2"`.
    pub fn slot(&self, name: &str) -> Option<&WornPiece> {
        self.slots.iter().find(|piece| piece.slot == name)
    }
}

/// Resolve what `doc` wears in every armament, armour and talisman slot.
///
/// Only the active set of each category counts, a contested position goes to the row the planner
/// shows, and a talisman with no equip position is carried, not worn -- all decided by
/// [`equip_plan`], not here.
pub fn worn_gear(doc: &BuildDoc, catalog: &dyn Catalog) -> WornGear {
    // Only what a `ChrAsm` wears. Spells, consumables and the great rune are left out so the
    // plan's rejections are about gear alone, not about a host catalog that never listed goods.
    let worn = BuildDoc {
        spells: SlotList::default(),
        items: Items::default(),
        great_rune: None,
        ..doc.clone()
    };
    let plan = equip_plan(&worn, catalog, Capacity::default());
    let mut out = WornGear {
        slots: Vec::new(),
        unresolved: plan
            .rejected
            .iter()
            .map(|rejected| Problem {
                name: rejected.name.clone(),
                reason: rejected.reason.clone(),
            })
            .collect(),
    };

    let active = worn.sets.active_weapons();
    for (index, item) in plan.armaments.iter().enumerate() {
        let Some(chr_asm_slot) = u32::try_from(index).ok().and_then(armament_slot) else {
            continue;
        };
        let Some(slot) = armament_slot_name(chr_asm_slot) else {
            continue;
        };
        let piece = match item {
            Some(item) => armament(&worn, catalog, active, index, item, &mut out.unresolved),
            None => Armament {
                id: i64::from(UNARMED_ITEM_ID & ITEM_ID_ROW_MASK),
                gem: None,
                ash: None,
            },
        };
        out.slots.push(WornPiece {
            slot,
            chr_asm_slot,
            id: piece.id,
            gem: piece.gem,
            name: item.as_ref().map(|item| item.name.clone()),
            ash: piece.ash,
        });
    }

    let armour = [&plan.head, &plan.body, &plan.arms, &plan.legs];
    for (((item, slot), empty), chr_asm_slot) in armour
        .into_iter()
        .zip(PROTECTOR_SLOT_NAMES)
        .zip(EMPTY_PROTECTOR_ITEM_IDS)
        .zip(CHR_ASM_SLOT_PROTECTOR_HEAD..)
    {
        out.slots.push(simple(
            slot,
            chr_asm_slot,
            item.as_ref(),
            i64::from(empty & ITEM_ID_ROW_MASK),
        ));
    }

    for ((index, slot), chr_asm_slot) in TALISMAN_SLOT_NAMES
        .into_iter()
        .enumerate()
        .zip(CHR_ASM_SLOT_ACCESSORY_1..)
    {
        let item = plan.talismans.get(index).and_then(Option::as_ref);
        out.slots
            .push(simple(slot, chr_asm_slot, item, EMPTY_TALISMAN_ID));
    }

    out
}

/// A piece with no level and no ash: armour or a talisman.
fn simple(slot: &'static str, chr_asm_slot: i32, item: Option<&EquipRef>, empty: i64) -> WornPiece {
    WornPiece {
        slot,
        chr_asm_slot,
        id: item.map_or(empty, |item| i64::from(item.param_id)),
        gem: None,
        name: item.map(|item| item.name.clone()),
        ash: None,
    }
}

/// What one hand holds.
struct Armament {
    id: i64,
    gem: Option<u32>,
    ash: Option<String>,
}

/// The id, ash and level of the armament worn at planner position `index`.
///
/// Computed by [`armament_grant`] from the worn row, so it is the grant list's own answer, with the
/// level put on the game's scale by [`game_level`] and folded into the id by [`armament_item_id`].
fn armament(
    doc: &BuildDoc,
    catalog: &dyn Catalog,
    active: usize,
    index: usize,
    item: &EquipRef,
    unresolved: &mut Vec<Problem>,
) -> Armament {
    let unlevelled = Armament {
        id: i64::from(item.param_id),
        gem: None,
        ash: None,
    };
    let Some(row) = worn_row(doc, active, index, &item.name) else {
        return unlevelled;
    };
    let mut missing = Vec::new();
    let grant = armament_grant(doc, catalog, row, &mut missing);
    unresolved.extend(missing.into_iter().map(|miss| Problem {
        reason: format!("{} did not resolve", miss.kind.label()),
        name: miss.name,
    }));
    let Some(grant) = grant else {
        return unlevelled;
    };
    let somber = catalog
        .lookup(Kind::Weapon, &row.name)
        .is_some_and(|entry| entry.somber);
    let level = game_level(
        grant.reinforce_lv,
        grant.upgrade_is_character_default,
        somber,
    );
    let gem = (grant.weapon_skill != NO_SKILL).then_some(grant.weapon_skill & ITEM_ID_ROW_MASK);
    Armament {
        id: i64::from(armament_item_id(item.param_id, level)),
        gem,
        ash: gem.and(row.weapon_art.clone()),
    }
}

/// The build row that won armament position `index` in the active set.
///
/// The equip plan keeps only the winner's name, while the level and the ash live on the row, and a
/// build often lists one armament several times -- once per ash. So the row is found the way the
/// plan chose it: among the rows of the active set claiming this position under the winner's name,
/// the last in `order`, which is the planner's last-wins fold (see [`crate::equip`]).
fn worn_row<'a>(doc: &'a BuildDoc, active: usize, index: usize, name: &str) -> Option<&'a Slot> {
    let index = u32::try_from(index).ok()?;
    doc.inventory
        .slots
        .iter()
        .filter(|row| row.name == name && row.equip_index_in_active_set(active) == Some(index))
        .max_by_key(|row| row.order)
}
