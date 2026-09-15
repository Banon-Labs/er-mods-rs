//! Which multiplayer effects are active on this host, for publishing onto its Steam lobby.
//!
//! # What this answers, and why an invader wants it
//!
//! `lobby_publish` tells an invader where a host is. This tells them whether getting in is even
//! possible. `CS::PlayerIns::CanBeSoloInvaded` (`0x14068da80`, 1.16.2) returns true immediately
//! when SpEffect 28 is present on the local player; otherwise it requires `soloBreakInPoint >=
//! GameSystemCommonParam.soloBreakInMaxPoint`, and in the shipped params that threshold is 10000
//! while every `addSoloBreakInPoint_*` is 0. The fallback branch is unreachable, so SpEffect 28 is
//! the only thing that makes a solo host invadable at all. A host advertising it is worth
//! querying; one advertising `none` is a world an invader cannot enter solo.
//!
//! # The id is 28, and it is emphatically not 533
//!
//! Taunter's Tongue is `EquipParamGoods` 108, whose `refId_default` is SpEffect 533. Publishing
//! 533 would have been the obvious mistake and a silent one: `SpEffectParam` 533 has
//! `effectEndurance = 0.0` and `iconId = -1`, so it is a one-shot trigger that is essentially
//! never present when asked about. What persists is 28 -- `effectEndurance = -1.0`, `iconId =
//! 20200`, the sole owner of that icon among all 11,354 SpEffect rows -- and nothing in
//! `regulation.bin` references it. Only native code applies it, inside
//! `CS::PlayerIns::UpdateMultiplayData` (`0x14065a930`), gated on `IsMainPlayerIns`:
//!
//! ```text
//! if (HasSpecialEffectWithStateInfo(chrIns->specialEffect, 0x198))   // stateInfo 408
//!     HasSpecialEffectId(chrIns->specialEffect, 0x1c)                // 28
//!         ? ChrIns::RemoveSpEffectById(chrIns, 0x1c)
//!         : ChrIns::ApplySpEffect(chrIns, 0x1c, false);
//! ```
//!
//! The chain, every link read out of the installed `regulation.bin` (sha256
//! `766521f9…12ca2ab`) and `msg/engus/item.msgbnd.dcx`: `GoodsName` 108 "Taunter's Tongue" ->
//! `EquipParamGoods` 108 `refId_default` 533 -> `SpEffectParam` 533 `stateInfo` 408 -> the native
//! toggle above -> 28. Recorded in bd `taunters-tongue-host-speffect-28-not-533-2026-09-15`.
//!
//! Elden Ring has no "Dried Finger", which is the Dark Souls name for the same idea; a scan of
//! every `GoodsName` across base, dlc01 and dlc02 finds only the Dried Liver consumables.
//!
//! # Why this reads the effect rather than the item
//!
//! An inventory read would answer "do they own the item", which is not the question -- the effect
//! toggles on use. Reading 28 directly is also what the game does:
//! `CS::SpecialEffect::HasTauntersTongueEffect` (`0x1404f9f60` on 1.16.2, `0x1404fad30` on 1.17.1)
//! walks the entry list testing `paramId == 0x1c` and nothing else.

/// `CS::ChrIns::specialEffect`. Named by the null-container guard's own disassembly of
/// `CS::ChrIns::HasSpecialEffectId`, whose first instruction is `MOV RCX,[RCX+0x178]`.
#[cfg(windows)]
const CHR_INS_SPECIAL_EFFECT_OFFSET: usize = 0x178;

/// `CS::SpecialEffect` -> the head of its entry list.
///
/// Read rather than called, and that is the point. This started as a `transmute` of
/// `CS::SpecialEffect::HasSpecialEffectId` and the call returned false on a host who had used
/// the item -- with no fault, no refusal and nothing in the log to say which of the two was
/// wrong, the id or the call. A walk cannot fail that quietly: every id it sees can be printed,
/// so "the effect is absent" and "we are reading the wrong list" stop being the same answer.
///
/// The chain is the game's own. `CS::SpecialEffect::HasTauntersTongueEffect` is thirteen
/// instructions and does nothing else, disassembled here out of `eldenring-deobf-1.17.1.bin` --
/// the installed build, not 1.16.2:
///
/// ```text
/// 1404fad30: mov  0x8(%rcx),%rcx      ; head = [container + 0x08]
/// 1404fad34: test %rcx,%rcx
/// 1404fad37: je   1404fad4f           ; empty list -> false
/// 1404fad40: cmpl $0x1c,0x8(%rcx)     ; id = [entry + 0x08]
/// 1404fad44: je   1404fad52           ; -> true
/// 1404fad46: mov  0x30(%rcx),%rcx     ; next = [entry + 0x30]
/// 1404fad4a: test %rcx,%rcx
/// 1404fad4d: jne  1404fad40
/// ```
#[cfg(windows)]
const SPECIAL_EFFECT_HEAD_OFFSET: usize = 0x08;

