//! The RSA public keys Elden Ring's archive headers are encrypted with.
//!
//! Each `Data*.bhd` is RSA-"encrypted" with a private key, which only means the matching public
//! key reads it. These five public keys are the ones every Souls unpacker carries (UXM, BinderTool,
//! Smithbox); the copies below were taken from UXM's `ArchiveKeys.cs`. They are not game content:
//! a key opens the player's own archive and contributes no bytes to anything the builder writes.
//!
//! They are PKCS#1 `RSAPublicKey` structures, base64 as they appear between the PEM armour lines,
//! decoded here by hand because a two-integer DER sequence is not worth a parser dependency.

use num_bigint::BigUint;

use crate::error::{Error, Result};

pub struct RsaPublicKey {
    pub modulus: BigUint,
    pub exponent: BigUint,
}

/// An archive, the key that opens its header, and whether a game without it is a broken install.
pub struct ArchiveKey {
    pub name: &'static str,
    pub der_base64: &'static str,
    pub required: bool,
}

pub const ARCHIVE_KEYS: &[ArchiveKey] = &[
    ArchiveKey {
        name: "Data0",
        der_base64: "MIIBCwKCAQEA9Rju2whruXDVQZpfylVEPeNxm7XgMHcDyaaRUIpXQE0qEo+6Y36LP0xpFvL0H0kKxHwpuISsdgrnMHJ/yj4S61MWzhO8y4BQbw/zJehhDSRCecFJmFBz3I2JC5FCjoK+82xd9xM5XXdfsdBzRiSghuIHL4qk2WZ/0f/nK5VygeWXn/oLeYBLjX1S8wSSASza64JXjt0bP/i6mpV2SLZqKRxo7x2bIQrR1yHNekSF2jBhZIgcbtMBxjCywn+7p954wjcfjxB5VWaZ4hGbKhi1bhYPccht4XnGhcUTWO3NmJWslwccjQ4ksutLq3uRjLMM0IeTkQO6Pv8/R7UNFtdCWwIERzH8IQ==",
        required: true,
    },
    ArchiveKey {
        name: "Data1",
        der_base64: "MIIBCwKCAQEAxaBCHQJrtLJiJNdG9nq3deA9sY4YCZ4dbTOHO+v+YgWRMcE6iK6oZIJq+nBMUNBbGPmbRrEjkkH9M7LAypAFOPKC6wMHzqIMBsUMuYffulBuOqtEBD11CAwfx37rjwJ+/1tnEqtJjYkrK9yyrIN6Y+jy4ftymQtjk83+L89pvMMmkNeZaPON4O9q5M9PnFoKvK8eY45ZV/Jyk+Pe+xc6+e4h4cx8ML5U2kMM3VDAJush4z/05hS3/bC4B6K9+7dPwgqZgKx1J7DBtLdHSAgwRPpijPeOjKcAa2BDaNp9Cfon70oC+ZCB+HkQ7FjJcF7KaHsH5oHvuI7EZAl2XTsLEQIENa/2JQ==",
        required: true,
    },
    ArchiveKey {
        name: "Data2",
        der_base64: "MIIBDAKCAQEA0iDVVQ230RgrkIHJNDgxE7I/2AaH6Li1Eu9mtpfrrfhfoK2e7y4OWU+lj7AGI4GIgkWpPw8JHaV970Cr6+sTG4Tr5eMQPxrCIH7BJAPCloypxcs2BNfTGXzm6veUfrGzLIDp7wy24lIA8r9ZwUvpKlN28kxBDGeCbGCkYeSVNuF+R9rN4OAMRYh0r1Q950xc2qSNloNsjpDoSKoYN0T7u5rnMn/4mtclnWPVRWU940zr1rymv4Jc3umNf6cT1XqrS1gSaK1JWZfsSeD6Dwk3uvquvfY6YlGRygIlVEMAvKrDRMHylsLtqqhYkZNXMdy0NXopf1rEHKy9poaHEmJldwIFAP////8=",
        required: true,
    },
    ArchiveKey {
        name: "Data3",
        der_base64: "MIIBCwKCAQEAvRRNBnVq3WknCNHrJRelcEA2v/OzKlQkxZw1yKll0Y2Kn6G9ts94SfgZYbdFCnIXy5NEuyHRKrxXz5vurjhrcuoYAI2ZUhXPXZJdgHywac/i3S/IY0V/eDbqepyJWHpP6I565ySqlol1p/BScVjbEsVyvZGtWIXLPDbx4EYFKA5B52uK6Gdz4qcyVFtVEhNoMvg+EoWnyLD7EUzuB2Khl46CuNictyWrLlIHgpKJr1QD8a0ld0PDPHDZn03q6QDvZd23UW2d9J+/HeBt52j08+qoBXPwhndZsmPMWngQDaik6FM7EVRQetKPi6h5uprVmMAS5wR/jQIVTMpTj/zJdwIEXszeQw==",
        required: true,
    },
    ArchiveKey {
        name: "DLC",
        der_base64: "MIIBCwKCAQEAmYJ/5GJU4boJSvZ81BFOHYTGdBWPHnWYly3yWo01BYjGRnz8NTkzDHUxsbjIgtG5XqsQfZstZILQ97hgSI5AaAoCGrT8sn0PeXg2i0mKwL21gRjRUdvPDp1Y+7hgrGwuTkjycqqsQ/qILm4NvJHvGRd7xLOJ9rs2zwYhceRVrq9XU2AXbdY4pdCQ3+HuoaFiJ0dW0ly5qdEXjbSv2QEYe36nWCtsd6hEY9LjbBX8D1fK3D2c6C0gNdHJGH2iEONUN6DMK9t0v2JBnwCOZQ7W+Gt7SpNNrkx8xKEM8gH9na10g9ne11MiO1FnLm8i4zOxVdPHQBKICkKcGS1o3C2dfwIEXw/f3w==",
        required: false,
    },
];

