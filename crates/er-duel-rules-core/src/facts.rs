//! What the rules need from the game's param tables, behind a trait so the DLL answers from the
//! live `SoloParamRepository` and the tests answer from a table.

use crate::EquipKind;
use crate::player::PlayerSnapshot;

/// One weapon row, as the rules read it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct WeaponFacts {
    /// `EquipParamWeapon.wepType`.
    pub wep_type: u16,
    pub weight: f32,
    /// Physical guard absorption in percent at this upgrade level:
    /// `physGuardCutRate * ReinforceParamWeapon.physicsGuardCutRate`.
    pub phys_guard_cut: f32,
    /// Whether the row's hit effects put status on a target (`spEffectBehaviorId0..2`).
    pub inflicts_status: bool,
    /// `isEnhance`: a grease can be applied to it (`CanUseGoods`). Set only on the Standard,
    /// Heavy, Keen and Quality rows (bd buff-ashes-share-grease-slot-162-isenhance-2026-09-29).
    pub takes_grease: bool,
}

/// One SpEffect row, as the rules read it.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct EffectFacts {
    /// `spCategory`: 162 is a buff on the right weapon, 163 on the left.
    pub category: u16,
    /// `equipWeightChangeRate`; rates at or below 0 are skipped the way the game skips them.
    pub equip_weight_rate: f32,
    /// `addEndureStatus`.
    pub add_endurance: i32,
}

/// `spCategory` of a weapon buff, by hand.
pub const CATEGORY_RIGHT_WEAPON_BUFF: u16 = 162;
pub const CATEGORY_LEFT_WEAPON_BUFF: u16 = 163;

pub trait GameFacts {
    fn weapon(&self, id: i32) -> Option<WeaponFacts>;
    fn armor_weight(&self, id: i32) -> Option<f32>;
    fn talisman_weight(&self, id: i32) -> Option<f32>;
    /// SpEffect rows a worn talisman or armour piece keeps on its wearer.
    fn resident_effects(&self, kind: EquipKind, id: i32) -> Vec<i32>;
    fn effect(&self, id: i32) -> Option<EffectFacts>;
    /// `CalcCorrectGraph` row 220 at this effective endurance: max equip load before rates.
    fn equip_load_base(&self, endurance: i32) -> Option<f32>;
}

/// One `CalcCorrectGraph` row.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CorrectGraph {
    pub stage_max_val: [f32; 5],
    pub stage_max_grow_val: [f32; 5],
    pub adj_pt_max_grow_val: [f32; 5],
}

impl CorrectGraph {
    /// `PerformCalcCorrection` (1.16.2 `0x140690f30`) in single precision, as
    /// `scripts/er-mechanics-resources.py` reproduces it: clamp to the last stage, find the
    /// stage, raise the position inside it to the stage's exponent, interpolate, clamp.
    pub fn at(&self, x: f32) -> f32 {
        let sv = self.stage_max_val;
        let g = self.stage_max_grow_val;
        let x = x.min(sv[4]);
        if x.is_nan() || x <= 0.0 {
            return g[0];
        }
        let mut i = 0;
        while i < 4 && x > sv[i + 1] {
            i += 1;
        }
        if i == 4 {
            return g[4];
        }
        let lo = sv[i];
        let span = sv[i + 1] - sv[i];
        let dg = g[i + 1] - g[i];
        let e = self.adj_pt_max_grow_val[i];
        let v = if e < 0.0 {
            let p = ((span - (x - lo)) / span).powf(-e);
            g[i] + (1.0 - p) * dg
        } else {
            let p = ((x - lo) / span).powf(e);
            g[i] + p * dg
        };
        v.max(g[i].min(g[i + 1])).min(g[i].max(g[i + 1]))
    }
}

/// Attribute cap `CalculateStatus` applies after talisman and armour additions.
const ATTRIBUTE_CAP: i32 = 99;

/// The load a player carries against what they can carry.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct EquipLoad {
    pub burden: f32,
    pub max: f32,
}

impl EquipLoad {
    pub fn ratio(&self) -> f32 {
        if self.max > 0.0 {
            self.burden / self.max
        } else {
            0.0
        }
    }

    /// `GetWeightType` (1.16.2 `0x14068c630`) calls a ratio at or below 0.3 light.
    pub fn is_light(&self) -> bool {
        self.max > 0.0 && self.ratio() <= LIGHT_LOAD_CEILING
    }
}

/// `.rdata` 1.17.1 `0x143b37d64`, the medium-load threshold `GetWeightType` compares against.
pub const LIGHT_LOAD_CEILING: f32 = 0.3;

/// Burden and max load the way the game sums them (`CalculateEquipmentWeight` 1.16.2
/// `0x140247b80`: six weapons, four armour pieces, the talismans; ammunition is not counted).
/// `None` when an equipped item has no row, so a half-known build is never called light.
pub fn equip_load(p: &PlayerSnapshot, facts: &impl GameFacts) -> Option<EquipLoad> {
    let mut burden = 0.0_f32;
    for id in p.weapons() {
        burden += facts.weapon(id)?.weight;
    }
    for &id in p.armor.iter().filter(|&&id| id >= 0) {
        burden += facts.armor_weight(id)?;
    }
    for &id in p.talismans.iter().filter(|&&id| id >= 0) {
        burden += facts.talisman_weight(id)?;
    }
    // Every effect on the character, plus what its talismans and armour keep on it, each once:
    // a remote player's resident effects may or may not be in their list on this machine.
    let mut effects: Vec<i32> = p.speffects.clone();
    for &id in p.talismans.iter().filter(|&&id| id >= 0) {
        effects.extend(facts.resident_effects(EquipKind::Talisman, id));
    }
    for &id in p.armor.iter().filter(|&&id| id >= 0) {
        effects.extend(facts.resident_effects(EquipKind::Armor, id));
    }
    effects.retain(|&id| id > 0);
    effects.sort_unstable();
    effects.dedup();
    let mut rate = 1.0_f32;
    let mut add_endurance = 0;
    for id in effects {
        if let Some(effect) = facts.effect(id) {
            if effect.equip_weight_rate > 0.0 {
                rate *= effect.equip_weight_rate;
            }
            add_endurance += effect.add_endurance;
        }
    }
    let endurance = (p.endurance as i32 + add_endurance).min(ATTRIBUTE_CAP);
    let max = facts.equip_load_base(endurance)? * rate;
    Some(EquipLoad { burden, max })
}