/// `entry -> SpEffectParam id`, from `cmpl $0x1c,0x8(%rcx)` above.
#[cfg(windows)]
const SPECIAL_EFFECT_ENTRY_ID_OFFSET: usize = 0x08;

/// `entry -> next`, from `mov 0x30(%rcx),%rcx` above.
#[cfg(windows)]
const SPECIAL_EFFECT_ENTRY_NEXT_OFFSET: usize = 0x30;

/// How far the walk will follow `next` before giving up.
///
/// A corrupt or torn list would otherwise be an unbounded walk on the game task. No player
/// carries anything near this many effects; the cap is a backstop, not a limit.
#[cfg(windows)]
const MAX_SPECIAL_EFFECT_ENTRIES: usize = 512;

/// The effects worth telling an invader about: the name published, and the SpEffect asked for.
///
/// A name rather than the raw id, because the id is a fact about this game build and a lobby
/// outlives it. A reader on a different build must be able to understand the advertisement
/// without our param table.
///
/// One entry today. The shape is a table because the invader-side fingers are the obvious next
/// additions and they are the same question asked of different ids -- Bloody and Festering Finger
/// are 11, Recusant Finger 16, the phantom fingers 536. They are deliberately absent: those sit on
/// an invader, and this key describes a host.
pub const TABLE: &[(&str, i32)] = &[("tongue", 28)];

/// The comma-separated list of active effect names, or `LOBBY_HOST_EFFECTS_NONE` when none are.
///
/// An unreadable player publishes `none` too, because a reader cannot act on the difference: both
/// mean "do not come here expecting to get in". The log does say which, once per change, and now
/// also prints every id the player is carrying -- which is the line that answers "the item is on
/// and the key still says none" without another build.
#[cfg(windows)]
#[must_use]
pub fn active_effects_value() -> String {
    let ids = local_player_speffects();
    let active: Vec<&str> = match &ids {
        Some(ids) => TABLE
            .iter()
            .filter(|(_, id)| ids.contains(id))
            .map(|(name, _)| *name)
            .collect(),
        None => Vec::new(),
    };
    let value = if active.is_empty() {
        crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE.to_owned()
    } else {
        active.join(",")
    };
    say_once(&value, ids.as_deref());
    value
}

/// Say what `value` means and what the player is actually carrying, once per distinct answer.
///
/// This runs on the game task and is asked every tick, so it is latched on the sentence rather
/// than on a flag: the ids change and the line reappears, the player stands still and it does not.
#[cfg(windows)]
fn say_once(value: &str, ids: Option<&[i32]>) {
    use std::sync::Mutex;
    static SAID: Mutex<Option<String>> = Mutex::new(None);

    let text = match ids {
        None => format!(
            "host-effects: publishing {value} because the player cannot be read -- no world, or \
             a null SpEffect container. This is not the same as the item being off, and an \
             invader cannot tell the two apart from the lobby."
        ),
        // The list, every time, even on success. It is the only thing that can distinguish "the
        // id we ask about is wrong" from "the effect really is absent", and the first version of
        // this module could tell you neither: it called `HasSpecialEffectId` and reported the
        // `false` it got back as fact.
        Some(ids) => {
            let carried = if ids.is_empty() {
                "nothing".to_owned()
            } else {
                ids.iter()
                    .map(ToString::to_string)
                    .collect::<Vec<_>>()
                    .join(" ")
            };
            if value == crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE {
                format!(
                    "host-effects: publishing {value} -- the player carries {carried}, and none \
                     of those is in this build's table ({})",
                    table_summary()
                )
            } else {
                format!("host-effects: publishing {value} -- the player carries {carried}")
            }
        }
    };
    let mut said = SAID.lock().unwrap_or_else(|e| e.into_inner());
    if said.as_deref() == Some(text.as_str()) {
        return;
    }
    crate::standalone_log(format_args!("{text}"));
    *said = Some(text);
}

