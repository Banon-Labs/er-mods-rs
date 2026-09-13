//! A save-independent `PlaceName` id for a map, read from the game's own param tables.
//!
//! # Why a third source exists
//!
//! `er_save_loader::profile_summary::slot_place_name_ids` answers from the save container alone,
//! in two tiers: the slot's own `ProfileSummary` record when that record describes the body in that
//! slot, and otherwise the id this save pairs with the body's map in some *other* record the game
//! itself wrote. Both are grounded, and both can come up empty for the same reason -- a character
//! who is the only one in the container standing on their map has no donor record.
//!
//! Measured on run br-20260913-155423-2fe7, where four of five rows read `Academy of Raya Lucaria`
//! and one read nothing:
//!
//! ```text
//! stats-text: slot 1 Location WITHHELD -- its record is another character's (body map 0x15010000)
//! and no consistent record in this save covers that map, so there is no place name to show
//! ```
//!
//! Slots 0, 2, 3 and 4 all sit on body map `0x0e000000` and each borrowed id 14000 from a sibling.
//! Slot 1 sits on `0x15010000` -- area 21, the Shadow of the Erdtree range -- alone, so the pairing
//! had nothing to offer and the row rendered blank. Vanilla shows a real place name there.
//!
//! # The source
//!
//! `WORLD_MAP_PLACE_NAME_PARAM_ST` and `BONFIRE_WARP_PARAM_ST` both carry the same three-byte map
//! key (`area_no`, `grid_x_no`, `grid_z_no`) beside a `PlaceName` text id, and both are already
//! bound upstream with public accessors. They are read through `SoloParamRepository` exactly as
//! `er_build_import_runtime::catalog` reads the equipment tables -- rows only, never written.
//!
//! Two tables rather than one because they cover different ground: the world-map table names the
//! overworld tiles a player sees on the map screen, and the bonfire table names every site of grace,
//! which is what covers a legacy dungeon whose interior has no map tile of its own. The world map is
//! asked first because its text is the name of the *place*; the grace text is the fallback.
//!
//! # What this deliberately does not do
//!
//! It never lends the loaded character's place name to a row that has none. That would make every
//! row claim a location it cannot evidence, which is the defect the withhold in
//! `er_save_loader::profile_summary` exists to prevent. A map neither param table names still
//! renders blank, and the log says which of the three sources were asked.

use std::collections::BTreeMap;
use std::sync::OnceLock;

/// The three bytes of a saved map id that both param tables key on.
///
/// Elden Ring packs `mAA_BB_CC_DD` as one big-endian byte per component, so the area is the top
/// byte and the two grid coordinates follow. The low byte is the map's index within its block and
/// neither table carries it.
#[must_use]
pub(crate) fn map_key(saved_map: i32) -> MapKey {
    let map = saved_map as u32;
    (
        ((map >> 24) & 0xff) as u8,
        ((map >> 16) & 0xff) as u8,
        ((map >> 8) & 0xff) as u8,
    )
}

/// Which table answered, for the log line and for the counter below.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum PlaceNameSource {
    WorldMap,
    BonfireWarp,
}

impl PlaceNameSource {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::WorldMap => "WORLD_MAP_PLACE_NAME_PARAM_ST",
            Self::BonfireWarp => "BONFIRE_WARP_PARAM_ST",
        }
    }
}

/// Built once, the first time a row asks and the param tables are populated.
///
/// A `OnceLock` and not a `Mutex<Option<..>>`: the tables are read-only for the life of the process,
/// so the only concurrency question is who builds first, and the loser discards its copy. An empty
/// map is a legitimate build result (the params were not ready) and is retried by the
/// `PLACE_NAME_TABLE_BUILT` guard rather than latched, because a boot-time row build can precede
/// `SoloParamRepository` being populated.
/// The map key both param tables carry: `area_no`, `grid_x_no`, `grid_z_no`.
type MapKey = (u8, u8, u8);

/// A resolved `PlaceName` id and which table it came from.
type NamedPlace = (u32, PlaceNameSource);

static PLACE_NAME_TABLE: OnceLock<BTreeMap<MapKey, NamedPlace>> = OnceLock::new();

#[cfg(windows)]
fn build_table() -> BTreeMap<MapKey, NamedPlace> {
    use eldenring::cs::{BonfireWarpParam, SoloParamRepository, WorldMapPlaceNameParam};
    use fromsoftware_shared::FromStatic;

    let mut out: BTreeMap<MapKey, NamedPlace> = BTreeMap::new();
    // Safety: `instance()` hands back a reference only when the singleton is populated, and every
    // row below is read, never written.
    let Ok(repo) = (unsafe { SoloParamRepository::instance() }) else {
        return out;
    };
    // Graces first, so the world-map pass below overwrites them where both tables cover a key.
    for (_, row) in repo.rows::<BonfireWarpParam>() {
        let id = row.text_id1();
        if id <= 0 {
            continue;
        }
        out.insert(
            (row.area_no(), row.grid_x_no(), row.grid_z_no()),
            (id as u32, PlaceNameSource::BonfireWarp),
        );
    }
    for (_, row) in repo.rows::<WorldMapPlaceNameParam>() {
        let id = row.text_id();
        if id <= 0 {
            continue;
        }
        out.insert(
            (row.area_no(), row.grid_x_no(), row.grid_z_no()),
            (id as u32, PlaceNameSource::WorldMap),
        );
    }
    out
}

#[cfg(not(windows))]
fn build_table() -> BTreeMap<MapKey, NamedPlace> {
    // Host builds exercise `map_key` and the tier order; there is no game to read params from.
    BTreeMap::new()
}

/// The `PlaceName` id this build of the game pairs with `saved_map`, or `None` when neither table
/// covers it.
///
/// The table is built on the first call that finds the params populated. An empty build is not
/// cached, so a row built before `SoloParamRepository` is ready does not poison every later row.
pub(crate) fn place_name_for_map(saved_map: i32) -> Option<NamedPlace> {
    if let Some(table) = PLACE_NAME_TABLE.get() {
        return table.get(&map_key(saved_map)).copied();
    }
    let built = build_table();
    if built.is_empty() {
        return None;
    }
    let table = PLACE_NAME_TABLE.get_or_init(|| built);
    table.get(&map_key(saved_map)).copied()
}

#[cfg(test)]
mod map_place_name_tests {
    use super::map_key;

    /// The packing both param tables key on, taken from the two maps the defect run measured.
    #[test]
    fn splits_a_saved_map_into_the_three_bytes_the_params_carry() {
        assert_eq!(map_key(0x0e00_0000u32 as i32), (0x0e, 0x00, 0x00));
        assert_eq!(map_key(0x1501_0000u32 as i32), (0x15, 0x01, 0x00));
    }

    /// An overworld tile carries its grid coordinates in the two middle bytes, and the low byte --
    /// the map's index within its block -- is not part of the key.
    #[test]
    fn an_overworld_tile_keys_on_its_grid_and_ignores_the_low_byte() {
        assert_eq!(map_key(0x3c33_2400u32 as i32), (0x3c, 0x33, 0x24));
        assert_eq!(map_key(0x3c33_2401u32 as i32), (0x3c, 0x33, 0x24));
    }
}
