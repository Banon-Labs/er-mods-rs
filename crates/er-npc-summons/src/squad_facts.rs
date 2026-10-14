//! The game-task side of the lab's squad producers: reads the world each frame, runs `stuck`,
//! `hits`, `swings`, `volley`, `stance`, `cure` and `restock`, and hands what changed to `facts`.
//!
//! The squad is the companions this DLL built (`team::companions`), alive. `watched` is the lab's
//! list of characters near the squad: alive, not the player or the squad, within
//! `swings::READ_RADIUS` m of a companion, re-listed every `swings::READ_LIST_MS`. A companion's
//! enemy, as the lab's `enemyOf` picked it, is its lock-on target when it has a living one, else
//! the nearest watched character.

#![cfg(windows)]

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Instant;

use eldenring::cs::{EquipParamWeapon, SoloParamRepository, SpEffectParam};
use er_game_base::mem::game_module_base;
use fromsoftware_shared::FromStatic;

use crate::addr::lab;
use crate::cure::{self, Cure, Need, cure_for};
use crate::facts::{self, Notify};
use crate::hits::live::{self as hits_live, Publisher};
use crate::log::summons_log;
use crate::restock::{
    self, FOODS, Grace, HEAL_GOODS, HEALS, Step, Stock, goods_handle, goods_item, is_flask,
};
use crate::stance::{Crouch, MIMIC_VEIL, OnChange, STEALTH, crouched};
use crate::stuck::{self, Member, Stuck};
use crate::swings::{READ_LIST_MS, READ_RADIUS, Swings};
use crate::volley::{Target, Volley, is_status_category};
use crate::world::{
    GameMem, Mem, PLAYER_INS_RTTI, ai_target_handle, alive, all_chrs, anim_of, distance, gauges_of,
    hp_of, lock_handle_of, npc_param_of, pos_of, round_to, rtti_name, speffect_ids,
};
use crate::{game, team};

struct State {
    start: Instant,
    watched: Vec<usize>,
    watched_at: Option<u64>,
    stuck: Stuck,
    stuck_at: Option<u64>,
    hits: Publisher,
    swings: Swings,
    volley: Volley,
    crouch: Crouch,
    veil: OnChange<bool>,
    targeted: OnChange<Vec<(usize, usize)>>,
    cure: Cure,
    cure_at: Option<u64>,
    stock: Stock,
    stock_at: Option<u64>,
    grace: Grace,
    healed: Vec<usize>,
    categories: HashMap<i32, u16>,
    enhance: HashMap<i32, bool>,
}

static STATE: Mutex<Option<State>> = Mutex::new(None);

fn due(last: &mut Option<u64>, now: u64, every: u64) -> bool {
    if last.is_some_and(|at| now.saturating_sub(at) < every) {
        return false;
    }
    *last = Some(now);
    true
}

/// Install the `CalculateDamage2` detour; the caller applies the queue.
pub(crate) fn install() {
    hits_live::install();
}

