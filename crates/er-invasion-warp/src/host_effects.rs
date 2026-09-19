//! Which of Seamless's own game rules this host has turned on, for publishing onto its lobby.
//!
//! # What this answers, and why an invader wants it
//!
//! `lobby_publish` tells an invader where a host is. This tells them what invading that host
//! would be like -- above all whether the host has used the Dried Fingers, Seamless's own item
//! for "open world to more invaders". A host advertising it is worth querying; one that is not
//! may be a world that already has its invader and cannot take another.
//!
//! # The rules are bits in one qword, and the item is not a `SpEffect`
//!
//! This module read `SpEffectParam` 28 until 2026-09-15, on the theory that Seamless leaves the
//! vanilla Taunter's Tongue effect behind it. It does not, and the reason is simpler than a
//! missed offset: the item is Seamless's, not the game's. `SeamlessCoop/locale/english.json`
//! names it `MODGOODSNAME_DRIEDFINGERITEM` -- "Dried Fingers", "Attracts more invaders to
//! current world" -- and Elden Ring itself has no such good. A live host who had used it was
//! carrying nine effects, none of them 28 and none carrying the Tongue's `iconId` 20200, which
//! is what sent this module to `ersc.dll` instead.
//!
//! Every rule Seamless toggles lives in one bitfield, the qword at `session+0x00`. Its three
//! rulebook rows are byte-identical actions differing only in mask and message id, read out of
//! `vendor-archive/seamless/ersc-2.0.1.dll`:
//!
//! ```text
//! ersc+0x25a8c:  xor rcx,0x10000   ; OPTIONSELECT_TOGGLEPVP,           message 0x6fff43e1
//! ersc+0x25c0c:  xor rcx,0x20000   ; OPTIONSELECT_TOGGLEPVPTEAMS,      message 0x6fff43e2
//! ersc+0x25d8c:  xor rcx,0x40000   ; OPTIONSELECT_TOGGLEFRIENDLYFIRE,  message 0x6fff43e3
//! ```
//!
//! Those three are the only `xor r64, <single bit>` sites in the whole module, so the Dried
//! Fingers row does not toggle its bit that way and its own action cannot be read: the table at
//! `ersc+0x2a1e0` that registers every row is a `jmp` into the Themida-protected `ERSC` section.
//! So the bits came from the live qword instead, and the one this module publishes was settled by
//! changing a setting and watching the difference rather than by argument. Two runs, identical in
//! every other respect:
//!
//! ```text
//! ersc_settings.ini allow_invaders = 1   (br-20260915-183757-9c51)
//!   0x0 -> 0x11        session live (0x1 and 0x10 together)
//!   0x11 -> 0x100011   changed 0x100000
//!   0x100011 -> 0x100111   changed 0x100
//!
//! ersc_settings.ini allow_invaders = 0   (br-20260915-184252-7424)
//!   0x0 -> 0x11        session live
//!   0x11 <-> 0x19      0x8 flapping, and nothing else ever sets
//! ```
//!
//! So [`ALLOW_INVADERS`] is `0x100000`: it appears the moment the session comes up when the
//! setting is on and never appears when it is off. That is a fact about the host worth telling an
//! invader -- a world with invasions disabled cannot be entered at all, whatever else it
//! advertises.
//!
//! # The Dried Fingers bit is still unidentified, and nothing is published for it
//!
//! `0x100000` was this module's candidate for the item until the run above falsified it: it was
//! already set on a fresh boot, before the player had used anything. `0x80000` was the candidate
//! before that, falsified the same way in reverse -- clear on a host who had the item on. What
//! remains unexplained is `0x100`, which also sets during bring-up with invaders allowed.
//!
//! Nothing is guessed in its place. Publishing a bit that is not the item is worse than
//! publishing nothing, because an invader filtering on it would be filtering on noise. The log
//! prints the whole qword and diffs it every time it moves, so the next time the item is turned
//! on or off the line names the bit and this module gains one entry.
//!
//! # Reading the rules, not the item
//!
//! An inventory read would answer "do they own the item", which is not the question -- the rule
//! toggles on use and stays on. The flags are also the same thing Seamless itself consults, so
//! the answer cannot drift from the behaviour it describes.

