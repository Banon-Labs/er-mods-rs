//! Make a FLVER draw nothing while keeping its file intact: every face set's indices become 0,
//! so every triangle is degenerate. The binder and the FLVER keep their exact shape, which is
//! what lets them stand in for the part the game asks for. Moved from
//! `scripts/route_a_mushroom_hide_flver_faces.rs`.

use super::{Flver, index_range};
use crate::{bytes::slice, error::Result};

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

/// Zero every vertex of a FLVER whose faces are already hidden, returning how many bytes that
/// cleared. Nothing reads them to draw once every index is 0, and zeroes are what Kraken
/// compresses to nearly nothing: the vertices are most of a hair part's size, and the hidden
/// face and hair parts are most of the package.
pub fn blank_vertex_buffers(bytes: &mut [u8]) -> Result<usize> {
    let flver = Flver::parse(bytes)?;
    let mut cleared = 0;
    for buffer in &flver.vertex_buffers {
        let start = flver.header.data_offset + buffer.buffer_offset;
        slice(bytes, start, buffer.buffer_length)?;
        bytes[start..start + buffer.buffer_length].fill(0);
        cleared += buffer.buffer_length;
    }
    Ok(cleared)
}
