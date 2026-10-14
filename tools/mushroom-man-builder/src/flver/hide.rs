//! Make a FLVER draw nothing while keeping its file intact: every face set's indices become 0,
//! so every triangle is degenerate. The binder and the FLVER keep their exact shape, which is
//! what lets them stand in for the part the game asks for. Moved from
//! `scripts/route_a_mushroom_hide_flver_faces.rs`.

use super::{Flver, index_range};
use crate::error::Result;

/// How many face sets were emptied, and how many indices that zeroed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct HideReport {
    pub face_sets: usize,
    pub indices: usize,
}

pub fn hide_all_face_sets(bytes: &mut [u8]) -> Result<HideReport> {
    let flver = Flver::parse(bytes)?;
    let mut report = HideReport {
        face_sets: 0,
        indices: 0,
    };
    for face_set in &flver.face_sets {
        let range = index_range(bytes, &flver, face_set)?;
        bytes[range].fill(0);
        report.face_sets += 1;
        report.indices += face_set.index_count;
    }
    Ok(report)
}
