//! Row copies: every param row this DLL changes is a copy it owns, never the game's own row.
//!
//! Seamless Co-op's matchmaking `lobby_key` is a fingerprint of the param tables as they sit in
//! memory, compared for equality, so a write to any resident row puts the player in a lobby pool
//! of their own (bd `death-persist-is-the-lobby-key-mover-seamless-201-2720-2026-10-22`). The DLL
//! therefore reads the row a typed getter just found, changes a copy of it, and points the
//! getter's out struct at the copy. The tables Seamless hashes are never touched.
//!
//! # A copy outlives every reader
//!
//! The game is handed a copy as a bare row pointer, and nothing proves no reader keeps one: the
//! builder that asked reads it after the getter returns, and whatever it passes the pointer to may
//! hold it longer. So a copy is never freed and never written after it is handed out. What keeps
//! that from growing without bound is the key: copies are interned by their whole content, so
//! every summon of the same companion gets the same copy back, and the number of copies is the
//! number of distinct rows the config has described this session, not the number of summons.
//! [`MAX_COPIES`] caps even that, refusing a new copy rather than allocating past it.

use std::collections::BTreeMap;

/// The most distinct copies one process makes. Each is a row of a few hundred bytes, and a copy is
/// only made for a row the config describes, so reaching this means something is generating
/// contents it should not; refusing is safer than allocating forever.
pub const MAX_COPIES: usize = 1024;

/// Bytes per allocation unit, and so the alignment of every copy. Param rows sit at least 4-byte
/// aligned in the game's own buffer; 16 covers any vector load a reader might use.
const CHUNK: usize = 16;

/// One aligned unit of a copy's storage.
#[repr(C, align(16))]
#[derive(Clone, Copy)]
struct Chunk([u8; CHUNK]);

/// Why a copy was not made.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopyRefused {
    /// [`MAX_COPIES`] distinct copies exist already.
    Full,
    /// A row of no bytes was offered.
    Empty,
}

impl core::fmt::Display for CopyRefused {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Full => write!(
                f,
                "{MAX_COPIES} row copies exist already, so no new one is made"
            ),
            Self::Empty => write!(f, "an empty row has nothing to copy"),
        }
    }
}

/// Every copy made, by content. Entries are never removed and their storage is never written
/// after it is filled.
#[derive(Default)]
pub struct RowCopies {
    /// Content to the address of the leaked, immutable storage holding it.
    by_content: BTreeMap<Box<[u8]>, usize>,
}

impl RowCopies {
    /// No copies yet. `const` so a `static Mutex<RowCopies>` needs no lazy initialisation.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            by_content: BTreeMap::new(),
        }
    }

    /// The address of a copy holding exactly `content`: the one made earlier for the same bytes,
    /// or a new one. The address is 16-byte aligned and stays valid, unchanged, for the life of
    /// the process.
    ///
    /// # Errors
    ///
    /// [`CopyRefused::Full`] when `content` is new and [`MAX_COPIES`] copies exist already;
    /// [`CopyRefused::Empty`] for an empty row.
    pub fn intern(&mut self, content: &[u8]) -> Result<usize, CopyRefused> {
        if content.is_empty() {
            return Err(CopyRefused::Empty);
        }
        if let Some(&address) = self.by_content.get(content) {
            return Ok(address);
        }
        if self.by_content.len() >= MAX_COPIES {
            return Err(CopyRefused::Full);
        }
        let mut storage = vec![Chunk([0; CHUNK]); content.len().div_ceil(CHUNK)].into_boxed_slice();
        for (chunk, bytes) in storage.iter_mut().zip(content.chunks(CHUNK)) {
            chunk.0[..bytes.len()].copy_from_slice(bytes);
        }
        // Leaked on purpose; see the module docs. Only the address is kept, so no Rust reference
        // to the storage outlives this line.
        let address = Box::leak(storage).as_mut_ptr() as usize;
        self.by_content.insert(content.into(), address);
        Ok(address)
    }

    /// How many distinct copies exist.
    #[must_use]
    pub fn len(&self) -> usize {
        self.by_content.len()
    }

    /// Whether no copy has been made yet.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.by_content.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The bytes at a copy's address.
    fn read(address: usize, len: usize) -> Vec<u8> {
        // SAFETY: `address` came from `intern`, whose storage is leaked and at least `len` long.
        unsafe { std::slice::from_raw_parts(address as *const u8, len) }.to_vec()
    }

    fn row(seed: u8, len: usize) -> Vec<u8> {
        (0..len).map(|i| seed.wrapping_add(i as u8)).collect()
    }

    #[test]
    fn the_same_content_gets_the_same_copy() {
        let mut copies = RowCopies::new();
        let a = copies.intern(&row(1, 0xa0)).expect("first copy");
        let b = copies.intern(&row(1, 0xa0)).expect("same content");
        assert_eq!(a, b);
        assert_eq!(copies.len(), 1);
    }

    #[test]
    fn a_copy_holds_its_content_aligned() {
        let mut copies = RowCopies::new();
        let content = row(7, 0x140);
        let address = copies.intern(&content).expect("copy");
        assert_eq!(address % 16, 0);
        assert_eq!(read(address, content.len()), content);
    }

    #[test]
    fn a_new_content_never_rewrites_an_earlier_copy() {
        let mut copies = RowCopies::new();
        let first = row(1, 0xa0);
        let a = copies.intern(&first).expect("first");
        let mut changed = first.clone();
        changed[0x54] ^= 0xff;
        let b = copies.intern(&changed).expect("second");
        assert_ne!(a, b);
        assert_eq!(read(a, first.len()), first);
        assert_eq!(read(b, changed.len()), changed);
        assert_eq!(copies.len(), 2);
    }

    #[test]
    fn the_cap_refuses_a_new_content_but_still_answers_an_old_one() {
        let mut copies = RowCopies::new();
        let mut first = None;
        for n in 0..MAX_COPIES {
            let address = copies
                .intern(&(n as u32).to_le_bytes())
                .expect("under the cap");
            first.get_or_insert(address);
        }
        assert_eq!(
            copies.intern(&u32::MAX.to_le_bytes()),
            Err(CopyRefused::Full)
        );
        assert_eq!(copies.intern(&0u32.to_le_bytes()), Ok(first.expect("one")));
    }

    #[test]
    fn an_empty_row_is_refused() {
        assert_eq!(RowCopies::new().intern(&[]), Err(CopyRefused::Empty));
    }
}
