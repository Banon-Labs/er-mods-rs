//! Reading single files out of Elden Ring's `Data*.bhd` / `Data*.bdt` archives.
//!
//! A `.bhd` is the table of contents and is RSA-encrypted as a whole: 256-byte blocks, each read
//! with the archive's public key into 255 bytes of plain text. The plain text is a `BHD5`
//! header, hash buckets, and one record per file -- the 64-bit hash of its path, where its
//! bytes sit in the `.bdt`, and the AES-128 key and byte ranges that are encrypted in place.
//! Paths themselves are not stored; a file is found by hashing the path the caller asks for.
//!
//! Only the handful of files the builder asks for are ever read out of the `.bdt` -- each one a
//! seek and a read -- so opening the archives costs the header decryption and nothing more.
//!
//! Dark Souls II: Scholar of the First Sin packs its files the same way, in `GameDataEbl` and the
//! `Lq*Ebl` archives beside `DarkSoulsII.exe`, each with its public key shipped beside it as
//! `<name>KeyCode.pem`. Its records are narrower: a 32-bit path hash (multiplier 37), the padded
//! size, the offset, and the hash and key offsets, with no unpadded size.

use std::{
    collections::HashMap,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

use aes::{
    Aes128,
    cipher::{BlockDecrypt, KeyInit, generic_array::GenericArray},
};
use num_bigint::BigUint;

use crate::{
    bytes::{i32_le, i64_le, u8_at, u32_le, u64_le},
    error::{Context, Error, Result},
    keys::{ARCHIVE_KEYS, RsaPublicKey},
};

const BHD5_MAGIC: &[u8; 4] = b"BHD5";
const LITTLE_ENDIAN_MARKER: u8 = 0xFF;
const BUCKET_RECORD_SIZE: usize = 8;
const AES_BLOCK: usize = 16;
const PATH_HASH_PRIME: u64 = 0x85;
const DS2_PATH_HASH_PRIME: u32 = 37;
/// The archive every Dark Souls II install has; the `Lq*Ebl` ones are read when present.
const DS2_REQUIRED_ARCHIVE: &str = "GameDataEbl";
const DS2_ARCHIVE_SUFFIX: &str = "Ebl";
const DS2_KEY_SUFFIX: &str = "KeyCode.pem";

/// Which game's `BHD5` records an archive holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Layout {
    EldenRing,
    DarkSouls2,
}

impl Layout {
    fn hash(self, logical_path: &str) -> u64 {
        match self {
            Self::EldenRing => path_hash(logical_path),
            Self::DarkSouls2 => u64::from(ds2_path_hash(logical_path)),
        }
    }

    fn record_size(self) -> usize {
        match self {
            Self::EldenRing => 0x28,
            Self::DarkSouls2 => 0x20,
        }
    }

    /// `(hash, padded size, unpadded size, offset, AES key offset)` of the record at `at`; a
    /// Dark Souls II record has no unpadded size and reads as 0.
    fn record(self, plain: &[u8], at: usize) -> Result<(u64, u32, u32, u64, u64)> {
        Ok(match self {
            Self::EldenRing => (
                u64_le(plain, at)?,
                u32_le(plain, at + 0x08)?,
                u32_le(plain, at + 0x0C)?,
                u64_le(plain, at + 0x10)?,
                u64_le(plain, at + 0x20)?,
            ),
            Self::DarkSouls2 => (
                u64::from(u32_le(plain, at)?),
                u32_le(plain, at + 0x04)?,
                0,
                u64_le(plain, at + 0x08)?,
                u64_le(plain, at + 0x18)?,
            ),
        })
    }
}

#[derive(Clone, Debug)]
struct Entry {
    offset: u64,
    padded_size: u32,
    size: u32,
    aes_key: [u8; 16],
    encrypted_ranges: Vec<(u64, u64)>,
}

struct Archive {
    bdt: PathBuf,
    entries: HashMap<u64, Entry>,
}

/// Every archive of one install, searchable by logical path.
pub struct ArchiveSet {
    archives: Vec<Archive>,
    layout: Layout,
}

