//! The parameter block every port variant and layer reads at `b0, space42` (design 5.2).
//!
//! # The ring
//!
//! One upload buffer of [`RING_BLOCKS`] blocks of [`BLOCK_BYTES`], mapped for the life of the
//! process. Once per present, [`publish`] writes the whole of the next block from the staged
//! values and then stores that block's GPU address with `Release`; the `SetGraphicsRootSignature`
//! hook binds whichever address is current. A block is therefore rewritten [`RING_BLOCKS`]
//! presents after it was published, and a frame never sees a block change under it.
//!
//! Every block is written in full because a root CBV has no bounds check: a shader may read any of
//! the 1024 registers `shaders/editor_abi.hlsli` declares, and each must hold this present's value.
//!
//! # Layout
//!
//! [`reg`] names the registers. The layout is this DLL's and stays the same across recompiles, so
//! an old variant and its replacement read the same bytes. The cel knobs come first, in exactly
//! the order of [`crate::settings::Look::gpu_block`], which is where the ports' `cel_*` names in
//! `shaders/editor_abi.hlsli` read them.
//!
//! # The present count
//!
//! [`publish`] counts presents, and that count is the frame it writes into `er_p[3].y`, the frame
//! the probe headers carry, and the readback ring's slot index. It starts at 1, so a probe header
//! still holding 0 was never written.
//!
//! The block and its layout are pure and tested on the host. The Windows half owns the buffer.

use crate::settings::GPU_FLOATS;

/// Registers in one block: `float4 er_p[1024]`.
pub const REGISTERS: usize = 1024;

/// Bytes in one block. A multiple of the 256-byte constant buffer alignment, so every block's
/// address is a valid root CBV.
pub const BLOCK_BYTES: usize = REGISTERS * 16;

/// Blocks in the ring.
pub const RING_BLOCKS: usize = 8;

/// Bytes in the ring buffer.
pub const RING_BYTES: usize = RING_BLOCKS * BLOCK_BYTES;

const _: () = assert!(BLOCK_BYTES.is_multiple_of(256));

/// Register indexes into `er_p`, as `shaders/editor_abi.hlsli` declares them.
pub mod reg {
    /// The cel knobs the AO port reads, `Look::gpu_block`. Register 2 is unused since the other
    /// four knobs became layers.
    pub const CEL: usize = 0;
    pub const CEL_REGISTERS: usize = 2;
    /// `(time_s, asfloat(frame), display_w, display_h)`.
    pub const FRAME: usize = 3;
    /// `asfloat` of `(mask_pitch, mask_rows, reverse_z, flags)` (P2).
    pub const MASK: usize = 4;
    /// Selector configuration (P2, P3).
    pub const SELECTOR: usize = 5;
    pub const SELECTOR_REGISTERS: usize = 27;
    /// `(previous time_s, asfloat(previous frame), asfloat(vertex proof mode), 0)`: what a velocity
    /// pass gives its previous position the vertex layers at, and the proof switch
    /// (`docs/plans/er-shader-editor-vertex.md`, section 7.7).
    pub const PREVIOUS: usize = 32;
    /// Capsule anchors, two registers each (P4).
    pub const CAPSULES: usize = 64;
    pub const CAPSULE_REGISTERS: usize = 128;
    /// One register per layer slot: the bounds of the slot's stage-local mask (P0.2a,
    /// `crate::editor::mask`).
    pub const LAYER_MASKS: usize = 192;
    /// Layer slots: a header register `(on, opacity, blend, mask word)` and up to 15 parameters
    /// (P0.2a).
    pub const LAYERS: usize = 256;
    pub const LAYER_SLOTS: usize = 48;
    pub const LAYER_REGISTERS: usize = 16;
}

