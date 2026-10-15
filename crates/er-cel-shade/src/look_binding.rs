//! Binding the editor's parameters, probe and mask to every pass while the game runs (design 5.1).
//!
//! # The mechanism
//!
//! Our pixel shaders read four things the game never binds, all in register space 42
//! (`shaders/editor_abi.hlsli`):
//!
//! | Param | Register | Kind | `DWORD`s | Bound to |
//! |---|---|---|---|---|
//! | A | `b0, space42` | root CBV, `DATA_VOLATILE` | 2 | the parameter block current this present ([`crate::params`]) |
//! | B | `u1, space42` | root UAV | 2 | the probe ([`crate::probe`]) |
//! | C | `u0, space42` | root UAV | 2 | the mask plane (P2); a 4 KB dummy until then |
//! | D | `b1, space42` | two 32-bit root constants | 2 | set only around duplicated draws (P3) |
//!
//! So this module, in three steps:
//!
//! 1. appends those parameters, visible to pixel shaders, to every root signature the game
//!    creates. Appending leaves every parameter the game knows about at its index, so the game's
//!    own `SetGraphicsRoot*` calls are unchanged;
//! 2. creates the buffers behind A, B and C on the game's device, once;
//! 3. after every `SetGraphicsRootSignature` that sets one of those root signatures, binds A, B
//!    and C through the command list's own vtable. Setting a root signature invalidates every root
//!    argument, the appended ones included, so the bind follows every set.
//!
//! # The ladder
//!
//! A root signature holds at most 64 `DWORD`s. One with room for all four parameters (8 `DWORD`s)
//! gets them all: [`Level::Full`]. One with less gets A and B ([`Level::CbvProbe`], 4), or A alone
//! ([`Level::Cbv`], 2), or nothing ([`Level::None`]); [`ladder`] picks. The game's raster root
//! signature, 36 descriptor tables, goes from 36 to 44. A port is redirected only on a root
//! signature at `Full`; one on a lower level stays vanilla and is logged. The count per level is
//! logged as `rs: full=.. cbv_probe=.. cbv=.. none=..`.
//!
//! # Why every root signature, not only the replaced passes' ones
//!
//! A root signature is created long before any pipeline that uses it, and a pipeline must be
//! created with the very root signature the game binds for it -- two root signatures with
//! different parameter lists are not interchangeable. So the only point where the parameters can
//! be added is creation, before it is known which root signatures the replaced passes will use. A
//! ray-tracing local root signature, one that denies pixel shaders root access, or one already
//! using register space 42 is left as the game made it.
//!
//! A pipeline created with no root signature takes the one serialized into its shaders, which
//! would not carry the appended parameters while the root signature the game binds does. `hooks`
//! gives such a pipeline the augmented root signature built from those same shader bytes, through
//! [`augmented_from_shader`].
//!
//! # The lookup
//!
//! The bind runs on every recording thread, about once per command list. It finds a root
//! signature's [`Binding`] in [`RootSignatures`]: 256 slots of open addressing, append-only, a key
//! and one `AtomicU64` per slot, so the lookup takes no lock. A root signature created again at a
//! freed one's address overwrites that slot's value.
//!
//! The ladder, the binding's packing and the table are pure and tested on the host. The Windows
//! half augments, records and binds.

use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use crate::replace::MAX_ROOT_SIGNATURE_DWORDS;
use crate::table::HASH_MULTIPLIER;

/// The register space every appended parameter is declared in. Unusual on purpose: the game's root
/// signatures would have to use space 42 for the two to collide, and one that does is left alone.
pub const REGISTER_SPACE: u32 = 42;

/// Shader registers of the appended parameters, in register space [`REGISTER_SPACE`].
pub mod register {
    /// A: `b0`, the parameter block.
    pub const PARAMS_CBV: u32 = 0;
    /// B: `u1`, the probe.
    pub const PROBE_UAV: u32 = 1;
    /// C: `u0`, the mask plane.
    pub const MASK_UAV: u32 = 0;
    /// D: `b1`, the draw constants.
    pub const DRAW_CONSTANTS: u32 = 1;
    /// D's size in 32-bit values: the selection bits and one auxiliary word.
    pub const DRAW_CONSTANT_COUNT: u32 = 2;
}

/// How many of the appended parameters a root signature carries. Ordered by how much it carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[repr(u8)]
pub enum Level {
    /// Nothing appended: the root signature is as the game made it.
    None = 0,
    /// A only.
    Cbv = 1,
    /// A and B.
    CbvProbe = 2,
    /// A, B, C and D.
    Full = 3,
}

impl Level {
    /// Every level, most first: the order [`ladder`] tries them in.
    pub const LADDER: [Self; 4] = [Self::Full, Self::CbvProbe, Self::Cbv, Self::None];

    /// Parameters appended at this level.
    #[must_use]
    pub const fn parameters(self) -> u32 {
        match self {
            Self::None => 0,
            Self::Cbv => 1,
            Self::CbvProbe => 2,
            Self::Full => 4,
        }
    }

    /// `DWORD`s those parameters cost: two for each root descriptor, one for each constant.
    #[must_use]
    pub const fn dwords(self) -> u32 {
        match self {
            Self::None => 0,
            Self::Cbv => 2,
            Self::CbvProbe => 4,
            Self::Full => 6 + register::DRAW_CONSTANT_COUNT,
        }
    }

    /// The level's name in the `rs:` log line and the diagnostics.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Cbv => "cbv",
            Self::CbvProbe => "cbv_probe",
            Self::Full => "full",
        }
    }

    const fn from_u8(value: u8) -> Option<Self> {
        match value {
            0 => Some(Self::None),
            1 => Some(Self::Cbv),
            2 => Some(Self::CbvProbe),
            3 => Some(Self::Full),
            _ => None,
        }
    }
}

/// The most a root signature already using `used` of its 64 `DWORD`s has room for.
#[must_use]
pub fn ladder(used: u32) -> Level {
    Level::LADDER
        .into_iter()
        .find(|level| used.saturating_add(level.dwords()) <= MAX_ROOT_SIGNATURE_DWORDS)
        .unwrap_or(Level::None)
}

/// A parameter index the binding's level does not carry.
pub const NO_INDEX: u8 = u8::MAX;

/// Where the appended parameters sit in one root signature: its level and the root parameter
/// index of each of A, B, C and D it carries, [`NO_INDEX`] for the rest.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Binding {
    pub level: Level,
    /// A: the parameter block's root CBV.
    pub a: u8,
    /// B: the probe's root UAV.
    pub b: u8,
    /// C: the mask's root UAV.
    pub c: u8,
    /// D: the draw constants.
    pub d: u8,
}

