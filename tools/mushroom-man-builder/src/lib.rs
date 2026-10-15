//! Mushroom Man, built on the player's machine from the player's own Dark Souls Remastered and
//! Elden Ring installs. See `Cargo.toml` for why the model cannot be shipped instead.

pub mod archive;
pub mod bnd;
pub mod bytes;
pub mod character;
pub mod dcx;
pub mod edge;
pub mod error;
pub mod fit;
pub mod flver;
pub mod keys;
pub mod locate;
pub mod map_report;
pub mod oodle;
pub mod package;
pub mod profile;
pub mod textures;

/// The installer's Steam discovery, shared by path; see [`locate`].
#[path = "../../er-installer/src/steam.rs"]
pub mod steam;
