//! DCX, the compression wrapper around nearly every FromSoftware file.
//!
//! A big-endian header -- `DCX\0`, `DCS\0` with the two sizes, `DCP\0` naming the algorithm,
//! `DCA\0` -- and then the compressed stream. Dark Souls Remastered stores `DFLT` (zlib), read
//! here with `miniz_oxide`. Elden Ring stores `KRAK` (Oodle Kraken), read and written through
//! the game's own Oodle library ([`crate::oodle`]), so what the builder writes carries the same
//! codec, level and header bytes as the file it replaces.

use crate::{
    bytes::{Writer, slice, u32_be},
    error::{Context, Error, Result},
    oodle::Oodle,
};

const DCX_MAGIC: &[u8; 4] = b"DCX\0";
const DCS_MAGIC: &[u8; 4] = b"DCS\0";
const DCP_MAGIC: &[u8; 4] = b"DCP\0";
const DCA_MAGIC: &[u8; 4] = b"DCA\0";
const DCS_OFFSET: usize = 0x18;
const DCP_OFFSET: usize = 0x24;

pub const ALGORITHM_DEFLATE: &[u8; 4] = b"DFLT";
pub const ALGORITHM_KRAKEN: &[u8; 4] = b"KRAK";

/// What a DCX header says about the stream behind it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DcxInfo {
    pub version: u32,
    pub algorithm: [u8; 4],
    pub uncompressed_size: usize,
    pub compressed_size: usize,
    pub data_offset: usize,
}

pub fn is_dcx(bytes: &[u8]) -> bool {
    bytes.get(..4) == Some(DCX_MAGIC.as_slice())
}

pub fn info(bytes: &[u8]) -> Result<DcxInfo> {
    if !is_dcx(bytes) {
        return Err(Error::new("not a DCX file"));
    }
    if slice(bytes, DCS_OFFSET, 4)? != DCS_MAGIC || slice(bytes, DCP_OFFSET, 4)? != DCP_MAGIC {
        return Err(Error::new("DCX header is missing its DCS/DCP sections"));
    }
    let dcp_len = u32_be(bytes, DCP_OFFSET + 8)? as usize;
    let dca_offset = DCP_OFFSET + dcp_len;
    if slice(bytes, dca_offset, 4)? != DCA_MAGIC {
        return Err(Error::new("DCX header is missing its DCA section"));
    }
    let dca_len = u32_be(bytes, dca_offset + 4)? as usize;
    let mut algorithm = [0_u8; 4];
    algorithm.copy_from_slice(slice(bytes, DCP_OFFSET + 4, 4)?);
    Ok(DcxInfo {
        version: u32_be(bytes, 4)?,
        algorithm,
        uncompressed_size: u32_be(bytes, DCS_OFFSET + 4)? as usize,
        compressed_size: u32_be(bytes, DCS_OFFSET + 8)? as usize,
        data_offset: dca_offset + dca_len,
    })
}

/// The payload of a DCX. `oodle` is only consulted for Kraken, so DSR's zlib files read
/// without it.
pub fn decompress(bytes: &[u8], oodle: Option<&Oodle>) -> Result<Vec<u8>> {
    let header = info(bytes)?;
    let stream = slice(bytes, header.data_offset, header.compressed_size)
        .context("DCX compressed stream")?;
    let out = match &header.algorithm {
        ALGORITHM_DEFLATE => inflate(stream)?,
        ALGORITHM_KRAKEN => oodle
            .ok_or_else(|| Error::new("a Kraken DCX needs the game's Oodle library"))?
            .decompress(stream, header.uncompressed_size)?,
        other => {
            return Err(Error::new(format!(
                "DCX algorithm {other:02X?} is not one this builder reads"
            )));
        }
    };
    if out.len() != header.uncompressed_size {
        return Err(Error::new(format!(
            "DCX decompressed to {} bytes, header promised {}",
            out.len(),
            header.uncompressed_size
        )));
    }
    Ok(out)
}

