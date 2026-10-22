//! Always-on native Scaleform descriptor-ring guard, as a mid hook on the page-roll branch.
//!
//! # The crash
//!
//! The Scaleform CBV_SRV_UAV descriptor-ring advance, `advance(ring, count)` (1.16.2 rva
//! `0xec9530`, 1.17.1 `0x140ecb3a0`; `IDENTICAL-WHOLE` between them), faults on a ring whose
//! current page `ring+0x38` is null. That is the state the ring's reset (`0x140ec9470`) leaves:
//! it destroys the current page, nulls `+0x38` and drains the free list, and leaves the cursor and
//! the CPU/GPU handles where they were. The fault is on the new-page branch only:
//!
//! ```text
//! +0x0b: ADD [RCX+0x28],RAX; ...; CMP RAX,[RCX+0x20]; JB +0x177  ; fast path: no page read
//! +0x8c: MOV RAX,[RBX+0x38]                                      ; current page -- null
//! +0x90: LEA R8,[RSP+0x30]
//! +0x95: MOV [RSP+0x30],RSI                                      ; RSI is zero here
//! +0x9a: LEA RDX,[RIP+..]
//! +0xa1: MOV RCX,[RAX+0x20]                                      ; faults (crash rva 0xec95d1)
//! +0xa5: MOV RAX,[RCX]; CALL [RAX+0x38]                          ; page heap -> GetDevice
//! ```
//!
//! # Why a mid hook and not the entry guard it replaces
//!
//! The entry guard returned before the function did anything, so a null page also dropped every
//! fast-path advance: the cursor `+0x28` and the handles `+0x50`/`+0x58` stood still and
//! successive callers got the same descriptor slots. The fast path never reads `+0x38`, so this
//! hook leaves it alone and diverts only the page-roll branch.
//!
//! # Where the diversion goes, and why not `+0xab`
//!
//! The obvious resume is `+0xab`, the page selection right after the skipped `GetDevice` call. It
//! is not safe with no current page, read from both images on 2026-10-19:
//!
//! * With `ring+0x48 == 1` it takes the free list's tail, `[ring+0x18]`. After the reset the list
//!   is empty and circular, so the tail is the ring itself. The reuse branch at `+0xe2` would
//!   unlink the ring from its own list, store the ring as its own current page and call through
//!   `[ring+0x20]` -- the capacity integer -- as a heap object.
//! * Otherwise it calls `0x140ec97a0(ring, device)` with the device the skipped call would have
//!   produced, here null. That function zeroes `+0x50`/`+0x58` before it tests the device and
//!   returns null without creating a heap, so the ring stays without a page and the next caller
//!   reads a zero CPU handle and hands it to `CreateShaderResourceView`.
//!
//! Neither path existed before: the original code faulted at `+0xa1` first. So the hook resumes
//! at `+0x157` instead, the join both page-selection branches reach. It reads `[RSP+0x30]` and
//! releases the device only when it is non-null, restores `RDI` and `RSI` from `[RSP+0x48]` and
//! `[RSP+0x38]` (saved by this same slow path at `+0x21`/`+0x28`), and returns. What the routine
//! must leave for it:
//!
//! * `[RSP+0x30]` = 0. The skipped `MOV [RSP+0x30],RSI` is what initialises the slot, so without
//!   the write the join would release stack garbage.
//! * `RAX` = 0, the value the entry guard returned. None of the seven direct callers reads `RAX`
//!   before overwriting it (checked in the 1.16.2 image), so this only keeps the return
//!   deterministic.
//! * `RBX` (the ring) is untouched, and nothing else is read before the `RET`.
//!
//! The diverted call leaves the ring exactly as the entry guard did except for the cursor, which
//! the function had already advanced at `+0x0b`. That keeps every later call on the page-roll
//! branch until the ring's init (`0x140ec93e0`) gives it a page and resets the cursor.
//!
//! The 14 bytes ilhook overwrites at `+0x8c` are the three instructions above, none of them
//! RIP-relative. No branch in the function lands inside `+0x8c..+0x9a`: every jump target in the
//! function is `+0x5c`, `+0x74`, `+0xe2`, `+0x10c`, `+0x146`, `+0x157`, `+0x177` or `+0x185`,
//! collected from both images' disassembly on 2026-10-19. The two images differ in the function
//! only at `+0x9d..+0x9f`, the displacement of the `LEA RDX,[RIP+..]` just past the window.

