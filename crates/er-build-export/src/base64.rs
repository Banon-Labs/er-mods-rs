//! Standard, padded base64 -- the dialect both of the planner's two base64 stages use.
//!
//! Both stages are `btoa`-shaped: `btoa` in the browser and Node's
//! `Buffer.toString("base64")` (which LZ-UTF8 calls for `outputEncoding: "Base64"`) emit the
//! standard `A-Za-z0-9+/` alphabet and **pad** with `=`. The URL-safe substitution the planner
//! makes afterwards is a separate, later step over the finished string, not a different
//! alphabet here -- see [`crate::to_url_alphabet`]. Getting that backwards produces a payload
//! whose base64 the site's decoder rejects for the one input in four that needs padding.
//!
//! Encode-only on purpose. This crate writes links; reading them back is
//! `er-build-import`'s job.

/// The standard alphabet, in index order.
const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/// The pad character appended to round the output up to a multiple of four.
const PAD: u8 = b'=';

/// Bytes consumed per output quantum.
const BYTES_PER_GROUP: usize = 3;

/// Characters produced per output quantum.
const CHARS_PER_GROUP: usize = 4;

/// Encode `bytes` as padded standard base64.
///
/// ```
/// assert_eq!(er_build_export::base64::encode(b""), "");
/// assert_eq!(er_build_export::base64::encode(b"f"), "Zg==");
/// assert_eq!(er_build_export::base64::encode(b"foobar"), "Zm9vYmFy");
/// ```
pub fn encode(bytes: &[u8]) -> String {
    let groups = bytes.len().div_ceil(BYTES_PER_GROUP);
    let mut out = String::with_capacity(groups * CHARS_PER_GROUP);

    for chunk in bytes.chunks(BYTES_PER_GROUP) {
        // Pack the chunk into the low 24 bits, missing bytes reading as zero -- which is what
        // the padded form encodes: the discarded bits are required to be zero, so the
        // sextets we do emit are the same ones a three-byte chunk would have produced.
        let mut packed = 0u32;
        for (index, byte) in chunk.iter().enumerate() {
            packed |= u32::from(*byte) << (16 - 8 * index);
        }

        // Emit one character per sextet, replacing with padding those that lie entirely
        // beyond the bytes we actually had.
        for index in 0..CHARS_PER_GROUP {
            let sextet_covers_a_real_byte = index <= chunk.len();
            if sextet_covers_a_real_byte {
                let sextet = (packed >> (18 - 6 * index)) & 0b11_1111;
                out.push(char::from(ALPHABET[sextet as usize]));
            } else {
                out.push(char::from(PAD));
            }
        }
    }

    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// RFC 4648 section 10, which exists precisely to pin the padding cases.
    #[test]
    fn rfc4648_test_vectors() {
        assert_eq!(encode(b""), "");
        assert_eq!(encode(b"f"), "Zg==");
        assert_eq!(encode(b"fo"), "Zm8=");
        assert_eq!(encode(b"foo"), "Zm9v");
        assert_eq!(encode(b"foob"), "Zm9vYg==");
        assert_eq!(encode(b"fooba"), "Zm9vYmE=");
        assert_eq!(encode(b"foobar"), "Zm9vYmFy");
    }

    #[test]
    fn output_is_always_a_multiple_of_four() {
        for len in 0..64 {
            let input = vec![0xA5; len];
            assert_eq!(encode(&input).len() % CHARS_PER_GROUP, 0, "len {len}");
        }
    }

    #[test]
    fn every_alphabet_index_is_reachable() {
        // 0x00..0x3F across the three byte positions covers all 64 sextet values.
        let input: Vec<u8> = (0u16..=255).map(|b| b as u8).collect();
        let encoded = encode(&input);
        for symbol in ALPHABET {
            assert!(
                encoded.contains(char::from(*symbol)),
                "symbol {} never emitted",
                char::from(*symbol)
            );
        }
    }

    #[test]
    fn uses_the_standard_alphabet_not_the_url_safe_one() {
        // 0xFB, 0xFF pack to sextets 62 and 63, which is where the two dialects differ.
        let encoded = encode(&[0xFB, 0xFF, 0xFF]);
        assert!(encoded.contains('+'), "{encoded}");
        assert!(encoded.contains('/'), "{encoded}");
    }
}