impl ArchiveSet {
    /// Open the archives beside `eldenring.exe`. The four base archives must all be present;
    /// the DLC archive is read when it is there.
    pub fn open(game_dir: &Path) -> Result<Self> {
        let mut archives = Vec::new();
        for key in ARCHIVE_KEYS {
            let bhd = game_dir.join(format!("{}.bhd", key.name));
            let bdt = game_dir.join(format!("{}.bdt", key.name));
            if !bhd.is_file() || !bdt.is_file() {
                if key.required {
                    return Err(Error::new(format!(
                        "{} is missing from {} -- is this the Elden Ring `Game` folder?",
                        bhd.display(),
                        game_dir.display()
                    )));
                }
                continue;
            }
            let encrypted = std::fs::read(&bhd).context(format!("reading {}", bhd.display()))?;
            let plain = rsa_decrypt(&encrypted, &key.decode()?)
                .context(format!("decrypting {}", bhd.display()))?;
            let entries = parse_bhd5(&plain, Layout::EldenRing)
                .context(format!("parsing {}", bhd.display()))?;
            archives.push(Archive { bdt, entries });
        }
        Ok(Self {
            archives,
            layout: Layout::EldenRing,
        })
    }

    /// Open the archives beside `DarkSoulsII.exe`: every `<name>Ebl.bhd` with its `.bdt` and the
    /// `<name>KeyCode.pem` the game ships to read it. `GameDataEbl` must be one of them.
    pub fn open_ds2(game_dir: &Path) -> Result<Self> {
        let mut stems: Vec<String> = std::fs::read_dir(game_dir)
            .context(game_dir.display())?
            .filter_map(|entry| {
                let name = entry.ok()?.file_name().into_string().ok()?;
                let stem = name.strip_suffix(".bhd")?;
                stem.ends_with(DS2_ARCHIVE_SUFFIX).then(|| stem.to_owned())
            })
            .collect();
        stems.sort();
        if !stems.iter().any(|stem| stem == DS2_REQUIRED_ARCHIVE) {
            return Err(Error::new(format!(
                "{DS2_REQUIRED_ARCHIVE}.bhd is missing from {} -- is this the Dark Souls II \
                 `Game` folder?",
                game_dir.display()
            )));
        }
        let mut archives = Vec::new();
        for stem in stems {
            let bhd = game_dir.join(format!("{stem}.bhd"));
            let bdt = game_dir.join(format!("{stem}.bdt"));
            let key_name = stem.strip_suffix(DS2_ARCHIVE_SUFFIX).unwrap_or(&stem);
            let pem = game_dir.join(format!("{key_name}{DS2_KEY_SUFFIX}"));
            if !bdt.is_file() || !pem.is_file() {
                continue;
            }
            let key_text = std::fs::read_to_string(&pem).context(pem.display())?;
            let key = RsaPublicKey::from_pem(&key_text).context(pem.display())?;
            let encrypted = std::fs::read(&bhd).context(format!("reading {}", bhd.display()))?;
            let plain =
                rsa_decrypt(&encrypted, &key).context(format!("decrypting {}", bhd.display()))?;
            let entries = parse_bhd5(&plain, Layout::DarkSouls2)
                .context(format!("parsing {}", bhd.display()))?;
            archives.push(Archive { bdt, entries });
        }
        Ok(Self {
            archives,
            layout: Layout::DarkSouls2,
        })
    }

    pub fn contains(&self, logical_path: &str) -> bool {
        let hash = self.layout.hash(logical_path);
        self.archives
            .iter()
            .any(|archive| archive.entries.contains_key(&hash))
    }

    /// The file at `logical_path` (`/parts/fc_m_0000.partsbnd.dcx`), decrypted but still in
    /// whatever container it was stored in -- for parts, a DCX.
    pub fn read(&self, logical_path: &str) -> Result<Vec<u8>> {
        let hash = self.layout.hash(logical_path);
        let (archive, entry) = self
            .archives
            .iter()
            .find_map(|archive| archive.entries.get(&hash).map(|entry| (archive, entry)))
            .ok_or_else(|| Error::new(format!("{logical_path} is not in the game's archives")))?;
        read_entry(&archive.bdt, entry).context(logical_path)
    }