#[cfg(test)]
pub(crate) mod test_support {
    use std::collections::HashMap;

    use super::*;

    /// `CalcCorrectGraph` 220 in the installed regulation (read with `er-param-read.py
    /// CalcCorrectGraph --row 220`).
    pub(crate) const GRAPH_220: CorrectGraph = CorrectGraph {
        stage_max_val: [1.0, 8.0, 25.0, 60.0, 99.0],
        stage_max_grow_val: [45.0, 45.0, 72.0, 120.0, 160.0],
        adj_pt_max_grow_val: [1.0, 1.0, 1.1, 1.0, 1.0],
    };

    /// A param table the tests fill in by hand.
    #[derive(Default)]
    pub(crate) struct Table {
        pub weapons: HashMap<i32, WeaponFacts>,
        pub armor: HashMap<i32, f32>,
        pub talismans: HashMap<i32, f32>,
        pub resident: HashMap<i32, Vec<i32>>,
        pub effects: HashMap<i32, EffectFacts>,
    }

    impl Table {
        pub(crate) fn weapon(mut self, id: i32, wep_type: u16, weight: f32) -> Self {
            self.weapons.insert(
                crate::player::weapon_row(id),
                WeaponFacts {
                    wep_type,
                    weight,
                    phys_guard_cut: 50.0,
                    inflicts_status: false,
                    takes_grease: true,
                },
            );
            self
        }

        pub(crate) fn with(mut self, id: i32, facts: WeaponFacts) -> Self {
            self.weapons.insert(crate::player::weapon_row(id), facts);
            self
        }

        pub(crate) fn effect(mut self, id: i32, facts: EffectFacts) -> Self {
            self.effects.insert(id, facts);
            self
        }
    }

    impl GameFacts for Table {
        fn weapon(&self, id: i32) -> Option<WeaponFacts> {
            self.weapons.get(&crate::player::weapon_row(id)).copied()
        }
        fn armor_weight(&self, id: i32) -> Option<f32> {
            self.armor.get(&id).copied()
        }
        fn talisman_weight(&self, id: i32) -> Option<f32> {
            self.talismans.get(&id).copied()
        }
        fn resident_effects(&self, _kind: EquipKind, id: i32) -> Vec<i32> {
            self.resident.get(&id).cloned().unwrap_or_default()
        }
        fn effect(&self, id: i32) -> Option<EffectFacts> {
            self.effects.get(&id).copied()
        }
        fn equip_load_base(&self, endurance: i32) -> Option<f32> {
            Some(GRAPH_220.at(endurance as f32))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::{GRAPH_220, Table};
    use super::*;

    #[test]
    fn graph_220_matches_the_game_at_its_stage_ends() {
        // The same points scripts/er-mechanics-resources.py --selftest checks.
        assert_eq!(GRAPH_220.at(8.0), 45.0);
        assert_eq!(GRAPH_220.at(99.0), 160.0);
        assert_eq!(GRAPH_220.at(150.0), 160.0);
        assert_eq!(GRAPH_220.at(0.0), 45.0);
        assert_eq!(GRAPH_220.at(60.0), 120.0);
    }

    #[test]
    fn light_is_at_or_under_thirty_percent() {
        let load = |burden| EquipLoad { burden, max: 100.0 };
        assert!(load(30.0).is_light());
        assert!(!load(30.1).is_light());
        assert!(
            !EquipLoad {
                burden: 0.0,
                max: 0.0
            }
            .is_light()
        );
    }

    #[test]
    fn equip_load_counts_rates_and_endurance_from_worn_talismans_once() {
        let mut table = Table::default().weapon(2_000_000, 3, 3.5);
        table.armor.insert(100, 10.0);
        table.talismans.insert(200, 0.5);
        // A talisman that raises max load by 19% and endurance by 5.
        table.resident.insert(200, vec![900]);
        let table = table.effect(
            900,
            EffectFacts {
                category: 0,
                equip_weight_rate: 1.19,
                add_endurance: 5,
            },
        );
        let p = PlayerSnapshot {
            endurance: 20,
            weapons_right: [2_000_025, -1, -1],
            armor: [100, -1, -1, -1],
            talismans: [200, -1, -1, -1],
            // The same resident effect also shows in the list; it must not count twice.
            speffects: vec![900],
            ..PlayerSnapshot::default()
        };
        let load = equip_load(&p, &table).expect("every item is known");
        assert!((load.burden - 14.0).abs() < 1e-4);
        let expected = GRAPH_220.at(25.0) * 1.19;
        assert!(
            (load.max - expected).abs() < 1e-3,
            "{} vs {expected}",
            load.max
        );
    }

    #[test]
    fn an_unknown_item_gives_no_load_rather_than_a_light_one() {
        let p = PlayerSnapshot {
            endurance: 20,
            weapons_right: [2_000_000, -1, -1],
            ..PlayerSnapshot::default()
        };
        assert_eq!(equip_load(&p, &Table::default()), None);
    }
}
