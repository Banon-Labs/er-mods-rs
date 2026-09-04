//! An encoder for the LZ-UTF8 stream format (`rotemdan/lzutf8.js`), which is the compressor
//! the planner runs over a `?i=` payload.
//!
//! # The format
//!
//! LZ-UTF8 is LZ77 over UTF-8 bytes, arranged so that a compressed stream is still a valid
//! byte stream to scan and so that plain ASCII passes through unchanged. Two kinds of item:
//!
//! * a **literal**, which is the input byte, emitted as itself;
//! * a **match**, a (length, distance) back-reference, in one of two forms:
//!   - `0b110L_LLLL` `DDDD_DDDD` -- two bytes, for `distance < 128`;
//!   - `0b111L_LLLL` `0DDD_DDDD` `DDDD_DDDD` -- three bytes, for anything larger.
//!
//! Length lives in the header's low five bits, hence [`MAXIMUM_SEQUENCE_LENGTH`]; distance is
//! big-endian and capped at [`MAXIMUM_MATCH_DISTANCE`] so the three-byte form's first distance
//! byte always has its top bit clear. A match must be at least [`MINIMUM_SEQUENCE_LENGTH`]
//! bytes or it would not pay for its header.
//!
//! # How a literal is told from a match
//!
//! A match header collides with the UTF-8 lead-byte range, so the decoder cannot dispatch on
//! the header byte alone. It dispatches on the byte **after** it: a byte `>= 0xC0` is a match
//! header only when the next byte has its top bit **clear**, and is otherwise a literal. That
//! rule works because of two facts that hold together:
//!
//! * a match header is always followed by a distance byte, which is `< 0x80` in both forms
//!   (guaranteed by [`MAXIMUM_MATCH_DISTANCE`]); and
//! * in valid UTF-8 a byte `>= 0xC0` is a lead byte, so the next byte of the *input* is a
//!   continuation byte, `0x80..=0xBF`.
//!
//! The second fact is why [`compress`] takes a `&str` rather than `&[u8]`. Feed the same
//! algorithm arbitrary bytes and a literal `0xC0..=0xFF` followed by a literal `< 0x80` --
//! trivial to produce, e.g. Latin-1 text -- emits a byte pair the decoder reads as a match,
//! silently decompressing to something else entirely. The reference implementation has this
//! hazard and relies on its callers not to hit it; taking `&str` removes it by construction.
//! The one remaining case, a literal lead byte followed by a *match*, is safe: the match
//! header is itself `>= 0xC0`, so the top bit is set and the lead byte still reads as a
//! literal.
//!
//! # What this encoder is not
//!
//! It does not aim to be byte-identical to the JavaScript encoder, and it is not: it uses a
//! plain bounded-bucket hash chain rather than the reference's compacting `Uint32Array`
//! table, and it takes the longest match it finds rather than the reference's
//! encoding-cost heuristic. That is deliberate. Any valid LZ-UTF8 stream decompresses to the
//! same text, so the acceptance bar is what the site's decoder recovers, never what our bytes
//! look like -- and against that bar a conservative matcher that emits fewer matches is
//! strictly safer than a clever one that emits a wrong one.

/// Shortest back-reference worth encoding. Below this a match costs more than the literals.
pub const MINIMUM_SEQUENCE_LENGTH: usize = 4;

/// Longest back-reference the header's five length bits can express.
pub const MAXIMUM_SEQUENCE_LENGTH: usize = 31;

/// Furthest back a match may reach. Chosen so the three-byte form's first distance byte is
/// always `<= 0x7F`, which is what keeps a match header distinguishable from a literal.
pub const MAXIMUM_MATCH_DISTANCE: usize = 32_767;

/// Header bits for the two-byte (near) match form.
const SHORT_MATCH_HEADER: u8 = 0b1100_0000;

/// Header bits for the three-byte (far) match form.
const LONG_MATCH_HEADER: u8 = 0b1110_0000;

/// First distance that no longer fits the two-byte form's single unsigned byte with the top
/// bit clear.
const FIRST_LONG_FORM_DISTANCE: usize = 128;

/// Number of prefix-hash buckets. The reference's value; a prime keeps the multiplicative
/// hash below from clustering.
const PREFIX_HASH_TABLE_SIZE: usize = 65_537;

/// Candidate positions kept per bucket. The reference caps buckets at the same figure, and it
/// bounds the match search to a fixed cost regardless of how repetitive the input is.
const MAXIMUM_BUCKET_CAPACITY: usize = 64;

/// Multipliers for the four-byte prefix hash, from the reference implementation.
const HASH_MULTIPLIERS: [usize; MINIMUM_SEQUENCE_LENGTH] = [7_880_599, 39_601, 199, 1];