    /// Every hash in every archive, for scans that do not know file names.
    pub fn hashes(&self) -> impl Iterator<Item = u64> + '_ {
        self.archives
            .iter()
            .flat_map(|archive| archive.entries.keys().copied())
    }

    /// Where a file sits and which of its byte ranges are encrypted, for diagnostics.
    pub fn describe(&self, logical_path: &str) -> Option<String> {
        let hash = self.layout.hash(logical_path);
        self.archives.iter().find_map(|archive| {
            archive.entries.get(&hash).map(|entry| {
                format!(
                    "{} offset=0x{:X} padded=0x{:X} size=0x{:X} ranges={:X?}",
                    archive.bdt.display(),
                    entry.offset,
                    entry.padded_size,
                    entry.size,
                    entry.encrypted_ranges
                )
            })
        })
    }

    pub fn read_hash(&self, hash: u64) -> Result<Vec<u8>> {
        let (archive, entry) = self
            .archives
            .iter()
            .find_map(|archive| archive.entries.get(&hash).map(|entry| (archive, entry)))
            .ok_or_else(|| Error::new(format!("hash 0x{hash:016X} is not in the archives")))?;
        read_entry(&archive.bdt, entry)
    }
}

/// A logical path as both games hash it: lower-cased, forward slashes, rooted at `/`.
fn normalized_path(logical_path: &str) -> String {
    let mut normalized = logical_path.trim().replace('\\', "/").to_lowercase();
    if !normalized.starts_with('/') {
        normalized.insert(0, '/');
    }
    normalized
}

/// The Elden Ring archive hash of a logical path.
pub fn path_hash(logical_path: &str) -> u64 {
    normalized_path(logical_path)
        .bytes()
        .fold(0_u64, |hash, byte| {
            hash.wrapping_mul(PATH_HASH_PRIME)
                .wrapping_add(u64::from(byte))
        })
}

/// The Dark Souls II archive hash of a logical path.
pub fn ds2_path_hash(logical_path: &str) -> u32 {
    normalized_path(logical_path)
        .bytes()
        .fold(0_u32, |hash, byte| {
            hash.wrapping_mul(DS2_PATH_HASH_PRIME)
                .wrapping_add(u32::from(byte))
        })
}

/// Raw RSA with the public key, block by block: `m = c^e mod n`, written big-endian into
/// `modulus_bytes - 1` bytes of output.
fn rsa_decrypt(encrypted: &[u8], key: &RsaPublicKey) -> Result<Vec<u8>> {
    let input_size = key.modulus.bits().div_ceil(8) as usize;
    let output_size = ((key.modulus.bits() - 1) / 8) as usize;
    if !encrypted.len().is_multiple_of(input_size) {
        return Err(Error::new(format!(
            "length {} is not a whole number of {input_size}-byte RSA blocks",
            encrypted.len()
        )));
    }
    let block_count = encrypted.len() / input_size;
    let mut plain = vec![0_u8; block_count * output_size];
    // Each block is independent and a 32-bit exponent makes them slow one at a time (12 s for
    // the four base archives on one core, measured), so the blocks are split across threads.
    let threads = std::thread::available_parallelism().map_or(1, usize::from);
    let blocks_per_thread = block_count.div_ceil(threads).max(1);
    std::thread::scope(|scope| {
        let workers: Vec<_> = encrypted
            .chunks(blocks_per_thread * input_size)
            .zip(plain.chunks_mut(blocks_per_thread * output_size))
            .map(|(input, output)| {
                scope.spawn(move || -> Result<()> {
                    for (block, out) in input.chunks(input_size).zip(output.chunks_mut(output_size))
                    {
                        let value =
                            BigUint::from_bytes_be(block).modpow(&key.exponent, &key.modulus);
                        let digits = value.to_bytes_be();
                        if digits.len() > output_size {
                            return Err(Error::new(
                                "RSA block decrypted wider than its output size",
                            ));
                        }
                        out[output_size - digits.len()..].copy_from_slice(&digits);
                    }
                    Ok(())
                })
            })
            .collect();
        workers.into_iter().try_for_each(|worker| {
            worker
                .join()
                .map_err(|_| Error::new("RSA worker thread panicked"))?
        })
    })?;
    Ok(plain)
}

