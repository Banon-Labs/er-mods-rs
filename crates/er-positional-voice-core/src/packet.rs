//! One compressed voice chunk on the wire: a five-byte header and the bytes `GetVoice` returned.
//!
//! The header is what keeps anything else that arrives on the channel from being handed to the
//! decoder: a message without it, or with another version, is dropped.

/// `ERPV`: Elden Ring positional voice.
pub const MAGIC: [u8; 4] = *b"ERPV";
pub const VERSION: u8 = 1;
pub const HEADER_LEN: usize = MAGIC.len() + 1;

/// The largest chunk sent or accepted. `GetVoice` returns well under a kilobyte per poll; this
/// bounds what a peer can make the decoder chew on.
pub const MAX_PAYLOAD: usize = 8 * 1024;

/// The message for one chunk, or `None` for an empty or oversized one.
pub fn encode(payload: &[u8]) -> Option<Vec<u8>> {
    if payload.is_empty() || payload.len() > MAX_PAYLOAD {
        return None;
    }
    let mut out = Vec::with_capacity(HEADER_LEN + payload.len());
    out.extend_from_slice(&MAGIC);
    out.push(VERSION);
    out.extend_from_slice(payload);
    Some(out)
}

/// The chunk inside a received message, or `None` when it is not one of ours.
pub fn decode(message: &[u8]) -> Option<&[u8]> {
    let payload = message.strip_prefix(&MAGIC)?.strip_prefix(&[VERSION])?;
    (!payload.is_empty() && payload.len() <= MAX_PAYLOAD).then_some(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip() {
        let m = encode(&[1, 2, 3]).unwrap();
        assert_eq!(&m[..5], b"ERPV\x01");
        assert_eq!(decode(&m), Some(&[1u8, 2, 3][..]));
    }

    #[test]
    fn rejects_foreign_and_empty() {
        assert_eq!(encode(&[]), None);
        assert_eq!(encode(&vec![0; MAX_PAYLOAD + 1]), None);
        assert_eq!(decode(b"ERPV\x01"), None);
        assert_eq!(decode(b"ERPV\x02abc"), None);
        assert_eq!(decode(b"XXXX\x01abc"), None);
        assert_eq!(decode(b""), None);
        let mut big = b"ERPV\x01".to_vec();
        big.extend(vec![0; MAX_PAYLOAD + 1]);
        assert_eq!(decode(&big), None);
    }
}