/// What joins two rule names in the published value.
///
/// A comma, because the key it feeds is documented as comma-separated and a reader on someone
/// else's build splits on it. Named so that the test which forbids a name containing it and the
/// code which joins on it cannot drift apart.
pub const RULE_NAME_SEPARATOR: &str = ",";

/// `OPTIONSELECT_TOGGLEDRIEDFINGER` -- "Open world to more invaders".
///
/// `ersc_settings.ini` `allow_invaders`, measured by differential: see the module docs for the
/// two runs. Not a rule the player toggles in game -- it is read from the settings file when the
/// session is built -- which is exactly why it is stable enough to advertise.
pub const ALLOW_INVADERS: u64 = 0x10_0000;

/// The rules published to invaders: the name that goes on the wire, and the bit it reads.
///
/// A name rather than a raw mask, because a mask is a fact about one Seamless build and the lobby
/// outlives it. A reader on a different build must be able to understand the advertisement
/// without disassembling anything.
///
/// One entry. The three rulebook toggles below are deliberately absent: they describe how players
/// already inside a world treat each other, which is not something an invader chooses a host on.
///
/// The Dried Fingers rule is not here because it is not a bit in this qword, and not a bit
/// anywhere else in Seamless's module memory either -- see [`DRIED_FINGERS_NAME`]. It is published
/// beside these, from a different source.
pub const TABLE: &[(&str, u64)] = &[("allow_invaders", ALLOW_INVADERS)];

/// What the Dried Fingers rule is called on the wire.
///
/// # Why this one is latched and the others are read
///
/// Every other rule here is a bit this module can read at any moment, so it needs no memory. The
/// Dried Fingers has no such bit. Measured on run `br-20260915-202555-3500`, with the session
/// taken from Seamless's own `show` and the baseline re-taken once the session had settled, a
/// confirmed use of the item changed:
///
/// | scope | changed |
/// |---|---|
/// | `ersc.dll` `.data`, 32,924 bytes | nothing |
/// | every writable page of `ersc.dll`, 11.5 MB | nothing |
/// | the session object's first 4 KB | nothing |
///
/// A control makes those zeroes mean something: an ordinary item on the same settled session moves
/// exactly two session fields and nothing in the module. So the rule is not stored anywhere this
/// module can read, and a key built on reading it cannot exist.
///
/// # Why a latch is honest here, when it would not have been
///
/// A latch that never clears is a lie an invader filters on, and that is the thing to avoid. This
/// one is keyed to the advertisement lobby, and the item's lifetime is that lobby's: it cannot be
/// activated twice in one hosted session, so the only way Seamless stops honouring it is for that
/// session to end -- and a new one declares a new lobby.
///
/// # The session object's address was tried first, and it is not an identity
///
/// Measured on run `br-20260915-210236-4479`, which is why this is keyed the way it is. The player
/// used the item, rehosted, and did not use it again:
///
/// ```text
/// er_invasion_warp_effects = allow_invaders,dried_fingers on lobby 0x1860000265cce34
/// er_invasion_warp_effects = allow_invaders,dried_fingers on lobby 0x186000026634b46
/// host-rules: session 0x469ac930      <- the only address in the whole run
/// ```
///
/// Two advertisement lobbies, one session address, and no clear line: Seamless reuses the
/// allocation across a rehost, so a latch keyed to it never drops and the key advertises a rule
/// the host does not have. The lobby id changed at exactly the moment the latch should have
/// cleared, and it is also the thing the key is written onto, so the two cannot drift apart.
pub const DRIED_FINGERS_NAME: &str = "dried_fingers";

/// `EquipParamGoods` row `0x7fde6c`, Seamless's own injected Dried Fingers.
///
/// Confirmed two ways: statically, `ersc+0x4640b` writes this id beside the only reference to
/// `MODGOODSNAME_DRIEDFINGERITEM`; live, `ChrIns+0x164` read `0x407fde6c` after a use and
/// `0x407fde64` before one.
const DRIED_FINGERS_ROW: u32 = 0x7f_de6c;

