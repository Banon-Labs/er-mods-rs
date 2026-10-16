//! Every player in the world, read out of the live game as [`PlayerSnapshot`]s.
//!
//! Read-only throughout: no detour, no write, no game function called. Every read happens on the
//! game thread from inside a `FrameBegin` task, so the ChrSet and the bullet list being walked are
//! the ones the engine owns at that instant.
//!
//! The local player and every remote one sit in `WorldChrMan::player_chr_set` (measured live
//! 2026-08-25 under Seamless: `sets=1 widened=false types=[2:1 0:3]`, bd
//! chrtype-7-is-not-a-player-wide-sweep-must-be-allow-list-2026-08-25). A remote player's
//! `PlayerGameData` is one of `GameDataMan`'s five remote slots, filled by packets 8, 11 and 12;
//! its slot state reads `0xF` once all three have arrived (`IsRemotePlayerGameDataFullySynced`,
//! 1.16.2 `0x1402569b0`), and a remote player is judged only from then on.

#![cfg(windows)]

use std::collections::{HashMap, HashSet};

use eldenring::cs::{CSBulletManager, FieldInsHandle, GameDataMan, PlayerGameData, WorldChrMan};
use er_duel_rules_core::player::{AnimCursor, ArmStyle, NO_ITEM, PlayerSnapshot, anim_starts};
use fromsoftware_shared::FromStatic;

/// `RemotePlayerDataSlotState::FullySynced`: base data, equipment and type all received.
const REMOTE_FULLY_SYNCED: u8 = 0xF;
/// `GameDataMan` keeps five remote `PlayerGameData` slots.
const REMOTE_SLOTS: usize = 5;
/// `ChrAsm::equipment_param_ids` layout (`ChrAsmSlot`): weapons left/right alternate from 0,
/// armour from 12, talismans from 17.
const SLOT_WEAPON_LEFT: [usize; 3] = [0, 2, 4];
const SLOT_WEAPON_RIGHT: [usize; 3] = [1, 3, 5];
const SLOT_ARMOR: usize = 12;
const SLOT_TALISMAN: usize = 17;
/// `CSRideNode::ride_state`: 0 is on foot; 3 getting on, 5 riding, 7 getting off.
const RIDE_STATE_NONE: u32 = 0;

/// What the reader carries between frames.
#[derive(Default)]
pub(crate) struct Reader {
    /// Where each player's TimeAct history stood last frame, to tell a newly started clip from
    /// one already playing.
    anims: HashMap<u64, AnimCursor>,
}

/// Counts for the status line.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct ReadStats {
    pub players: usize,
    pub remote_pending: usize,
    pub bullets_owned: usize,
}

fn handle_key(handle: &FieldInsHandle) -> u64 {
    // SAFETY: `FieldInsHandle` is `repr(C, align(8))`: two `u32`s, eight bytes.
    unsafe { std::ptr::read(std::ptr::from_ref(handle).cast::<u64>()) }
}

fn name_of(pgd: &PlayerGameData) -> String {
    let len = pgd
        .character_name
        .iter()
        .position(|&c| c == 0)
        .unwrap_or(pgd.character_name.len());
    String::from_utf16(&pgd.character_name[..len]).unwrap_or_else(|_| "?".to_owned())
}

/// The remote slot state of `pgd`, or `None` when it is not one of the five remote slots.
///
/// # Safety
///
/// Game thread, with `GameDataMan` live.
unsafe fn remote_state(game_data: &GameDataMan, pgd: *const PlayerGameData) -> Option<u8> {
    let base = game_data
        .player_game_data_list
        .as_ptr()
        .cast::<PlayerGameData>();
    // SAFETY: both pointers are into game memory; only their distance is used.
    let offset = unsafe { pgd.offset_from(base) };
    let index = usize::try_from(offset).ok().filter(|&i| i < REMOTE_SLOTS)?;
    // The slot state is a bitfield byte; reading it as the upstream enum would be undefined for
    // the partial states (3, 5, 7, ...), so it is read as the byte it is.
    let states = game_data.remote_game_data_states.as_ptr().cast::<u8>();
    // SAFETY: five bytes, `index < 5`.
    Some(unsafe { states.add(index).read() })
}