/// Called from the game task every frame.
pub(crate) fn tick() {
    let mut guard = STATE.lock().unwrap_or_else(|e| e.into_inner());
    let state = guard.get_or_insert_with(|| State {
        start: Instant::now(),
        watched: Vec::new(),
        watched_at: None,
        stuck: Stuck::default(),
        stuck_at: None,
        hits: Publisher::default(),
        swings: Swings::default(),
        volley: Volley::default(),
        crouch: Crouch::default(),
        veil: OnChange::default(),
        targeted: OnChange::default(),
        cure: Cure::default(),
        cure_at: None,
        stock: Stock::default(),
        stock_at: None,
        grace: Grace::default(),
        healed: Vec::new(),
        categories: HashMap::new(),
        enhance: HashMap::new(),
    });
    let now = state.start.elapsed().as_millis() as u64;
    let mem = GameMem;
    let player = game::main_player();
    let squad: Vec<usize> = team::companions()
        .into_iter()
        .filter(|&c| alive(&mem, c))
        .collect();
    hits_live::set_squad(player.unwrap_or(0), &squad);
    state.hits.tick();
    for (npc, anim, time) in hits_live::take_windups() {
        state.swings.learn(npc, anim, time);
    }
    if squad.is_empty() {
        return;
    }
    if due(&mut state.watched_at, now, READ_LIST_MS) {
        state.watched = watched(&mem, player, &squad);
        state.swings.retain(&state.watched);
    }
    let me = player.and_then(|p| pos_of(&mem, p));
    swings(state, &mem, &squad, me);
    if let Some(player) = player {
        stance(state, &mem, now, player, &squad, me);
        volley(state, &mem, now, player);
    }
    if let Some(me) = me
        && due(&mut state.stuck_at, now, stuck::CHECK_MS)
    {
        stuck(state, &mem, now, me, &squad);
    }
    if let Some(me) = me
        && due(&mut state.cure_at, now, cure::CHECK_MS)
    {
        cure(state, &mem, me, &squad);
    }
    if due(&mut state.stock_at, now, restock::CHECK_MS) {
        restock(state, &mem, now, player, &squad);
    }
}

fn watched(mem: &GameMem, player: Option<usize>, squad: &[usize]) -> Vec<usize> {
    let Some(wcm) = game::world_chr_man() else {
        return Vec::new();
    };
    let at: Vec<[f32; 3]> = squad.iter().filter_map(|&c| pos_of(mem, c)).collect();
    all_chrs(mem, wcm)
        .into_iter()
        .filter(|&chr| Some(chr) != player && !squad.contains(&chr) && alive(mem, chr))
        .filter(|&chr| {
            pos_of(mem, chr).is_some_and(|p| at.iter().any(|&q| distance(p, q) <= READ_RADIUS))
        })
        .collect()
}

/// The living character behind `chr`'s lock-on handle.
fn lock_target(mem: &GameMem, chr: usize) -> Option<(u64, usize)> {
    let handle = lock_handle_of(mem, chr)?;
    let target = game::chr_by_handle(handle)?;
    alive(mem, target).then_some((handle, target))
}

fn enemy_of(mem: &GameMem, chr: usize, watched: &[usize]) -> Option<usize> {
    if let Some((_, t)) = lock_target(mem, chr) {
        return Some(t);
    }
    let p = pos_of(mem, chr)?;
    watched
        .iter()
        .filter(|&&w| alive(mem, w))
        .filter_map(|&w| pos_of(mem, w).map(|q| (w, distance(p, q))))
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .map(|(w, _)| w)
}

fn is_player_ins(mem: &GameMem, chr: usize) -> bool {
    game_module_base()
        .ok()
        .and_then(|base| rtti_name(mem, base, chr))
        .is_some_and(|name| name == PLAYER_INS_RTTI)
}

fn sp_category(cache: &mut HashMap<i32, u16>, id: i32) -> Option<u16> {
    if let Some(&c) = cache.get(&id) {
        return Some(c);
    }
    // SAFETY: game thread; the repository is resident in a loaded world.
    let repo = unsafe { SoloParamRepository::instance() }.ok()?;
    let c = repo
        .get::<SpEffectParam>(u32::try_from(id).ok()?)?
        .sp_category();
    cache.insert(id, c);
    Some(c)
}

/// The status categories running on `chr`, in the order its SpEffects list them.
fn status_categories(cache: &mut HashMap<i32, u16>, mem: &GameMem, chr: usize) -> Vec<u16> {
    let mut out = Vec::new();
    for id in speffect_ids(mem, chr) {
        if let Some(c) = sp_category(cache, id)
            && is_status_category(c)
            && !out.contains(&c)
        {
            out.push(c);
        }
    }
    out
}

