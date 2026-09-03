//! Product re-export facade: the Windows clipboard reader/writer behind the build-url editor and
//! the Generate Build Link row now lives in `er_quit_menu_core::build_url_clipboard`.
//!
//! Pure code reorganization, no behavior change: the moved module reaches the product's debug log
//! through the `QuitMenuHost` seam, which `DllMain` installs before any hook can run this code.

pub(crate) use er_quit_menu_core::build_url_clipboard::*;
