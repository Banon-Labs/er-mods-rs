//! experiments module (split from lib.rs; pure code reorganization, no behavior change).

use super::*;

mod picked_summary_refresh;
pub(crate) use picked_summary_refresh::*;

mod product_continue;
pub(crate) use product_continue::*;

mod slot_resolution;
pub(crate) use slot_resolution::*;