impl Binding {
    /// A root signature that carries nothing.
    pub const NONE: Self = Self {
        level: Level::None,
        a: NO_INDEX,
        b: NO_INDEX,
        c: NO_INDEX,
        d: NO_INDEX,
    };

    /// The binding of `level`'s parameters appended after the game's `first` parameters, in the
    /// order A, B, C, D. `None` when an index would not fit a root signature.
    #[must_use]
    pub fn appended(level: Level, first: u32) -> Option<Self> {
        let index = |n: u32| -> Option<u8> {
            if n >= level.parameters() {
                return Some(NO_INDEX);
            }
            u8::try_from(first.checked_add(n)?)
                .ok()
                .filter(|index| *index != NO_INDEX)
        };
        Some(Self {
            level,
            a: index(0)?,
            b: index(1)?,
            c: index(2)?,
            d: index(3)?,
        })
    }

    /// The indexes as an array, A first.
    #[must_use]
    pub const fn indexes(self) -> [u8; 4] {
        [self.a, self.b, self.c, self.d]
    }

    /// One `u64`: the level in byte 0, then A, B, C and D in bytes 1 to 4.
    #[must_use]
    pub fn pack(self) -> u64 {
        let [a, b, c, d] = self.indexes();
        u64::from_le_bytes([self.level as u8, a, b, c, d, 0, 0, 0])
    }

    /// The binding [`pack`](Self::pack) made, or `None` for any other value: an unknown level,
    /// spare bytes set, or an index present where the level carries none or absent where it does.
    #[must_use]
    pub fn unpack(value: u64) -> Option<Self> {
        let [level, a, b, c, d, rest @ ..] = value.to_le_bytes();
        if rest != [0; 3] {
            return None;
        }
        let level = Level::from_u8(level)?;
        let binding = Self { level, a, b, c, d };
        let carried = level.parameters() as usize;
        let consistent = binding
            .indexes()
            .iter()
            .enumerate()
            .all(|(n, index)| (n < carried) == (*index != NO_INDEX));
        consistent.then_some(binding)
    }
}

/// Slots in [`RootSignatures`].
pub const ROOT_SIGNATURE_SLOTS: usize = 256;
const SLOT_BITS: u32 = ROOT_SIGNATURE_SLOTS.trailing_zeros();
const _: () = assert!(ROOT_SIGNATURE_SLOTS.is_power_of_two());

/// A root signature pointer is a heap allocation aligned to at least 16 bytes, so its low four
/// bits carry nothing.
const KEY_ALIGN_BITS: u32 = 4;

/// Root signatures by the pointer the game holds, each with a packed [`Binding`]: open addressing
/// with linear probing over [`ROOT_SIGNATURE_SLOTS`] slots. Keys are only ever added, and a key's
/// value may be overwritten, so a reader needs no lock: it loads a key, and on a match the value
/// beside it.
pub struct RootSignatures {
    keys: [AtomicUsize; ROOT_SIGNATURE_SLOTS],
    values: [AtomicU64; ROOT_SIGNATURE_SLOTS],
}

impl Default for RootSignatures {
    fn default() -> Self {
        Self::new()
    }
}

impl RootSignatures {
    #[must_use]
    pub const fn new() -> Self {
        Self {
            keys: [const { AtomicUsize::new(0) }; ROOT_SIGNATURE_SLOTS],
            values: [const { AtomicU64::new(0) }; ROOT_SIGNATURE_SLOTS],
        }
    }

    fn home(key: usize) -> usize {
        (((key as u64) >> KEY_ALIGN_BITS).wrapping_mul(HASH_MULTIPLIER) >> (u64::BITS - SLOT_BITS))
            as usize
    }

    /// The slots a probe for `key` visits, in order.
    fn probe(key: usize) -> impl Iterator<Item = usize> {
        let home = Self::home(key);
        (0..ROOT_SIGNATURE_SLOTS).map(move |step| (home + step) & (ROOT_SIGNATURE_SLOTS - 1))
    }

    /// The value recorded for `key`. A null key is never recorded.
    #[must_use]
    pub fn get(&self, key: usize) -> Option<u64> {
        if key == 0 {
            return None;
        }
        for slot in Self::probe(key) {
            match self.keys[slot].load(Ordering::Acquire) {
                found if found == key => return Some(self.values[slot].load(Ordering::Acquire)),
                0 => return None,
                _ => {}
            }
        }
        None
    }

    /// Record `value` for `key`, overwriting what an earlier root signature at the same address
    /// left. `false` when the key is null or every slot holds another key.
    ///
    /// A reader that finds a freshly claimed key before its value is stored reads 0, which unpacks
    /// to no binding; nothing can bind a root signature before its creation has returned, so in
    /// practice no reader is there to see it.
    pub fn insert(&self, key: usize, value: u64) -> bool {
        if key == 0 {
            return false;
        }
        for slot in Self::probe(key) {
            let found = self.keys[slot].load(Ordering::Acquire);
            let claimed = found == key
                || (found == 0
                    && match self.keys[slot].compare_exchange(
                        0,
                        key,
                        Ordering::AcqRel,
                        Ordering::Acquire,
                    ) {
                        Ok(_) => true,
                        Err(raced) => raced == key,
                    });
            if claimed {
                self.values[slot].store(value, Ordering::Release);
                return true;
            }
        }
        false
    }

    /// Overwrite `key`'s value if it is recorded, and claim no slot if it is not. `false` when it
    /// is not recorded.
    pub fn replace(&self, key: usize, value: u64) -> bool {
        if key == 0 {
            return false;
        }
        for slot in Self::probe(key) {
            match self.keys[slot].load(Ordering::Acquire) {
                found if found == key => {
                    self.values[slot].store(value, Ordering::Release);
                    return true;
                }
                0 => return false,
                _ => {}
            }
        }
        false
    }

    /// Keys recorded.
    #[must_use]
    pub fn len(&self) -> usize {
        self.keys
            .iter()
            .filter(|key| key.load(Ordering::Relaxed) != 0)
            .count()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Why a root signature was left as the game made it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Skip {
    /// `DWORD`s used: no room even for A.
    NoRoom(u32),
    Local,
    DeniesPixelShaders,
    SpaceTaken,
    Version(i32),
    /// Created on a device other than the one the editor's buffers live on.
    OtherDevice(usize),
    Failed(String),
}

impl std::fmt::Display for Skip {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoRoom(used) => write!(
                f,
                "{used} of 64 DWORDs used, no room for even the parameter block"
            ),
            Self::Local => f.write_str("a ray-tracing local root signature"),
            Self::DeniesPixelShaders => f.write_str("denies pixel shaders root access"),
            Self::SpaceTaken => write!(f, "already uses register space {REGISTER_SPACE}"),
            Self::Version(version) => write!(f, "root signature version {version} not handled"),
            Self::OtherDevice(owner) => write!(
                f,
                "created on another device than 0x{owner:x}, which holds the editor's buffers"
            ),
            Self::Failed(why) => f.write_str(why),
        }
    }
}

