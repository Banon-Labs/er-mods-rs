//! End-to-end: a document in, a `?i=` URL out, and the document back again -- in pure Rust.
//!
//! This is the same claim `reference_decoder.rs` makes with the site's own decoder, made
//! here against the transcribed one in `common` so it holds with no Node installed. What it
//! cannot prove on its own is that the transcription is faithful; that is the node gate's job.

mod common;

use common::{decode_payload, representative_build};
use er_build_export::{
    SHARE_URL_PREFIX, ascii_json, model::BuildExportDoc, share_payload, share_url,
};

/// Round-trip `doc` through the whole pipeline and assert the JSON comes back byte-identical.
///
/// Returns the finished URL so a caller can measure it.
fn round_trip(doc: &BuildExportDoc) -> String {
    let url = share_url(doc);
    let payload = url
        .strip_prefix(SHARE_URL_PREFIX)
        .expect("share_url always writes the prefix");
    assert_eq!(payload, share_payload(doc));
    assert_eq!(decode_payload(payload), ascii_json(doc));
    url
}

#[test]
fn a_default_document_round_trips() {
    round_trip(&BuildExportDoc::default());
}

#[test]
fn a_representative_build_round_trips() {
    let url = round_trip(&representative_build());
    // Not an assertion on the exact figure -- the encoder is allowed to get better -- but a
    // link that no longer fits a browser's address bar would be a product failure, and the
    // conservative bound below is the only place that would show up.
    assert!(url.len() < 8_000, "share link is {} characters", url.len());
}

#[test]
fn a_unicode_name_survives_the_round_trip() {
    let mut doc = representative_build();
    doc.name = "Dongerino \u{e9}\u{30c6}\u{30b9}\u{30c8} \u{1f525}".to_string();
    let url = round_trip(&doc);

    // And the recovered JSON really does carry the name back, not a mangled copy.
    let payload = url.strip_prefix(SHARE_URL_PREFIX).expect("prefix");
    let recovered: serde_json::Value =
        serde_json::from_str(&decode_payload(payload)).expect("payload is a json document");
    assert_eq!(recovered["name"], doc.name);
}

#[test]
fn an_empty_document_still_produces_a_usable_link() {
    let url = share_url(&BuildExportDoc::default());
    assert!(url.starts_with(SHARE_URL_PREFIX));
    assert!(url.len() > SHARE_URL_PREFIX.len());
}

#[test]
fn the_link_is_query_safe() {
    // Every character must survive being pasted into a browser without escaping. `=` is the
    // padding, which is legal unescaped in a query value.
    let url = share_url(&representative_build());
    let payload = url.strip_prefix(SHARE_URL_PREFIX).expect("prefix");
    for character in payload.chars() {
        assert!(
            character.is_ascii_alphanumeric() || matches!(character, '-' | '_' | '='),
            "{character:?} needs escaping"
        );
    }
}

#[test]
fn a_long_description_round_trips() {
    // Highly compressible, and long enough to force far matches.
    let mut doc = representative_build();
    doc.description = "The Erdtree governs all. ".repeat(2_000);
    round_trip(&doc);
}

#[test]
fn report_the_representative_link_length() {
    // Reported rather than asserted: the figure is what a reader wants to know, and pinning
    // it would turn any encoder improvement into a red test.
    let doc = representative_build();
    let json = ascii_json(&doc);
    let url = share_url(&doc);
    println!("character json : {} bytes", json.len());
    println!("share payload  : {} characters", share_payload(&doc).len());
    println!("share url      : {} characters", url.len());
    // The link itself, so `cargo test -- --nocapture` hands over something openable rather
    // than three numbers about a document the reader cannot see.
    println!("share url      : {url}");
}