/// `CS::ChrIns::confirmedUsedGoods` -- the id of the goods the player actually confirmed using.
///
/// Read out of `CS::ChrIns::GetToUseItemId`. The field holds an `ItemId`, not a bare row: the top
/// nibble is the category and the low 28 bits are the row, so a comparison has to mask. Calling
/// the residue a float cost a wrong reading once; `0x407fde64` is goods row `0x7fde64`.
const CHR_INS_CONFIRMED_USED_GOODS: usize = 0x164;

/// The goods category in the top nibble of an `ItemId`.
const ITEM_CATEGORY_GOODS: u32 = 0x4000_0000;

/// The mask that separates the category from the row.
const ITEM_CATEGORY_MASK: u32 = 0xf000_0000;

/// The rules this module can name but does not publish, for the log alone.
///
/// They earn their place by validating the read rather than by being interesting. Each was taken
/// from its own toggle action, so if this qword is the flags qword then toggling player-versus-
/// player in game must flip `0x10000` here and nothing else. A read that cannot do that is
/// reading the wrong object, whatever it says about [`DRIED_FINGERS`].
const MEASURED: &[(&str, u64)] = &[
    ("allow_invaders", ALLOW_INVADERS),
    ("friendly_fire", 0x4_0000),
    ("pvp_teams", 0x2_0000),
    ("pvp", 0x1_0000),
];

/// The separated list of active rule names, `LOBBY_HOST_EFFECTS_NONE` when the session is
/// readable and no rule is on, and `None` when the session cannot be read at all.
///
/// The three outcomes are not two. An unreadable session used to publish `none`, on the argument
/// that a reader cannot act on the difference -- and that was wrong in a way the log caught:
/// `er_invasion_warp_effects` flapped `allow_invaders` -> `none` -> `allow_invaders` on one lobby
/// within a single run (writes #21 to #25 of `br-20260915-190211-db2f`), because the session
/// pointer resolves on some ticks and not others. An invader filtering on the key therefore saw
/// "this host has no rules on" for half of the ticks of a host that had one on the whole time.
///
/// `none` is a claim about the host and has to stay one: it is what clears the key when the game
/// clears the rule, which is the only reason the key can be trusted. Going blind is not the game
/// clearing anything, so it publishes nothing and leaves whatever the lobby already carries.
#[must_use]
pub fn active_effects_value() -> Option<String> {
    let flags = crate::local_invasion_filter::seamless_game_rules();
    let active: Vec<&str> = match flags {
        Ok(flags) => {
            let mut names: Vec<&str> = TABLE
                .iter()
                .filter(|(_, bit)| flags & bit != 0)
                .map(|(name, _)| *name)
                .collect();
            if dried_fingers_latched(flags) {
                names.push(DRIED_FINGERS_NAME);
            }
            names
        }
        Err(_) => {
            say_once(UNREADABLE, flags);
            return None;
        }
    };
    let value = if active.is_empty() {
        crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE.to_owned()
    } else {
        active.join(RULE_NAME_SEPARATOR)
    };
    say_once(&value, flags);
    watch_session_head();
    Some(value)
}

/// What the log calls the no-publish outcome. Never reaches the wire: the sentence for an
/// unreadable session is written in `say_once` and does not interpolate this, which is here so the
/// call reads as the outcome it reports.
const UNREADABLE: &str = "nothing";

