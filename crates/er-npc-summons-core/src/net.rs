//! Companions across a Seamless session: what a summoner tells the other players running this DLL,
//! and how such a peer matches a companion the game built from the network to what it was told.
//!
//! # Why this exists
//!
//! The game already shows a summoner's spirit ash to every other player. `BuddyGenerator` builds
//! each summon locally, then broadcasts packet 78 (`BroadcastBuddySummon`, 2.7.2.0 `0x140ca1030`,
//! called at `0x1404bc77b`) carrying a `BuddyPacketEntry`: `+0x4` block id, `+0x8` the new
//! character's own `FieldInsHandle` (read from `ChrIns+0x8` at `0x1404bc729`), `+0x10` the owner's
//! SteamID, `+0x1c` position, `+0x28` yaw, `+0x2c` npc, `+0x30` think, `+0x34` charaInit, `+0x38`
//! buddyParamId, `+0x3c` doping level, `+0x40` buddy stone. The peer's
//! `SummonBuddyManager::CreateBuddyFromPacket` (2.7.2.0 `0x1404b7d80`) calls the same
//! `CreateSummonChr` (`0x1404baf80`, call at `0x1404b7eed`) with `fromNetwork = 1`, the packet's
//! handle as `FieldInsHandle*` and the owner's SteamID as `creator steam id*`. A handle other than
//! -1 goes to `ChrSet::SpawnSummonBuddy` (`0x1404932f0`), which builds the character in exactly that
//! slot, so the peer's copy carries the summoner's handle. Both copies also store the owner's
//! SteamID at `ChrIns+0x218` (`0x1404bb633`).
//!
//! The packet carries the body, never the dressing: the gear comes from the peer's own
//! `CharaInitParam` row and the name from its own `NpcParam`, so every Mimic companion arrives as
//! whatever character its body row belongs to (bd `er-effects-rs-1zhn`: three dressed companions
//! all reached the host as Moongrum). The fix is for the summoner to say what each companion is,
//! on a channel of its own, and for the peer to dress it through the same `CreateSummonChr`
//! detour the summoner's own companions go through.
//!
//! # The two messages
//!
//! * [`Message::State`]: the summoner's roster (slot, the npc/think/charaInit the request carries,
//!   the build's share id, the display name) and the companions it has alive, by handle. Sent
//!   when the roster changes, when a player joins, and every few seconds, so a peer knows every
//!   companion before it is ever summoned and can fetch the builds in advance.
//! * [`Message::Built`]: one companion just built, `(handle, slot)`. Sent from inside the
//!   summoner's `CreateSummonChr`, before `BuddyGenerator` reaches its broadcast.
//!
//! The two travel on Steam's `ISteamNetworkingMessages`, the game's packets on its own session,
//! so neither order is promised. [`RemoteBook::resolve`] therefore uses the announced slot when it
//! has one and otherwise the roster's first unused slot carrying the same body; a later
//! announcement that disagrees renames the companion after the fact.
//!
//! Every field a peer acts on is checked here: the magic and version, lengths, UTF-8, the share
//! id's alphabet, the slot range. The owner of a message is always the Steam sender, never a
//! field inside it.

use std::collections::HashMap;

use crate::config::{Companion, MAX_COMPANIONS};
use crate::dress::{Dressing, dressing};

/// The `ISteamNetworkingMessages` channel, `ERNS`. Distinct from positional voice's `ERPV`.
pub const CHANNEL: i32 = 0x4552_4e53;
/// The first bytes of every message on [`CHANNEL`].
pub const MAGIC: [u8; 4] = *b"ERNS";
/// Bumped whenever the layout changes; a message of another version is dropped.
pub const VERSION: u8 = 1;
/// The longest share id or name carried, in bytes. Longer names are cut at a character boundary.
pub const MAX_TEXT: usize = 64;
/// The most live companions one [`Message::State`] lists.
pub const MAX_LIVE: usize = 16;

const KIND_STATE: u8 = 1;
const KIND_BUILT: u8 = 2;

