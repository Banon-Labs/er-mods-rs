// The native-load / full-save-read / own-stepper autoload constant table moved verbatim into
// the er-title-flow crate (crates/er-title-flow/src/constants_autoload_state.rs) with the
// autoload/title-flow slice. Only visibility changed (`pub(crate)` -> `pub`); the single
// `pub(crate) use er_title_flow::*;` shim in constants.rs re-exports the whole table into this
// module, so every `crate::constants::NAME` and flat-namespace reference resolves unchanged.
//
// The generated-prologue include that stood here is gone: all nine specs moved to
// `er-quit-menu-core`'s build script on 2026-09-12, with the Save Game flow that reads them.
// One generator, one set of bytes, and now a crate a standalone shell can link.