#[cfg(windows)]
pub use process::{
    Augmented, BINDS, LEVELS, SET_ROOT_SIGNATURE, SKIPPED_NO_ROOM, SKIPPED_OTHER, TABLE_FULL,
    augment, augmented_from_shader, binding, committed_buffer, ladder_counts, ladder_line,
    log_ladder, note_skip, record, root_signature_pointer, set_mask_address,
    set_root_signature_hook,
};

/// The augmentation, the root-signature table, the device's buffers and the bind.
#[cfg(windows)]
mod process {
    use core::ffi::c_void;
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
    use std::sync::{LazyLock, Mutex, PoisonError};

    use windows::Win32::Graphics::Direct3D::ID3DBlob;
    use windows::Win32::Graphics::Direct3D12::{
        D3D_ROOT_SIGNATURE_VERSION_1_0, D3D_ROOT_SIGNATURE_VERSION_1_1,
        D3D_ROOT_SIGNATURE_VERSION_1_2, D3D12_CPU_PAGE_PROPERTY_UNKNOWN, D3D12_DESCRIPTOR_RANGE,
        D3D12_DESCRIPTOR_RANGE1, D3D12_HEAP_FLAG_NONE, D3D12_HEAP_PROPERTIES, D3D12_HEAP_TYPE,
        D3D12_HEAP_TYPE_DEFAULT, D3D12_MEMORY_POOL_UNKNOWN, D3D12_RESOURCE_DESC,
        D3D12_RESOURCE_DIMENSION_BUFFER, D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
        D3D12_RESOURCE_FLAGS, D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATES,
        D3D12_ROOT_CONSTANTS, D3D12_ROOT_DESCRIPTOR, D3D12_ROOT_DESCRIPTOR_FLAG_DATA_VOLATILE,
        D3D12_ROOT_DESCRIPTOR1, D3D12_ROOT_PARAMETER, D3D12_ROOT_PARAMETER_0,
        D3D12_ROOT_PARAMETER_TYPE, D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS,
        D3D12_ROOT_PARAMETER_TYPE_CBV, D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE,
        D3D12_ROOT_PARAMETER_TYPE_UAV, D3D12_ROOT_PARAMETER1, D3D12_ROOT_PARAMETER1_0,
        D3D12_ROOT_SIGNATURE_DESC, D3D12_ROOT_SIGNATURE_DESC1, D3D12_ROOT_SIGNATURE_DESC2,
        D3D12_ROOT_SIGNATURE_FLAG_DENY_PIXEL_SHADER_ROOT_ACCESS,
        D3D12_ROOT_SIGNATURE_FLAG_LOCAL_ROOT_SIGNATURE, D3D12_ROOT_SIGNATURE_FLAGS,
        D3D12_SHADER_VISIBILITY_PIXEL, D3D12_TEXTURE_LAYOUT_ROW_MAJOR,
        D3D12_VERSIONED_ROOT_SIGNATURE_DESC, D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0,
        D3D12CreateVersionedRootSignatureDeserializer, D3D12SerializeVersionedRootSignature,
        ID3D12Device, ID3D12GraphicsCommandList_Vtbl, ID3D12Resource, ID3D12RootSignature,
        ID3D12VersionedRootSignatureDeserializer,
    };
    use windows::Win32::Graphics::Dxgi::Common::{DXGI_FORMAT_UNKNOWN, DXGI_SAMPLE_DESC};
    use windows::core::{IUnknown, Interface};

    use super::{Binding, Level, REGISTER_SPACE, RootSignatures, Skip, ladder, register};
    use crate::dxbc::{self, container_part};
    use crate::replace::{self, RootParameterCost, root_signature_dwords};
    use crate::{log, params, probe};

    /// Every recorded root signature's binding.
    static ROOT_SIGNATURES: RootSignatures = RootSignatures::new();

    /// Root signature creations by the level they got, indexed by `Level as usize`.
    pub static LEVELS: [AtomicUsize; 4] = [const { AtomicUsize::new(0) }; 4];
    /// Augmented root signatures that found no free slot, and so bind nothing.
    pub static TABLE_FULL: AtomicUsize = AtomicUsize::new(0);
    /// Root signatures left as the game made them: for want of room, and for any other reason.
    pub static SKIPPED_NO_ROOM: AtomicUsize = AtomicUsize::new(0);
    pub static SKIPPED_OTHER: AtomicUsize = AtomicUsize::new(0);
    /// Root signature sets the parameters were bound after.
    pub static BINDS: AtomicUsize = AtomicUsize::new(0);

    /// GPU address C binds: the 4 KB dummy, until the mask plane exists (P2).
    static MASK_ADDRESS: AtomicU64 = AtomicU64::new(0);

    /// Size of the dummy C points at before the mask plane exists.
    const MASK_DUMMY_BYTES: u64 = 4096;

    /// The device the editor's buffers were created on, or why they could not be. Taken once, by
    /// the first root signature with room for any parameter.
    static RESOURCES: Mutex<Option<Result<usize, String>>> = Mutex::new(None);

    /// Root signatures built from a pipeline's own shader bytes, by FNV-1 of the `RTS0` part. Kept
    /// for the life of the process, like the game's own.
    static FROM_SHADER: LazyLock<Mutex<HashMap<u64, usize>>> =
        LazyLock::new(|| Mutex::new(HashMap::new()));

    /// The counts per level as last logged, so [`log_ladder`] logs a change only.
    static LADDER_LOGGED: Mutex<[usize; 4]> = Mutex::new([0; 4]);