/// Log any field in the head of the session that moves, for one session object at a time.
///
/// The flags qword answers "which rules are on". It cannot answer "how many invaders may come",
/// which is what the Dried Fingers rule actually changes -- a solo host goes from one to three.
/// A limit like that is a number in a field, not a bit in a mask, so the whole head is diffed and
/// the offset that moves is reported with its old and new value.
///
/// Two fields of the record are load-bearing and neither is the window. The address is, because
/// subtracting one allocation from another produces a diff in which everything moved: measured on
/// run `br-20260915-184655-ba0a`, all eight qwords "changed" at once, pointers included, while
/// the player had touched nothing. A changed address re-baselines and says so instead. The move
/// counts are, because a field the engine ticks every frame would otherwise be the only thing in
/// the log.
fn watch_session_head() {
    use std::sync::Mutex;
    static LAST: Mutex<Option<(usize, [u64; WINDOW])>> = Mutex::new(None);
    static MOVES: Mutex<[u8; WINDOW]> = Mutex::new([0; WINDOW]);

    let Ok((session, window)) = crate::local_invasion_filter::seamless_session_head() else {
        return;
    };
    let previous = {
        let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
        last.replace((session, window))
    };
    let (baseline, previous_window) = match previous {
        // A different object, or the first one seen. Either way there is nothing to subtract, so
        // the window is printed whole and the move counts start again -- a field that was restless
        // in the last session says nothing about this one.
        Some((baseline, _)) if baseline != session => (Some(baseline), None),
        None => (None, None),
        Some((_, window)) => (None, Some(window)),
    };
    if let Some(previous_window) = previous_window {
        let mut moves = MOVES.lock().unwrap_or_else(|e| e.into_inner());
        let mut moved = Vec::new();
        for (index, (was, now)) in previous_window.iter().zip(window.iter()).enumerate() {
            if was == now {
                continue;
            }
            moves[index] = moves[index].saturating_add(1);
            if moves[index] < FIELD_MOVES_BEFORE_IT_IS_NOISE {
                moved.push(format!("+{:#x} {was:#x} -> {now:#x}", index * 8));
            }
        }
        if !moved.is_empty() {
            crate::standalone_log(format_args!(
                "host-rules: session {session:#x} head moved -- {}",
                moved.join(", ")
            ));
        }
        return;
    }
    *MOVES.lock().unwrap_or_else(|e| e.into_inner()) = [0; WINDOW];
    match baseline {
        Some(baseline) => crate::standalone_log(format_args!(
            "host-rules: session moved {baseline:#x} -> {session:#x}, so the previous window is \
             not comparable and nothing is diffed against it. New head {}",
            render(&window)
        )),
        None => crate::standalone_log(format_args!(
            "host-rules: session {session:#x} head {}",
            render(&window)
        )),
    }
}

/// The window width, as the reader hands it over.
const WINDOW: usize = crate::local_invasion_filter::SESSION_WINDOW_QWORDS;

/// How many times a field may move before it stops being reported.
///
/// Eight, higher than the per-bit limit, because a count the player drives can legitimately step
/// through several values while a rule is being set up.
const FIELD_MOVES_BEFORE_IT_IS_NOISE: u8 = 8;