use core::sync::atomic::{AtomicUsize, Ordering};

#[cfg(windows)]
use er_telemetry_core::counters::SCALEFORM_DESC_ADVANCE_INSTALLED;
use er_telemetry_core::counters::SCALEFORM_DESC_PROVIDER_NULL_HITS;

/// 1.16.2 rva of the descriptor-ring advance, the `.pdata` function containing both hook
/// addresses. 1.17.1 `0x140ecb3a0`.
pub(crate) const SCALEFORM_DESC_ADVANCE_RVA: usize = 0x00ec_9530;
/// `ring+0x38`, the current page.
pub(crate) const SCALEFORM_DESC_PROVIDER_OFFSET: usize = 0x38;
/// The page-roll branch's load of the current page, where the hook goes.
pub(crate) const PAGE_ROLL_LOAD: usize = 0x8c;
/// The join both page-selection branches reach, where the diversion resumes.
pub(crate) const EPILOGUE_JOIN: usize = 0x157;
/// `[RSP+0x30]`, the `GetDevice` out slot the join releases when non-null.
pub(crate) const DEVICE_OUT_STACK_SLOT: u64 = 0x30;

/// Absolute address of [`EPILOGUE_JOIN`] on the running build, published before the hook goes
/// live. Zero means unresolved, and the routine then takes the original path.
static EPILOGUE_JOIN_RESUME: AtomicUsize = AtomicUsize::new(0);

include!(concat!(
    env!("OUT_DIR"),
    "/generated_descriptor_advance_windows.rs"
));

/// Whether the ring in `ring` has no current page.
///
/// # Safety
///
/// `ring` must be the ring the advance holds in `RBX` at `+0x8c` (`MOV RBX,RCX` at `+0x08`); its
/// callers pass an embedded member of a live renderer object.
unsafe fn current_page_missing(ring: u64) -> bool {
    let slot = (ring + SCALEFORM_DESC_PROVIDER_OFFSET as u64) as *const u64;
    unsafe { core::ptr::read_volatile(slot) == 0 }
}

/// `+0x8c`: no current page -> the epilogue join at `+0x157`, with the device slot cleared.
///
/// # Safety
///
/// Installed by ilhook as the jmp-ret routine at [`PAGE_ROLL_LOAD`]; never called from Rust
/// outside tests. `regs.rsp` is the advance's own stack pointer at the hook site, so
/// `[rsp+0x30]` is its frame slot.
unsafe extern "win64" fn page_roll_route(
    regs: *mut ilhook::x64::Registers,
    original: usize,
    _user_data: usize,
) -> usize {
    let resume = EPILOGUE_JOIN_RESUME.load(Ordering::Acquire);
    let regs = unsafe { &mut *regs };
    if resume == 0 || !unsafe { current_page_missing(regs.rbx) } {
        return original;
    }
    let device_slot = (regs.rsp + DEVICE_OUT_STACK_SLOT) as *mut u64;
    unsafe { core::ptr::write_volatile(device_slot, 0) };
    regs.rax = 0;
    SCALEFORM_DESC_PROVIDER_NULL_HITS.fetch_add(1, Ordering::SeqCst);
    resume
}

/// Outcome of an idempotent descriptor guard installation.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum DescriptorGuardInstall {
    AlreadyInstalled,
    /// The mid hook is live at `site` and diverts to `resume`.
    Installed {
        site: usize,
        resume: usize,
    },
}