const _: () = assert!(reg::CEL_REGISTERS * 4 == GPU_FLOATS);
const _: () = assert!(reg::CEL + reg::CEL_REGISTERS <= reg::FRAME);
const _: () = assert!(reg::SELECTOR + reg::SELECTOR_REGISTERS == reg::PREVIOUS);
const _: () = assert!(reg::PREVIOUS < reg::CAPSULES);
const _: () = assert!(reg::CAPSULES + reg::CAPSULE_REGISTERS == reg::LAYER_MASKS);
const _: () = assert!(reg::LAYER_MASKS + reg::LAYER_SLOTS <= reg::LAYERS);
const _: () = assert!(reg::LAYERS + reg::LAYER_SLOTS * reg::LAYER_REGISTERS == REGISTERS);

/// What `er_p[3]` says about this present.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FrameInfo {
    /// Seconds since the first publish.
    pub time_s: f32,
    /// The present count, truncated to the 32 bits the shader reads.
    pub frame: u32,
    /// The display size in pixels.
    pub display: [f32; 2],
}

/// One parameter block's contents, as 32-bit words: every register's x, y, z and w.
#[derive(Clone, PartialEq, Eq)]
pub struct Block {
    registers: Box<[[u32; 4]; REGISTERS]>,
}

impl Default for Block {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Debug for Block {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Block")
            .field("cel", &&self.registers[reg::CEL..reg::FRAME])
            .field("frame", &self.registers[reg::FRAME])
            .finish_non_exhaustive()
    }
}

impl Block {
    /// Every register zero.
    #[must_use]
    pub fn new() -> Self {
        Self {
            registers: Box::new([[0; 4]; REGISTERS]),
        }
    }

    /// One register's four words.
    #[must_use]
    pub fn register(&self, index: usize) -> Option<[u32; 4]> {
        self.registers.get(index).copied()
    }

    /// Set one register's four words. An index past the block is refused with `false`.
    pub fn set_register(&mut self, index: usize, words: [u32; 4]) -> bool {
        match self.registers.get_mut(index) {
            Some(register) => {
                *register = words;
                true
            }
            None => false,
        }
    }

    /// The cel knobs, `Look::gpu_block`, into `er_p[0..1]`.
    pub fn set_look(&mut self, block: &[f32; GPU_FLOATS]) {
        let (registers, _) = block.as_chunks::<4>();
        for (at, register) in registers.iter().enumerate() {
            self.registers[reg::CEL + at] = register.map(f32::to_bits);
        }
    }

    /// The present's time, frame and display size into `er_p[3]`, and what `er_p[3]` held before
    /// -- the previous present's time and frame -- into `er_p[32]` with the vertex proof mode.
    pub fn set_frame(&mut self, info: FrameInfo, vertex_proof: u32) {
        let [time, frame, _, _] = self.registers[reg::FRAME];
        self.registers[reg::PREVIOUS] = [time, frame, vertex_proof, 0];
        self.registers[reg::FRAME] = [
            info.time_s.to_bits(),
            info.frame,
            info.display[0].to_bits(),
            info.display[1].to_bits(),
        ];
    }

    /// The block as the GPU reads it: little-endian words, register after register.
    #[must_use]
    pub fn to_le_bytes(&self) -> Vec<u8> {
        self.registers
            .iter()
            .flatten()
            .flat_map(|word| word.to_le_bytes())
            .collect()
    }

    /// The words, register after register, for a copy into mapped memory.
    #[must_use]
    pub fn words(&self) -> &[[u32; 4]; REGISTERS] {
        &self.registers
    }
}

/// The ring slot published after `slot`.
#[must_use]
pub fn next_slot(slot: usize) -> usize {
    (slot + 1) % RING_BLOCKS
}

/// The ABI header the layout tests compare against.
#[cfg(test)]
pub(crate) const ABI_HEADER: &str = include_str!("../shaders/editor_abi.hlsli");

/// `#define NAME value` lines of the ABI header whose value is a number.
#[cfg(test)]
pub(crate) fn abi_header_defines() -> std::collections::BTreeMap<String, usize> {
    ABI_HEADER
        .lines()
        .filter_map(|line| line.trim().strip_prefix("#define "))
        .filter_map(|rest| {
            let mut words = rest.split_whitespace();
            let name = words.next()?;
            let value = words.next()?;
            let value = match value.strip_prefix("0x") {
                Some(hex) => usize::from_str_radix(hex, 16).ok()?,
                None => value.parse().ok()?,
            };
            Some((name.to_owned(), value))
        })
        .collect()
}