fn swings(state: &mut State, mem: &GameMem, squad: &[usize], me: Option<[f32; 3]>) {
    let at: Vec<[f32; 3]> = squad.iter().filter_map(|&c| pos_of(mem, c)).collect();
    for &chr in &state.watched {
        let (Some(anim), Some(npc), Some(p)) =
            (anim_of(mem, chr), npc_param_of(mem, chr), pos_of(mem, chr))
        else {
            continue;
        };
        if let Some(roll) = state.swings.check(chr, npc, anim, p, &at, me) {
            facts::set_number("tt_roll", f64::from(roll.count), Notify::Quiet);
            facts::set_numbers("tt_roll_d", roll.to_attacker, Notify::Quiet);
            facts::set_numbers("tt_roll_p", roll.to_player, Notify::Quiet);
            facts::set_number("tt_roll_parry", 1.0, Notify::Quiet);
        }
    }
}

fn stance(
    state: &mut State,
    mem: &GameMem,
    now: u64,
    player: usize,
    squad: &[usize],
    me: Option<[f32; 3]>,
) {
    let effects = speffect_ids(mem, player);
    let anim = anim_of(mem, player).map(|a| a.anim);
    if let Some(on) = state
        .crouch
        .update(now, crouched(effects.contains(&STEALTH), anim))
    {
        facts::set_number("tt_crouch", f64::from(u8::from(on)), Notify::Replan);
    }
    if let Some(on) = state.veil.update(effects.contains(&MIMIC_VEIL)) {
        facts::set_number("tt_veil", f64::from(u8::from(on)), Notify::Replan);
        summons_log(format_args!(
            "squad: player's Mimic's Veil {}",
            if on { "up" } else { "down" }
        ));
    }
    let Some(me) = me else {
        return;
    };
    // Each watched enemy's pick, read once.
    let picks: Vec<(usize, u64)> = state
        .watched
        .iter()
        .filter_map(|&e| ai_target_handle(mem, e, is_player_ins(mem, e)).map(|h| (e, h)))
        .collect();
    let mut who = Vec::new();
    let mut out = Vec::new();
    for &c in squad {
        let Some(handle) = game::chr_handle(c) else {
            continue;
        };
        let Some(&(by, _)) = picks.iter().find(|(_, h)| *h == handle) else {
            continue;
        };
        let Some(p) = pos_of(mem, c) else {
            continue;
        };
        out.push(round_to(distance(p, me), 2));
        who.push((c, by));
    }
    if let Some(who) = state.targeted.update(who) {
        facts::set_numbers("tt_targeted_p", out, Notify::Replan);
        if !who.is_empty() {
            summons_log(format_args!("squad: targeted {who:x?}"));
        }
    }
}

fn volley(state: &mut State, mem: &GameMem, now: u64, player: usize) {
    let target = lock_target(mem, player).and_then(|(handle, chr)| {
        let (hp, _) = hp_of(mem, chr)?;
        let mut cats = status_categories(&mut state.categories, mem, chr);
        cats.sort_unstable();
        let human = is_player_ins(mem, chr) && npc_param_of(mem, chr).is_some_and(|n| n <= 0);
        Some(Target {
            handle,
            hp,
            cats,
            human,
        })
    });
    if let Some(f) = state.volley.update(now, target.as_ref()) {
        facts::set_number("tt_volley", f64::from(u8::from(f.on)), Notify::Quiet);
        facts::set_number("tt_volley_hp", f64::from(f.hp), Notify::Quiet);
        facts::set_numbers(
            "tt_volley_cats",
            f.cats.iter().map(|&c| f64::from(c)).collect(),
            Notify::Quiet,
        );
        facts::set_number(
            "tt_volley_human",
            f64::from(u8::from(f.human)),
            Notify::Quiet,
        );
    }
}