    /// A committed buffer of `bytes`.
    ///
    /// # Safety
    ///
    /// `device` must be a live device.
    pub unsafe fn committed_buffer(
        device: &ID3D12Device,
        heap: D3D12_HEAP_TYPE,
        bytes: u64,
        flags: D3D12_RESOURCE_FLAGS,
        state: D3D12_RESOURCE_STATES,
    ) -> Result<ID3D12Resource, String> {
        let properties = D3D12_HEAP_PROPERTIES {
            Type: heap,
            CPUPageProperty: D3D12_CPU_PAGE_PROPERTY_UNKNOWN,
            MemoryPoolPreference: D3D12_MEMORY_POOL_UNKNOWN,
            CreationNodeMask: 0,
            VisibleNodeMask: 0,
        };
        let desc = D3D12_RESOURCE_DESC {
            Dimension: D3D12_RESOURCE_DIMENSION_BUFFER,
            Alignment: 0,
            Width: bytes,
            Height: 1,
            DepthOrArraySize: 1,
            MipLevels: 1,
            Format: DXGI_FORMAT_UNKNOWN,
            SampleDesc: DXGI_SAMPLE_DESC {
                Count: 1,
                Quality: 0,
            },
            Layout: D3D12_TEXTURE_LAYOUT_ROW_MAJOR,
            Flags: flags,
        };
        let mut resource: Option<ID3D12Resource> = None;
        unsafe {
            device.CreateCommittedResource(
                &properties,
                D3D12_HEAP_FLAG_NONE,
                &desc,
                state,
                None,
                &mut resource,
            )
        }
        .map_err(|error| format!("CreateCommittedResource({bytes} bytes) failed: {error}"))?;
        resource.ok_or_else(|| "CreateCommittedResource returned no resource".to_owned())
    }

    /// The parameter ring, the probe and its readback ring, and the mask dummy, on `device`.
    unsafe fn create_resources(device: &ID3D12Device) -> Result<(), String> {
        unsafe { params::create(device) }?;
        unsafe { probe::create(device) }?;
        let mask = unsafe {
            committed_buffer(
                device,
                D3D12_HEAP_TYPE_DEFAULT,
                MASK_DUMMY_BYTES,
                D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                D3D12_RESOURCE_STATE_COMMON,
            )
        }
        .map_err(|error| format!("mask dummy: {error}"))?;
        MASK_ADDRESS.store(unsafe { mask.GetGPUVirtualAddress() }, Ordering::Release);
        // Kept for the life of the process, like the buffers above.
        let _ = mask.into_raw();
        Ok(())
    }

    /// The editor's buffers exist on `device`: created now if this is the first call, refused if
    /// they were created on another device or could not be created.
    fn resources_on(device: &ID3D12Device) -> Result<(), Skip> {
        let raw = device.as_raw() as usize;
        let mut slot = RESOURCES.lock().unwrap_or_else(PoisonError::into_inner);
        match &*slot {
            Some(Ok(owner)) if *owner == raw => return Ok(()),
            Some(Ok(owner)) => return Err(Skip::OtherDevice(*owner)),
            Some(Err(why)) => return Err(Skip::Failed(why.clone())),
            None => {}
        }
        // SAFETY: `device` is the live device a root signature is being created on.
        let created = unsafe { create_resources(device) };
        match &created {
            Ok(()) => log(format_args!("editor buffers on device 0x{raw:x}")),
            Err(why) => log(format_args!(
                "editor buffers not created on device 0x{raw:x}: {why}; every root signature is \
                 left as the game made it"
            )),
        }
        *slot = Some(created.clone().map(|()| raw));
        created.map_err(Skip::Failed)
    }

    /// Point C at the mask plane from now on (P2).
    pub fn set_mask_address(address: u64) {
        MASK_ADDRESS.store(address, Ordering::Release);
    }

    /// A root signature's binding, if it carries any parameter.
    #[must_use]
    pub fn binding(root_signature: usize) -> Option<Binding> {
        ROOT_SIGNATURES
            .get(root_signature)
            .and_then(Binding::unpack)
            .filter(|binding| binding.level != Level::None)
    }

    /// Root signature creations per level, indexed by `Level as usize`.
    #[must_use]
    pub fn ladder_counts() -> [usize; 4] {
        [0, 1, 2, 3].map(|level| LEVELS[level].load(Ordering::Relaxed))
    }

    /// `rs: full=.. cbv_probe=.. cbv=.. none=..`.
    #[must_use]
    pub fn ladder_line() -> String {
        let counts = ladder_counts();
        format!(
            "rs: full={} cbv_probe={} cbv={} none={}",
            counts[Level::Full as usize],
            counts[Level::CbvProbe as usize],
            counts[Level::Cbv as usize],
            counts[Level::None as usize]
        )
    }

    /// Log [`ladder_line`] if the counts changed since it was last logged. For the worker's
    /// periodic tick; the creation hook also logs it at every power-of-two total.
    pub fn log_ladder() {
        let counts = ladder_counts();
        let mut logged = LADDER_LOGGED.lock().unwrap_or_else(PoisonError::into_inner);
        if *logged != counts {
            *logged = counts;
            log(format_args!("{}", ladder_line()));
        }
    }

    /// Record what a root signature creation produced: its parameters appended, or none.
    pub fn record(root_signature: usize, augmented: Option<&Augmented>) {
        let mut level = augmented.map_or(Level::None, |a| a.binding.level);
        if root_signature != 0 {
            match augmented {
                Some(augmented) => {
                    if !ROOT_SIGNATURES.insert(root_signature, augmented.binding.pack()) {
                        level = Level::None;
                        if TABLE_FULL.fetch_add(1, Ordering::Relaxed) < 4 {
                            log(format_args!(
                                "root signature 0x{root_signature:x}: all {} table slots are \
                                 taken, so nothing is bound for it",
                                super::ROOT_SIGNATURE_SLOTS
                            ));
                        }
                    }
                }
                // Only an address a freed root signature recorded needs overwriting; a miss already
                // reads as no binding, and claiming a slot for it would waste one.
                None => {
                    ROOT_SIGNATURES.replace(root_signature, Binding::NONE.pack());
                }
            }
        }
        let seen = LEVELS[level as usize].fetch_add(1, Ordering::Relaxed);
        if let Some(augmented) = augmented
            && level != Level::None
            && seen < 4
        {
            let [a, b, c, d] = augmented.binding.indexes();
            log(format_args!(
                "root signature 0x{root_signature:x} at level {}: {} -> {} DWORDs, parameters \
                 a={a} b={b} c={c} d={d} (255 = absent)",
                level.name(),
                augmented.used,
                augmented.used + level.dwords()
            ));
        }
        let total: usize = ladder_counts().iter().sum();
        if total.is_power_of_two() {
            log(format_args!("{}", ladder_line()));
        }
    }

    /// A root signature blob with the parameters its room allows appended.
    pub struct Augmented {
        pub blob: Vec<u8>,
        pub binding: Binding,
        /// `DWORD`s the game's own parameters use.
        pub used: u32,
    }

