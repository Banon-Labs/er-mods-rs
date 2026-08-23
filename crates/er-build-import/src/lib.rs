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
