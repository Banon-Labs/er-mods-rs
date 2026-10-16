//! The pixel probe (design 5.3): 8 KB that only our port variants write, and its readback ring.
//!
//! # Why it proves anything
//!
//! The game's own passes never touch this buffer; only a variant this DLL compiled does, and each
//! one stamps its region's header with the present count it read from the parameter block. A
//! header that keeps advancing therefore proves a variant is bound, and one that stalls proves the
//! game's own pass is. The samples carry the colour before and after the user's layers on an 8x8
//! grid, which is what the editor's proofs (invert, red, masks) are measured on.
//!
//! # The path back to the CPU
//!
//! The probe is a buffer on the `DEFAULT` heap with unordered access, bound as a root UAV at
//! `u1, space42`. At the `Close` of every command list that bound a port variant, the command-list
//! hook records a copy of it into slot `present % 8` of a ring on the `READBACK` heap, between a
//! barrier to `COPY_SOURCE` and one to `COMMON`, the state the list found it in. The CPU reads
//! slot `(present - 3) % 8`, three presents behind, and [`decode_region`]
//! accepts a sample only when its frame equals its header's: a sample written by an earlier frame
//! sits beside a newer header whenever a pass covered fewer grid points than before.
//!
//! The layout and the decoder are pure and tested on the host. The Windows half owns the buffers.

/// Bytes in the probe, and in one readback slot.
pub const PROBE_BYTES: usize = 0x2000;

/// Distance between region headers.
pub const HEADER_STRIDE: usize = 0x20;

/// The counters block: sampled coverage (P3), marked and changed pixels per selector bit.
pub const COUNTERS: usize = 0x60;

/// The pick record (P4).
pub const PICK: usize = 0x1900;

/// Samples per region lie on a grid this many points wide and high.
pub const GRID: usize = 8;

/// Samples per region.
pub const SAMPLES: usize = GRID * GRID;

/// Bytes in one sample.
pub const SAMPLE_BYTES: usize = 32;

/// Byte offsets inside a region header.
pub mod header {
    /// The present count the variant read from `er_p[3].y`.
    pub const FRAME: usize = 0;
    /// `ER_VARIANT_ID`: the port kind in the top byte, and for a runtime-compiled variant its
    /// build number below.
    pub const VARIANT: usize = 4;
    /// The pass's target size: the depth buffer at AO and albedo, the HDR source at tonemap.
    pub const WIDTH: usize = 8;
    pub const HEIGHT: usize = 12;
    /// The largest `SV_Position` seen, taken with `InterlockedMax`.
    pub const MAX_X: usize = 16;
    pub const MAX_Y: usize = 20;
    /// Bytes the header uses of its stride.
    pub const BYTES: usize = 24;
}

/// Byte offsets inside a sample.
pub mod sample {
    /// `float3`: the colour after the game's maths and cel.
    pub const PRE: usize = 0;
    /// The present count the variant read when it wrote this sample.
    pub const FRAME: usize = 12;
    /// `float3`: the colour after the layers.
    pub const POST: usize = 16;
    /// The depth bits at AO and albedo, the mask word at tonemap.
    pub const AUX: usize = 28;
}

/// One stage's part of the probe. AO and AO with SSR share a region, as do the two tonemaps; the
/// header's variant id says which wrote it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Region {
    Ao,
    Albedo,
    Tonemap,
}

impl Region {
    pub const ALL: [Self; 3] = [Self::Ao, Self::Albedo, Self::Tonemap];

    /// Offset of the region's header.
    #[must_use]
    pub const fn header(self) -> usize {
        match self {
            Self::Ao => 0x0000,
            Self::Albedo => 0x0020,
            Self::Tonemap => 0x0040,
        }
    }

    /// Offset of the region's first sample.
    #[must_use]
    pub const fn samples(self) -> usize {
        match self {
            Self::Ao => 0x0100,
            Self::Albedo => 0x0900,
            Self::Tonemap => 0x1100,
        }
    }

    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Ao => "ao",
            Self::Albedo => "albedo",
            Self::Tonemap => "tonemap",
        }
    }
}

const REGION_SAMPLE_BYTES: usize = SAMPLES * SAMPLE_BYTES;
const _: () = assert!(header::BYTES <= HEADER_STRIDE);
const _: () = assert!(Region::Albedo.header() == Region::Ao.header() + HEADER_STRIDE);
const _: () = assert!(Region::Tonemap.header() == Region::Albedo.header() + HEADER_STRIDE);
const _: () = assert!(Region::Tonemap.header() + HEADER_STRIDE == COUNTERS);
const _: () = assert!(COUNTERS < Region::Ao.samples());
const _: () = assert!(Region::Ao.samples() + REGION_SAMPLE_BYTES == Region::Albedo.samples());
const _: () = assert!(Region::Albedo.samples() + REGION_SAMPLE_BYTES == Region::Tonemap.samples());
const _: () = assert!(Region::Tonemap.samples() + REGION_SAMPLE_BYTES == PICK);
const _: () = assert!(PICK < PROBE_BYTES);
const _: () = assert!(sample::AUX + 4 == SAMPLE_BYTES);