fn stuck(state: &mut State, mem: &GameMem, now: u64, me: [f32; 3], squad: &[usize]) {
    let members: Vec<Member> = squad
        .iter()
        .filter_map(|&c| {
            let pos = pos_of(mem, c)?;
            let enemy = enemy_of(mem, c, &state.watched).and_then(|e| pos_of(mem, e));
            Some(Member { chr: c, pos, enemy })
        })
        .collect();
    if let Some(r) = state.stuck.update(now, me, &members) {
        if let Some(why) = &r.changed
            && !why.is_empty()
        {
            summons_log(format_args!("squad: stuck {why:x?}"));
        }
        facts::set_numbers("tt_stuck_p", r.to_player, Notify::Quiet);
        facts::set_numbers("tt_stuck_e", r.to_enemy, Notify::Quiet);
    }
}

fn cure(state: &mut State, mem: &GameMem, me: [f32; 3], squad: &[usize]) {
    let mut needs = Vec::new();
    for &c in squad {
        let (Some((hp, max)), Some(p)) = (hp_of(mem, c), pos_of(mem, c)) else {
            continue;
        };
        let cats = status_categories(&mut state.categories, mem, c);
        let gauges = gauges_of(mem, c);
        let Some(item) = cure_for(&cats, gauges.as_ref()) else {
            continue;
        };
        needs.push(Need {
            chr: c,
            to_player: distance(p, me),
            hp_share: if max > 0 {
                f64::from(hp) / f64::from(max)
            } else {
                0.0
            },
            item,
        });
    }
    if let Some((flat, changed)) = state.cure.update(&needs) {
        if changed && !needs.is_empty() {
            let list: Vec<(usize, u32)> = needs.iter().map(|n| (n.chr, n.item)).collect();
            summons_log(format_args!("squad: cure {list:x?}"));
        }
        facts::set_numbers("tt_cure", flat, Notify::Quiet);
    }
}

/// `EquipGameData*` of a `PlayerIns`.
fn equip_game_data(mem: &GameMem, chr: usize) -> Option<usize> {
    let pgd = mem.usize(chr + lab::PLAYER_GAME_DATA).filter(|&p| p != 0)?;
    Some(pgd + lab::EQUIP_GAME_DATA)
}

/// The normal inventory: `(entry address, item id, quantity)` per entry, at most 1024.
fn inventory(mem: &GameMem, egd: usize) -> Vec<(usize, u32, u32)> {
    let inv = egd + lab::INVENTORY;
    let (Some(head), Some(len)) = (
        mem.usize(inv + lab::INV_ENTRIES).filter(|&p| p != 0),
        mem.u32(inv + lab::INV_COUNT),
    ) else {
        return Vec::new();
    };
    (0..len.min(1024) as usize)
        .filter_map(|i| {
            let e = head + i * lab::INV_ENTRY;
            Some((e, mem.u32(e + 4)?, mem.u32(e + 8)?))
        })
        .collect()
}

fn greasable(state: &mut State, mem: &GameMem, chr: usize) -> bool {
    let Some(asm) = mem.usize(chr + lab::CHR_ASM).filter(|&p| p != 0) else {
        return false;
    };
    let Some(id) = mem.u32(asm + lab::CHR_ASM_PARAM_IDS + lab::CHR_ASM_RIGHT1 * 4) else {
        return false;
    };
    // The row is the param id with the upgrade level dropped.
    let base = (id as i32 / 100) * 100;
    if let Some(&e) = state.enhance.get(&base) {
        return e;
    }
    // SAFETY: game thread; the repository is resident in a loaded world.
    let Some(e) = unsafe { SoloParamRepository::instance() }
        .ok()
        .and_then(|repo| {
            repo.get::<EquipParamWeapon>(u32::try_from(base).ok()?)
                .map(|r| r.is_enhance())
        })
    else {
        return false;
    };
    state.enhance.insert(base, e);
    e
}

