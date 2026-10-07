//! The game-free half of `er_npc_summons.dll`.
//!
//! The DLL does two things, both designed in `docs/plans/npc-duel-signs-and-custom-mimic.md`:
//!
//! * NPC duel signs. The Duelist's Furled Finger opens a picker; the chosen NPC is spawned hidden
//!   and a red summon sign keyed to it is placed at the player's feet. Touching the sign runs the
//!   game's own phantom join, which enables the NPC and makes it a red phantom.
//! * Custom Mimic Tear. Using the Mimic Tear Ashes summons up to four configured companions
//!   through the native spirit-ash flow, each from a build URL and with its own AI.
//!
//! This crate holds every decision that does not need a running game:
//!
//! * [`toml`] reads the config file (hand-rolled, like the other config crates here).
//! * [`config`] turns it into a validated [`config::Config`].
//! * [`duel`] is the duel state machine and the hidden-NPC readiness verdict.
//! * [`mimic`] plans the companions: which BuddyParam values each summon request gets.
//! * [`dress`] turns a companion's build URL into the `CharaInitParam` gear it is built with.

pub mod config;
pub mod dress;
pub mod duel;
pub mod mimic;
pub mod toml;