/// Slots in the readback ring.
pub const READBACK_SLOTS: usize = 8;

/// Bytes in the readback ring.
pub const READBACK_BYTES: usize = READBACK_SLOTS * PROBE_BYTES;

/// How many presents the CPU reads behind the present being recorded.
pub const READ_LAG_PRESENTS: u64 = 3;

/// The readback slot a command list closed during present `present` copies the probe into.
#[must_use]
pub fn write_slot(present: u64) -> usize {
    (present % READBACK_SLOTS as u64) as usize
}

/// The readback slot the CPU reads during present `present`: the one written
/// [`READ_LAG_PRESENTS`] earlier, or none that early in the session.
#[must_use]
pub fn read_slot(present: u64) -> Option<usize> {
    present.checked_sub(READ_LAG_PRESENTS).map(write_slot)
}

/// How many presents ago a header's frame was, from the present count now. The header holds the
/// count's low 32 bits.
#[must_use]
pub fn frame_age(present: u64, frame: u32) -> u32 {
    (present as u32).wrapping_sub(frame)
}

/// A region's header.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Header {
    pub frame: u32,
    pub variant_id: u32,
    pub width: u32,
    pub height: u32,
    pub max_x: u32,
    pub max_y: u32,
}

/// One accepted sample.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Sample {
    /// Its place on the grid: row times [`GRID`] plus column.
    pub index: usize,
    pub pre: [f32; 3],
    pub post: [f32; 3],
    /// See [`sample::AUX`].
    pub aux: u32,
}

/// One region as a readback slot holds it.
#[derive(Clone, Debug, PartialEq)]
pub struct RegionRecord {
    pub region: Region,
    pub header: Header,
    /// The samples written in the header's frame.
    pub samples: Vec<Sample>,
    /// Samples refused because their frame is not the header's.
    pub stale: usize,
}

fn word(bytes: &[u8], at: usize) -> Option<u32> {
    let end = at.checked_add(4)?;
    let slice = bytes.get(at..end)?;
    Some(u32::from_le_bytes([slice[0], slice[1], slice[2], slice[3]]))
}

fn float3(bytes: &[u8], at: usize) -> Option<[f32; 3]> {
    Some([
        f32::from_bits(word(bytes, at)?),
        f32::from_bits(word(bytes, at + 4)?),
        f32::from_bits(word(bytes, at + 8)?),
    ])
}

/// One region of a probe copy. `None` when the bytes are shorter than a probe, or the region's
/// header was never written (frame 0: the present count starts at 1).
#[must_use]
pub fn decode_region(probe: &[u8], region: Region) -> Option<RegionRecord> {
    if probe.len() < PROBE_BYTES {
        return None;
    }
    let at = region.header();
    let header = Header {
        frame: word(probe, at + header::FRAME)?,
        variant_id: word(probe, at + header::VARIANT)?,
        width: word(probe, at + header::WIDTH)?,
        height: word(probe, at + header::HEIGHT)?,
        max_x: word(probe, at + header::MAX_X)?,
        max_y: word(probe, at + header::MAX_Y)?,
    };
    if header.frame == 0 {
        return None;
    }
    let mut samples = Vec::with_capacity(SAMPLES);
    let mut stale = 0;
    for index in 0..SAMPLES {
        let at = region.samples() + index * SAMPLE_BYTES;
        if word(probe, at + sample::FRAME)? != header.frame {
            stale += 1;
            continue;
        }
        samples.push(Sample {
            index,
            pre: float3(probe, at + sample::PRE)?,
            post: float3(probe, at + sample::POST)?,
            aux: word(probe, at + sample::AUX)?,
        });
    }
    Some(RegionRecord {
        region,
        header,
        samples,
        stale,
    })
}

/// Every region of a probe copy that has been written.
#[must_use]
pub fn decode(probe: &[u8]) -> Vec<RegionRecord> {
    Region::ALL
        .into_iter()
        .filter_map(|region| decode_region(probe, region))
        .collect()
}

#[cfg(windows)]
pub use process::{
    create, probe_address, probe_resource, read_latest, read_slot_bytes, readback_resource,
};