    fn uses_space(space: u32) -> bool {
        space == REGISTER_SPACE
    }

    fn check_flags(flags: D3D12_ROOT_SIGNATURE_FLAGS) -> Result<(), Skip> {
        if flags.0 & D3D12_ROOT_SIGNATURE_FLAG_LOCAL_ROOT_SIGNATURE.0 != 0 {
            return Err(Skip::Local);
        }
        if flags.0 & D3D12_ROOT_SIGNATURE_FLAG_DENY_PIXEL_SHADER_ROOT_ACCESS.0 != 0 {
            return Err(Skip::DeniesPixelShaders);
        }
        Ok(())
    }

    /// One parameter's cost, unless one of its register spaces is 42.
    fn cost(
        ty: D3D12_ROOT_PARAMETER_TYPE,
        constants: u32,
        mut spaces: impl Iterator<Item = u32>,
    ) -> Result<RootParameterCost, Skip> {
        if spaces.any(uses_space) {
            return Err(Skip::SpaceTaken);
        }
        Ok(if ty == D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE {
            RootParameterCost::Table
        } else if ty == D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS {
            RootParameterCost::Constants(constants)
        } else {
            RootParameterCost::Descriptor
        })
    }

    /// `DWORD`s a 1.0 parameter list uses, unless it already uses register space 42.
    unsafe fn used_1_0(params: &[D3D12_ROOT_PARAMETER]) -> Result<u32, Skip> {
        let mut costs = Vec::with_capacity(params.len());
        for param in params {
            let ty = param.ParameterType;
            costs.push(if ty == D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE {
                let table = unsafe { param.Anonymous.DescriptorTable };
                let ranges: &[D3D12_DESCRIPTOR_RANGE] =
                    unsafe { slice_or_empty(table.pDescriptorRanges, table.NumDescriptorRanges) };
                cost(ty, 0, ranges.iter().map(|r| r.RegisterSpace))?
            } else if ty == D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS {
                let constants = unsafe { param.Anonymous.Constants };
                cost(
                    ty,
                    constants.Num32BitValues,
                    std::iter::once(constants.RegisterSpace),
                )?
            } else {
                let descriptor = unsafe { param.Anonymous.Descriptor };
                cost(ty, 0, std::iter::once(descriptor.RegisterSpace))?
            });
        }
        Ok(root_signature_dwords(costs))
    }

    /// `DWORD`s a 1.1 or 1.2 parameter list uses, unless it already uses register space 42.
    unsafe fn used_1_1(params: &[D3D12_ROOT_PARAMETER1]) -> Result<u32, Skip> {
        let mut costs = Vec::with_capacity(params.len());
        for param in params {
            let ty = param.ParameterType;
            costs.push(if ty == D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE {
                let table = unsafe { param.Anonymous.DescriptorTable };
                let ranges: &[D3D12_DESCRIPTOR_RANGE1] =
                    unsafe { slice_or_empty(table.pDescriptorRanges, table.NumDescriptorRanges) };
                cost(ty, 0, ranges.iter().map(|r| r.RegisterSpace))?
            } else if ty == D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS {
                let constants = unsafe { param.Anonymous.Constants };
                cost(
                    ty,
                    constants.Num32BitValues,
                    std::iter::once(constants.RegisterSpace),
                )?
            } else {
                let descriptor = unsafe { param.Anonymous.Descriptor };
                cost(ty, 0, std::iter::once(descriptor.RegisterSpace))?
            });
        }
        Ok(root_signature_dwords(costs))
    }

    unsafe fn slice_or_empty<'a, T>(start: *const T, count: u32) -> &'a [T] {
        if start.is_null() || count == 0 {
            &[]
        } else {
            unsafe { std::slice::from_raw_parts(start, count as usize) }
        }
    }

    /// The parameters `level` carries, as 1.0 parameters, in the order A, B, C, D.
    fn appended_1_0(level: Level) -> Vec<D3D12_ROOT_PARAMETER> {
        let descriptor = |ty, shader_register| D3D12_ROOT_PARAMETER {
            ParameterType: ty,
            Anonymous: D3D12_ROOT_PARAMETER_0 {
                Descriptor: D3D12_ROOT_DESCRIPTOR {
                    ShaderRegister: shader_register,
                    RegisterSpace: REGISTER_SPACE,
                },
            },
            ShaderVisibility: D3D12_SHADER_VISIBILITY_PIXEL,
        };
        let constants = D3D12_ROOT_PARAMETER {
            ParameterType: D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS,
            Anonymous: D3D12_ROOT_PARAMETER_0 {
                Constants: draw_constants(),
            },
            ShaderVisibility: D3D12_SHADER_VISIBILITY_PIXEL,
        };
        let all = [
            descriptor(D3D12_ROOT_PARAMETER_TYPE_CBV, register::PARAMS_CBV),
            descriptor(D3D12_ROOT_PARAMETER_TYPE_UAV, register::PROBE_UAV),
            descriptor(D3D12_ROOT_PARAMETER_TYPE_UAV, register::MASK_UAV),
            constants,
        ];
        all[..level.parameters() as usize].to_vec()
    }

    /// The parameters `level` carries, as 1.1 parameters, in the order A, B, C, D.
    fn appended_1_1(level: Level) -> Vec<D3D12_ROOT_PARAMETER1> {
        let descriptor = |ty, shader_register| D3D12_ROOT_PARAMETER1 {
            ParameterType: ty,
            Anonymous: D3D12_ROOT_PARAMETER1_0 {
                Descriptor: D3D12_ROOT_DESCRIPTOR1 {
                    ShaderRegister: shader_register,
                    RegisterSpace: REGISTER_SPACE,
                    // The parameter block is rewritten while frames are in flight, and the probe and
                    // mask are written by the GPU itself; volatile tells the driver not to assume
                    // any of them stays put for a whole command list.
                    Flags: D3D12_ROOT_DESCRIPTOR_FLAG_DATA_VOLATILE,
                },
            },
            ShaderVisibility: D3D12_SHADER_VISIBILITY_PIXEL,
        };
        let constants = D3D12_ROOT_PARAMETER1 {
            ParameterType: D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS,
            Anonymous: D3D12_ROOT_PARAMETER1_0 {
                Constants: draw_constants(),
            },
            ShaderVisibility: D3D12_SHADER_VISIBILITY_PIXEL,
        };
        let all = [
            descriptor(D3D12_ROOT_PARAMETER_TYPE_CBV, register::PARAMS_CBV),
            descriptor(D3D12_ROOT_PARAMETER_TYPE_UAV, register::PROBE_UAV),
            descriptor(D3D12_ROOT_PARAMETER_TYPE_UAV, register::MASK_UAV),
            constants,
        ];
        all[..level.parameters() as usize].to_vec()
    }

