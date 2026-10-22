//! The custom Mimic Tear: which summon requests the game's `BuddyGenerator` should make.
//!
//! The native flow (bd `mimic-tear-summon-hijack-1171-2026-10-06`): using Mimic Tear Ashes +N
//! sets `SummonBuddyManager+0x20` to `207000 + N`; `BuddyGenerator` asks `GetBuddyList` for the
//! BuddyParam rows whose trigger is `207000` (rows 20700000 and 20700001), reads each row through
//! `GetBuddyParam` and turns it into a spawn request. The DLL rewrites that list to one entry per
//! companion, all pointing at the human row, and answers each read of that row with a copy
//! carrying the companion's values ([`RowValues::write_into`], [`crate::row_copy`]). The resident
//! row is never written.
//!
//! This module decides those values and lays them into a copy's bytes; the DLL serves the copy.

use crate::config::Companion;

/// `BuddyParam.triggerSpEffectId` of both Mimic Tear rows; the request is this plus the upgrade
/// level (Mimic Tear Ashes +0..+10 are goods/SpEffects 207000..207010).
pub const MIMIC_TRIGGER: i32 = 207_000;

/// The Mimic Tear's c0000 human row. Every companion request points here, so no request carries
/// row 20700000 and the copy-the-player branch (`cmp $0x13bdb60` at `0x1404bc384`) never arms.
pub const HUMAN_ROW: i32 = 20_700_001;

/// Highest Mimic Tear upgrade level.
const MAX_LEVEL: i32 = 10;

/// The Mimic Tear upgrade level of a summon request, or `None` when the request is not a Mimic
/// Tear at all (`SummonBuddyManager+0x20`, read where `0x1404bbed9` reads it).
#[must_use]
pub fn mimic_level(request: i32) -> Option<i32> {
    let level = request - MIMIC_TRIGGER;
    (0..=MAX_LEVEL).contains(&level).then_some(level)
}

/// Where each companion stands around the summoning point, in block-local metres and degrees.
/// The first three are the Lone Wolf Ashes rows 21400000..2, which the turtle prototype used;
/// the fourth mirrors the third behind the player.
pub const FORMATION: [Offset; crate::config::MAX_COMPANIONS] = [
    Offset {
        x: 1.2,
        z: 1.3,
        yaw: 0.0,
    },
    Offset {
        x: 1.5,
        z: -1.2,
        yaw: 55.0,
    },
    Offset {
        x: -1.4,
        z: 0.8,
        yaw: -10.0,
    },
    Offset {
        x: -1.3,
        z: -1.4,
        yaw: 30.0,
    },
];

/// A BuddyParam row's `x_offset`, `z_offset` and `y_angle`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Offset {
    pub x: f32,
    pub z: f32,
    pub yaw: f32,
}

/// `BUDDY_PARAM_ST`'s size, the bytes a copy of a BuddyParam row carries. The last field the game
/// reads is `generateAnimId` at `+0x58` (`CreateSummonChr`, 2.7.2.0 `0x1404bb7c9`); the paramdef
/// runs on to `+0xa0`.
pub const BUDDY_ROW_SIZE: usize = 0xa0;

/// `BUDDY_PARAM_ST` offsets of the fields a companion's copy changes. `triggerSpEffectId` (`+0x4`)
/// is deliberately not among them: `UpdateTriggerSpEffectMap` (2.7.2.0 `0x1404bd040`) reads the
/// resident rows by index, so a copy could never move a row to another trigger.
pub mod buddy_field {
    pub const NPC: usize = 0x08;
    pub const THINK: usize = 0x0c;
    pub const X: usize = 0x18;
    pub const Z: usize = 0x1c;
    pub const YAW: usize = 0x20;
    pub const CHARA_INIT: usize = 0x54;
}

/// The values one read of [`HUMAN_ROW`] is given before `BuddyGenerator` copies them out.
/// `BUDDY_PARAM_ST` offsets are in [`buddy_field`].
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RowValues {
    /// The companion's config slot, 1..=4: the key its dressing is found by.
    pub slot: u8,
    pub npc_param: i32,
    pub think: i32,
    pub chara_init: i32,
    pub offset: Offset,
}

