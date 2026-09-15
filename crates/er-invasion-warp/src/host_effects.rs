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

/// `CS::SpecialEffect::HasSpecialEffectId` -- `(SpecialEffect*, i32 id) -> bool`.
///
/// The same function `er-seamless-bugfixes` pins in its null-container guard, and the same one the
/// native toggle above tail-jumps into from `CS::ChrIns::HasSpecialEffectId` (`0x1403f1f80`, two
/// instructions: `MOV RCX,[RCX+0x178]` then `JMP` here). Calling the inner function directly means
/// this module does the `+0x178` hop itself and can refuse a null container rather than fault on
/// it -- which is the exact crash that guard exists for.
///
/// Mapped for the running build: `docs/recon/rva-map-1162-to-1170.verified.tsv` carries
/// `0x1404f9940 -> 0x1404fa710`, verdict `IDENTICAL-LEAF`, ratio 1.000 over 13 instructions.
#[cfg(windows)]
const HAS_SPECIAL_EFFECT_ID_RVA: usize = 0x4f9940;

/// `CS::ChrIns::specialEffect`. Named by the null-container guard's own disassembly of
/// `CS::ChrIns::HasSpecialEffectId`, whose first instruction is `MOV RCX,[RCX+0x178]`.
#[cfg(windows)]
const CHR_INS_SPECIAL_EFFECT_OFFSET: usize = 0x178;

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
/// mean "do not come here expecting to get in". The log does say which, once per change, and that
/// is not decoration -- `none` on the wire has two causes and the first question asked of a host
/// advertising it is which one applies. Without the line, answering needs a rebuild.
#[cfg(windows)]
#[must_use]
pub fn active_effects_value() -> String {
    let mut active: Vec<&str> = Vec::new();
    let mut unreadable = 0usize;
    for (name, id) in TABLE {
        match local_player_has_speffect(*id) {
            Some(true) => active.push(name),
            Some(false) => {}
            None => unreadable += 1,
        }
    }
    let value = if active.is_empty() {
        crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE.to_owned()
    } else {
        active.join(",")
    };
    say_once(&value, unreadable);
    value
}

/// Say what `value` means, once per distinct answer.
///
/// This runs on the game task and is asked every tick, so it is latched on the sentence rather
/// than on a flag: a host who uses the item, drops it and uses it again gets three lines, and a
/// host standing still gets one.
#[cfg(windows)]
fn say_once(value: &str, unreadable: usize) {
    use std::sync::Mutex;
    static SAID: Mutex<Option<String>> = Mutex::new(None);

    let text = if unreadable == TABLE.len() {
        format!(
            "host-effects: publishing {value} because the player cannot be read -- no world, or \
             a null SpEffect container. This is not the same as the item being off, and an \
             invader cannot tell the two apart from the lobby."
        )
    } else if value == crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE {
        format!(
            "host-effects: publishing {value} -- asked and answered. SpEffect 28 is absent, so \
             `CanBeSoloInvaded` is false and no invader can enter this world solo."
        )
    } else {
        format!("host-effects: publishing {value} -- this host is invadable")
    };
    let mut said = SAID.lock().unwrap_or_else(|e| e.into_inner());
    if said.as_deref() == Some(text.as_str()) {
        return;
    }
    crate::standalone_log(format_args!("{text}"));
    *said = Some(text);
}

/// Host-side stub: there is no player off the game, so nothing is active.
#[cfg(not(windows))]
#[must_use]
pub fn active_effects_value() -> String {
    crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE.to_owned()
}

/// Whether the local player carries `id`, or `None` when the question cannot be asked.
///
/// `None` and `Some(false)` are kept apart on purpose. "The world is not up" and "the host is not
/// running the item" are the same silence to a caller that collapses them, and only one of the two
/// is worth publishing.
///
/// # Safety
///
/// Reads are fault-closed and the call is made only with a non-null container, so a world that is
/// still loading yields `None` rather than a fault. It must still run on the game task thread:
/// `WorldChrMan::instance` and the effect list both belong to it.
#[cfg(windows)]
fn local_player_has_speffect(id: i32) -> Option<bool> {
    use fromsoftware_shared::FromStatic;

    type HasSpecialEffectIdFn = unsafe extern "system" fn(usize, i32) -> bool;

    let base = er_game_base::mem::game_module_base().ok()?;
    let world_chr_man = unsafe { eldenring::cs::WorldChrMan::instance() }.ok()?;
    let player = world_chr_man.main_player.as_ref()?;
    // `PlayerIns.chr_ins` is the struct's first field, so this pointer is the `ChrIns` the engine
    // expects -- the same identity `warp::player_physics_position` relies on.
    let chr_ins = core::ptr::from_ref(&player.chr_ins) as usize;
    // The `+0x178` hop, done here rather than by calling `CS::ChrIns::HasSpecialEffectId`, so a
    // null container is a `None` instead of the `0x8` access violation recorded in
    // `er-seamless-bugfixes`'s null-container guard.
    let container =
        unsafe { er_game_base::mem::safe_read_usize(chr_ins + CHR_INS_SPECIAL_EFFECT_OFFSET) }?;
    if container == 0 {
        return None;
    }
    let address = er_game_base::game_build::resolve_game_address(
        base + HAS_SPECIAL_EFFECT_ID_RVA,
        "HAS_SPECIAL_EFFECT_ID_RVA",
    )?;
    let has: HasSpecialEffectIdFn = unsafe { core::mem::transmute(address) };
    Some(unsafe { has(container, id) })
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
