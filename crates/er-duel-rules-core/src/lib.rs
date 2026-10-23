//! The competitive duel ruleset, checked against what the game shows of every player in the
//! session.
//!
//! A remote player's own machine decides what they wear and cast; ours only sees what their game
//! sends. That turns out to be most of a build, and the checks here use only that (the addresses
//! in this list are 1.16.2 ones):
//!
//! - packet 8 fills a remote `PlayerGameData` with level and attributes (`TryDequeuePacket8`,
//!   1.16.2 `0x140ca3030`, once per player entry);
//! - packet 12 fills its `ChrAsm` with all six weapons and their Ash of War, armour, talismans
//!   and the three physick tears (`ChrAsm::UpdateFromPacket12` `0x1403bf690`, on every change);
//! - packet 13 carries the two-hand state and the active slots (`FUN_140c9dc80`);
//! - every SpEffect a player puts on themself is broadcast as packet 38 and applied to their
//!   `PlayerIns` here (`PlayerIns::SendSpEffectIdSync` `0x1403f5dd0`, `TryDequeuePacket38`
//!   `0x140c9b760`), so buffs, greases and heals are visible;
//! - animations and bullets are replicated as the game plays them.
//!
//! [`player::PlayerSnapshot`] is one frame of that for one player, [`rules::check`] turns it into
//! the rules it breaks, and [`referee::Referee`] decides which of those become a popup.
//!
//! The ban lists themselves are param ids in [`tables`], generated from the installed game by
//! `scripts/er-duel-rules-gen.py` with every row's name beside it.
//!
//! What is enforced can be changed without a rebuild: [`config`] reads `er-duel-rules.json`
//! beside the game, which switches built-in rules off, changes the rune level, blue flasks and
//! shield cap, and adds bans of the player's own ([`bans`]) over anything in [`catalog`].

pub mod bans;
pub mod catalog;
pub mod config;
pub mod facts;
pub mod feed;
pub mod player;
pub mod referee;
pub mod rules;
pub mod tables;

/// A param row and the name the game gives it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Named {
    pub id: i32,
    pub name: &'static str,
}

/// Which equipment slot an item that regenerates HP sits in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EquipKind {
    Talisman,
    Armor,
    Shield,
}

/// The name of `id` in `rows`, if it is listed.
pub fn lookup(rows: &'static [Named], id: i32) -> Option<&'static str> {
    rows.iter().find(|row| row.id == id).map(|row| row.name)
}