/// The three ids a summon request carries, which a peer's `CreateSummonChr` receives unchanged.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Triple {
    pub npc: i32,
    pub think: i32,
    pub chara_init: i32,
}

/// One companion the summoner has configured.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RosterEntry {
    /// Config slot, 1..=[`MAX_COMPANIONS`].
    pub slot: u8,
    /// What its summon request carries: the body's npc and charaInit, and the think the plan
    /// gives it ([`Companion::think`]).
    pub triple: Triple,
    /// The planner share id of the build it wears, or `None` when it wears its body's own gear.
    pub share_id: Option<String>,
    /// The name it is shown under.
    pub name: String,
}

/// One companion the summoner has built: its `FieldInsHandle` and config slot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Built {
    pub handle: u64,
    pub slot: u8,
}

/// What travels on [`CHANNEL`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Message {
    State {
        roster: Vec<RosterEntry>,
        live: Vec<Built>,
    },
    Built(Built),
}

/// Why a message was dropped.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecodeError {
    NotOurs,
    Version(u8),
    Kind(u8),
    Truncated,
    Trailing,
    TooMany,
    BadSlot(u8),
    BadText,
    BadShareId,
}

/// The roster to announce, from the config's companions.
#[must_use]
pub fn roster(companions: &[Companion]) -> Vec<RosterEntry> {
    companions
        .iter()
        .take(MAX_COMPANIONS)
        .map(|companion| RosterEntry {
            slot: companion.slot,
            triple: Triple {
                npc: companion.body.npc_param,
                think: companion.think(),
                chara_init: companion.body.chara_init,
            },
            share_id: match dressing(companion) {
                Dressing::Build(id) => Some(id).filter(|id| valid_share_id(id)),
                Dressing::Own | Dressing::Refused(_) => None,
            },
            name: clip(&companion.name),
        })
        .collect()
}

/// Is `id` a share id a peer may fetch: non-empty, at most [`MAX_TEXT`] bytes, ASCII letters and
/// digits only (the alphabet `validate_build_url` accepts).
#[must_use]
pub fn valid_share_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= MAX_TEXT && id.bytes().all(|b| b.is_ascii_alphanumeric())
}

/// `name` cut to at most [`MAX_TEXT`] bytes at a character boundary.
fn clip(name: &str) -> String {
    let mut end = name.len().min(MAX_TEXT);
    while !name.is_char_boundary(end) {
        end -= 1;
    }
    name[..end].to_owned()
}

fn valid_slot(slot: u8) -> bool {
    (1..=MAX_COMPANIONS as u8).contains(&slot)
}

fn put_text(out: &mut Vec<u8>, text: &str) {
    let text = clip(text);
    out.push(text.len() as u8);
    out.extend_from_slice(text.as_bytes());
}