/// A back-reference to earlier output.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Match {
    /// Bytes copied, in `MINIMUM_SEQUENCE_LENGTH..=MAXIMUM_SEQUENCE_LENGTH`.
    length: usize,
    /// How far back the copy starts, in `1..=MAXIMUM_MATCH_DISTANCE`.
    distance: usize,
}

/// Compress `input` into a single LZ-UTF8 block.
///
/// A single block is what the planner produces: the library's synchronous `compress` entry
/// point makes one `Compressor` and calls `compressBlock` once, with no splitting. Multi-block
/// streams exist only on the async path, and carry cross-block state this crate would then
/// have to reproduce for nothing.
///
/// The output never exceeds the input's UTF-8 length: literals are one byte each and every
/// match replaces at least [`MINIMUM_SEQUENCE_LENGTH`] bytes with at most three.
///
/// ```
/// let text = "the quick brown fox, the quick brown fox, the quick brown fox";
/// let compressed = er_build_export::lzutf8::compress(text);
/// assert!(compressed.len() < text.len());
/// ```
pub fn compress(input: &str) -> Vec<u8> {
    let bytes = input.as_bytes();
    let length = bytes.len();
    let mut out = Vec::with_capacity(length);

    // One bucket per prefix hash, each holding the most recent positions with that prefix,
    // oldest first.
    let mut buckets: Vec<Vec<u32>> = vec![Vec::new(); PREFIX_HASH_TABLE_SIZE];

    // First position not yet covered by an emitted match. Positions below it are consumed.
    let mut covered_through = 0usize;

    for position in 0..length {
        let inside_a_match = position < covered_through;

        // The final MINIMUM_SEQUENCE_LENGTH - 1 bytes cannot start a match and have no
        // four-byte prefix to hash, so they are literals and nothing is indexed for them.
        if position + MINIMUM_SEQUENCE_LENGTH > length {
            if !inside_a_match {
                out.push(bytes[position]);
            }
            continue;
        }

        let bucket = prefix_bucket(bytes, position);

        if !inside_a_match {
            match longest_match(bytes, position, &buckets[bucket]) {
                Some(found) => {
                    emit_match(&mut out, found);
                    covered_through = position + found.length;
                }
                None => out.push(bytes[position]),
            }
        }

        // Index every position, including those inside a match: the reference does, and a
        // position skipped here is a back-reference target lost for the rest of the input.
        remember(&mut buckets[bucket], position);
    }

    out
}

/// Hash the four-byte prefix at `position`.
///
/// # Panics
///
/// Panics unless `position + MINIMUM_SEQUENCE_LENGTH <= bytes.len()`, which the one caller
/// guarantees.
fn prefix_bucket(bytes: &[u8], position: usize) -> usize {
    let mut hash = 0usize;
    for (offset, multiplier) in HASH_MULTIPLIERS.iter().enumerate() {
        hash += usize::from(bytes[position + offset]) * multiplier;
    }
    hash % PREFIX_HASH_TABLE_SIZE
}

/// Record `position` in `bucket`, evicting the oldest entry once the bucket is full.
fn remember(bucket: &mut Vec<u32>, position: usize) {
    if bucket.len() == MAXIMUM_BUCKET_CAPACITY {
        bucket.remove(0);
    }
    // An LZ-UTF8 block addresses at most MAXIMUM_MATCH_DISTANCE back, and this crate's inputs
    // are share links, so a position always fits a u32 many times over.
    debug_assert!(u32::try_from(position).is_ok());
    bucket.push(position as u32);
}

/// Find the best back-reference for `position` among `bucket`'s candidates.
///
/// Candidates are visited newest first, so the scan can stop at the first one that is out of
/// range: everything behind it is older still.
fn longest_match(bytes: &[u8], position: usize, bucket: &[u32]) -> Option<Match> {
    let mut best: Option<Match> = None;

    for candidate in bucket.iter().rev() {
        let candidate = *candidate as usize;
        let distance = position - candidate;
        if distance > MAXIMUM_MATCH_DISTANCE {
            break;
        }

        let length = matching_prefix_length(bytes, candidate, position);
        if length < MINIMUM_SEQUENCE_LENGTH {
            continue;
        }

        // Longer wins; at equal length the nearer one wins, because a distance under
        // FIRST_LONG_FORM_DISTANCE encodes in two bytes rather than three.
        let improves = match best {
            None => true,
            Some(current) => {
                length > current.length || (length == current.length && distance < current.distance)
            }
        };
        if improves {
            best = Some(Match { length, distance });
        }
        if length == MAXIMUM_SEQUENCE_LENGTH && distance < FIRST_LONG_FORM_DISTANCE {
            // Longest possible match in the cheapest possible encoding; nothing can beat it.
            break;
        }
    }

    best
}