    fn draw_constants() -> D3D12_ROOT_CONSTANTS {
        D3D12_ROOT_CONSTANTS {
            ShaderRegister: register::DRAW_CONSTANTS,
            RegisterSpace: REGISTER_SPACE,
            Num32BitValues: register::DRAW_CONSTANT_COUNT,
        }
    }

    /// The level for a root signature using `used` `DWORD`s, with the editor's buffers made ready
    /// when it carries anything.
    fn level_for(device: &ID3D12Device, used: u32) -> Result<Level, Skip> {
        match ladder(used) {
            Level::None => Err(Skip::NoRoom(used)),
            level => resources_on(device).map(|()| level),
        }
    }

    /// The game's root signature blob with the parameters its room allows appended, serialized at
    /// the version the game wrote it in.
    ///
    /// # Safety
    ///
    /// `blob` must be readable for `length` bytes.
    pub unsafe fn augment(
        device: &ID3D12Device,
        blob: *const c_void,
        length: usize,
    ) -> Result<Augmented, Skip> {
        let mut raw: *mut c_void = std::ptr::null_mut();
        unsafe {
            D3D12CreateVersionedRootSignatureDeserializer(
                blob,
                length,
                &ID3D12VersionedRootSignatureDeserializer::IID,
                &mut raw,
            )
        }
        .map_err(|e| Skip::Failed(format!("deserialize: {e}")))?;
        // SAFETY: the call above succeeded and handed over one reference.
        let deserializer = unsafe { ID3D12VersionedRootSignatureDeserializer::from_raw(raw) };
        // Owned by the deserializer, which outlives every use below.
        let original = unsafe { &*deserializer.GetUnconvertedRootSignatureDesc() };
        let version = original.Version;

        let mut serialized: Option<ID3DBlob> = None;
        let mut errors: Option<ID3DBlob> = None;
        let (index, level, used);
        if version == D3D_ROOT_SIGNATURE_VERSION_1_0 {
            let desc = unsafe { original.Anonymous.Desc_1_0 };
            check_flags(desc.Flags)?;
            let params = unsafe { slice_or_empty(desc.pParameters, desc.NumParameters) };
            used = unsafe { used_1_0(params) }?;
            level = level_for(device, used)?;
            let mut extended = params.to_vec();
            extended.extend(appended_1_0(level));
            index = desc.NumParameters;
            let augmented = D3D12_VERSIONED_ROOT_SIGNATURE_DESC {
                Version: version,
                Anonymous: D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0 {
                    Desc_1_0: D3D12_ROOT_SIGNATURE_DESC {
                        NumParameters: extended.len() as u32,
                        pParameters: extended.as_ptr(),
                        ..desc
                    },
                },
            };
            unsafe {
                D3D12SerializeVersionedRootSignature(&augmented, &mut serialized, Some(&mut errors))
            }
            .map_err(|e| Skip::Failed(format!("serialize 1.0: {e} {}", blob_text(&errors))))?;
        } else if version == D3D_ROOT_SIGNATURE_VERSION_1_1 {
            let desc = unsafe { original.Anonymous.Desc_1_1 };
            check_flags(desc.Flags)?;
            let params = unsafe { slice_or_empty(desc.pParameters, desc.NumParameters) };
            used = unsafe { used_1_1(params) }?;
            level = level_for(device, used)?;
            let mut extended = params.to_vec();
            extended.extend(appended_1_1(level));
            index = desc.NumParameters;
            let augmented = D3D12_VERSIONED_ROOT_SIGNATURE_DESC {
                Version: version,
                Anonymous: D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0 {
                    Desc_1_1: D3D12_ROOT_SIGNATURE_DESC1 {
                        NumParameters: extended.len() as u32,
                        pParameters: extended.as_ptr(),
                        ..desc
                    },
                },
            };
            unsafe {
                D3D12SerializeVersionedRootSignature(&augmented, &mut serialized, Some(&mut errors))
            }
            .map_err(|e| Skip::Failed(format!("serialize 1.1: {e} {}", blob_text(&errors))))?;
        } else if version == D3D_ROOT_SIGNATURE_VERSION_1_2 {
            let desc = unsafe { original.Anonymous.Desc_1_2 };
            check_flags(desc.Flags)?;
            let params = unsafe { slice_or_empty(desc.pParameters, desc.NumParameters) };
            used = unsafe { used_1_1(params) }?;
            level = level_for(device, used)?;
            let mut extended = params.to_vec();
            extended.extend(appended_1_1(level));
            index = desc.NumParameters;
            let augmented = D3D12_VERSIONED_ROOT_SIGNATURE_DESC {
                Version: version,
                Anonymous: D3D12_VERSIONED_ROOT_SIGNATURE_DESC_0 {
                    Desc_1_2: D3D12_ROOT_SIGNATURE_DESC2 {
                        NumParameters: extended.len() as u32,
                        pParameters: extended.as_ptr(),
                        ..desc
                    },
                },
            };
            unsafe {
                D3D12SerializeVersionedRootSignature(&augmented, &mut serialized, Some(&mut errors))
            }
            .map_err(|e| Skip::Failed(format!("serialize 1.2: {e} {}", blob_text(&errors))))?;
        } else {
            return Err(Skip::Version(version.0));
        }
        let serialized =
            serialized.ok_or_else(|| Skip::Failed("serializer returned no blob".into()))?;
        let bytes = unsafe {
            std::slice::from_raw_parts(
                serialized.GetBufferPointer().cast::<u8>(),
                serialized.GetBufferSize(),
            )
        }
        .to_vec();
        let binding = Binding::appended(level, index)
            .ok_or_else(|| Skip::Failed(format!("parameter index {index} out of range")))?;
        Ok(Augmented {
            blob: bytes,
            binding,
            used,
        })
    }

    fn blob_text(blob: &Option<ID3DBlob>) -> String {
        let Some(blob) = blob else {
            return String::new();
        };
        // SAFETY: a live blob's pointer and size.
        let bytes = unsafe {
            std::slice::from_raw_parts(blob.GetBufferPointer().cast::<u8>(), blob.GetBufferSize())
        };
        String::from_utf8(bytes.to_vec())
            .unwrap_or_default()
            .trim_end_matches('\0')
            .to_owned()
    }