impl Message {
    /// The bytes sent: [`MAGIC`], [`VERSION`], a kind byte, then little-endian fields.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(64);
        out.extend_from_slice(&MAGIC);
        out.push(VERSION);
        match self {
            Message::State { roster, live } => {
                out.push(KIND_STATE);
                let roster = &roster[..roster.len().min(MAX_COMPANIONS)];
                out.push(roster.len() as u8);
                for entry in roster {
                    out.push(entry.slot);
                    out.extend_from_slice(&entry.triple.npc.to_le_bytes());
                    out.extend_from_slice(&entry.triple.think.to_le_bytes());
                    out.extend_from_slice(&entry.triple.chara_init.to_le_bytes());
                    put_text(&mut out, entry.share_id.as_deref().unwrap_or(""));
                    put_text(&mut out, &entry.name);
                }
                let live = &live[..live.len().min(MAX_LIVE)];
                out.push(live.len() as u8);
                for built in live {
                    out.extend_from_slice(&built.handle.to_le_bytes());
                    out.push(built.slot);
                }
            }
            Message::Built(built) => {
                out.push(KIND_BUILT);
                out.extend_from_slice(&built.handle.to_le_bytes());
                out.push(built.slot);
            }
        }
        out
    }

    /// Read a message, refusing anything this version did not write.
    pub fn decode(bytes: &[u8]) -> Result<Message, DecodeError> {
        let rest = bytes.strip_prefix(&MAGIC).ok_or(DecodeError::NotOurs)?;
        let mut r = Reader(rest);
        let version = r.u8()?;
        if version != VERSION {
            return Err(DecodeError::Version(version));
        }
        let message = match r.u8()? {
            KIND_STATE => {
                let count = r.u8()?;
                if usize::from(count) > MAX_COMPANIONS {
                    return Err(DecodeError::TooMany);
                }
                let mut roster = Vec::with_capacity(usize::from(count));
                for _ in 0..count {
                    let slot = r.slot()?;
                    let triple = Triple {
                        npc: r.i32()?,
                        think: r.i32()?,
                        chara_init: r.i32()?,
                    };
                    let share_id = r.text()?;
                    let share_id = if share_id.is_empty() {
                        None
                    } else if valid_share_id(&share_id) {
                        Some(share_id)
                    } else {
                        return Err(DecodeError::BadShareId);
                    };
                    let name = r.text()?;
                    roster.push(RosterEntry {
                        slot,
                        triple,
                        share_id,
                        name,
                    });
                }
                let count = r.u8()?;
                if usize::from(count) > MAX_LIVE {
                    return Err(DecodeError::TooMany);
                }
                let mut live = Vec::with_capacity(usize::from(count));
                for _ in 0..count {
                    live.push(Built {
                        handle: r.u64()?,
                        slot: r.slot()?,
                    });
                }
                Message::State { roster, live }
            }
            KIND_BUILT => Message::Built(Built {
                handle: r.u64()?,
                slot: r.slot()?,
            }),
            kind => return Err(DecodeError::Kind(kind)),
        };
        if r.0.is_empty() {
            Ok(message)
        } else {
            Err(DecodeError::Trailing)
        }
    }
}

struct Reader<'a>(&'a [u8]);

impl Reader<'_> {
    fn bytes(&mut self, len: usize) -> Result<&[u8], DecodeError> {
        if self.0.len() < len {
            return Err(DecodeError::Truncated);
        }
        let (head, tail) = self.0.split_at(len);
        self.0 = tail;
        Ok(head)
    }
    fn u8(&mut self) -> Result<u8, DecodeError> {
        Ok(self.bytes(1)?[0])
    }
    fn i32(&mut self) -> Result<i32, DecodeError> {
        let mut raw = [0u8; 4];
        raw.copy_from_slice(self.bytes(4)?);
        Ok(i32::from_le_bytes(raw))
    }
    fn u64(&mut self) -> Result<u64, DecodeError> {
        let mut raw = [0u8; 8];
        raw.copy_from_slice(self.bytes(8)?);
        Ok(u64::from_le_bytes(raw))
    }
    fn slot(&mut self) -> Result<u8, DecodeError> {
        let slot = self.u8()?;
        if valid_slot(slot) {
            Ok(slot)
        } else {
            Err(DecodeError::BadSlot(slot))
        }
    }
    fn text(&mut self) -> Result<String, DecodeError> {
        let len = usize::from(self.u8()?);
        if len > MAX_TEXT {
            return Err(DecodeError::BadText);
        }
        let raw = self.bytes(len)?.to_vec();
        String::from_utf8(raw).map_err(|_| DecodeError::BadText)
    }
}

/// What a peer knows about the other players' companions.
#[derive(Default, Debug)]
pub struct RemoteBook {
    rosters: HashMap<u64, Vec<RosterEntry>>,
    /// `(owner, handle)` -> slot, from [`Message::Built`] and the live lists.
    announced: HashMap<(u64, u64), u8>,
}

/// What [`RemoteBook::apply`] changed.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Applied {
    /// The sender's roster is new or different.
    pub roster_changed: bool,
    /// Announcements this message added or changed.
    pub announced: Vec<Built>,
}

/// How [`RemoteBook::resolve`] chose a companion.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Basis {
    /// The summoner said which slot this handle is.
    Announced,
    /// No announcement yet: the first roster slot with this body not already in use.
    Guessed,
}