/// Set the heal entry to [`HEALS`], or grant it back when it was drunk to zero.
fn set_heals(mem: &GameMem, chr: usize) {
    let Some(egd) = equip_game_data(mem, chr) else {
        return;
    };
    let item = goods_item(HEAL_GOODS);
    if let Some(&(entry, _, before)) = inventory(mem, egd).iter().find(|(_, i, _)| *i == item) {
        if before != HEALS {
            // SAFETY: the quantity field of a live inventory entry this walk just read.
            unsafe { core::ptr::write_volatile((entry + 8) as *mut u32, HEALS) };
            summons_log(format_args!("squad: 0x{chr:x} heals {before} -> {HEALS}"));
        }
        return;
    }
    match game::add_inventory_equip(egd, goods_handle(HEAL_GOODS), item, HEALS) {
        Ok(r) => summons_log(format_args!("squad: 0x{chr:x} heals granted back ({r})")),
        Err(why) => summons_log(format_args!("squad: 0x{chr:x} heals not granted: {why}")),
    }
}

fn restock(state: &mut State, mem: &GameMem, now: u64, player: Option<usize>, squad: &[usize]) {
    state.stock.retain(squad);
    state.healed.retain(|c| squad.contains(c));
    // A grace rest refills everyone; a new companion is refilled once.
    let rest = player.is_some_and(|p| {
        let flasks = equip_game_data(mem, p)
            .map(|egd| {
                inventory(mem, egd)
                    .iter()
                    .filter(|(_, item, _)| is_flask(*item))
                    .map(|(_, _, q)| q)
                    .sum()
            })
            .unwrap_or(0);
        state.grace.update(anim_of(mem, p).map(|a| a.anim), flasks)
    });
    for &c in squad {
        if rest || !state.healed.contains(&c) {
            set_heals(mem, c);
            if !state.healed.contains(&c) {
                state.healed.push(c);
            }
        }
        for (index, food) in FOODS.iter().enumerate() {
            if food.needs_enhance && !greasable(state, mem, c) {
                continue;
            }
            stock_food(state, mem, now, c, index);
        }
    }
}

fn stock_food(state: &mut State, mem: &GameMem, now: u64, chr: usize, index: usize) {
    let food = FOODS[index];
    let Some(egd) = equip_game_data(mem, chr) else {
        return;
    };
    let item = goods_item(food.goods);
    let handle = goods_handle(food.goods);
    let find = |mem: &GameMem| {
        inventory(mem, egd)
            .iter()
            .position(|&(_, i, q)| i == item && q > 0)
    };
    let at = match state.stock.step(now, chr, index, find(mem)) {
        Step::Wait => return,
        Step::Eaten => {
            summons_log(format_args!("squad: 0x{chr:x} used its {}", food.name));
            return;
        }
        Step::Slot(at) => at,
        Step::Grant => {
            if let Err(why) = game::add_inventory_equip(egd, handle, item, food.qty) {
                summons_log(format_args!("squad: {} not granted: {why}", food.name));
                return;
            }
            let Some(at) = find(mem) else {
                return;
            };
            state.stock.granted(chr, index);
            at
        }
    };
    let Some(key_capacity) = mem.u32(egd + lab::INV_KEY_CAPACITY) else {
        return;
    };
    let inv_index = key_capacity as i32 + at as i32;
    let slot = egd + lab::QUICK_SLOTS + 8 * food.slot;
    if mem.u32(slot) == Some(handle) && mem.u32(slot + 4) == Some(inv_index as u32) {
        return;
    }
    // SAFETY: the quick slot and item-id mirror of this companion's live `EquipGameData`, written
    // the way the lab wrote them.
    unsafe {
        core::ptr::write_volatile(slot as *mut u32, handle);
        core::ptr::write_volatile((slot + 4) as *mut i32, inv_index);
        core::ptr::write_volatile(
            (egd + lab::QUICK_ITEM_IDS + 4 * food.slot) as *mut u32,
            item,
        );
    }
}
