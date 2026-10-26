//! Companions across a Seamless session, game side: the summoner tells the other players running
//! this DLL what each of its companions is, and a peer dresses and names the copies the game
//! builds from packet 78. What is said and how it is matched is `er_npc_summons_core::net`; this
//! module carries it over Steam ([`crate::steam`]) and into the `CreateSummonChr` detour.
//!
//! * Summoner: [`set_roster`] on every config read, [`announce_built`] from inside the detour for
//!   each companion built (before `BuddyGenerator` reaches its own broadcast), and a state message
//!   on a roster change, when a player joins and every few seconds from [`tick`].
//! * Peer: [`resolve_remote`] and [`record_remote`] from the detour when `fromNetwork` is set, and
//!   from [`tick`] a rename of every remote companion whose roster or announcement arrived after
//!   the game had built it.
//!
//! Messages are accepted only from players `CSSessionManager` lists in the current session, and
//! the owner of everything a message says is the SteamID Steam delivered it from.

#![cfg(windows)]

use std::sync::Mutex;

use eldenring::cs::CSSessionManager;
use er_game_base::mem::safe_read_usize;
use er_npc_summons_core::config::Companion;
use er_npc_summons_core::net::{
    self, Basis, Built, DecodeError, Message, RemoteBook, Resolved, RosterEntry, Spawn, Spawns,
    Triple,
};
use fromsoftware_shared::FromStatic;

use crate::addr::chr_ins;
use crate::game;
use crate::log::summons_log;
use crate::steam::{NotYet, Steam};

/// Frames between attempts to reach Steam before `SteamAPI_Init`, and between session accepts.
const SECOND_FRAMES: u32 = 60;
/// Frames between periodic state messages.
const STATE_FRAMES: u32 = 180;
/// Frames between status lines in the log.
const STATUS_FRAMES: u32 = 60 * 60;
/// Rejected messages logged one by one before only the count is kept.
const REJECTS_LOGGED: u64 = 20;
/// Messages read per frame at most.
const RECEIVE_PER_FRAME: usize = 64;

enum Link {
    Waiting { logged: bool },
    Up(Steam),
    Off,
}

#[derive(Default)]
struct Counts {
    sent: u64,
    send_failed: u64,
    received: u64,
    rejected: u64,
}

struct Net {
    link: Link,
    local: u64,
    frames: u32,
    /// This player's roster, as announced.
    roster: Vec<RosterEntry>,
    /// The roster changed since the last state message.
    roster_dirty: bool,
    /// This player's companions alive, with the `ChrIns*` each was built at.
    live: Vec<(usize, Built)>,
    /// The other players in the session, from `CSSessionManager`.
    peers: Vec<u64>,
    book: RemoteBook,
    spawns: Spawns,
    counts: Counts,
}

static NET: Mutex<Option<Net>> = Mutex::new(None);

fn with<T>(f: impl FnOnce(&mut Net) -> T) -> T {
    let mut guard = NET.lock().unwrap_or_else(|e| e.into_inner());
    let net = guard.get_or_insert_with(|| Net {
        link: Link::Waiting { logged: false },
        local: 0,
        frames: 0,
        roster: Vec::new(),
        roster_dirty: false,
        live: Vec::new(),
        peers: Vec::new(),
        book: RemoteBook::default(),
        spawns: Spawns::default(),
        counts: Counts::default(),
    });
    f(net)
}

fn describe_roster(roster: &[RosterEntry]) -> String {
    if roster.is_empty() {
        return "none".to_owned();
    }
    roster
        .iter()
        .map(|e| {
            format!(
                "{} \"{}\" (npc {}, think {}, charaInit {}, build {})",
                e.slot,
                e.name,
                e.triple.npc,
                e.triple.think,
                e.triple.chara_init,
                e.share_id.as_deref().unwrap_or("none")
            )
        })
        .collect::<Vec<_>>()
        .join(", ")
}

fn describe_live(live: &[Built]) -> String {
    if live.is_empty() {
        return "none".to_owned();
    }
    live.iter()
        .map(|b| format!("handle 0x{:x} = slot {}", b.handle, b.slot))
        .collect::<Vec<_>>()
        .join(", ")
}

fn basis(resolved: &Resolved) -> &'static str {
    match resolved.basis {
        Basis::Announced => "announced by its summoner",
        Basis::Guessed => "guessed from its body, no announcement yet",
    }
}

/// The roster to announce, from a config read (empty when the Mimic Tear is off).
pub(crate) fn set_roster(companions: &[Companion]) {
    let roster = net::roster(companions);
    with(|n| {
        if n.roster != roster {
            summons_log(format_args!(
                "net: roster to announce: {}",
                describe_roster(&roster)
            ));
            n.roster = roster;
            n.roster_dirty = true;
        }
    });
}

impl Net {
    fn steam(&self) -> Option<&Steam> {
        match &self.link {
            Link::Up(steam) => Some(steam),
            _ => None,
        }
    }