    /// Count a skip, and log the first few of each kind.
    pub fn note_skip(skip: &Skip) {
        let seen = match skip {
            Skip::NoRoom(_) => SKIPPED_NO_ROOM.fetch_add(1, Ordering::Relaxed),
            _ => SKIPPED_OTHER.fetch_add(1, Ordering::Relaxed),
        };
        if seen < 8 {
            log(format_args!(
                "root signature left as the game made it: {skip}"
            ));
        }
    }

    /// The augmented root signature a pipeline created without one should use: the one serialized
    /// into its shaders, with the parameters appended, created once per distinct `RTS0` part.
    ///
    /// Created through the device's own `CreateRootSignature`, so it goes through the hook in
    /// `hooks` and is augmented and recorded exactly like the game's.
    ///
    /// # Safety
    ///
    /// `container` must be shader bytecode the game passed to a pipeline creation.
    pub unsafe fn augmented_from_shader(device: &ID3D12Device, container: &[u8]) -> Option<usize> {
        let part = container_part(container, dxbc::ROOT_SIGNATURE_PART)?;
        let key = replace::fnv1_64(part);
        if let Some(found) = FROM_SHADER.lock().ok()?.get(&key).copied() {
            return Some(found);
        }
        // Not held across the call: creation re-enters this module through the hook.
        let created: ID3D12RootSignature = match unsafe { device.CreateRootSignature(0, container) }
        {
            Ok(created) => created,
            Err(error) => {
                log(format_args!(
                    "root signature from shader bytes failed: {error}"
                ));
                return None;
            }
        };
        let raw = created.into_raw() as usize;
        let mut cache = FROM_SHADER.lock().ok()?;
        Some(*cache.entry(key).or_insert(raw))
    }

    /// `ID3D12GraphicsCommandList::SetGraphicsRootSignature`.
    pub type SetRootSignatureFn = unsafe extern "system" fn(*mut c_void, *mut c_void);

    /// The originals behind the two `SetGraphicsRootSignature` detours: direct command lists and
    /// bundles, which vkd3d-proton serves from different vtables. Both may be the same function, in
    /// which case only the first is hooked.
    pub static SET_ROOT_SIGNATURE: [AtomicUsize; 2] = [const { AtomicUsize::new(0) }; 2];

    /// Calls the original, notes the binding in the list's slot ([`crate::cmdlist`]), then binds A,
    /// B and C as far as the root signature carries them.
    ///
    /// Runs about once per command list on every recording thread, so it takes no lock and
    /// allocates nothing: one table probe, then up to three calls through the list's own vtable.
    ///
    /// # Safety
    ///
    /// Installed as the detour of `SetGraphicsRootSignature` in slot `SLOT`; called by D3D12's
    /// callers with a live command list, never directly.
    pub unsafe extern "system" fn set_root_signature_hook<const SLOT: usize>(
        list: *mut c_void,
        root_signature: *mut c_void,
    ) {
        let original = SET_ROOT_SIGNATURE[SLOT].load(Ordering::Acquire);
        if original == 0 {
            return;
        }
        // SAFETY: the trampoline MinHook returned for this exact function.
        let original: SetRootSignatureFn = unsafe { std::mem::transmute(original) };
        unsafe { original(list, root_signature) };
        let binding = if root_signature.is_null() {
            None
        } else {
            binding(root_signature as usize)
        };
        // The list's slot keeps where the parameters sit, for what later draws on it need (P3).
        // Not from the bundle detour: a bundle has a `Close` of its own under vkd3d-proton, which
        // is not detoured, so a slot it claimed would sit until it went stale, and the game
        // creates no bundle allocator for a slot to be worth anything.
        if SLOT == 0 {
            crate::cmdlist::note_root_signature(list as usize, binding.unwrap_or(Binding::NONE));
        }
        let Some(binding) = binding else {
            return;
        };
        // SAFETY: `list` is a live graphics command list or bundle that just set this root
        // signature; its own vtable's entries are called, and none of them is hooked.
        unsafe { bind(list, binding) };
        BINDS.fetch_add(1, Ordering::Relaxed);
    }

    /// Bind the parameter block, the probe and the mask to the parameters `binding` carries.
    ///
    /// # Safety
    ///
    /// `list` must be a live graphics command list whose current root signature has `binding`.
    unsafe fn bind(list: *mut c_void, binding: Binding) {
        unsafe {
            let vtable = &**(list as *const *const ID3D12GraphicsCommandList_Vtbl);
            (vtable.SetGraphicsRootConstantBufferView)(
                list,
                u32::from(binding.a),
                params::current_address(),
            );
            if binding.level >= Level::CbvProbe {
                (vtable.SetGraphicsRootUnorderedAccessView)(
                    list,
                    u32::from(binding.b),
                    probe::probe_address(),
                );
            }
            if binding.level >= Level::Full {
                (vtable.SetGraphicsRootUnorderedAccessView)(
                    list,
                    u32::from(binding.c),
                    MASK_ADDRESS.load(Ordering::Acquire),
                );
            }
        }
    }