impl ArchiveKey {
    pub fn decode(&self) -> Result<RsaPublicKey> {
        parse_pkcs1(&base64_decode(self.der_base64)?)
            .map_err(|error| error.context(format!("archive key {}", self.name)))
    }
}

/// `RSAPublicKey ::= SEQUENCE { modulus INTEGER, publicExponent INTEGER }`, DER.
fn parse_pkcs1(der: &[u8]) -> Result<RsaPublicKey> {
    let (tag, body, _) = der_element(der)?;
    if tag != DER_SEQUENCE {
        return Err(Error::new("key is not a DER sequence"));
    }
    let (modulus_tag, modulus, rest) = der_element(body)?;
    let (exponent_tag, exponent, _) = der_element(rest)?;
    if modulus_tag != DER_INTEGER || exponent_tag != DER_INTEGER {
        return Err(Error::new("key sequence does not hold two integers"));
    }
    Ok(RsaPublicKey {
        modulus: BigUint::from_bytes_be(modulus),
        exponent: BigUint::from_bytes_be(exponent),
    })
}

const DER_SEQUENCE: u8 = 0x30;
const DER_INTEGER: u8 = 0x02;
const DER_LONG_LENGTH: u8 = 0x80;

/// One DER element: its tag, its contents, and whatever follows it.
fn der_element(bytes: &[u8]) -> Result<(u8, &[u8], &[u8])> {
    let truncated = || Error::new("truncated DER element");
    let tag = *bytes.first().ok_or_else(truncated)?;
    let first_len = *bytes.get(1).ok_or_else(truncated)?;
    let (len, header) = if first_len & DER_LONG_LENGTH == 0 {
        (usize::from(first_len), 2)
    } else {
        let width = usize::from(first_len & !DER_LONG_LENGTH);
        let len_bytes = bytes.get(2..2 + width).ok_or_else(truncated)?;
        let len = len_bytes
            .iter()
            .fold(0_usize, |acc, &byte| (acc << 8) | usize::from(byte));
        (len, 2 + width)
    };
    let body = bytes.get(header..header + len).ok_or_else(truncated)?;
    Ok((tag, body, &bytes[header + len..]))
}

fn base64_decode(text: &str) -> Result<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    let mut accumulator = 0_u32;
    let mut bits = 0_u32;
    for ch in text.bytes() {
        let value = match ch {
            b'A'..=b'Z' => ch - b'A',
            b'a'..=b'z' => ch - b'a' + 26,
            b'0'..=b'9' => ch - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            _ => return Err(Error::new(format!("invalid base64 byte 0x{ch:02X}"))),
        };
        accumulator = (accumulator << 6) | u32::from(value);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((accumulator >> bits) as u8);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_matches_known_vectors() {
        assert_eq!(base64_decode("TWFu").unwrap(), b"Man");
        assert_eq!(base64_decode("TWE=").unwrap(), b"Ma");
        assert_eq!(base64_decode("TQ==").unwrap(), b"M");
    }

    #[test]
    fn every_archive_key_is_a_2048_bit_modulus_with_its_exponent() {
        for key in ARCHIVE_KEYS {
            let decoded = key.decode().unwrap();
            assert_eq!(decoded.modulus.bits(), 2048, "{}", key.name);
            assert!(decoded.exponent.bits() > 1, "{}", key.name);
        }
    }

    #[test]
    fn data2_carries_the_all_ones_exponent_it_was_published_with() {
        let data2 = ARCHIVE_KEYS.iter().find(|key| key.name == "Data2").unwrap();
        assert_eq!(
            data2.decode().unwrap().exponent,
            BigUint::from(0xFFFF_FFFF_u32)
        );
    }
}
