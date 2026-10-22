//! What the positional voice DLL decides without the game.
//!
//! The DLL carries voice itself: it reads the microphone through `ISteamUser::GetVoice`, sends
//! each compressed frame to every other player in the session over `ISteamNetworkingMessages` on
//! its own channel, decodes what arrives with `ISteamUser::DecompressVoice`, and plays it through
//! one of the game's `DLMOW::MOAudioInputObject`s placed at that player's character. It reads and
//! writes nothing in Seamless Co-op.
//!
//! - [`bank`]: the Wwise bank that turns the audio input into a positional dialogue sound;
//! - [`packet`]: the frame a compressed voice chunk travels in;
//! - [`gain`]: the volume applied to decoded PCM;
//! - [`slots`]: which SteamID owns which of the sixteen voice objects;
//! - [`sites`]: the game bytes checked before any game function is called;
//! - [`config`]: `er-positional-voice.toml` beside the game.

pub mod bank;
pub mod config;
pub mod gain;
pub mod packet;
pub mod sites;
pub mod slots;