impl RowValues {
    /// Lay these values into a copy of a BuddyParam row: npc, think, charaInit and the formation
    /// offset. Every other byte, `triggerSpEffectId` included, keeps the row's own value.
    ///
    /// Returns `false`, with `row` untouched, when `row` is shorter than [`BUDDY_ROW_SIZE`].
    #[must_use]
    pub fn write_into(&self, row: &mut [u8]) -> bool {
        if row.len() < BUDDY_ROW_SIZE {
            return false;
        }
        put(row, buddy_field::NPC, self.npc_param.to_le_bytes());
        put(row, buddy_field::THINK, self.think.to_le_bytes());
        put(row, buddy_field::X, self.offset.x.to_le_bytes());
        put(row, buddy_field::Z, self.offset.z.to_le_bytes());
        put(row, buddy_field::YAW, self.offset.yaw.to_le_bytes());
        put(row, buddy_field::CHARA_INIT, self.chara_init.to_le_bytes());
        true
    }
}

/// Write `bytes` at `offset`. The caller has checked the row is long enough.
fn put<const N: usize>(row: &mut [u8], offset: usize, bytes: [u8; N]) {
    row[offset..offset + N].copy_from_slice(&bytes);
}

/// The plan for one summon: one entry per companion, in slot order. Its length is the length the
/// buddy list is grown (or shrunk) to.
#[must_use]
pub fn plan(companions: &[Companion]) -> Vec<RowValues> {
    companions
        .iter()
        .take(crate::config::MAX_COMPANIONS)
        .map(|companion| {
            let slot = usize::from(companion.slot.max(1)) - 1;
            RowValues {
                slot: companion.slot,
                npc_param: companion.body.npc_param,
                think: companion.think(),
                chara_init: companion.body.chara_init,
                offset: FORMATION[slot.min(FORMATION.len() - 1)],
            }
        })
        .collect()
}

/// Which plan entry the `n`th read of the human row inside one `BuddyGenerator` call gets. Reads
/// past the plan reuse the last entry rather than handing back the game's own Mimic Tear values.
#[must_use]
pub fn entry_for_read(plan: &[RowValues], read: usize) -> Option<RowValues> {
    plan.get(read).or_else(|| plan.last()).copied()
}