/// zlib when the stream carries a zlib header, raw deflate when it does not.
fn inflate(stream: &[u8]) -> Result<Vec<u8>> {
    let zlib_header = stream.len() >= 2
        && stream[0] & 0x0F == 8
        && (u16::from(stream[0]) << 8 | u16::from(stream[1])) % 31 == 0;
    let result = if zlib_header {
        miniz_oxide::inflate::decompress_to_vec_zlib(stream)
    } else {
        miniz_oxide::inflate::decompress_to_vec(stream)
    };
    result.map_err(|error| Error::new(format!("deflate stream: {error:?}")))
}

const DCP_LEVEL_OFFSET: usize = DCP_OFFSET + 0x0C;

/// `payload` packed exactly the way `source` -- the game file it replaces -- is packed: the
/// same header bytes, algorithm and compression level, with only the two sizes rewritten.
///
/// The game ships both codecs on the paths this builder writes (measured 2026-10-14: the parts
/// are `KRAK` at level 6, `facegen.fgbnd.dcx` and `fg_a_0000_m.partsbnd.dcx` are `DFLT` at
/// level 9, and the two carry different header layouts), so copying the source's choice is
/// what keeps every output a file the loader already reads.
pub fn recompress_like(source: &[u8], payload: &[u8], oodle: Option<&Oodle>) -> Result<Vec<u8>> {
    let header = info(source)?;
    let level = slice(source, DCP_LEVEL_OFFSET, 1)?[0];
    let stream = match &header.algorithm {
        ALGORITHM_DEFLATE => miniz_oxide::deflate::compress_to_vec_zlib(payload, level),
        ALGORITHM_KRAKEN => oodle
            .ok_or_else(|| Error::new("a Kraken DCX needs the game's Oodle library"))?
            .compress_kraken(payload, i32::from(level))?,
        other => {
            return Err(Error::new(format!(
                "DCX algorithm {other:02X?} is not one this builder writes"
            )));
        }
    };
    let mut out = Writer::default();
    out.bytes(slice(source, 0, header.data_offset)?);
    out.patch_u32_be(
        DCS_OFFSET + 4,
        u32::try_from(payload.len()).context("DCX payload size")?,
    );
    out.patch_u32_be(
        DCS_OFFSET + 8,
        u32::try_from(stream.len()).context("DCX compressed size")?,
    );
    out.bytes(&stream);
    Ok(out.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `DFLT` DCX laid out like Elden Ring's `fg_a_0000_m.partsbnd.dcx` (`10000_24_9`).
    fn deflate_template() -> Vec<u8> {
        let mut out = Writer::default();
        out.bytes(DCX_MAGIC);
        for field in [0x0001_0000, 0x18, 0x24, 0x24, 0x2C] {
            out.u32_be(field);
        }
        out.bytes(DCS_MAGIC);
        out.u32_be(0);
        out.u32_be(0);
        out.bytes(DCP_MAGIC);
        out.bytes(ALGORITHM_DEFLATE);
        out.u32_be(0x20);
        out.bytes(&[9, 0, 0, 0]);
        for field in [0, 0, 0, 0x0001_0100] {
            out.u32_be(field);
        }
        out.bytes(DCA_MAGIC);
        out.u32_be(8);
        out.into_inner()
    }

    #[test]
    fn deflate_files_are_rewritten_with_their_own_header_and_read_back() {
        let payload: Vec<u8> = (0..5_000_u32).map(|i| (i % 13) as u8).collect();
        let template = deflate_template();
        let packed = recompress_like(&template, &payload, None).unwrap();
        assert_eq!(packed[..0x1C], template[..0x1C]);
        assert_eq!(packed[0x24..0x4C], template[0x24..0x4C]);
        let header = info(&packed).unwrap();
        assert_eq!(header.uncompressed_size, payload.len());
        assert_eq!(header.data_offset, 0x4C);
        assert_eq!(decompress(&packed, None).unwrap(), payload);
    }

    #[test]
    fn a_kraken_file_without_oodle_is_an_error_not_a_panic() {
        let mut kraken = deflate_template();
        kraken[0x28..0x2C].copy_from_slice(ALGORITHM_KRAKEN);
        assert!(decompress(&kraken, None).is_err());
        assert!(recompress_like(&kraken, b"payload", None).is_err());
    }

    #[test]
    fn a_file_without_the_magic_is_refused() {
        assert!(info(b"BND4\0\0\0\0").is_err());
    }
}
