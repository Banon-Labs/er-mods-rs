//! Masked byte scanning over the running game image.
//!
//! Every address this crate uses is discovered here rather than pinned, because the whole
//! failure being fixed is a version drift: a table of 1.17 RVAs would break again on 1.18 in
//! exactly the way ersc just did.

use er_game_base::mem;

/// Bytes read per scan pass.
const SCAN_CHUNK: usize = 1 << 20;

/// One byte of a pattern: `None` matches anything.
pub(crate) type MaskedByte = Option<u8>;

/// A place in `.text` matching a pattern, with the bytes actually found there.
pub(crate) struct Match {
    pub(crate) address: usize,
    pub(crate) bytes: Vec<u8>,
}

fn matches_at(window: &[u8], pattern: &[MaskedByte]) -> bool {
    pattern
        .iter()
        .zip(window)
        .all(|(expected, found)| expected.is_none_or(|byte| byte == *found))
}

/// Every match of `pattern` in `[start, start+len)`, stopping at `max_hits`.
///
/// The caller decides what a hit count means; this returns what is there. Unreadable pages are
/// skipped rather than aborting the scan: the image has holes, and a hole is not a failure.
pub(crate) fn scan(
    start: usize,
    len: usize,
    pattern: &[MaskedByte],
    max_hits: usize,
) -> Vec<Match> {
    let mut hits = Vec::new();
    if pattern.is_empty() || len < pattern.len() {
        return hits;
    }
    let overlap = pattern.len() - 1;
    let mut buf = vec![0u8; SCAN_CHUNK + overlap];
    let mut offset = 0usize;
    while offset < len && hits.len() < max_hits {
        let want = (SCAN_CHUNK + overlap).min(len - offset);
        let window = &mut buf[..want];
        if unsafe { mem::read_bytes(start + offset, window) } {
            let last = want - pattern.len();
            for i in 0..=last {
                // A match starting past SCAN_CHUNK is found again as the next pass's first
                // bytes; only the final chunk, which has no successor, keeps those.
                if i >= SCAN_CHUNK && offset + SCAN_CHUNK < len {
                    break;
                }
                if matches_at(&window[i..], pattern) {
                    hits.push(Match {
                        address: start + offset + i,
                        bytes: window[i..i + pattern.len()].to_vec(),
                    });
                    if hits.len() >= max_hits {
                        break;
                    }
                }
            }
        }
        offset += SCAN_CHUNK;
    }
    hits
}

/// Parse a pattern written the way ersc writes its own: `"80 B9 ? ? 00"`, where `?` is a
/// wildcard. Panics on a malformed literal, which is a source bug, not a runtime condition.
pub(crate) fn pattern(text: &str) -> Vec<MaskedByte> {
    text.split_whitespace()
        .map(|token| {
            if token == "?" {
                None
            } else {
                Some(u8::from_str_radix(token, 16).expect("pattern literal is hex or `?`"))
            }
        })
        .collect()
}