    /// Send `message` to every peer; how many took it.
    fn broadcast(&mut self, message: &Message) -> usize {
        let Some(steam) = self.steam() else {
            return 0;
        };
        let bytes = message.encode();
        let mut ok = 0;
        let mut failed = Vec::new();
        for &peer in &self.peers {
            let result = steam.send(peer, &bytes);
            if result == 1 {
                ok += 1;
            } else {
                failed.push((peer, result));
            }
        }
        self.counts.sent += ok as u64;
        self.counts.send_failed += failed.len() as u64;
        for (peer, result) in failed {
            summons_log(format_args!(
                "net: send to {peer:#018x} failed, EResult {result}"
            ));
        }
        ok
    }

    fn state(&self) -> Message {
        Message::State {
            roster: self.roster.clone(),
            live: self.live.iter().map(|&(_, built)| built).collect(),
        }
    }

    fn send_state_to(&mut self, peer: u64, why: &str) {
        let Some(steam) = self.steam() else {
            return;
        };
        let result = steam.send(peer, &self.state().encode());
        if result == 1 {
            self.counts.sent += 1;
            summons_log(format_args!(
                "net: sent state to {peer:#018x} ({why}): {} companion(s), {} live",
                self.roster.len(),
                self.live.len()
            ));
        } else {
            self.counts.send_failed += 1;
            summons_log(format_args!(
                "net: state to {peer:#018x} ({why}) failed, EResult {result}"
            ));
        }
    }
}

/// A companion this player summoned was just built at `chr` for config `slot`: tell the peers
/// which slot its handle is. Called from inside `CreateSummonChr`, so the message leaves before
/// `BuddyGenerator` broadcasts the summon itself.
pub(crate) fn announce_built(slot: u8, chr: usize) {
    let Some(handle) = game::chr_handle(chr) else {
        return;
    };
    let built = Built { handle, slot };
    with(|n| {
        n.live.retain(|&(_, b)| b.handle != handle);
        n.live.push((chr, built));
        let reached = n.broadcast(&Message::Built(built));
        summons_log(format_args!(
            "net: sent built handle 0x{handle:x} = slot {slot} to {reached} of {} player(s)",
            n.peers.len()
        ));
    });
}

/// Which companion the character the game is about to build from `owner`'s packet is, if this
/// player was told.
pub(crate) fn resolve_remote(owner: u64, handle: u64, triple: Triple) -> Option<Resolved> {
    with(|n| {
        let occupied = n.spawns.occupied(owner, handle);
        n.book.resolve(owner, handle, triple, &occupied)
    })
}

/// Remember a character the game built from `owner`'s packet, so a later roster or announcement
/// can still name it.
pub(crate) fn record_remote(
    owner: u64,
    handle: u64,
    triple: Triple,
    chr: usize,
    resolved: Option<Resolved>,
) {
    if chr == 0 {
        return;
    }
    with(|n| {
        n.spawns.record(Spawn {
            owner,
            handle,
            triple,
            chr,
            resolved,
        })
    });
}

/// The other players in the session, from `CSSessionManager`.
pub(crate) fn session_peers(local: u64) -> Vec<u64> {
    let mut out = Vec::new();
    // SAFETY: game thread.
    let Ok(session) = (unsafe { CSSessionManager::instance() }) else {
        return out;
    };
    for p in session.players.iter() {
        let id = p.base.steam_id;
        if id != 0 && id != local && !p.is_local_player && !out.contains(&id) {
            out.push(id);
        }
    }
    out
}

/// Does `chr` still hold `handle` and belong to `owner`?
fn still(chr: usize, handle: u64, owner: u64) -> bool {
    // SAFETY: fault-tolerant reads off a pointer that may be stale.
    game::chr_handle(chr) == Some(handle)
        && unsafe { safe_read_usize(chr + chr_ins::OWNER_STEAM_ID) }.map(|o| o as u64)
            == Some(owner)
}

fn reject(n: &mut Net, from: Option<u64>, why: &str) {
    n.counts.rejected += 1;
    if n.counts.rejected <= REJECTS_LOGGED {
        match from {
            Some(from) => summons_log(format_args!(
                "net: rejected a message from {from:#018x}: {why}"
            )),
            None => summons_log(format_args!(
                "net: rejected a message with no SteamID sender: {why}"
            )),
        }
    }
}

fn decode_error(error: DecodeError) -> String {
    format!("{error:?}")
}