/// The companion a remote character is.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Resolved {
    pub entry: RosterEntry,
    pub basis: Basis,
}

impl RemoteBook {
    /// Take in one message from `sender`, who is the owner of everything in it.
    pub fn apply(&mut self, sender: u64, message: Message) -> Applied {
        let mut applied = Applied::default();
        match message {
            Message::State { mut roster, live } => {
                roster.sort_by_key(|entry| entry.slot);
                roster.dedup_by_key(|entry| entry.slot);
                if self.rosters.get(&sender) != Some(&roster) {
                    applied.roster_changed = true;
                    self.rosters.insert(sender, roster);
                }
                // The live list is the summoner's whole truth at sending time.
                self.announced.retain(|&(owner, _), _| owner != sender);
                for built in live {
                    self.announce(sender, built, &mut applied);
                }
            }
            Message::Built(built) => self.announce(sender, built, &mut applied),
        }
        applied
    }

    fn announce(&mut self, owner: u64, built: Built, applied: &mut Applied) {
        if self.announced.insert((owner, built.handle), built.slot) != Some(built.slot) {
            applied.announced.push(built);
        }
    }

    /// Which companion the character with `handle` that `owner` summoned is, given the body the
    /// packet built it from and the slots `owner`'s other live companions already hold here.
    ///
    /// `None` when the owner has sent no roster, when no roster entry carries that body, or when
    /// the announced slot's entry carries a different body (then the summoner's and this peer's
    /// views disagree, and dressing it would be a guess against evidence).
    #[must_use]
    pub fn resolve(
        &self,
        owner: u64,
        handle: u64,
        triple: Triple,
        occupied: &[u8],
    ) -> Option<Resolved> {
        let roster = self.rosters.get(&owner)?;
        if let Some(&slot) = self.announced.get(&(owner, handle)) {
            let entry = roster.iter().find(|entry| entry.slot == slot)?;
            return (entry.triple == triple).then(|| Resolved {
                entry: entry.clone(),
                basis: Basis::Announced,
            });
        }
        roster
            .iter()
            .find(|entry| entry.triple == triple && !occupied.contains(&entry.slot))
            .map(|entry| Resolved {
                entry: entry.clone(),
                basis: Basis::Guessed,
            })
    }

    /// Forget every player not in `present`.
    pub fn retain_owners(&mut self, present: &[u64]) {
        self.rosters.retain(|owner, _| present.contains(owner));
        self.announced
            .retain(|(owner, _), _| present.contains(owner));
    }

    /// Every share id any roster names, for fetching ahead of the summon.
    #[must_use]
    pub fn share_ids(&self) -> Vec<String> {
        let mut ids: Vec<String> = self
            .rosters
            .values()
            .flatten()
            .filter_map(|entry| entry.share_id.clone())
            .collect();
        ids.sort();
        ids.dedup();
        ids
    }

    /// Every announced `(owner, Built)`, for finding companions built before this peer listened.
    #[must_use]
    pub fn announcements(&self) -> Vec<(u64, Built)> {
        self.announced
            .iter()
            .map(|(&(owner, handle), &slot)| (owner, Built { handle, slot }))
            .collect()
    }
}

/// One character the game built here from another player's packet 78.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Spawn {
    /// The summoner's SteamID, `CreateSummonChr`'s `creator steam id`.
    pub owner: u64,
    /// The `FieldInsHandle` the packet carried, which the character holds here too.
    pub handle: u64,
    /// The body the packet built it from.
    pub triple: Triple,
    /// The `ChrIns*` it was built at, for the caller; never read here.
    pub chr: usize,
    /// Which companion it was taken for, if any yet.
    pub resolved: Option<Resolved>,
}

/// A spawn whose companion became known, or changed, after it was built.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Late {
    pub owner: u64,
    pub handle: u64,
    pub chr: usize,
    /// The slot it had been taken for before, `None` when it had been taken for nothing.
    pub was: Option<u8>,
    pub now: Resolved,
}

/// The remote characters built here, so a roster or announcement arriving after the build can
/// still name the companion, and so two of one owner's companions on the same body are not both
/// taken for the same slot.
#[derive(Default, Debug)]
pub struct Spawns(Vec<Spawn>);

