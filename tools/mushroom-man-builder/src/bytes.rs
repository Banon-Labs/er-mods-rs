//! Bounds-checked reads out of a byte slice, and a growable little/big-endian writer.
//!
//! Every format here is offsets into one buffer, and every one of those offsets comes out of a
//! file this tool did not write. A read past the end is therefore an ordinary input error, never
//! a panic, and it names the offset it wanted.

use crate::error::{Error, Result};

pub fn slice(bytes: &[u8], offset: usize, len: usize) -> Result<&[u8]> {
    offset
        .checked_add(len)
        .and_then(|end| bytes.get(offset..end))
        .ok_or_else(|| {
            Error::new(format!(
                "read of 0x{len:X} bytes at 0x{offset:X} runs past the end (0x{:X})",
                bytes.len()
            ))
        })
}

fn array<const N: usize>(bytes: &[u8], offset: usize) -> Result<[u8; N]> {
    let mut out = [0_u8; N];
    out.copy_from_slice(slice(bytes, offset, N)?);
    Ok(out)
}

pub fn u8_at(bytes: &[u8], offset: usize) -> Result<u8> {
    Ok(array::<1>(bytes, offset)?[0])
}

pub fn i8_at(bytes: &[u8], offset: usize) -> Result<i8> {
    Ok(i8::from_le_bytes(array(bytes, offset)?))
}

pub fn u16_le(bytes: &[u8], offset: usize) -> Result<u16> {
    Ok(u16::from_le_bytes(array(bytes, offset)?))
}

pub fn i16_le(bytes: &[u8], offset: usize) -> Result<i16> {
    Ok(i16::from_le_bytes(array(bytes, offset)?))
}

pub fn u32_le(bytes: &[u8], offset: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(array(bytes, offset)?))
}

pub fn i32_le(bytes: &[u8], offset: usize) -> Result<i32> {
    Ok(i32::from_le_bytes(array(bytes, offset)?))
}

pub fn u64_le(bytes: &[u8], offset: usize) -> Result<u64> {
    Ok(u64::from_le_bytes(array(bytes, offset)?))
}

pub fn i64_le(bytes: &[u8], offset: usize) -> Result<i64> {
    Ok(i64::from_le_bytes(array(bytes, offset)?))
}

pub fn f32_le(bytes: &[u8], offset: usize) -> Result<f32> {
    Ok(f32::from_le_bytes(array(bytes, offset)?))
}

pub fn u32_be(bytes: &[u8], offset: usize) -> Result<u32> {
    Ok(u32::from_be_bytes(array(bytes, offset)?))
}

/// A NUL-terminated UTF-16LE string starting at `offset`.
pub fn utf16z(bytes: &[u8], offset: usize) -> Result<String> {
    let mut units = Vec::new();
    let mut cursor = offset;
    loop {
        let unit = u16_le(bytes, cursor)?;
        if unit == 0 {
            break;
        }
        units.push(unit);
        cursor += 2;
    }
    String::from_utf16(&units)
        .map_err(|_| Error::new(format!("string at 0x{offset:X} is not valid UTF-16")))
}

/// A NUL-terminated single-byte string starting at `offset`.
///
/// The binders this reads name their files in Shift-JIS, and the names this tool cares about
/// are all ASCII paths. A name that is not valid UTF-8 is rejected rather than guessed at.
pub fn byte_string_z(bytes: &[u8], offset: usize) -> Result<String> {
    let tail = bytes
        .get(offset..)
        .ok_or_else(|| Error::new(format!("string offset 0x{offset:X} is past the end")))?;
    let len = tail
        .iter()
        .position(|&byte| byte == 0)
        .ok_or_else(|| Error::new(format!("string at 0x{offset:X} has no terminator")))?;
    String::from_utf8(tail[..len].to_vec())
        .map_err(|_| Error::new(format!("string at 0x{offset:X} is not ASCII/UTF-8")))
}

/// Writes into a growing buffer, and patches values in after the fact for the offsets that are
/// only known once the data they point at has been laid out.
#[derive(Default)]
pub struct Writer {
    buf: Vec<u8>,
}

impl Writer {
    pub fn position(&self) -> usize {
        self.buf.len()
    }

    pub fn bytes(&mut self, data: &[u8]) {
        self.buf.extend_from_slice(data);
    }

    pub fn u8(&mut self, value: u8) {
        self.buf.push(value);
    }

    pub fn u32_le(&mut self, value: u32) {
        self.bytes(&value.to_le_bytes());
    }

    pub fn i32_le(&mut self, value: i32) {
        self.bytes(&value.to_le_bytes());
    }

    pub fn u64_le(&mut self, value: u64) {
        self.bytes(&value.to_le_bytes());
    }

    pub fn i64_le(&mut self, value: i64) {
        self.bytes(&value.to_le_bytes());
    }

    pub fn u32_be(&mut self, value: u32) {
        self.bytes(&value.to_be_bytes());
    }

    /// Zero bytes up to the next multiple of `align`.
    pub fn pad(&mut self, align: usize) {
        while !self.buf.len().is_multiple_of(align) {
            self.buf.push(0);
        }
    }

    pub fn patch_u32_le(&mut self, offset: usize, value: u32) {
        self.buf[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
    }

    pub fn patch_u64_le(&mut self, offset: usize, value: u64) {
        self.buf[offset..offset + 8].copy_from_slice(&value.to_le_bytes());
    }

    pub fn patch_u32_be(&mut self, offset: usize, value: u32) {
        self.buf[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }

    pub fn into_inner(self) -> Vec<u8> {
        self.buf
    }
}