/// Bullet param ids by the handle of the character that owns them.
///
/// # Safety
///
/// Game thread.
unsafe fn bullets_by_owner() -> HashMap<u64, Vec<i32>> {
    let mut out: HashMap<u64, Vec<i32>> = HashMap::new();
    // SAFETY: game thread.
    let Ok(manager) = (unsafe { CSBulletManager::instance() }) else {
        return out;
    };
    for bullet in manager.bullets() {
        let owner = handle_key(&bullet.targeting_owner.owner_chr_handle);
        let id = [
            bullet.fly_state.base.param.param_id,
            bullet.wait_state.base.param.param_id,
            bullet.exp_state.base.param.param_id,
        ]
        .into_iter()
        .find(|&id| id > 0);
        if let Some(id) = id {
            out.entry(owner).or_default().push(id);
        }
    }
    out
}

/// Every player the rules may judge this frame.
///
/// # Safety
///
/// Game thread, inside a game task.
pub(crate) unsafe fn read_players(reader: &mut Reader) -> (Vec<PlayerSnapshot>, ReadStats) {
    let mut stats = ReadStats::default();
    // SAFETY: game thread.
    let (Ok(world), Ok(game_data)) = (unsafe { WorldChrMan::instance() }, unsafe {
        GameDataMan::instance()
    }) else {
        reader.anims.clear();
        return (Vec::new(), stats);
    };
    let main_player = world
        .main_player
        .as_ref()
        .map(|p| std::ptr::from_ref::<eldenring::cs::PlayerIns>(p));
    // SAFETY: game thread.
    let mut bullets = unsafe { bullets_by_owner() };
    let mut players = Vec::new();
    let mut seen = HashSet::new();
    for player in world.player_chr_set.characters() {
        let chr = &player.chr_ins;
        let key = handle_key(&chr.field_ins_handle);
        seen.insert(key);
        let is_local = main_player == Some(std::ptr::from_ref(&*player));
        // SAFETY: a PlayerIns always carries its PlayerGameData.
        let pgd = unsafe { player.player_game_data.as_ref() };
        if !is_local {
            // SAFETY: game thread.
            let state = unsafe { remote_state(game_data, std::ptr::from_ref(pgd)) };
            if state != Some(REMOTE_FULLY_SYNCED) {
                stats.remote_pending += 1;
                continue;
            }
        }
        let asm = &pgd.equipment.chr_asm;
        let ids = &asm.equipment_param_ids;
        // Raw, for the same reason as the slot state: an unexpected value must not become an
        // invalid enum.
        // SAFETY: `ChrAsmArmStyle` is a `u32`-sized field.
        let arm_style = unsafe {
            std::ptr::from_ref(&asm.equipment.arm_style)
                .cast::<u32>()
                .read()
        };
        let slots = &asm.equipment.selected_slots;
        let equipment = &pgd.equipment;
        let physick = [
            equipment.physick_tears[0].param_id(),
            equipment.physick_tears[1].param_id(),
            equipment.extra_physick_tear.param_id(),
        ]
        .map(|id| id.and_then(|id| i32::try_from(id).ok()).unwrap_or(NO_ITEM));

        let time_act = &chr.modules.time_act;
        let history = time_act
            .anim_queue
            .each_ref()
            .map(|a| (a.anim_id, a.play_time));
        let (new_anims, cursor) = anim_starts(
            reader.anims.get(&key).copied(),
            &history,
            time_act.write_idx as usize,
        );
        if let Some(cursor) = cursor {
            reader.anims.insert(key, cursor);
        }

        let owned = bullets.remove(&key).unwrap_or_default();
        stats.bullets_owned += owned.len();
        players.push(PlayerSnapshot {
            key,
            name: name_of(pgd),
            is_local,
            level: pgd.level,
            endurance: pgd.endurance,
            arm_style: ArmStyle::from_raw(arm_style),
            left_slot: slots.left_weapon_slot as usize,
            right_slot: slots.right_weapon_slot as usize,
            weapons_left: SLOT_WEAPON_LEFT.map(|i| ids[i]),
            weapons_right: SLOT_WEAPON_RIGHT.map(|i| ids[i]),
            armor: std::array::from_fn(|i| ids[SLOT_ARMOR + i]),
            talismans: std::array::from_fn(|i| ids[SLOT_TALISMAN + i]),
            physick,
            speffects: chr.special_effect.entries().map(|e| e.param_id).collect(),
            new_anims,
            bullets: owned,
            hp: chr.modules.data.hp,
            riding: chr.modules.ride.ride_node.ride_state != RIDE_STATE_NONE,
        });
    }
    reader.anims.retain(|key, _| seen.contains(key));
    stats.players = players.len();
    (players, stats)
}