/// Why the mid hook is not installed. In every case nothing was written to the game.
#[derive(Debug)]
pub enum DescriptorGuardInstallError {
    /// No game module base yet.
    ModuleBase(String),
    /// The running build has no mapping for the containing function. Nothing was read.
    Refused { build: String },
    /// The address resolved and the bytes there are not the verified ones.
    Disarmed {
        window: &'static str,
        address: usize,
        found: Vec<u8>,
        expected: &'static [u8],
    },
    /// The window could not be read at all.
    Unreadable {
        window: &'static str,
        address: usize,
    },
    /// ilhook refused the hook.
    Hook(String),
}

/// Compare the live bytes at `address` with a window `build.rs` generated.
#[cfg(windows)]
fn check_window(
    window: &'static str,
    address: usize,
    expected: &'static [u8],
) -> Result<(), DescriptorGuardInstallError> {
    let mut found = vec![0_u8; expected.len()];
    if !unsafe { er_game_base::mem::read_bytes(address, &mut found) } {
        return Err(DescriptorGuardInstallError::Unreadable { window, address });
    }
    if found != expected {
        return Err(DescriptorGuardInstallError::Disarmed {
            window,
            address,
            found,
            expected,
        });
    }
    Ok(())
}

/// Install the descriptor-ring mid hook.
///
/// Both addresses are offsets into the advance, so both go through
/// [`er_game_base::game_build::resolve_call_site_rva`], and both windows are checked before
/// anything is written. The product caller decides how an outcome is reported; a successful
/// installation is idempotent.
#[cfg(windows)]
pub fn install_scaleform_descriptor_guard()
-> Result<DescriptorGuardInstall, DescriptorGuardInstallError> {
    use ilhook::x64::{CallbackOption, HookFlags, HookType, Hooker};

    if SCALEFORM_DESC_ADVANCE_INSTALLED.load(Ordering::SeqCst) != 0 {
        return Ok(DescriptorGuardInstall::AlreadyInstalled);
    }
    let base =
        er_game_base::mem::game_module_base().map_err(DescriptorGuardInstallError::ModuleBase)?;
    // Named `at_offset`, not `resolve`: `scripts/check-native-call-rva-coverage.py` reads every
    // `resolve(` argument as a game address, and these are offsets into the advance.
    let at_offset = |offset: usize| {
        er_game_base::game_build::resolve_call_site_rva(
            SCALEFORM_DESC_ADVANCE_RVA,
            offset,
            "scaleform descriptor advance",
        )
        .map(|rva| base + rva)
    };
    let (Some(site), Some(resume)) = (at_offset(PAGE_ROLL_LOAD), at_offset(EPILOGUE_JOIN)) else {
        return Err(DescriptorGuardInstallError::Refused {
            build: er_game_base::game_build::describe_build(),
        });
    };
    check_window("page-roll load", site, PAGE_ROLL_LOAD_WINDOW)?;
    check_window("epilogue join", resume, EPILOGUE_JOIN_WINDOW)?;

    EPILOGUE_JOIN_RESUME.store(resume, Ordering::Release);
    let hooker = Hooker::new(
        site,
        HookType::JmpToRet(page_roll_route),
        CallbackOption::None,
        0,
        HookFlags::empty(),
    );
    match unsafe { hooker.hook() } {
        Ok(point) => {
            // The hook lives as long as the process; dropping the point would unhook it.
            core::mem::forget(point);
            SCALEFORM_DESC_ADVANCE_INSTALLED.store(1, Ordering::SeqCst);
            Ok(DescriptorGuardInstall::Installed { site, resume })
        }
        Err(err) => {
            EPILOGUE_JOIN_RESUME.store(0, Ordering::Release);
            Err(DescriptorGuardInstallError::Hook(err.to_string()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Serialises the tests that publish a resume address, since it is one process-wide static.
    static RESUME_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn offsets_match_er_1162_static_re() {
        assert_eq!(SCALEFORM_DESC_ADVANCE_RVA, 0xec_9530);
        assert_eq!(SCALEFORM_DESC_PROVIDER_OFFSET, 0x38);
        assert_eq!((PAGE_ROLL_LOAD, EPILOGUE_JOIN), (0x8c, 0x157));
        assert_eq!(DEVICE_OUT_STACK_SLOT, 0x30);
    }

    /// ilhook overwrites 14 bytes, and the window is exactly the three instructions that covers,
    /// so the moved bytes are the checked bytes and the jump ends on an instruction boundary.
    #[test]
    fn the_page_roll_window_is_the_jump() {
        const ILHOOK_JUMP_BYTES: usize = 14;
        assert_eq!(PAGE_ROLL_LOAD_WINDOW.len(), ILHOOK_JUMP_BYTES);
        assert_eq!(EPILOGUE_JOIN_WINDOW.len(), 18);
    }

    fn regs_for(ring: &[u64; 8], frame: &mut [u64; 8]) -> ilhook::x64::Registers {
        let mut regs: ilhook::x64::Registers = unsafe { core::mem::zeroed() };
        regs.rbx = ring.as_ptr() as u64;
        regs.rsp = frame.as_mut_ptr() as u64;
        regs.rax = 0xdead_beef;
        regs
    }

    #[test]
    fn the_route_takes_the_original_path_until_resolved() {
        let _lock = RESUME_LOCK
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        EPILOGUE_JOIN_RESUME.store(0, Ordering::SeqCst);
        let ring = [0_u64; 8];
        let mut frame = [0x5a5a_u64; 8];
        let mut regs = regs_for(&ring, &mut frame);
        let back = unsafe { page_roll_route(&mut regs, 0x1234, 0) };
        assert_eq!(
            back, 0x1234,
            "an unresolved resume address must never be jumped to"
        );
        assert_eq!(frame[6], 0x5a5a, "the original path writes nothing");
    }

    #[test]
    fn a_ring_with_a_page_takes_the_original_path() {
        let _lock = RESUME_LOCK
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        EPILOGUE_JOIN_RESUME.store(0x5000, Ordering::SeqCst);
        let mut ring = [0_u64; 8];
        ring[SCALEFORM_DESC_PROVIDER_OFFSET / 8] = 0x7000;
        let mut frame = [0x5a5a_u64; 8];
        let mut regs = regs_for(&ring, &mut frame);
        let before = SCALEFORM_DESC_PROVIDER_NULL_HITS.load(Ordering::SeqCst);
        let back = unsafe { page_roll_route(&mut regs, 0x1234, 0) };
        EPILOGUE_JOIN_RESUME.store(0, Ordering::SeqCst);
        assert_eq!(back, 0x1234);
        assert_eq!(frame[6], 0x5a5a);
        assert_eq!(regs.rax, 0xdead_beef);
        assert_eq!(
            SCALEFORM_DESC_PROVIDER_NULL_HITS.load(Ordering::SeqCst),
            before
        );
    }

    #[test]
    fn a_ring_without_a_page_diverts_with_the_device_slot_cleared() {
        let _lock = RESUME_LOCK
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        EPILOGUE_JOIN_RESUME.store(0x5000, Ordering::SeqCst);
        let ring = [0_u64; 8];
        let mut frame = [0x5a5a_u64; 8];
        let mut regs = regs_for(&ring, &mut frame);
        let before = SCALEFORM_DESC_PROVIDER_NULL_HITS.load(Ordering::SeqCst);
        let back = unsafe { page_roll_route(&mut regs, 0x1234, 0) };
        EPILOGUE_JOIN_RESUME.store(0, Ordering::SeqCst);
        assert_eq!(back, 0x5000);
        assert_eq!(
            frame[(DEVICE_OUT_STACK_SLOT / 8) as usize],
            0,
            "the join releases [rsp+0x30] when it is non-null, so it must be zero"
        );
        assert_eq!(frame[5], 0x5a5a, "only the device slot is written");
        assert_eq!(frame[7], 0x5a5a, "only the device slot is written");
        assert_eq!(regs.rax, 0);
        assert!(SCALEFORM_DESC_PROVIDER_NULL_HITS.load(Ordering::SeqCst) > before);
    }
}