/// Which companion a `CreateSummonChr` call is building.
///
/// `BuddyGenerator` reads every row first (loop one, where each read is given a companion's
/// values and recorded in `pending`), then calls `CreateSummonChr` once per request it kept (loop
/// two, in the same list order). A request whose `NpcParam` row is missing, or whose block does
/// not resolve, is dropped between the loops, so the call count alone could pair a companion with
/// the next one's build. The call's own npc, think and charaInit arguments are what loop one
/// wrote, so the first pending entry carrying all three is the one being built. It is removed, so
/// two companions with the same body are matched in order.
pub fn claim(pending: &mut Vec<RowValues>, npc: i32, think: i32, chara_init: i32) -> Option<u8> {
    let index = pending.iter().position(|values| {
        values.npc_param == npc && values.think == think && values.chara_init == chara_init
    })?;
    Some(pending.remove(index).slot)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{Config, MIMIC_HUMAN_BODY};

    #[test]
    fn only_mimic_tear_requests_are_recognised() {
        assert_eq!(mimic_level(207_000), Some(0));
        assert_eq!(mimic_level(207_010), Some(10));
        assert_eq!(mimic_level(207_011), None);
        assert_eq!(mimic_level(206_999), None);
        assert_eq!(mimic_level(-1), None);
    }

    #[test]
    fn the_plan_follows_slots_and_ai() {
        let config = Config::parse(
            "[mimic.companion.1]\nai = { like_npc = 523180000 }\n\
             [mimic.companion.4]\nnpc_param = 523590024\nthink = 523590100\nchara_init = 23590\n",
        );
        let plan = plan(&config.mimic.companions);
        assert_eq!(plan.len(), 2);
        assert_eq!(plan[0].think, 523_180_000);
        assert_eq!(plan[0].offset, FORMATION[0]);
        assert_eq!(plan[1].npc_param, 523_590_024);
        assert_eq!(plan[1].offset, FORMATION[3]);
    }

    #[test]
    fn a_dropped_request_does_not_shift_the_pairing() {
        let config = Config::parse(
            "[mimic.companion.1]\nnpc_param = 1\n[mimic.companion.2]\n[mimic.companion.3]\n",
        );
        let mut pending = plan(&config.mimic.companions);
        let human = MIMIC_HUMAN_BODY;
        // Companion 1's request was dropped between the loops; the first call builds companion 2.
        assert_eq!(
            claim(&mut pending, human.npc_param, human.think, human.chara_init),
            Some(2)
        );
        assert_eq!(
            claim(&mut pending, human.npc_param, human.think, human.chara_init),
            Some(3)
        );
        assert_eq!(
            claim(&mut pending, human.npc_param, human.think, human.chara_init),
            None
        );
        assert_eq!(pending.len(), 1);
    }

    fn i32_at(row: &[u8], offset: usize) -> i32 {
        i32::from_le_bytes(row[offset..offset + 4].try_into().expect("four bytes"))
    }

    fn f32_at(row: &[u8], offset: usize) -> f32 {
        f32::from_le_bytes(row[offset..offset + 4].try_into().expect("four bytes"))
    }

    #[test]
    fn a_copy_changes_only_the_six_companion_fields() {
        // A stand-in for row 20700001 as the game holds it: every byte distinct, and the Mimic
        // Tear trigger at +0x4.
        let mut resident: Vec<u8> = (0..BUDDY_ROW_SIZE).map(|i| i as u8).collect();
        resident[4..8].copy_from_slice(&MIMIC_TRIGGER.to_le_bytes());
        let values = RowValues {
            slot: 2,
            npc_param: 523_590_024,
            think: 523_590_100,
            chara_init: 23_590,
            offset: FORMATION[1],
        };
        let mut copy = resident.clone();
        assert!(values.write_into(&mut copy));

        assert_eq!(i32_at(&copy, buddy_field::NPC), 523_590_024);
        assert_eq!(i32_at(&copy, buddy_field::THINK), 523_590_100);
        assert_eq!(i32_at(&copy, buddy_field::CHARA_INIT), 23_590);
        assert_eq!(f32_at(&copy, buddy_field::X), FORMATION[1].x);
        assert_eq!(f32_at(&copy, buddy_field::Z), FORMATION[1].z);
        assert_eq!(f32_at(&copy, buddy_field::YAW), FORMATION[1].yaw);
        assert_eq!(i32_at(&copy, 4), MIMIC_TRIGGER);

        let written = [
            buddy_field::NPC,
            buddy_field::THINK,
            buddy_field::X,
            buddy_field::Z,
            buddy_field::YAW,
            buddy_field::CHARA_INIT,
        ];
        for (offset, (&was, &now)) in resident.iter().zip(&copy).enumerate() {
            if !written
                .iter()
                .any(|&field| (field..field + 4).contains(&offset))
            {
                assert_eq!(was, now, "byte +0x{offset:x} changed");
            }
        }
    }

    #[test]
    fn a_short_row_is_left_alone() {
        let values = plan(&Config::parse("[mimic.companion.1]\n").mimic.companions)[0];
        let mut short = vec![0xaa; BUDDY_ROW_SIZE - 1];
        assert!(!values.write_into(&mut short));
        assert!(short.iter().all(|&b| b == 0xaa));
    }

    #[test]
    fn extra_reads_reuse_the_last_entry() {
        let config = Config::parse("[mimic.companion.1]\n[mimic.companion.2]\n");
        let plan = plan(&config.mimic.companions);
        assert_eq!(entry_for_read(&plan, 5), Some(plan[1]));
        assert_eq!(entry_for_read(&[], 0), None);
    }
}
