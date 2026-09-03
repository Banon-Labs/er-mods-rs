//! Bump allocator over the `0xCC` padding runs in the de-Arxan'd `.text`.
//!
//! Both fixups need a few dozen bytes of executable space *inside the region ersc scans* --
//! a `VirtualAlloc` block would be outside the game module and invisible to its scan. The
//! padding between functions is executable, mapped, and never executed, so it is where the
//! decoy and the relocated function body go. Runs are consumed left to right and never
//! reused, so two fixups cannot be handed overlapping space.

use er_game_base::mem;

/// Byte an unused code cave is filled with in the de-Arxan'd image.
pub(crate) const CAVE_FILL: u8 = 0xCC;
/// Bytes read per scan pass.
const SCAN_CHUNK: usize = 1 << 20;
/// Bytes of padding left untouched at each end of a run, so nothing written here can abut the
/// real code on either side of it.
const CAVE_MARGIN: usize = 1;

pub(crate) struct CaveAllocator {
    text_start: usize,
    text_len: usize,
    cursor: usize,
}

impl CaveAllocator {
    pub(crate) fn new(text_start: usize, text_len: usize) -> Self {
        Self {
            text_start,
            text_len,
            cursor: 0,
        }
    }

    /// Address of `len` writable, never-executed bytes, or `None` when no run is left.
    pub(crate) fn alloc(&mut self, len: usize) -> Option<usize> {
        let needed = len + CAVE_MARGIN * 2;
        let mut buf = vec![0u8; SCAN_CHUNK];
        while self.cursor < self.text_len {
            let want = SCAN_CHUNK.min(self.text_len - self.cursor);
            let window = &mut buf[..want];
            if unsafe { mem::read_bytes(self.text_start + self.cursor, window) } {
                let mut i = 0usize;
                while i < want {
                    if window[i] != CAVE_FILL {
                        i += 1;
                        continue;
                    }
                    let run_start = i;
                    while i < want && window[i] == CAVE_FILL {
                        i += 1;
                    }
                    if i - run_start >= needed {
                        let address = self.text_start + self.cursor + run_start + CAVE_MARGIN;
                        // Resume after this run so the next allocation cannot overlap it.
                        self.cursor += i;
                        return Some(address);
                    }
                }
            }
            self.cursor += want;
        }
        None
    }
}

/// Write `bytes` over `address` after confirming it currently holds `expected`, then read the
/// result back. A successful `VirtualProtect` is not proof the bytes landed -- another mod can
/// own the same address -- so the readback is what the caller is told about.
#[cfg(windows)]
pub(crate) fn write_verified(
    address: usize,
    bytes: &[u8],
    expected: Option<&[u8]>,
) -> Result<(), String> {
    if let Some(expected) = expected {
        let mut before = vec![0u8; expected.len()];
        if !unsafe { mem::read_bytes(address, &mut before) } {
            return Err(format!("0x{address:x} is unreadable"));
        }
        if before != expected {
            return Err(format!(
                "0x{address:x} holds {before:02x?}, expected {expected:02x?}"
            ));
        }
    }
    for (i, byte) in bytes.iter().enumerate() {
        if !unsafe { er_hook::write_code_byte(address + i, *byte) } {
            return Err(format!(
                "VirtualProtect refused the write at 0x{:x}",
                address + i
            ));
        }
    }
    let mut after = vec![0u8; bytes.len()];
    if !unsafe { mem::read_bytes(address, &mut after) } {
        return Err(format!("0x{address:x} unreadable after write"));
    }
    if after != bytes {
        return Err(format!(
            "readback mismatch at 0x{address:x}: wrote {bytes:02x?}, found {after:02x?}"
        ));
    }
    Ok(())
}