/// The `register(...)` clause of the ABI header's declaration of `name`.
#[cfg(test)]
pub(crate) fn abi_header_register(name: &str) -> Option<String> {
    ABI_HEADER
        .lines()
        .filter(|line| !line.trim_start().starts_with("//") && line.contains("register("))
        .find(|line| {
            line.split(|c: char| !c.is_alphanumeric() && c != '_')
                .any(|word| word == name)
        })
        .and_then(|line| {
            let start = line.find("register(")? + "register(".len();
            let end = start + line[start..].find(')')?;
            Some(line[start..end].to_owned())
        })
}

/// Presents whose published time [`time_of`] can still answer for.
pub const TIMES_KEPT: usize = 256;

#[cfg(windows)]
pub use process::{
    create, current_address, current_block, presents, publish, publish_then, set_look,
    set_vertex_proof, stage, time_of, vertex_proof,
};

/// The ring buffer, the staged block, and the present count.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::sync::atomic::{AtomicU32, AtomicU64, AtomicUsize, Ordering};
    use std::sync::{LazyLock, Mutex, MutexGuard, PoisonError};
    use std::time::Instant;

    use windows::Win32::Graphics::Direct3D12::{
        D3D12_HEAP_TYPE_UPLOAD, D3D12_RANGE, D3D12_RESOURCE_FLAG_NONE,
        D3D12_RESOURCE_STATE_GENERIC_READ, ID3D12Device,
    };
    use windows::core::Interface;

    use super::{BLOCK_BYTES, Block, FrameInfo, RING_BLOCKS, RING_BYTES, TIMES_KEPT, next_slot};
    use crate::log;
    use crate::look_binding::committed_buffer;
    use crate::settings::GPU_FLOATS;

    /// What the next publish writes, and the ring slot last published.
    struct Staged {
        block: Block,
        slot: usize,
    }

    static STAGED: LazyLock<Mutex<Staged>> = LazyLock::new(|| {
        Mutex::new(Staged {
            block: Block::new(),
            slot: 0,
        })
    });

    /// CPU address of block 0, or 0 before [`create`].
    static RING_MAPPED: AtomicUsize = AtomicUsize::new(0);
    /// GPU address of block 0.
    static RING_GPU: AtomicU64 = AtomicU64::new(0);
    /// GPU address of the block last published: what the root-signature hook binds.
    static CURRENT: AtomicU64 = AtomicU64::new(0);
    /// Presents counted by [`publish`].
    static PRESENTS: AtomicU64 = AtomicU64::new(0);
    /// The present each ring slot was last published at, written before the slot is made
    /// current, so a reader that found a slot current finds its present here.
    static SLOT_PRESENT: [AtomicU64; RING_BLOCKS] = [const { AtomicU64::new(0) }; RING_BLOCKS];
    /// The time each recent present published, as `f32` bits, by `present % TIMES_KEPT`, with the
    /// present beside it so a slot written by a later one is told apart.
    static TIMES: [(AtomicU64, AtomicU32); TIMES_KEPT] =
        [const { (AtomicU64::new(0), AtomicU32::new(0)) }; TIMES_KEPT];
    /// The vertex proof mode the next block carries in `er_p[32].z`.
    static VERTEX_PROOF: AtomicU32 = AtomicU32::new(0);

    static START: LazyLock<Instant> = LazyLock::new(Instant::now);

    fn staged() -> MutexGuard<'static, Staged> {
        // Every write under it leaves the block whole, so a panic mid-write cannot tear it.
        STAGED.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Create the ring on `device`, fill every block with what is staged, and make block 0
    /// current. Called once, before the first root signature that binds it is recorded.
    ///
    /// # Safety
    ///
    /// `device` must be the live device the ring's root signatures belong to.
    pub unsafe fn create(device: &ID3D12Device) -> Result<(), String> {
        let resource = unsafe {
            committed_buffer(
                device,
                D3D12_HEAP_TYPE_UPLOAD,
                RING_BYTES as u64,
                D3D12_RESOURCE_FLAG_NONE,
                D3D12_RESOURCE_STATE_GENERIC_READ,
            )
        }
        .map_err(|error| format!("parameter ring: {error}"))?;
        let mut mapped: *mut c_void = std::ptr::null_mut();
        // An empty read range: the CPU never reads the ring back.
        let no_read = D3D12_RANGE { Begin: 0, End: 0 };
        unsafe { resource.Map(0, Some(&no_read), Some(&mut mapped)) }
            .map_err(|error| format!("parameter ring: Map failed: {error}"))?;
        if mapped.is_null() {
            return Err("parameter ring: Map returned no address".to_owned());
        }
        let gpu = unsafe { resource.GetGPUVirtualAddress() };
        {
            let staged = staged();
            for slot in 0..RING_BLOCKS {
                // SAFETY: `mapped` is the start of the ring, `RING_BLOCKS` blocks long.
                unsafe { write_block(mapped as usize + slot * BLOCK_BYTES, &staged.block) };
            }
            RING_MAPPED.store(mapped as usize, Ordering::Release);
            RING_GPU.store(gpu, Ordering::Release);
            CURRENT.store(gpu + (staged.slot * BLOCK_BYTES) as u64, Ordering::Release);
        }
        // Kept for the life of the process: its addresses are bound into command lists the GPU
        // may still be reading.
        let _ = resource.into_raw();
        log(format_args!(
            "parameter ring: gpu 0x{gpu:x}, {RING_BLOCKS} x {BLOCK_BYTES} bytes, mapped"
        ));
        Ok(())
    }

    /// Copy one block into mapped upload memory.
    ///
    /// # Safety
    ///
    /// `at` must be the CPU address of a mapped block of [`BLOCK_BYTES`].
    unsafe fn write_block(at: usize, block: &Block) {
        // One sequential copy, which is what write-combined upload memory wants.
        unsafe {
            std::ptr::copy_nonoverlapping(
                block.words().as_ptr().cast::<u8>(),
                at as *mut u8,
                BLOCK_BYTES,
            );
        }
    }

    /// Stage the cel knobs, `Look::gpu_block`; the next present's block carries them.
    pub fn set_look(look: [f32; GPU_FLOATS]) {
        staged().block.set_look(&look);
    }

    /// Stage any registers. The seam for what later phases publish: mask configuration, selector
    /// configuration, capsules and layer slots. `edit` runs under the staged block's lock, which
    /// [`publish_then`] holds from the block's publish through its `after`.
    pub fn stage(edit: impl FnOnce(&mut Block)) {
        edit(&mut staged().block);
    }

    /// Once per present: count it, write the next block in full from what is staged, and make that
    /// block current. Returns the present count, starting at 1.
    pub fn publish(display: [f32; 2]) -> u64 {
        publish_then(display, || {})
    }

    /// [`publish`], then `after`, both under the staged block's lock, so nothing staged in a
    /// [`stage`] call can land between the two. The editor's worker stages a new layer packing
    /// and hands the table swap to its variants over in one [`stage`] call
    /// (`crate::table::defer`), and the present applies that swap here, with the block that
    /// carries the packing: the redirect and the parameters change on the same present.
    pub fn publish_then(display: [f32; 2], after: impl FnOnce()) -> u64 {
        let present = PRESENTS.fetch_add(1, Ordering::AcqRel) + 1;
        let time_s = START.elapsed().as_secs_f32();
        let (stamp, time) = &TIMES[(present % TIMES_KEPT as u64) as usize];
        time.store(time_s.to_bits(), Ordering::Relaxed);
        stamp.store(present, Ordering::Release);
        let mut staged = staged();
        staged.block.set_frame(
            FrameInfo {
                time_s,
                frame: present as u32,
                display,
            },
            VERTEX_PROOF.load(Ordering::Relaxed),
        );
        let mapped = RING_MAPPED.load(Ordering::Acquire);
        if mapped != 0 {
            let slot = next_slot(staged.slot);
            // SAFETY: the ring is mapped for the life of the process and `slot` is inside it.
            unsafe { write_block(mapped + slot * BLOCK_BYTES, &staged.block) };
            staged.slot = slot;
            SLOT_PRESENT[slot].store(present, Ordering::Release);
            let gpu = RING_GPU.load(Ordering::Acquire) + (slot * BLOCK_BYTES) as u64;
            CURRENT.store(gpu, Ordering::Release);
        }
        after();
        present
    }

    /// GPU address of the block to bind now, or 0 before the ring exists.
    #[must_use]
    pub fn current_address() -> u64 {
        CURRENT.load(Ordering::Acquire)
    }

    /// The block to bind now and the present it was published at: `(0, 0)` before the ring
    /// exists. The command-list hooks note the present of every block a recording binds, which is
    /// how a frame whose lists read different blocks is counted (gate V0.R1).
    #[must_use]
    pub fn current_block() -> (u64, u64) {
        let address = CURRENT.load(Ordering::Acquire);
        let base = RING_GPU.load(Ordering::Acquire);
        if address == 0 || address < base {
            return (address, 0);
        }
        let slot = ((address - base) / BLOCK_BYTES as u64) as usize;
        let present = SLOT_PRESENT
            .get(slot)
            .map_or(0, |stamp| stamp.load(Ordering::Acquire));
        (address, present)
    }

    /// The time present `present` published in `er_p[3].x`, while it is among the last
    /// [`TIMES_KEPT`].
    #[must_use]
    pub fn time_of(present: u64) -> Option<f32> {
        let (stamp, time) = &TIMES[(present % TIMES_KEPT as u64) as usize];
        let bits = time.load(Ordering::Relaxed);
        (stamp.load(Ordering::Acquire) == present && present != 0).then(|| f32::from_bits(bits))
    }

    /// Set the vertex proof mode the next block carries: 0 off, any other value on.
    pub fn set_vertex_proof(mode: u32) -> u32 {
        VERTEX_PROOF.swap(mode, Ordering::AcqRel)
    }

    /// The vertex proof mode the next block carries.
    #[must_use]
    pub fn vertex_proof() -> u32 {
        VERTEX_PROOF.load(Ordering::Acquire)
    }

    /// Presents counted so far: the frame the last block carries.
    #[must_use]
    pub fn presents() -> u64 {
        PRESENTS.load(Ordering::Acquire)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::{Look, builtin_presets};

    #[test]
    fn the_register_layout_matches_the_abi_header() {
        let declared = abi_header_defines();
        let expected = [
            ("ER_P_REGISTERS", REGISTERS),
            ("ER_P_CEL", reg::CEL),
            ("ER_P_CEL_REGISTERS", reg::CEL_REGISTERS),
            ("ER_P_FRAME", reg::FRAME),
            ("ER_P_MASK", reg::MASK),
            ("ER_P_SELECTOR", reg::SELECTOR),
            ("ER_P_SELECTOR_REGISTERS", reg::SELECTOR_REGISTERS),
            ("ER_P_PREVIOUS", reg::PREVIOUS),
            ("ER_P_CAPSULES", reg::CAPSULES),
            ("ER_P_CAPSULE_REGISTERS", reg::CAPSULE_REGISTERS),
            ("ER_P_LAYER_MASKS", reg::LAYER_MASKS),
            ("ER_P_LAYERS", reg::LAYERS),
            ("ER_P_LAYER_SLOTS", reg::LAYER_SLOTS),
            ("ER_P_LAYER_REGISTERS", reg::LAYER_REGISTERS),
        ];
        for (name, value) in expected {
            assert_eq!(declared.get(name), Some(&value), "{name}");
        }
        // Every `ER_P_` the header declares is one of the above: a register added on one side only
        // would otherwise go unnoticed.
        let known: Vec<&str> = expected.iter().map(|(name, _)| *name).collect();
        for name in declared.keys().filter(|name| name.starts_with("ER_P_")) {
            assert!(known.contains(&name.as_str()), "{name} has no Rust twin");
        }
    }

    #[test]
    fn the_cbuffer_is_one_block_at_b0_space42() {
        assert_eq!(
            abi_header_register("EditorParams").as_deref(),
            Some("b0, space42")
        );
        assert!(
            ABI_HEADER
                .lines()
                .any(|line| line.trim() == format!("float4 er_p[{REGISTERS}];")),
            "er_p is not declared with {REGISTERS} registers"
        );
        assert_eq!(BLOCK_BYTES, 16 * 1024);
        assert_eq!(RING_BYTES, 128 * 1024);
    }

    #[test]
    fn the_cel_knobs_sit_where_the_existing_ports_read_them() {
        let look = builtin_presets()[0].look;
        let gpu = look.gpu_block(true);
        let mut block = Block::new();
        block.set_look(&gpu);
        let bytes = block.to_le_bytes();
        assert_eq!(bytes.len(), BLOCK_BYTES);
        // The `cel_*` names read the five floats from byte 0, four to a register.
        for (index, value) in look.clamped().0.iter().enumerate() {
            let at = reg::CEL * 16 + index * 4;
            let word = u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]]);
            assert_eq!(f32::from_bits(word), *value, "knob {index}");
        }
        // Padding after the knobs, the register the moved knobs left, and everything after the
        // cel registers, stay zero.
        assert_eq!(block.register(1), Some([gpu[4].to_bits(), 0, 0, 0]));
        assert_eq!(block.register(2), Some([0; 4]));
        assert_eq!(block.register(reg::FRAME), Some([0; 4]));
        let vanilla = Look::vanilla().gpu_block(true);
        block.set_look(&vanilla);
        assert_eq!(block.register(0).map(|w| f32::from_bits(w[0])), Some(0.0));
    }

    #[test]
    fn the_frame_register_carries_time_frame_and_display() {
        let mut block = Block::new();
        block.set_frame(
            FrameInfo {
                time_s: 12.5,
                frame: 4_000_000_007,
                display: [2560.0, 1440.0],
            },
            0,
        );
        assert_eq!(
            block.register(reg::FRAME),
            Some([
                12.5_f32.to_bits(),
                4_000_000_007,
                2560.0_f32.to_bits(),
                1440.0_f32.to_bits()
            ])
        );
    }

    #[test]
    fn the_previous_register_holds_the_present_before_and_the_proof_mode() {
        let mut block = Block::new();
        let at = |time_s: f32, frame: u32| FrameInfo {
            time_s,
            frame,
            display: [1920.0, 1080.0],
        };
        block.set_frame(at(1.0, 60), 0);
        block.set_frame(at(1.0166, 61), 1);
        // er_p[32] is what er_p[3] held: the time and frame the previous present published.
        assert_eq!(
            block.register(reg::PREVIOUS),
            Some([1.0_f32.to_bits(), 60, 1, 0])
        );
        assert_eq!(
            block
                .register(reg::FRAME)
                .map(|r| (f32::from_bits(r[0]), r[1])),
            Some((1.0166, 61))
        );
        block.set_frame(at(1.0333, 62), 0);
        assert_eq!(
            block.register(reg::PREVIOUS),
            Some([1.0166_f32.to_bits(), 61, 0, 0])
        );
        // Nothing else moved.
        assert_eq!(block.register(reg::SELECTOR), Some([0; 4]));
        assert_eq!(block.register(reg::CAPSULES), Some([0; 4]));
    }

    #[test]
    fn registers_outside_the_block_are_refused() {
        let mut block = Block::new();
        assert!(block.set_register(REGISTERS - 1, [1, 2, 3, 4]));
        assert!(!block.set_register(REGISTERS, [1, 2, 3, 4]));
        assert_eq!(block.register(REGISTERS), None);
        assert_eq!(block.register(REGISTERS - 1), Some([1, 2, 3, 4]));
    }

    #[test]
    fn the_ring_reuses_a_slot_after_every_other_one() {
        let mut slot = 0;
        let mut seen = vec![slot];
        for _ in 1..RING_BLOCKS {
            slot = next_slot(slot);
            seen.push(slot);
        }
        seen.sort_unstable();
        assert_eq!(seen, (0..RING_BLOCKS).collect::<Vec<_>>());
        assert_eq!(next_slot(slot), 0);
    }
}