fn parse_bhd5(plain: &[u8], layout: Layout) -> Result<HashMap<u64, Entry>> {
    if plain.get(..4) != Some(BHD5_MAGIC.as_slice()) {
        return Err(Error::new(
            "decrypted header is not BHD5 -- wrong key, or not this game's archive",
        ));
    }
    if u8_at(plain, 4)? != LITTLE_ENDIAN_MARKER {
        return Err(Error::new("big-endian BHD5 is a console archive, not PC"));
    }
    let bucket_count = usize::try_from(i32_le(plain, 0x10)?).context("bucket count")?;
    let buckets_offset = usize::try_from(i32_le(plain, 0x14)?).context("bucket offset")?;

    let mut entries = HashMap::new();
    for bucket in 0..bucket_count {
        let record = buckets_offset + bucket * BUCKET_RECORD_SIZE;
        let file_count = u32_le(plain, record)? as usize;
        let files_offset = u32_le(plain, record + 4)? as usize;
        for index in 0..file_count {
            let (hash, padded_size, size, offset, aes_offset) =
                layout.record(plain, files_offset + index * layout.record_size())?;
            let aes_offset = aes_offset as usize;
            let mut aes_key = [0_u8; 16];
            let mut encrypted_ranges = Vec::new();
            if aes_offset != 0 {
                aes_key.copy_from_slice(crate::bytes::slice(plain, aes_offset, 16)?);
                let range_count = u32_le(plain, aes_offset + 16)? as usize;
                for range in 0..range_count {
                    let at = aes_offset + 20 + range * 16;
                    let (start, end) = (i64_le(plain, at)?, i64_le(plain, at + 8)?);
                    // `(-1, -1)` and empty ranges are placeholders, not encrypted bytes.
                    if start >= 0 && end > start {
                        encrypted_ranges.push((start as u64, end as u64));
                    }
                }
            }
            entries.insert(
                hash,
                Entry {
                    offset,
                    padded_size,
                    size,
                    aes_key,
                    encrypted_ranges,
                },
            );
        }
    }
    Ok(entries)
}

fn read_entry(bdt: &Path, entry: &Entry) -> Result<Vec<u8>> {
    let mut file = File::open(bdt).context(format!("opening {}", bdt.display()))?;
    file.seek(SeekFrom::Start(entry.offset))?;
    let mut data = vec![0_u8; entry.padded_size as usize];
    file.read_exact(&mut data).context(format!(
        "reading 0x{:X} bytes at 0x{:X}",
        entry.padded_size, entry.offset
    ))?;

    let cipher = Aes128::new(&GenericArray::from(entry.aes_key));
    for &(start, end) in &entry.encrypted_ranges {
        let range = data
            .get_mut(start as usize..end as usize)
            .ok_or_else(|| Error::new("encrypted range runs past the file"))?;
        for block in range.as_chunks_mut::<AES_BLOCK>().0 {
            cipher.decrypt_block(GenericArray::from_mut_slice(block));
        }
    }
    // A DCX records no unpadded size; the padded length is the file.
    if entry.size != 0 {
        data.truncate(entry.size as usize);
    }
    Ok(data)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_hash_normalizes_slashes_case_and_the_leading_slash() {
        let want = path_hash("/parts/fc_m_0000.partsbnd.dcx");
        assert_eq!(path_hash("parts\\FC_M_0000.partsbnd.dcx"), want);
        assert_eq!(path_hash("  parts/fc_m_0000.partsbnd.dcx "), want);
    }

    #[test]
    fn path_hash_matches_the_workspace_shader_reader() {
        // Same vector `crates/soulsformats` pins for its own copy of this hash.
        assert_eq!(
            path_hash("shader/GXShader.shaderbnd.dcx"),
            path_hash("/shader/gxshader.shaderbnd.dcx")
        );
    }

    #[test]
    fn rsa_with_exponent_one_is_the_identity_on_a_block() {
        let key = RsaPublicKey {
            modulus: (BigUint::from(1_u8) << 2047) + BigUint::from(1_u8),
            exponent: BigUint::from(1_u8),
        };
        let mut block = vec![0_u8; 256];
        block[255] = 0x42;
        let plain = rsa_decrypt(&block, &key).unwrap();
        assert_eq!(plain.len(), 255);
        assert_eq!(plain[254], 0x42);
    }
}