/// How many bytes `earlier` and `current` agree on, capped at [`MAXIMUM_SEQUENCE_LENGTH`] and
/// at the end of the input.
///
/// The comparison may run past `current` when `earlier` is close behind it, which produces an
/// overlapping match. That is legal and intended: the decoder copies byte by byte into its
/// output as it goes, so bytes written by this very match are available to it.
fn matching_prefix_length(bytes: &[u8], earlier: usize, current: usize) -> usize {
    let limit = MAXIMUM_SEQUENCE_LENGTH.min(bytes.len() - current);
    let mut matched = 0usize;
    while matched < limit && bytes[earlier + matched] == bytes[current + matched] {
        matched += 1;
    }
    matched
}

/// Append `found` in whichever of the two header forms its distance calls for.
fn emit_match(out: &mut Vec<u8>, found: Match) {
    debug_assert!((MINIMUM_SEQUENCE_LENGTH..=MAXIMUM_SEQUENCE_LENGTH).contains(&found.length));
    debug_assert!((1..=MAXIMUM_MATCH_DISTANCE).contains(&found.distance));

    // Length is 4..=31, so it occupies exactly the five bits below the header pattern.
    let length_bits = found.length as u8;

    if found.distance < FIRST_LONG_FORM_DISTANCE {
        out.push(SHORT_MATCH_HEADER | length_bits);
        out.push(found.distance as u8);
    } else {
        out.push(LONG_MATCH_HEADER | length_bits);
        out.push((found.distance >> 8) as u8);
        out.push((found.distance & 0xff) as u8);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_input_produces_empty_output() {
        assert!(compress("").is_empty());
    }

    #[test]
    fn short_input_is_all_literals() {
        // Below MINIMUM_SEQUENCE_LENGTH nothing can match, so the stream is the bytes.
        assert_eq!(compress("abc"), b"abc");
    }

    #[test]
    fn unmatchable_ascii_passes_through_unchanged() {
        let text = "abcdefghijklmnopqrstuvwxyz0123456789";
        assert_eq!(compress(text), text.as_bytes());
    }

    #[test]
    fn output_never_exceeds_the_input() {
        for text in ["", "a", "ab", "abc", "abcd", "hello world", "aaaaaaaaaaaa"] {
            assert!(compress(text).len() <= text.len(), "grew on {text:?}");
        }
    }

    #[test]
    fn a_near_repeat_uses_the_two_byte_form() {
        // "abcdefgh" twice: the second copy is a length-8 match at distance 8.
        let compressed = compress("abcdefghabcdefgh");
        assert_eq!(compressed.len(), 8 + 2);
        assert_eq!(compressed[8], SHORT_MATCH_HEADER | 8);
        assert_eq!(compressed[9], 8);
    }

    #[test]
    fn a_far_repeat_uses_the_three_byte_form() {
        let prefix = "0123456789abcdef";
        let filler: String = (0..FIRST_LONG_FORM_DISTANCE + 16)
            .map(|index| char::from(b'A' + (index % 26) as u8))
            .collect();
        let compressed = compress(&format!("{prefix}{filler}{prefix}"));
        let header = compressed
            .iter()
            .rev()
            .find(|byte| **byte >= LONG_MATCH_HEADER)
            .copied();
        assert!(
            header.is_some(),
            "no long-form match emitted: {compressed:?}"
        );
    }

    #[test]
    fn every_match_header_is_followed_by_a_byte_with_the_top_bit_clear() {
        // The invariant the decoder's literal/match dispatch rests on.
        let text = "the quick brown fox ".repeat(400);
        let compressed = compress(&text);
        let mut index = 0;
        while index < compressed.len() {
            let byte = compressed[index];
            if byte < SHORT_MATCH_HEADER {
                index += 1;
                continue;
            }
            let next = compressed[index + 1];
            assert!(next < 0x80, "ambiguous header at {index}");
            index += if byte >= LONG_MATCH_HEADER { 3 } else { 2 };
        }
    }

    #[test]
    fn multibyte_literals_are_never_followed_by_a_low_byte() {
        // The same invariant from the other side: a UTF-8 lead byte emitted as a literal must
        // be followed by its continuation byte or by a match header, never by ASCII.
        let text = "\u{30c6}\u{30b9}\u{30c8} \u{e9}a \u{1f525}b".repeat(50);
        let compressed = compress(&text);
        let mut index = 0;
        while index < compressed.len() {
            let byte = compressed[index];
            if byte < SHORT_MATCH_HEADER {
                index += 1;
                continue;
            }
            let next = compressed[index + 1];
            if next >= 0x80 {
                // Read as a literal lead byte by the decoder, which is what it is.
                index += 1;
                continue;
            }
            index += if byte >= LONG_MATCH_HEADER { 3 } else { 2 };
        }
        assert_eq!(index, compressed.len(), "stream did not scan cleanly");
    }

    #[test]
    fn highly_repetitive_input_compresses_hard() {
        let text = "a".repeat(10_000);
        // Every match carries 31 bytes for 2, so the ceiling is roughly len/31*2.
        assert!(compress(&text).len() < text.len() / 10);
    }
}
