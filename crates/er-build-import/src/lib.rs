//! Import an Elden Ring build from an `er-build-planner` share link.
//!
//! The planner's `?b=<id>` links are not encoded payloads -- they are ids for
//! builds stored server-side, fetched with a single unauthenticated
//! `GET https://er-inventory-api.nyasu.business/inventories/<id>`. The response
//! identifies every item by **display name**, so importing a build is two
//! problems: parsing the document ([`model`]) and resolving those names to item
//! ids ([`catalog`]) before computing what to grant ([`plan`]).
//!
//! This crate is deliberately host-buildable and free of game and network
//! dependencies, so the whole mapping is provable with `cargo test`.
//!
//! ```
//! use er_build_import::{catalog::{entry, Kind, MapCatalog}, model, plan::plan};
//!
//! let doc = model::parse(r#"{
//!     "name": "example", "weaponUpgrade": 25,
//!     "inventory": {"slots": [{"name": "Misericorde", "infusion": "Occult"}]}
//! }"#).expect("valid build document");
//!
//! let catalog = MapCatalog::new().with(Kind::Weapon, "Misericorde", entry(1_070_000));
//! let result = plan(&doc, &catalog);
//!
//! assert!(result.is_complete());
//! assert_eq!(result.grants[0].item_id, 1_070_000 + 1200); // Occult offset
//! assert_eq!(result.grants[0].reinforce_lv, 25);
//! ```

pub mod catalog;
pub mod equip;
pub mod model;
pub mod name;
pub mod plan;

pub use catalog::{Catalog, Entry, Kind};
pub use equip::{Capacity, EquipPlan, EquipRef};
pub use model::BuildDoc;
pub use plan::{Grant, Plan, Unresolved};

/// The API host serving `?b=` builds.
pub const API_HOST: &str = "er-inventory-api.nyasu.business";

/// Path for fetching a build by its share id.
///
/// Verified to need no `Authorization` header: the response is byte-identical
/// with and without a session.
pub fn build_path(share_id: &str) -> String {
    format!("/inventories/{share_id}")
}

/// Config key naming the build to import, in the game-directory `er-effects.toml`.
///
/// A key in the file the product already ships rather than an environment variable: a
/// runtime-affecting product lever behind an agent-only env var is not a product lever.
pub const BUILD_URL_KEY: &str = "build_url";

/// Pull `build_url` out of an `er-effects.toml`'s text.
///
/// A deliberate one-key scan rather than a TOML dependency. The product's own parser is private to
/// `er-effects-rs`, and pulling a whole TOML crate into a crate that needs exactly one string would
/// be the larger sin -- but the scan still has to agree with the file the product writes, which is
/// why it lives here, where `cargo test` can hold it to that.
///
/// Accepts `key = 'value'` and `key = "value"`, ignores comment lines, and returns `None` when the
/// key is absent or its value is empty, so an unset key means "import nothing" rather than "import
/// the empty build".
///
/// ```
/// assert_eq!(
///     er_build_import::build_url_from_config("# comment\nbuild_url = 'x?b=abc'\n"),
///     Some("x?b=abc"),
/// );
/// assert_eq!(er_build_import::build_url_from_config("slot = 0\n"), None);
/// ```
pub fn build_url_from_config(contents: &str) -> Option<&str> {
    for line in contents.lines() {
        let line = line.trim();
        if line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if key.trim() != BUILD_URL_KEY {
            continue;
        }
        let value = value.trim().trim_matches(|c| c == '\'' || c == '"').trim();
        if !value.is_empty() {
            return Some(value);
        }
    }
    None
}

/// Extract the `?b=` share id from a planner URL.
///
/// Returns `None` for a URL that carries no `b` parameter, including the
/// self-contained `?i=` form, which needs no network at all.
pub fn share_id_from_url(url: &str) -> Option<&str> {
    let query = url.split_once('?')?.1;
    query
        .split('&')
        .find_map(|pair| pair.strip_prefix("b="))
        .map(|id| id.split('#').next().unwrap_or(id))
        .filter(|id| !id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric()))
}
