//! The rules' param questions, answered from the live `SoloParamRepository`.
//!
//! Live rather than a table generated offline, so a weapon's guard and weight are the ones this
//! regulation gives it at this upgrade level.

#![cfg(windows)]

use eldenring::cs::{
    CalcCorrectGraph, EquipParamAccessory, EquipParamProtector, EquipParamWeapon,
    ReinforceParamWeapon, SoloParam, SoloParamRepository, SpEffectParam,
};
use er_duel_rules_core::EquipKind;
use er_duel_rules_core::facts::{CorrectGraph, EffectFacts, GameFacts, WeaponFacts};
use er_duel_rules_core::player::{weapon_level, weapon_row};
use fromsoftware_shared::FromStatic;

/// `CalcCorrectGraph` row `CalculateEffectiveEndurance` (1.16.2 `0x14068d000`) passes for max
/// equip load (`mov edx,0xdc`).
const GRAPH_EQUIP_LOAD: u32 = 220;
/// `EquipParamAccessory.refCategory` naming a SpEffect.
const REF_CATEGORY_SPEFFECT: u8 = 2;

pub(crate) struct LiveFacts {
    repo: &'static SoloParamRepository,
}

fn holder_ready<P: SoloParam>(repo: &SoloParamRepository) -> bool {
    repo.solo_param_holders
        .get(P::INDEX as usize)
        .and_then(|holder| holder.get_res_cap(0))
        .is_some()
}

impl LiveFacts {
    /// The repository, once every table the rules read has streamed in. `get` panics on a table
    /// that has not (`holder.get_res_cap(0).expect(..)`), so each is checked first.
    pub(crate) fn get() -> Option<Self> {
        // SAFETY: game thread; every row is read, never written.
        let repo = unsafe { SoloParamRepository::instance() }.ok()?;
        let ready = holder_ready::<EquipParamWeapon>(repo)
            && holder_ready::<ReinforceParamWeapon>(repo)
            && holder_ready::<EquipParamProtector>(repo)
            && holder_ready::<EquipParamAccessory>(repo)
            && holder_ready::<SpEffectParam>(repo)
            && holder_ready::<CalcCorrectGraph>(repo);
        ready.then_some(Self { repo })
    }
}

fn param_id(id: i32) -> Option<u32> {
    u32::try_from(id).ok()
}

impl GameFacts for LiveFacts {
    fn weapon(&self, id: i32) -> Option<WeaponFacts> {
        let row = self
            .repo
            .get::<EquipParamWeapon>(param_id(weapon_row(id))?)?;
        let reinforce_row = i32::from(row.reinforce_type_id()) + weapon_level(id);
        let guard_rate = param_id(reinforce_row)
            .and_then(|r| self.repo.get::<ReinforceParamWeapon>(r))
            .map_or(1.0, |r| r.physics_guard_cut_rate());
        Some(WeaponFacts {
            wep_type: row.wep_type(),
            weight: row.weight(),
            phys_guard_cut: row.phys_guard_cut_rate() * guard_rate,
            inflicts_status: [
                row.sp_effect_behavior_id0(),
                row.sp_effect_behavior_id1(),
                row.sp_effect_behavior_id2(),
            ]
            .iter()
            .any(|&behavior| behavior > 0),
            takes_grease: row.is_enhance(),
        })
    }

    fn armor_weight(&self, id: i32) -> Option<f32> {
        Some(
            self.repo
                .get::<EquipParamProtector>(param_id(id)?)?
                .weight(),
        )
    }

    fn talisman_weight(&self, id: i32) -> Option<f32> {
        Some(
            self.repo
                .get::<EquipParamAccessory>(param_id(id)?)?
                .weight(),
        )
    }

    fn resident_effects(&self, kind: EquipKind, id: i32) -> Vec<i32> {
        let Some(id) = param_id(id) else {
            return Vec::new();
        };
        match kind {
            EquipKind::Talisman => self
                .repo
                .get::<EquipParamAccessory>(id)
                .map(|row| {
                    let mut ids = vec![
                        row.resident_sp_effect_id1(),
                        row.resident_sp_effect_id2(),
                        row.resident_sp_effect_id3(),
                        row.resident_sp_effect_id4(),
                    ];
                    if row.ref_category() == REF_CATEGORY_SPEFFECT {
                        ids.push(row.ref_id());
                    }
                    ids
                })
                .unwrap_or_default(),
            EquipKind::Armor => self
                .repo
                .get::<EquipParamProtector>(id)
                .map(|row| {
                    vec![
                        row.resident_sp_effect_id(),
                        row.resident_sp_effect_id2(),
                        row.resident_sp_effect_id3(),
                    ]
                })
                .unwrap_or_default(),
            EquipKind::Shield => Vec::new(),
        }
    }

    fn effect(&self, id: i32) -> Option<EffectFacts> {
        let row = self.repo.get::<SpEffectParam>(param_id(id)?)?;
        Some(EffectFacts {
            category: row.sp_category(),
            equip_weight_rate: row.equip_weight_change_rate(),
            add_endurance: i32::from(row.add_endure_status()),
        })
    }

    fn equip_load_base(&self, endurance: i32) -> Option<f32> {
        let g = self.repo.get::<CalcCorrectGraph>(GRAPH_EQUIP_LOAD)?;
        let graph = CorrectGraph {
            stage_max_val: [
                g.stage_max_val0(),
                g.stage_max_val1(),
                g.stage_max_val2(),
                g.stage_max_val3(),
                g.stage_max_val4(),
            ],
            stage_max_grow_val: [
                g.stage_max_grow_val0(),
                g.stage_max_grow_val1(),
                g.stage_max_grow_val2(),
                g.stage_max_grow_val3(),
                g.stage_max_grow_val4(),
            ],
            adj_pt_max_grow_val: [
                g.adj_pt_max_grow_val0(),
                g.adj_pt_max_grow_val1(),
                g.adj_pt_max_grow_val2(),
                g.adj_pt_max_grow_val3(),
                g.adj_pt_max_grow_val4(),
            ],
        };
        Some(graph.at(endurance as f32))
    }
}