/// One frame on the game task.
pub(crate) fn tick() {
    let renames = with(|n| {
        n.frames = n.frames.wrapping_add(1);
        if !connect(n) {
            return Vec::new();
        }
        follow_session(n);
        receive(n);
        // This player's companions that are gone are no longer announced as live.
        n.live
            .retain(|&(chr, b)| game::chr_handle(chr) == Some(b.handle));
        n.spawns.retain(|s| still(s.chr, s.handle, s.owner));
        if n.roster_dirty {
            n.roster_dirty = false;
            let state = n.state();
            let reached = n.broadcast(&state);
            summons_log(format_args!(
                "net: sent state to {reached} of {} player(s) after a roster change: {}",
                n.peers.len(),
                describe_roster(&n.roster)
            ));
        } else if n.frames.is_multiple_of(STATE_FRAMES)
            && !n.peers.is_empty()
            && !n.roster.is_empty()
        {
            let state = n.state();
            n.broadcast(&state);
        }
        if n.frames.is_multiple_of(STATUS_FRAMES) {
            summons_log(format_args!(
                "net: status: {} player(s), sent {} (failed {}), received {}, rejected {}, {} \
                 remote companion(s) tracked",
                n.peers.len(),
                n.counts.sent,
                n.counts.send_failed,
                n.counts.received,
                n.counts.rejected,
                n.spawns.len()
            ));
        }
        n.spawns.reconcile(&n.book)
    });
    // Named outside the lock: `names` takes its own.
    for late in renames {
        crate::names::set(late.chr, &late.now.entry.name);
        summons_log(format_args!(
            "net: remote companion owner {:#018x} handle 0x{:x} at 0x{:x} is slot {} \"{}\" ({}), \
             learned after it was built (was {}): renamed; its gear could not be applied for this \
             spawn",
            late.owner,
            late.handle,
            late.chr,
            late.now.entry.slot,
            late.now.entry.name,
            basis(&late.now),
            late.was
                .map_or("no companion".to_owned(), |slot| format!("slot {slot}"))
        ));
    }
}

/// Reach Steam; false while it is not up.
fn connect(n: &mut Net) -> bool {
    match &mut n.link {
        Link::Up(_) => true,
        Link::Off => false,
        Link::Waiting { logged } => {
            if !n.frames.is_multiple_of(SECOND_FRAMES) {
                return false;
            }
            match Steam::load() {
                Ok(steam) => {
                    n.local = steam.local_steam_id();
                    summons_log(format_args!(
                        "net: steam up, local SteamID {:#018x}; companions are announced on \
                         channel 0x{:08x} (ERNS)",
                        n.local,
                        net::CHANNEL
                    ));
                    n.link = Link::Up(steam);
                    true
                }
                Err(NotYet::Missing(why)) => {
                    summons_log(format_args!(
                        "net: {why}; other players will see the companions undressed"
                    ));
                    n.link = Link::Off;
                    false
                }
                Err(NotYet::NotUp) => {
                    if !*logged {
                        summons_log(format_args!("net: waiting for SteamAPI_Init"));
                        *logged = true;
                    }
                    false
                }
            }
        }
    }
}

/// Track who is in the session: greet a player who joined with the roster, forget one who left,
/// and accept their sessions so their messages are delivered.
fn follow_session(n: &mut Net) {
    let peers = session_peers(n.local);
    if peers != n.peers {
        let joined: Vec<u64> = peers
            .iter()
            .copied()
            .filter(|p| !n.peers.contains(p))
            .collect();
        for left in n.peers.iter().filter(|p| !peers.contains(p)) {
            summons_log(format_args!("net: player {left:#018x} left the session"));
        }
        n.peers = peers;
        n.book.retain_owners(&n.peers);
        let present = n.peers.clone();
        n.spawns.retain(|s| present.contains(&s.owner));
        for p in joined {
            summons_log(format_args!("net: player {p:#018x} is in the session"));
            if let Some(steam) = n.steam()
                && steam.accept(p)
            {
                summons_log(format_args!("net: accepted session from {p:#018x}"));
            }
            n.send_state_to(p, "joined");
        }
    }
    if n.frames.is_multiple_of(SECOND_FRAMES) {
        let peers = n.peers.clone();
        if let Some(steam) = n.steam() {
            for p in peers {
                if steam.accept(p) {
                    summons_log(format_args!("net: accepted session from {p:#018x}"));
                }
            }
        }
    }
}

fn receive(n: &mut Net) {
    let mut inbox: Vec<(Option<u64>, Vec<u8>)> = Vec::new();
    if let Some(steam) = n.steam() {
        steam.receive(RECEIVE_PER_FRAME, |from, bytes| {
            inbox.push((from, bytes.to_vec()));
        });
    }
    let mut prefetch = false;
    for (from, bytes) in inbox {
        n.counts.received += 1;
        let Some(sender) = from.filter(|f| n.peers.contains(f)) else {
            reject(n, from, "not a player in this session");
            continue;
        };
        let message = match Message::decode(&bytes) {
            Ok(message) => message,
            Err(error) => {
                reject(n, Some(sender), &decode_error(error));
                continue;
            }
        };
        let summary = match &message {
            Message::State { roster, live } => {
                format!(
                    "state: roster {}; live {}",
                    describe_roster(roster),
                    describe_live(live)
                )
            }
            Message::Built(b) => format!("built handle 0x{:x} = slot {}", b.handle, b.slot),
        };
        let was_state = matches!(message, Message::State { .. });
        let applied = n.book.apply(sender, message);
        if !was_state || applied.roster_changed || !applied.announced.is_empty() {
            summons_log(format_args!("net: received from {sender:#018x}: {summary}"));
        }
        prefetch |= applied.roster_changed;
    }
    if prefetch {
        crate::dress::prefetch_remote(&n.book.share_ids());
    }
}
