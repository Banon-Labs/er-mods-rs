//! The message-box and save-request prologues the Save Game flow byte-checks.
//!
//! Assembled from named instructions by this crate's `build.rs` and, when a copy of
//! `eldenring-deobf.bin` is present, compared against the real image at the same address.
//! Hand-typing them is what the generator exists to prevent: a prologue one byte wrong fails its
//! own install-time check and disarms the hook silently.
//!
//! They are `pub` rather than crate-private because `er-quickload` reads the same bytes, and one
//! generator producing one set is the point of moving them here.

include!(concat!(
    env!("OUT_DIR"),
    "/generated_save_flow_prologues.rs"
));
