//! Round-trip the compressor against an independently written decompressor.
//!
//! The node gate in `reference_decoder.rs` is the authoritative proof, but it needs Node and
//! an extracted copy of the site's bundle, so it skips on a machine that has neither. These
//! tests need nothing, which is what makes a bare `cargo test -p er-build-export` worth
//! running: the decompressor in `common` is transcribed from the reference implementation's
//! decoder, so agreeing with it is the same claim the site's decoder makes.

mod common;

use common::lzutf8_decompress;
use er_build_export::lzutf8::{
    MAXIMUM_MATCH_DISTANCE, MAXIMUM_SEQUENCE_LENGTH, MINIMUM_SEQUENCE_LENGTH, compress,
};

/// Compress `text`, decompress it again, and insist on getting `text` back.
///
/// Returns the compressed length so a caller can assert on it.
fn round_trip(text: &str) -> usize {
    let compressed = compress(text);
    let recovered = lzutf8_decompress(&compressed);
    let recovered = String::from_utf8(recovered).unwrap_or_else(|error| {
        panic!(
            "decompressed {} bytes that are not UTF-8: {error}",
            compressed.len()
        )
    });
    assert_eq!(recovered, text, "round trip changed the text");
    compressed.len()
}

#[test]
fn empty_input() {
    assert_eq!(round_trip(""), 0);
}

#[test]
fn shorter_than_a_match() {
    for length in 0..MINIMUM_SEQUENCE_LENGTH {
        let text = "z".repeat(length);
        assert_eq!(round_trip(&text), length);
    }
}

#[test]
fn all_ascii() {
    let text = "The Erdtree governs all. Its grace is our guide, and our judgement.";
    round_trip(text);
}

#[test]
fn every_ascii_byte() {
    // Includes the control characters the planner's own JSON shorthand substitutes in.
    let text: String = (0u8..0x80).map(char::from).collect();
    round_trip(&text);
}

#[test]
fn highly_repetitive_exercises_matches() {
    let text = "Miriam's Vanishing, Miriam's Vanishing, ".repeat(500);
    let compressed = round_trip(&text);
    assert!(
        compressed < text.len() / 8,
        "{compressed} bytes from {} is barely compressed",
        text.len()
    );
}

#[test]
fn a_single_repeated_byte_exercises_overlapping_matches() {
    // Distance 1, length 31, over and over: every match reads bytes it is itself writing.
    let text = "a".repeat(4096);
    round_trip(&text);
}

#[test]
fn multibyte_utf8() {
    let text = "\u{30c6}\u{30b9}\u{30c8} Mis\u{e9}ricorde \u{1f525} \u{5024}".repeat(200);
    round_trip(&text);
}

#[test]
fn multibyte_utf8_adjacent_to_ascii_runs() {
    // The literal/match ambiguity lives exactly here: a lead byte next to a low byte.
    let text = "\u{e9}a\u{e9}b\u{e9}c\u{c0}\u{ff}\u{7f}\u{80}".repeat(300);
    round_trip(&text);
}

#[test]
fn mixed_json_shaped_text() {
    let unit = r#"{"name":"Glintstone Nail","order":4,"infusion":"Standard"},"#;
    round_trip(&unit.repeat(400));
}

#[test]
fn two_hundred_kilobytes() {
    // Deterministic pseudo-random text so a failure is reproducible. Mixing a compressible
    // and an incompressible half keeps both code paths under load.
    let mut state = 0x2545_f491_4f6c_dd1du64;
    let mut text = String::with_capacity(200 * 1024);
    while text.len() < 100 * 1024 {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        // Printable ASCII only, so the input stays a `str` without a UTF-8 dance.
        text.push(char::from(b' ' + (state % 95) as u8));
    }
    while text.len() < 200 * 1024 {
        text.push_str("Great Oracular Bubble, ");
    }
    assert!(text.len() >= 200 * 1024);
    round_trip(&text);
}

#[test]
fn a_match_at_the_maximum_distance() {
    // The far edge of the three-byte form: a repeat exactly MAXIMUM_MATCH_DISTANCE back.
    let head = "0123456789abcdefghij";
    let gap = "x".repeat(MAXIMUM_MATCH_DISTANCE - head.len());
    round_trip(&format!("{head}{gap}{head}"));
}

#[test]
fn a_repeat_beyond_the_maximum_distance_still_round_trips() {
    let head = "0123456789abcdefghij";
    let gap = "x".repeat(MAXIMUM_MATCH_DISTANCE * 2);
    round_trip(&format!("{head}{gap}{head}"));
}

#[test]
fn runs_longer_than_one_match_are_split_into_several() {
    let text = "q".repeat(MAXIMUM_SEQUENCE_LENGTH * 4);
    round_trip(&text);
}

#[test]
fn compression_never_expands() {
    let cases = [
        "",
        "a",
        "abcd",
        "\u{1f525}",
        "\u{30c6}\u{30b9}\u{30c8}",
        "no repeats here at all, honestly",
    ];
    for text in cases {
        let compressed = round_trip(text);
        assert!(compressed <= text.len(), "{text:?} grew to {compressed}");
    }
}