/// The probe buffer and the readback ring.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

    use windows::Win32::Graphics::Direct3D12::{
        D3D12_HEAP_TYPE_DEFAULT, D3D12_HEAP_TYPE_READBACK, D3D12_RANGE,
        D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, D3D12_RESOURCE_FLAG_NONE,
        D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_DEST, ID3D12Device,
    };
    use windows::core::Interface;

    use super::{PROBE_BYTES, READBACK_BYTES, READBACK_SLOTS, RegionRecord, decode, read_slot};
    use crate::log;
    use crate::look_binding::committed_buffer;

    /// The probe, as a raw `ID3D12Resource*`, and its GPU address. 0 before [`create`].
    static PROBE: AtomicUsize = AtomicUsize::new(0);
    static PROBE_GPU: AtomicU64 = AtomicU64::new(0);
    /// The readback ring, as a raw `ID3D12Resource*`, and its CPU address.
    static READBACK: AtomicUsize = AtomicUsize::new(0);
    static READBACK_MAPPED: AtomicUsize = AtomicUsize::new(0);

    /// Create the probe and the readback ring on `device`. Called once, before the first root
    /// signature that binds the probe is recorded.
    ///
    /// The probe starts in `COMMON`, and every recording that touches it leaves it there: the
    /// command-list hooks take it to `UNORDERED_ACCESS` with a barrier of their own at the
    /// recording's first redirect, and back through `COPY_SOURCE` to `COMMON` at its `Close`
    /// ([`crate::cmdlist`]). No barrier rests on what a draw promoted it to.
    ///
    /// # Safety
    ///
    /// `device` must be the live device the probe's root signatures belong to.
    pub unsafe fn create(device: &ID3D12Device) -> Result<(), String> {
        let probe = unsafe {
            committed_buffer(
                device,
                D3D12_HEAP_TYPE_DEFAULT,
                PROBE_BYTES as u64,
                D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                D3D12_RESOURCE_STATE_COMMON,
            )
        }
        .map_err(|error| format!("probe: {error}"))?;
        let readback = unsafe {
            committed_buffer(
                device,
                D3D12_HEAP_TYPE_READBACK,
                READBACK_BYTES as u64,
                D3D12_RESOURCE_FLAG_NONE,
                D3D12_RESOURCE_STATE_COPY_DEST,
            )
        }
        .map_err(|error| format!("probe readback: {error}"))?;
        let mut mapped: *mut c_void = std::ptr::null_mut();
        let everything = D3D12_RANGE {
            Begin: 0,
            End: READBACK_BYTES,
        };
        unsafe { readback.Map(0, Some(&everything), Some(&mut mapped)) }
            .map_err(|error| format!("probe readback: Map failed: {error}"))?;
        if mapped.is_null() {
            return Err("probe readback: Map returned no address".to_owned());
        }
        let probe_gpu = unsafe { probe.GetGPUVirtualAddress() };
        // Both kept for the life of the process: command lists the GPU has not finished may still
        // name them, and the hooks hold them as raw pointers.
        PROBE_GPU.store(probe_gpu, Ordering::Release);
        PROBE.store(probe.into_raw() as usize, Ordering::Release);
        READBACK_MAPPED.store(mapped as usize, Ordering::Release);
        READBACK.store(readback.into_raw() as usize, Ordering::Release);
        log(format_args!(
            "probe: gpu 0x{probe_gpu:x} ({PROBE_BYTES} bytes), readback {READBACK_SLOTS} x \
             {PROBE_BYTES} bytes, mapped"
        ));
        Ok(())
    }

    /// GPU address of the probe, for its root UAV; 0 before it exists.
    #[must_use]
    pub fn probe_address() -> u64 {
        PROBE_GPU.load(Ordering::Acquire)
    }

    /// The probe as a raw `ID3D12Resource*`, for the barriers and the copy at `Close`; 0 before
    /// it exists. Borrowed: the process holds the only reference and never releases it.
    #[must_use]
    pub fn probe_resource() -> usize {
        PROBE.load(Ordering::Acquire)
    }

    /// The readback ring as a raw `ID3D12Resource*`, the copy's destination; 0 before it exists.
    /// Slot `s` starts at byte `s * PROBE_BYTES`.
    #[must_use]
    pub fn readback_resource() -> usize {
        READBACK.load(Ordering::Acquire)
    }

    /// A copy of one readback slot, or `None` before the ring exists or for a slot past it.
    #[must_use]
    pub fn read_slot_bytes(slot: usize) -> Option<Vec<u8>> {
        if slot >= READBACK_SLOTS {
            return None;
        }
        let mapped = READBACK_MAPPED.load(Ordering::Acquire);
        if mapped == 0 {
            return None;
        }
        let mut bytes = vec![0_u8; PROBE_BYTES];
        // SAFETY: the ring is mapped for the life of the process and `slot` is inside it. The GPU
        // writes other slots meanwhile, never this one: it was written three presents ago.
        unsafe {
            std::ptr::copy_nonoverlapping(
                (mapped + slot * PROBE_BYTES) as *const u8,
                bytes.as_mut_ptr(),
                PROBE_BYTES,
            );
        }
        Some(bytes)
    }

    /// The regions of the slot to read during present `present`, decoded.
    #[must_use]
    pub fn read_latest(present: u64) -> Vec<RegionRecord> {
        read_slot(present)
            .and_then(read_slot_bytes)
            .map_or_else(Vec::new, |bytes| decode(&bytes))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::abi_header_defines;
    use crate::params::abi_header_register;

    #[test]
    fn the_probe_layout_matches_the_abi_header() {
        let declared = abi_header_defines();
        let expected = [
            ("ER_PROBE_BYTES", PROBE_BYTES),
            ("ER_PROBE_HEADER_STRIDE", HEADER_STRIDE),
            ("ER_PROBE_AO_HEADER", Region::Ao.header()),
            ("ER_PROBE_ALBEDO_HEADER", Region::Albedo.header()),
            ("ER_PROBE_TONEMAP_HEADER", Region::Tonemap.header()),
            ("ER_PROBE_HEADER_FRAME", header::FRAME),
            ("ER_PROBE_HEADER_VARIANT", header::VARIANT),
            ("ER_PROBE_HEADER_WIDTH", header::WIDTH),
            ("ER_PROBE_HEADER_HEIGHT", header::HEIGHT),
            ("ER_PROBE_HEADER_MAX_X", header::MAX_X),
            ("ER_PROBE_HEADER_MAX_Y", header::MAX_Y),
            ("ER_PROBE_COUNTERS", COUNTERS),
            ("ER_PROBE_AO_SAMPLES", Region::Ao.samples()),
            ("ER_PROBE_ALBEDO_SAMPLES", Region::Albedo.samples()),
            ("ER_PROBE_TONEMAP_SAMPLES", Region::Tonemap.samples()),
            ("ER_PROBE_GRID", GRID),
            ("ER_PROBE_SAMPLES", SAMPLES),
            ("ER_PROBE_SAMPLE_BYTES", SAMPLE_BYTES),
            ("ER_PROBE_SAMPLE_PRE", sample::PRE),
            ("ER_PROBE_SAMPLE_FRAME", sample::FRAME),
            ("ER_PROBE_SAMPLE_POST", sample::POST),
            ("ER_PROBE_SAMPLE_AUX", sample::AUX),
            ("ER_PROBE_PICK", PICK),
        ];
        for (name, value) in expected {
            assert_eq!(declared.get(name), Some(&value), "{name}");
        }
        let known: Vec<&str> = expected.iter().map(|(name, _)| *name).collect();
        for name in declared.keys().filter(|name| name.starts_with("ER_PROBE_")) {
            assert!(known.contains(&name.as_str()), "{name} has no Rust twin");
        }
    }

    #[test]
    fn the_probe_and_mask_are_raw_buffers_at_their_registers() {
        assert_eq!(
            abi_header_register("er_probe").as_deref(),
            Some("u1, space42")
        );
        assert_eq!(
            abi_header_register("er_mask_plane").as_deref(),
            Some("u0, space42")
        );
        assert!(
            crate::params::ABI_HEADER
                .lines()
                .any(|line| line.starts_with("RWByteAddressBuffer er_probe "))
        );
        // `er_mask` is the mask function (design 7.4), and HLSL refuses a global of the same name,
        // so the generated layer code could not compile against a buffer called that.
        assert_eq!(abi_header_register("er_mask"), None);
    }

    #[test]
    fn every_stage_context_has_its_mask_and_selector_functions() {
        // Design 3.1 and 7.3: the generated code calls `er_mask` for every stage with the slot's
        // mask word, its mask register and the colour reaching the layer (`editor::codegen`), and
        // `er_selected` is the seam for P3, so each context needs both overloads.
        let stage_ctx = include_str!("../shaders/stage_ctx.hlsli");
        for context in ["LightingCtx", "AlbedoCtx", "TonemapCtx"] {
            assert!(
                stage_ctx.contains(&format!("struct {context}\n")),
                "{context}"
            );
            let signatures = [
                format!("float er_mask(float word, float4 bounds, float3 x, {context} c)\n"),
                format!("float er_selected(float id, {context} c)\n"),
            ];
            for signature in signatures {
                assert!(stage_ctx.contains(&signature), "{signature}");
            }
        }
    }

    /// A probe copy with `region`'s header at `frame` and every sample at `frame` too, each sample
    /// numbered in its colours.
    fn probe_with(region: Region, frame: u32, variant: u32) -> Vec<u8> {
        let mut bytes = vec![0_u8; PROBE_BYTES];
        let mut put = |at: usize, value: u32| {
            bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
        };
        let at = region.header();
        put(at + header::FRAME, frame);
        put(at + header::VARIANT, variant);
        put(at + header::WIDTH, 1920);
        put(at + header::HEIGHT, 1080);
        put(at + header::MAX_X, 1919);
        put(at + header::MAX_Y, 1079);
        for index in 0..SAMPLES {
            let at = region.samples() + index * SAMPLE_BYTES;
            for c in 0..3 {
                put(at + sample::PRE + c * 4, (index as f32).to_bits());
                put(at + sample::POST + c * 4, (1.0 - index as f32).to_bits());
            }
            put(at + sample::FRAME, frame);
            put(at + sample::AUX, 0xA000 + index as u32);
        }
        bytes
    }

    fn set_word(bytes: &mut [u8], at: usize, value: u32) {
        bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
    }

    #[test]
    fn a_whole_region_decodes() {
        let bytes = probe_with(Region::Tonemap, 77, 3 << 24);
        let record = decode_region(&bytes, Region::Tonemap).expect("written");
        assert_eq!(
            record.header,
            Header {
                frame: 77,
                variant_id: 3 << 24,
                width: 1920,
                height: 1080,
                max_x: 1919,
                max_y: 1079,
            }
        );
        assert_eq!(record.samples.len(), SAMPLES);
        assert_eq!(record.stale, 0);
        let fifth = record.samples[5];
        assert_eq!(fifth.index, 5);
        assert_eq!(fifth.pre, [5.0; 3]);
        assert_eq!(fifth.post, [-4.0; 3]);
        assert_eq!(fifth.aux, 0xA005);
        // The other regions were never written.
        assert!(decode_region(&bytes, Region::Ao).is_none());
        assert!(decode_region(&bytes, Region::Albedo).is_none());
        assert_eq!(decode(&bytes).len(), 1);
    }

    #[test]
    fn the_decoder_refuses_samples_whose_frame_is_not_the_headers() {
        let mut bytes = probe_with(Region::Ao, 200, 0);
        // A newer header over samples an earlier frame wrote, and one sample from a later frame.
        set_word(&mut bytes, Region::Ao.header() + header::FRAME, 201);
        let at = Region::Ao.samples() + 9 * SAMPLE_BYTES;
        set_word(&mut bytes, at + sample::FRAME, 201);
        let record = decode_region(&bytes, Region::Ao).expect("written");
        assert_eq!(record.header.frame, 201);
        assert_eq!(record.samples.len(), 1);
        assert_eq!(record.samples[0].index, 9);
        assert_eq!(record.stale, SAMPLES - 1);

        set_word(&mut bytes, at + sample::FRAME, 202);
        let record = decode_region(&bytes, Region::Ao).expect("written");
        assert!(record.samples.is_empty());
        assert_eq!(record.stale, SAMPLES);
    }

    #[test]
    fn short_or_unwritten_probes_decode_to_nothing() {
        assert!(decode(&[]).is_empty());
        assert!(decode(&vec![0_u8; PROBE_BYTES]).is_empty());
        let bytes = probe_with(Region::Albedo, 5, 2 << 24);
        assert!(decode_region(&bytes[..PROBE_BYTES - 1], Region::Albedo).is_none());
        assert!(decode_region(&bytes, Region::Albedo).is_some());
    }

    #[test]
    fn the_cpu_reads_three_presents_behind_the_copy() {
        assert_eq!(write_slot(0), 0);
        assert_eq!(write_slot(9), 1);
        assert_eq!(read_slot(2), None);
        assert_eq!(read_slot(3), Some(0));
        for present in 3..100 {
            assert_eq!(read_slot(present), Some(write_slot(present - 3)));
            // The slot being read is never the one being written this present.
            assert_ne!(read_slot(present), Some(write_slot(present)));
        }
        assert_eq!(READBACK_BYTES, 64 * 1024);
    }

    #[test]
    fn frame_age_wraps_with_the_32_bit_header() {
        assert_eq!(frame_age(10, 7), 3);
        assert_eq!(frame_age(1 << 32, u32::MAX), 1);
        assert_eq!(frame_age(5, 5), 0);
    }
}