/// The table as `name=id` pairs, for the line above. Printed rather than described so the id the
/// build is asking about is in the log beside the ids the player has.
#[cfg(windows)]
fn table_summary() -> String {
    TABLE
        .iter()
        .map(|(name, id)| format!("{name}={id}"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Host-side stub: there is no player off the game, so nothing is active.
#[cfg(not(windows))]
#[must_use]
pub fn active_effects_value() -> String {
    crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE.to_owned()
}

/// Every SpEffect id currently on the local player, or `None` when the list cannot be read.
///
/// `None` and `Some(empty)` are kept apart on purpose. "The world is not up" and "the player has
/// no effects" are the same silence to a caller that collapses them, and only one of the two is
/// worth publishing.
///
/// # Safety
///
/// Every read is fault-closed, so a world that is still loading yields `None` rather than a
/// fault. It must still run on the game task thread: `WorldChrMan::instance` and the effect list
/// both belong to it.
#[cfg(windows)]
fn local_player_speffects() -> Option<Vec<i32>> {
    use fromsoftware_shared::FromStatic;

    let world_chr_man = unsafe { eldenring::cs::WorldChrMan::instance() }.ok()?;
    let player = world_chr_man.main_player.as_ref()?;
    // `PlayerIns.chr_ins` is the struct's first field, so this pointer is the `ChrIns` the engine
    // expects -- the same identity `warp::player_physics_position` relies on, and that one is
    // known good because its answer reaches the heartbeat.
    let chr_ins = core::ptr::from_ref(&player.chr_ins) as usize;
    let container =
        unsafe { er_game_base::mem::safe_read_usize(chr_ins + CHR_INS_SPECIAL_EFFECT_OFFSET) }?;
    if container == 0 {
        return None;
    }
    let mut entry =
        unsafe { er_game_base::mem::safe_read_usize(container + SPECIAL_EFFECT_HEAD_OFFSET) }?;
    let mut ids = Vec::new();
    while entry != 0 && ids.len() < MAX_SPECIAL_EFFECT_ENTRIES {
        let id =
            unsafe { er_game_base::mem::safe_read_i32(entry + SPECIAL_EFFECT_ENTRY_ID_OFFSET) }?;
        ids.push(id);
        entry = unsafe {
            er_game_base::mem::safe_read_usize(entry + SPECIAL_EFFECT_ENTRY_NEXT_OFFSET)
        }?;
    }
    Some(ids)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The trap this module exists to avoid. 533 is Taunter's Tongue's `refId_default` and a
    /// one-shot trigger (`effectEndurance = 0.0`, `iconId = -1`); asking about it would read false
    /// essentially always, and the key would advertise `none` on a host running the item.
    #[test]
    fn the_published_id_is_the_persistent_effect_not_the_one_shot_trigger() {
        let tongue = TABLE
            .iter()
            .find(|(name, _)| *name == "tongue")
            .expect("the tongue entry is the whole feature");
        assert_eq!(tongue.1, 28, "SpEffectParam 28 is the persistent effect");
        assert!(
            !TABLE.iter().any(|(_, id)| *id == 533),
            "533 is the trigger, not the state -- publishing it advertises `none` on a host who \
             has the item active"
        );
    }

    /// The invader-side fingers are a different question about a different player, and the key
    /// this feeds describes a host. Listed by id so adding one is a deliberate act.
    #[test]
    fn no_invader_side_effect_is_published_as_a_host_property() {
        for invader in [11, 16, 536] {
            assert!(
                !TABLE.iter().any(|(_, id)| *id == invader),
                "SpEffect {invader} sits on an invader, not on the host this key describes"
            );
        }
    }

    /// Names reach other people's builds, so they must be stable, lowercase and free of the
    /// comma the value is joined with.
    #[test]
    fn every_published_name_survives_the_wire() {
        assert!(!TABLE.is_empty(), "an empty table publishes nothing at all");
        for (name, _) in TABLE {
            assert!(!name.is_empty());
            assert!(!name.contains(','), "`{name}` would split the joined value");
            assert_eq!(*name, name.to_lowercase(), "`{name}` must be lowercase");
            assert_ne!(
                *name,
                crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE,
                "an effect named `none` is indistinguishable from no effects"
            );
        }
    }
}