/// The whole window, for the first line, where there is nothing to diff against.
fn render(window: &[u64; WINDOW]) -> String {
    window
        .iter()
        .enumerate()
        .map(|(index, value)| format!("+{:#x}={value:#x}", index * 8))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Say what `value` means and which bits moved to make it so, once per distinct sentence.
///
/// The diff is the whole point. A line that reports only the published value cannot tell "the
/// item is off" from "this build is asking about the wrong bit", which is the confusion that cost
/// this module its first design. A line that names the bits that changed since the last one
/// cannot: use the item, and exactly one bit moves.
fn say_once(value: &str, flags: Result<u64, &'static str>) {
    use std::sync::Mutex;
    static SAID: Mutex<Option<String>> = Mutex::new(None);
    static LAST: Mutex<Option<u64>> = Mutex::new(None);

    let text = match flags {
        Err(reason) => format!(
            "host-rules: publishing nothing and leaving the key as it stands -- Seamless's \
             session reads as {reason}. Going blind is not the rule being off, and writing \
             `none` here would tell every invader that it was."
        ),
        Ok(flags) => {
            let previous = {
                let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
                last.replace(flags)
            };
            let changed = match previous {
                Some(previous) if previous != flags => {
                    let moved = previous ^ flags;
                    note_flips(moved);
                    format!(" -- changed {}", describe(moved))
                }
                _ => String::new(),
            };
            format!(
                "host-rules: publishing {value} -- session flags {flags:#018x}, set {}{changed}",
                describe(flags)
            )
        }
    };
    // Latch on the sentence with the restless bits taken out of it, not on the sentence itself.
    // Measured on a live host: `0x8` flips every few ticks, which two sentences alternate over --
    // so a plain latch on the text logs both of them for as long as the session lasts. Taking the
    // flapping bits out of the latch key keeps the line that matters, the one where a rule bit
    // moved, without a rule bit ever being hidden: the full qword is still printed.
    let key = match flags {
        Ok(flags) => format!("{value}|{:#x}", flags & !flapping()),
        Err(_) => text.clone(),
    };
    let mut said = SAID.lock().unwrap_or_else(|e| e.into_inner());
    if said.as_deref() == Some(key.as_str()) {
        return;
    }
    crate::standalone_log(format_args!("{text}"));
    *said = Some(key);
}

/// How many times a bit may move before the latch stops treating it as news.
///
/// Four, because a rule the player toggles on and off twice while deciding is still worth a line,
/// and a bit the engine drives moves far more often than that within seconds.
const FLIPS_BEFORE_A_BIT_IS_NOISE: u8 = 4;

/// Per-bit flip counts, saturating. Indexed by bit position.
static FLIPS: std::sync::Mutex<[u8; 64]> = std::sync::Mutex::new([0; 64]);

/// Count one transition for every bit in `moved`.
fn note_flips(moved: u64) {
    let mut flips = FLIPS.lock().unwrap_or_else(|e| e.into_inner());
    for (bit, count) in flips.iter_mut().enumerate() {
        if moved & (1u64 << bit) != 0 {
            *count = count.saturating_add(1);
        }
    }
}

/// The mask of bits that have moved often enough to be the engine's rather than the player's.
fn flapping() -> u64 {
    let flips = FLIPS.lock().unwrap_or_else(|e| e.into_inner());
    flips
        .iter()
        .enumerate()
        .filter(|(_, count)| **count >= FLIPS_BEFORE_A_BIT_IS_NOISE)
        .fold(0u64, |mask, (bit, _)| mask | (1u64 << bit))
}

/// Name the bits in `mask`, falling back to the bare bit for one nobody has measured.
///
/// An unmeasured bit is printed rather than dropped. The bit this module most wants to learn is
/// by definition one it cannot name yet, and a summary that lists only known names would hide the
/// single line that identifies it.
fn describe(mask: u64) -> String {
    if mask == 0 {
        return "nothing".to_owned();
    }
    let mut parts = Vec::new();
    let mut rest = mask;
    for (name, bit) in MEASURED {
        if rest & bit != 0 {
            parts.push((*name).to_owned());
            rest &= !bit;
        }
    }
    let mut bit = 1u64;
    while bit != 0 {
        if rest & bit != 0 {
            parts.push(format!("{bit:#x}"));
        }
        bit <<= 1;
    }
    parts.join(" ")
}

/// The advertisement lobby this host was on when the Dried Fingers was confirmed used.
///
/// `None` means the rule is not on. The lobby id is the key, not a flag, so the latch and the
/// hosted session it describes live and die together -- see [`DRIED_FINGERS_NAME`] for the run
/// that proved the session object's address cannot do this job.
static LATCHED_ON_LOBBY: std::sync::Mutex<Option<u64>> = std::sync::Mutex::new(None);

/// Whether this host should be advertising the Dried Fingers rule.
///
/// Called once per publish, and it does three small reads: the advertisement lobby, the player's
/// confirmed-used-goods field, and the latch. No lobby declared leaves the latch exactly as it is
/// -- going blind is not the hosted session ending, and treating it as one is how the effects key
/// learned to flap.
///
/// `flags` is the session rulebook this tick, and a value of zero is what closes the world out.
/// Measured on run `br-20260915-211626-7d97`: the player left with the Separation Mist, the rules
/// qword read `0x0000000000000000` from that moment on, and the key went on advertising
/// `dried_fingers` alone on the dead lobby -- because closing a world does not declare a new
/// advertisement lobby, so the lobby key never changes and the latch never drops. Zero rules is a
/// readable statement that this host is not hosting, which is different from the session being
/// unreadable, and only the first of those may clear anything.
fn dried_fingers_latched(flags: u64) -> bool {
    let lobby = current_advertisement_lobby();
    let mut latched = LATCHED_ON_LOBBY
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if flags == 0 {
        clear_on_closed_world(&mut latched);
        return false;
    }
    if let Some(lobby) = lobby {
        clear_if_lobby_changed(&mut latched, lobby);
        if confirmed_dried_fingers() && latched.is_none() {
            *latched = Some(lobby);
            crate::standalone_log(format_args!(
                "host-rules: latching {DRIED_FINGERS_NAME} on lobby {lobby:#x} -- the player \
                 confirmed goods row {DRIED_FINGERS_ROW:#x}. It clears when this host declares a \
                 different advertisement lobby, which is the only way Seamless stops honouring it."
            ));
        }
    }
    latched.is_some()
}

/// The lobby this host is advertising on, or `None` when none has been declared.
///
/// Wrapped rather than called directly because `lobby_publish::advertisement_lobby` is re-exported
/// from a `#[cfg(windows)]` module, so off the game's target the name does not exist and the error
/// reads `cannot find function` -- which looks like a typo rather than a target that has no game in
/// it. The host-side build runs this module's tests, so every game read needs a pair like this one.
#[cfg(windows)]
fn current_advertisement_lobby() -> Option<u64> {
    crate::lobby_publish::advertisement_lobby()
}

#[cfg(not(windows))]
fn current_advertisement_lobby() -> Option<u64> {
    None
}

/// Drop the latch because this host stopped hosting.
///
/// The lobby key cannot catch this on its own: leaving a world keeps the same advertisement lobby,
/// so the only signal that the session ended is the rulebook reading zero.
fn clear_on_closed_world(latched: &mut Option<u64>) {
    let Some(previous) = latched.take() else {
        return;
    };
    crate::standalone_log(format_args!(
        "host-rules: clearing {DRIED_FINGERS_NAME} -- the session rules read zero, so this host \
         has closed the world it was advertising on lobby {previous:#x} and is not hosting."
    ));
}

/// Drop the latch when this host declares a different advertisement lobby.
///
/// The whole safety of publishing a latched rule rests here: the key must not outlive the hosted
/// session the rule belonged to. `advertisement_lobby` returning `None` never reaches this
/// function, so a moment with no lobby declared cannot clear anything.
fn clear_if_lobby_changed(latched: &mut Option<u64>, lobby: u64) {
    let Some(previous) = *latched else {
        return;
    };
    if previous == lobby {
        return;
    }
    *latched = None;
    crate::standalone_log(format_args!(
        "host-rules: clearing {DRIED_FINGERS_NAME} -- the advertisement lobby moved from \
         {previous:#x} to {lobby:#x}, so the rule this host was advertising belongs to a session \
         that is gone."
    ));
}

/// Whether the local player's last confirmed goods use was the Dried Fingers.
///
/// The field keeps the last id it was given, so this reads as true for the rest of the session
/// after one use -- which is the behaviour wanted, because the item cannot be used twice in a
/// session anyway. Faults closed: no world, no player, or an id whose category is not goods all
/// read as "no", and so does a build with no game to read.
/// The goods row the local player last confirmed using, or `None` if the last confirmed item was
/// not goods and when there is no world, no player or an unreadable field.
///
/// Split out of [`confirmed_dried_fingers`] so a caller can ask which item was used rather than
/// only whether it was one particular one. The bounds-popup takeover needs that: it arms an
/// invasion search, and a player who used no item at all reads identically to one who used a finger
/// unless the row is named. Reported on run br-20260917-220822-932d, where a search armed itself
/// with the player standing still.
#[cfg(windows)]
pub(crate) fn confirmed_goods_row() -> Option<u32> {
    // The accessors come from `FromStatic`, which has to be in scope for the call to resolve --
    // without the import the error reads `no associated function named instance`, which looks like
    // a missing singleton rather than a missing trait. `instance`, not `instance_mut`: this reads.
    use fromsoftware_shared::FromStatic;
    let world_chr_man = unsafe { eldenring::cs::WorldChrMan::instance() }.ok()?;
    let player = world_chr_man.main_player.as_ref()?;
    let address = player.as_ptr() as usize + CHR_INS_CONFIRMED_USED_GOODS;
    // SAFETY: fault-closed read of one dword inside an object the singleton just handed over.
    // `safe_read_i32` is the only signed-or-unsigned dword reader here; the value is an `ItemId`
    // bit pattern, so it is cast rather than interpreted as a number.
    let raw = unsafe { er_game_base::mem::safe_read_i32(address) }? as u32;
    if raw & ITEM_CATEGORY_MASK != ITEM_CATEGORY_GOODS {
        return None;
    }
    Some(raw & !ITEM_CATEGORY_MASK)
}

#[cfg(not(windows))]
pub(crate) fn confirmed_goods_row() -> Option<u32> {
    None
}

#[cfg(windows)]
fn confirmed_dried_fingers() -> bool {
    confirmed_goods_row() == Some(DRIED_FINGERS_ROW)
}

#[cfg(not(windows))]
fn confirmed_dried_fingers() -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::{ALLOW_INVADERS, MEASURED, RULE_NAME_SEPARATOR, TABLE, describe};

    /// The three confirmed masks come from their own toggle actions, and the candidate is the
    /// next bit above them. Pinned so that re-pinning to a new Seamless build has to move them
    /// together rather than one at a time.
    #[test]
    fn the_measured_masks_are_the_ones_read_out_of_the_toggle_actions() {
        let by_name = |want: &str| {
            MEASURED
                .iter()
                .find(|(name, _)| *name == want)
                .unwrap_or_else(|| panic!("`{want}` is one of the masks this module names"))
                .1
        };
        assert_eq!(by_name("pvp"), 0x1_0000, "ersc+0x25a8c");
        assert_eq!(by_name("pvp_teams"), 0x2_0000, "ersc+0x25c0c");
        assert_eq!(by_name("friendly_fire"), 0x4_0000, "ersc+0x25d8c");
        assert_eq!(
            ALLOW_INVADERS, 0x10_0000,
            "set with allow_invaders = 1 on br-20260915-183757-9c51, never set with it 0 on \
             br-20260915-184252-7424"
        );
    }

    /// A published name reaches strangers' builds, so it must be stable, lowercase, and free of
    /// the separator the value is joined with.
    #[test]
    fn every_published_name_survives_the_wire() {
        assert!(!TABLE.is_empty(), "an empty table publishes nothing at all");
        for (name, bit) in TABLE {
            assert!(!name.is_empty());
            assert!(
                !name.contains(RULE_NAME_SEPARATOR),
                "`{name}` would split the joined value"
            );
            assert_eq!(*name, name.to_lowercase(), "`{name}` must be lowercase");
            assert_ne!(
                *name,
                crate::lobby_publish::LOBBY_HOST_EFFECTS_NONE,
                "a rule named `none` is indistinguishable from no rules"
            );
            assert_eq!(bit.count_ones(), 1, "`{name}` must name one bit");
        }
    }

    /// Two bits were published as the Dried Fingers rule and both were wrong -- `0x80000`, which
    /// was clear on a host running the item, and `0x100000`, which turned out to be
    /// `allow_invaders` and was set before the player had touched anything. Nothing may claim
    /// that rule again until a toggle names its bit.
    #[test]
    fn no_bit_is_published_as_the_dried_fingers_rule() {
        assert!(
            !TABLE.iter().any(|(name, _)| name.contains("dried")),
            "the item's bit is not known, and a guess reads as a measurement from the lobby"
        );
        for (name, _) in MEASURED {
            assert!(
                !name.ends_with('?'),
                "`{name}` is a candidate, and candidates are not published or named as rules"
            );
        }
    }

    /// An unmeasured bit must reach the log, because the bit worth learning is by definition one
    /// this build cannot name.
    #[test]
    fn an_unknown_bit_is_printed_rather_than_dropped() {
        assert_eq!(describe(0), "nothing");
        assert_eq!(describe(0x1_0000), "pvp");
        assert_eq!(describe(ALLOW_INVADERS), "allow_invaders");
        assert_eq!(describe(0x100), "0x100");
        assert_eq!(describe(0x1_0000 | 0x100), "pvp 0x100");
    }
}