impl Spawns {
    /// The slots `owner`'s recorded spawns hold, other than the one with `handle`.
    #[must_use]
    pub fn occupied(&self, owner: u64, handle: u64) -> Vec<u8> {
        self.0
            .iter()
            .filter(|s| s.owner == owner && s.handle != handle)
            .filter_map(|s| s.resolved.as_ref().map(|r| r.entry.slot))
            .collect()
    }

    /// Record a spawn, replacing an earlier record of the same owner and handle.
    pub fn record(&mut self, spawn: Spawn) {
        self.0
            .retain(|s| !(s.owner == spawn.owner && s.handle == spawn.handle));
        self.0.push(spawn);
    }

    /// Keep only the spawns `keep` accepts (the caller drops the ones whose character is gone).
    pub fn retain(&mut self, keep: impl FnMut(&Spawn) -> bool) {
        self.0.retain(keep);
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.0.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    /// Resolve every spawn again against `book`. A spawn changes when it had no companion and now
    /// has one, or when its slot differs from the one it was taken for; an announced spawn only
    /// moves on a different announcement. Every change is returned once.
    pub fn reconcile(&mut self, book: &RemoteBook) -> Vec<Late> {
        let mut late = Vec::new();
        for index in 0..self.0.len() {
            let (owner, handle, triple) = {
                let s = &self.0[index];
                (s.owner, s.handle, s.triple)
            };
            let occupied = self.occupied(owner, handle);
            let Some(now) = book.resolve(owner, handle, triple, &occupied) else {
                continue;
            };
            let spawn = &mut self.0[index];
            let was = spawn.resolved.as_ref().map(|r| r.entry.slot);
            let held_announced = spawn
                .resolved
                .as_ref()
                .is_some_and(|r| r.basis == Basis::Announced);
            if was == Some(now.entry.slot) {
                // Same companion; an announcement confirming a guess is kept quietly.
                if now.basis == Basis::Announced {
                    spawn.resolved = Some(now);
                }
                continue;
            }
            if held_announced && now.basis == Basis::Guessed {
                continue;
            }
            spawn.resolved = Some(now.clone());
            late.push(Late {
                owner,
                handle,
                chr: spawn.chr,
                was,
                now,
            });
        }
        late
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;

    const MOONGRUM: Triple = Triple {
        npc: 523_590_024,
        think: 523_590_100,
        chara_init: 23_590,
    };
    const OWNER: u64 = 0x0110_0001_0000_0001;

    /// The reported case: three companions, all on Moongrum's rows, each with its own build.
    fn turtles() -> Vec<RosterEntry> {
        let config = Config::parse(
            "[mimic]\nenabled = true\n\
             [mimic.companion.1]\nname = \"Raphael\"\nnpc_param = 523590024\nthink = 523590100\n\
             chara_init = 23590\nbuild_url = \"https://er-build-planner.nyasu.business/?b=aaa111\"\n\
             [mimic.companion.2]\nname = \"Donatello\"\nnpc_param = 523590024\nthink = 523590100\n\
             chara_init = 23590\nbuild_url = \"https://er-build-planner.nyasu.business/?b=bbb222\"\n\
             [mimic.companion.3]\nname = \"Michelangelo\"\nnpc_param = 523590024\n\
             think = 523590100\nchara_init = 23590\n\
             build_url = \"https://er-build-planner.nyasu.business/?b=ccc333\"\n",
        );
        roster(&config.mimic.companions)
    }

    #[test]
    fn the_roster_carries_what_the_request_carries() {
        let roster = turtles();
        assert_eq!(roster.len(), 3);
        assert!(roster.iter().all(|entry| entry.triple == MOONGRUM));
        assert_eq!(roster[0].share_id.as_deref(), Some("aaa111"));
        assert_eq!(roster[2].name, "Michelangelo");
    }

    #[test]
    fn like_npc_think_is_what_is_announced() {
        let config = Config::parse("[mimic.companion.1]\nai = { like_npc = 523180000 }\n");
        assert_eq!(
            roster(&config.mimic.companions)[0].triple.think,
            523_180_000
        );
    }

    #[test]
    fn messages_round_trip() {
        let state = Message::State {
            roster: turtles(),
            live: vec![
                Built {
                    handle: 0xffff_ffff_0013_0007,
                    slot: 1,
                },
                Built {
                    handle: 0x0000_0001_0013_0008,
                    slot: 3,
                },
            ],
        };
        assert_eq!(Message::decode(&state.encode()), Ok(state));
        let built = Message::Built(Built {
            handle: 42,
            slot: 4,
        });
        assert_eq!(Message::decode(&built.encode()), Ok(built));
    }

    #[test]
    fn foreign_and_damaged_messages_are_refused() {
        let good = Message::Built(Built { handle: 7, slot: 2 }).encode();
        assert_eq!(Message::decode(b"ERPV\x01abc"), Err(DecodeError::NotOurs));
        let mut version = good.clone();
        version[4] = 9;
        assert_eq!(Message::decode(&version), Err(DecodeError::Version(9)));
        assert_eq!(
            Message::decode(&good[..good.len() - 1]),
            Err(DecodeError::Truncated)
        );
        let mut trailing = good.clone();
        trailing.push(0);
        assert_eq!(Message::decode(&trailing), Err(DecodeError::Trailing));
        let mut slot = good;
        if let Some(last) = slot.last_mut() {
            *last = 9;
        }
        assert_eq!(Message::decode(&slot), Err(DecodeError::BadSlot(9)));
    }

    #[test]
    fn a_share_id_outside_the_alphabet_is_refused() {
        let mut roster = turtles();
        roster[0].share_id = Some("../etc".to_owned());
        let bytes = Message::State {
            roster,
            live: Vec::new(),
        }
        .encode();
        assert_eq!(Message::decode(&bytes), Err(DecodeError::BadShareId));
    }

    #[test]
    fn long_names_are_cut_at_a_character_boundary() {
        let name = "\u{e9}".repeat(40);
        let clipped = clip(&name);
        assert!(clipped.len() <= MAX_TEXT);
        assert!(name.starts_with(&clipped));
    }

    fn book_with_turtles() -> RemoteBook {
        let mut book = RemoteBook::default();
        let applied = book.apply(
            OWNER,
            Message::State {
                roster: turtles(),
                live: Vec::new(),
            },
        );
        assert!(applied.roster_changed);
        book
    }

    #[test]
    fn an_announced_handle_resolves_to_its_slot() {
        let mut book = book_with_turtles();
        book.apply(
            OWNER,
            Message::Built(Built {
                handle: 77,
                slot: 2,
            }),
        );
        let resolved = book.resolve(OWNER, 77, MOONGRUM, &[]).expect("announced");
        assert_eq!(resolved.entry.name, "Donatello");
        assert_eq!(resolved.basis, Basis::Announced);
    }

    #[test]
    fn without_an_announcement_the_first_free_slot_is_guessed() {
        let book = book_with_turtles();
        let first = book.resolve(OWNER, 1, MOONGRUM, &[]).expect("guess");
        assert_eq!((first.entry.slot, first.basis), (1, Basis::Guessed));
        let second = book.resolve(OWNER, 2, MOONGRUM, &[1]).expect("guess");
        assert_eq!(second.entry.slot, 2);
        assert_eq!(book.resolve(OWNER, 4, MOONGRUM, &[1, 2, 3]), None);
    }

    #[test]
    fn a_different_body_or_owner_resolves_to_nothing() {
        let mut book = book_with_turtles();
        let other = Triple {
            npc: 100_000_010,
            ..MOONGRUM
        };
        assert_eq!(book.resolve(OWNER, 1, other, &[]), None);
        assert_eq!(book.resolve(OWNER + 1, 1, MOONGRUM, &[]), None);
        // An announcement whose slot carries another body is not trusted either.
        book.apply(OWNER, Message::Built(Built { handle: 5, slot: 1 }));
        assert_eq!(book.resolve(OWNER, 5, other, &[]), None);
    }

    #[test]
    fn a_state_replaces_the_senders_live_list_only() {
        let mut book = book_with_turtles();
        book.apply(OWNER, Message::Built(Built { handle: 1, slot: 1 }));
        book.apply(
            OWNER + 1,
            Message::State {
                roster: turtles(),
                live: vec![Built { handle: 9, slot: 3 }],
            },
        );
        let applied = book.apply(
            OWNER,
            Message::State {
                roster: turtles(),
                live: vec![Built { handle: 2, slot: 2 }],
            },
        );
        assert!(!applied.roster_changed);
        assert_eq!(applied.announced, vec![Built { handle: 2, slot: 2 }]);
        let mut all = book.announcements();
        all.sort_by_key(|(owner, built)| (*owner, built.handle));
        assert_eq!(
            all,
            vec![
                (OWNER, Built { handle: 2, slot: 2 }),
                (OWNER + 1, Built { handle: 9, slot: 3 })
            ]
        );
    }

    fn spawn(handle: u64, resolved: Option<Resolved>) -> Spawn {
        Spawn {
            owner: OWNER,
            handle,
            triple: MOONGRUM,
            chr: 0x1000 + handle as usize,
            resolved,
        }
    }

    #[test]
    fn a_roster_after_the_build_names_the_spawns_in_turn() {
        let mut spawns = Spawns::default();
        spawns.record(spawn(10, None));
        spawns.record(spawn(11, None));
        let mut book = RemoteBook::default();
        assert!(spawns.reconcile(&book).is_empty());
        book.apply(
            OWNER,
            Message::State {
                roster: turtles(),
                live: Vec::new(),
            },
        );
        let late = spawns.reconcile(&book);
        let slots: Vec<_> = late
            .iter()
            .map(|l| (l.handle, l.was, l.now.entry.slot))
            .collect();
        assert_eq!(slots, vec![(10, None, 1), (11, None, 2)]);
        // Nothing changed since: nothing is returned twice.
        assert!(spawns.reconcile(&book).is_empty());
    }

    #[test]
    fn an_announcement_corrects_a_guess_once() {
        let mut book = book_with_turtles();
        let mut spawns = Spawns::default();
        let guess = book.resolve(OWNER, 10, MOONGRUM, &[]);
        assert_eq!(guess.as_ref().map(|r| r.entry.slot), Some(1));
        spawns.record(spawn(10, guess));
        book.apply(
            OWNER,
            Message::Built(Built {
                handle: 10,
                slot: 3,
            }),
        );
        let late = spawns.reconcile(&book);
        assert_eq!(late.len(), 1);
        assert_eq!((late[0].was, late[0].now.entry.slot), (Some(1), 3));
        assert_eq!(late[0].now.basis, Basis::Announced);
        assert_eq!(spawns.occupied(OWNER, 99), vec![3]);
        assert!(spawns.reconcile(&book).is_empty());
    }

    #[test]
    fn an_announced_spawn_does_not_fall_back_to_a_guess() {
        let mut book = book_with_turtles();
        book.apply(
            OWNER,
            Message::Built(Built {
                handle: 10,
                slot: 2,
            }),
        );
        let mut spawns = Spawns::default();
        spawns.record(spawn(10, book.resolve(OWNER, 10, MOONGRUM, &[])));
        // A state with an empty live list drops the announcement; the guess would be slot 1.
        book.apply(
            OWNER,
            Message::State {
                roster: turtles(),
                live: Vec::new(),
            },
        );
        assert!(spawns.reconcile(&book).is_empty());
        assert_eq!(spawns.occupied(OWNER, 99), vec![2]);
    }

    #[test]
    fn players_who_leave_are_forgotten() {
        let mut book = book_with_turtles();
        book.apply(OWNER, Message::Built(Built { handle: 1, slot: 1 }));
        assert_eq!(book.share_ids(), vec!["aaa111", "bbb222", "ccc333"]);
        book.retain_owners(&[]);
        assert!(book.share_ids().is_empty());
        assert!(book.announcements().is_empty());
    }
}