    /// The `ID3D12RootSignature` pointer for whatever interface a creation returned.
    ///
    /// # Safety
    ///
    /// `raw` must be a live COM object.
    pub unsafe fn root_signature_pointer(raw: *mut c_void) -> usize {
        let Some(unknown) = (unsafe { IUnknown::from_raw_borrowed(&raw) }) else {
            return 0;
        };
        // The cast takes a reference and the drop releases it, so the count is unchanged.
        unknown
            .cast::<ID3D12RootSignature>()
            .map_or(raw as usize, |rs| rs.as_raw() as usize)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::abi_header_register;

    #[test]
    fn the_ladder_takes_what_fits_under_64_dwords() {
        assert_eq!(ladder(36), Level::Full);
        assert_eq!(36 + Level::Full.dwords(), 44);
        assert_eq!(ladder(57), Level::CbvProbe);
        assert_eq!(ladder(61), Level::Cbv);
        assert_eq!(ladder(63), Level::None);
        // The edges of each step.
        assert_eq!(ladder(0), Level::Full);
        assert_eq!(ladder(56), Level::Full);
        assert_eq!(ladder(60), Level::CbvProbe);
        assert_eq!(ladder(62), Level::Cbv);
        assert_eq!(ladder(64), Level::None);
        assert_eq!(ladder(u32::MAX), Level::None);
        for used in 0..=70 {
            let level = ladder(used);
            assert!(
                used + level.dwords() <= 64 || level == Level::None,
                "{used}"
            );
            // The next level up never fits.
            if let Some(more) = Level::LADDER
                .iter()
                .rev()
                .find(|other| other.parameters() > level.parameters())
            {
                assert!(used + more.dwords() > 64, "{used}: {more:?} fits too");
            }
        }
    }

    #[test]
    fn levels_cost_two_dwords_per_parameter() {
        assert_eq!(Level::None.dwords(), 0);
        assert_eq!(Level::Cbv.dwords(), 2);
        assert_eq!(Level::CbvProbe.dwords(), 4);
        assert_eq!(Level::Full.dwords(), 8);
        for level in Level::LADDER {
            assert_eq!(level.dwords(), 2 * level.parameters(), "{level:?}");
        }
    }

    #[test]
    fn appended_parameters_follow_the_games_in_order() {
        assert_eq!(
            Binding::appended(Level::Full, 36),
            Some(Binding {
                level: Level::Full,
                a: 36,
                b: 37,
                c: 38,
                d: 39,
            })
        );
        assert_eq!(
            Binding::appended(Level::CbvProbe, 4),
            Some(Binding {
                level: Level::CbvProbe,
                a: 4,
                b: 5,
                c: NO_INDEX,
                d: NO_INDEX,
            })
        );
        assert_eq!(
            Binding::appended(Level::Cbv, 16).map(Binding::indexes),
            Some([16, NO_INDEX, NO_INDEX, NO_INDEX])
        );
        assert_eq!(Binding::appended(Level::None, 2), Some(Binding::NONE));
        assert_eq!(Binding::appended(Level::Full, 252), None);
    }

    #[test]
    fn a_binding_packs_and_unpacks_unchanged() {
        for level in Level::LADDER {
            for first in 0..=64 {
                let binding = Binding::appended(level, first).expect("fits");
                assert_eq!(
                    Binding::unpack(binding.pack()),
                    Some(binding),
                    "{level:?} {first}"
                );
            }
        }
    }

    #[test]
    fn values_pack_never_made_do_not_unpack() {
        let full = Binding::appended(Level::Full, 36).expect("fits").pack();
        // An unknown level.
        assert_eq!(Binding::unpack((full & !0xff) | 7), None);
        // Spare bytes set.
        assert_eq!(Binding::unpack(full | (1 << 40)), None);
        // A level that carries D without an index for it.
        assert_eq!(Binding::unpack(full | (0xff << 32)), None);
        // An index where the level carries none.
        let cbv = Binding::appended(Level::Cbv, 3).expect("fits").pack();
        assert_eq!(Binding::unpack(cbv & !(0xff << 16)), None);
        // A fresh slot's value is no binding.
        assert_eq!(Binding::unpack(0), None);
    }

    /// A plausible root signature pointer: heap-like and 16-byte aligned.
    fn rs(n: usize) -> usize {
        0x0000_02a0_0000_0000 + n * 0x30
    }

    #[test]
    fn the_table_finds_what_it_recorded_and_overwrites_a_reused_address() {
        let table = RootSignatures::new();
        assert!(table.is_empty());
        assert!(table.insert(rs(1), 11));
        assert!(table.insert(rs(2), 22));
        assert_eq!(table.get(rs(1)), Some(11));
        assert_eq!(table.get(rs(2)), Some(22));
        assert_eq!(table.get(rs(3)), None);
        // A root signature created again at a freed one's address.
        assert!(table.insert(rs(1), 33));
        assert_eq!(table.get(rs(1)), Some(33));
        assert_eq!(table.len(), 2);
        // Replace overwrites a recorded key and claims no slot for another.
        assert!(table.replace(rs(2), 44));
        assert_eq!(table.get(rs(2)), Some(44));
        assert!(!table.replace(rs(3), 55));
        assert_eq!(table.get(rs(3)), None);
        assert_eq!(table.len(), 2);
    }

    #[test]
    fn the_table_never_records_a_null_key() {
        let table = RootSignatures::new();
        assert!(!table.insert(0, 1));
        assert!(!table.replace(0, 1));
        assert_eq!(table.get(0), None);
        assert!(table.is_empty());
    }

    #[test]
    fn a_full_table_refuses_new_keys_and_still_overwrites() {
        let table = RootSignatures::new();
        for n in 1..=ROOT_SIGNATURE_SLOTS {
            assert!(table.insert(rs(n), n as u64), "{n}");
        }
        assert_eq!(table.len(), ROOT_SIGNATURE_SLOTS);
        assert!(!table.insert(rs(ROOT_SIGNATURE_SLOTS + 1), 1));
        assert_eq!(table.get(rs(ROOT_SIGNATURE_SLOTS + 1)), None);
        for n in 1..=ROOT_SIGNATURE_SLOTS {
            assert_eq!(table.get(rs(n)), Some(n as u64), "{n}");
        }
        assert!(table.insert(rs(7), 700));
        assert_eq!(table.get(rs(7)), Some(700));
    }

    #[test]
    fn colliding_keys_probe_past_each_other_and_wrap() {
        let last = ROOT_SIGNATURE_SLOTS - 1;
        let colliding: Vec<usize> = (1..200_000)
            .map(rs)
            .filter(|key| RootSignatures::home(*key) == last)
            .take(3)
            .collect();
        assert_eq!(colliding.len(), 3);
        let table = RootSignatures::new();
        for (n, key) in colliding.iter().enumerate() {
            assert!(table.insert(*key, n as u64));
        }
        for (n, key) in colliding.iter().enumerate() {
            assert_eq!(table.get(*key), Some(n as u64));
        }
    }

    #[test]
    fn threads_recording_at_once_lose_nothing() {
        let table = RootSignatures::new();
        std::thread::scope(|scope| {
            for thread in 0..8 {
                let table = &table;
                scope.spawn(move || {
                    for n in 0..24 {
                        let key = rs(1 + thread * 24 + n);
                        assert!(table.insert(key, key as u64));
                    }
                });
            }
        });
        assert_eq!(table.len(), 8 * 24);
        for n in 1..=8 * 24 {
            assert_eq!(table.get(rs(n)), Some(rs(n) as u64));
        }
    }

    #[test]
    fn the_appended_registers_match_the_abi_header() {
        let declared =
            |register: &str, index: u32| format!("{register}{index}, space{REGISTER_SPACE}");
        assert_eq!(
            abi_header_register("EditorParams"),
            Some(declared("b", register::PARAMS_CBV))
        );
        assert_eq!(
            abi_header_register("er_probe"),
            Some(declared("u", register::PROBE_UAV))
        );
        assert_eq!(
            abi_header_register("er_mask_plane"),
            Some(declared("u", register::MASK_UAV))
        );
    }
}
