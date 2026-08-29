//! Tier A: fault-safe RAM readers + game module base / RVA resolution.
//!
//! Implemented over raw `#[link(name = "kernel32")]` externs so this stays a
//! zero-dependency leaf that all three DLLs (product, reload-trace, input-harness)
//! and er-telemetry-core can sit on without re-implementing `ReadProcessMemory` reads.
//! Ported from the product's `experiments/mem.rs` (single source of truth now).

use core::ffi::c_void;

/// `-1` cast to a handle: the current-process pseudo-handle accepted by
/// `ReadProcessMemory` without an `OpenProcess` round-trip.
const CURRENT_PROCESS_PSEUDO_HANDLE: isize = -1;
/// `ReadProcessMemory` returns a Win32 `BOOL`; zero means failure.
const RPM_FALSE: i32 = 0;
/// Init sentinel for the out-params / accumulators (was
/// `TITLE_OWNER_SCAN_START_ADDRESS` in the product tree; it is simply 0).
const ZERO: usize = 0;

unsafe extern "system" {
    fn GetModuleHandleA(module_name: *const u8) -> isize;
    fn ReadProcessMemory(
        process: isize,
        base_address: *const c_void,
        buffer: *mut c_void,
        size: usize,
        bytes_read: *mut usize,
    ) -> i32;
}

/// Resolve the running game module's base address (`GetModuleHandleA(NULL)`).
pub fn game_module_base() -> Result<usize, String> {
    let module = unsafe { GetModuleHandleA(core::ptr::null()) };
    if module == 0 {
        return Err("failed to resolve game module: GetModuleHandleA(NULL) returned 0".to_string());
    }
    Ok(module as usize)
}

/// `game_module_base() + rva`, resolved for the RUNNING build.
///
/// Every RVA in this workspace is a 1.16.2 RVA. On a build that moved the code, this returns the
/// translated address when one has been verified and an `Err` when it has not -- so a caller
/// that ignores the error cannot call into whatever now occupies those bytes. Plain addition on
/// the supported build, and for anything outside the game image.
pub fn game_rva(rva: u32) -> Result<usize, String> {
    let raw = game_module_base()? + rva as usize;
    crate::game_build::resolve_game_address(raw, "game_rva").ok_or_else(|| {
        format!(
            "rva 0x{rva:x} has no verified mapping for the running build: {}",
            crate::game_build::describe_build()
        )
    })
}

/// Cheap heap-pointer sanity check: above the low 64 KiB reserve and 8-byte aligned.
///
/// # Safety
///
/// There is NO precondition and no unsafety here: this function dereferences nothing.
/// It is integer arithmetic on `ptr` -- a range test against the low 64 KiB reserve and
/// an alignment mask -- and would be sound as a safe `fn`. The `unsafe` marker is
/// vestigial, kept only because removing it would change the signature of a function
/// called across every DLL in this workspace. A `true` result is a cheap plausibility
/// screen, NOT proof that `ptr` is mapped or points at a live object.
pub unsafe fn is_heap_aligned_ptr(ptr: usize) -> bool {
    const HEAP_LO: usize = 0x10000;
    const PTR_ALIGN_MASK: usize = 0x7;
    ptr >= HEAP_LO && (ptr & PTR_ALIGN_MASK) == ZERO
}

/// True if `vtable` falls inside the game image span `[base+0x1000, base+0x3000000)`.
pub fn vtable_in_game_image(vtable: usize, base: usize) -> bool {
    const MODULE_MIN_OFFSET: usize = 0x1000;
    const MODULE_SPAN_FALLBACK: usize = 0x3000000;
    vtable >= base + MODULE_MIN_OFFSET && vtable < base + MODULE_SPAN_FALLBACK
}

/// Fault-tolerant pointer-sized read: returns `None` on unmapped/freed memory
/// instead of raising an access violation.
///
/// # Safety
///
/// `addr` has NO precondition: any value, including 0, a freed pointer, or a wholly
/// unmapped address, is safe to pass. The read goes through `ReadProcessMemory`, which
/// validates the range in the kernel and returns `FALSE` rather than raising an access
/// violation, so this function cannot fault on a bad address -- that fault-tolerance is
/// the entire reason it exists.
///
/// What the CALLER owns is the meaning of the bytes that come back. A successful read
/// only proves those bytes were mapped at that instant; it does not prove they are a
/// live object of the expected type, and the game may free or overwrite the region on
/// another thread immediately afterwards. Treat the value as a sample, not a borrow.
///
/// The `unsafe` marker is therefore about interpretation, not memory validity, and is
/// retained rather than removed only because dropping it would change the signature of
/// a function every DLL in this workspace calls.
pub unsafe fn safe_read_usize(addr: usize) -> Option<usize> {
    let mut value: usize = ZERO;
    let mut read: usize = ZERO;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut usize as *mut c_void,
            core::mem::size_of::<usize>(),
            &mut read,
        )
    };
    if ok != RPM_FALSE && read == core::mem::size_of::<usize>() {
        Some(value)
    } else {
        None
    }
}

/// Fault-tolerant i32 read (None on unmapped memory).
///
/// # Safety
///
/// `addr` has NO precondition: any value, including 0, a freed pointer, or a wholly
/// unmapped address, is safe to pass. The read goes through `ReadProcessMemory`, which
/// validates the range in the kernel and returns `FALSE` rather than raising an access
/// violation, so this function cannot fault on a bad address -- that fault-tolerance is
/// the entire reason it exists.
///
/// What the CALLER owns is the meaning of the bytes that come back. A successful read
/// only proves those bytes were mapped at that instant; it does not prove they are a
/// live object of the expected type, and the game may free or overwrite the region on
/// another thread immediately afterwards. Treat the value as a sample, not a borrow.
///
/// The `unsafe` marker is therefore about interpretation, not memory validity, and is
/// retained rather than removed only because dropping it would change the signature of
/// a function every DLL in this workspace calls.
pub unsafe fn safe_read_i32(addr: usize) -> Option<i32> {
    let mut value: i32 = 0;
    let mut read: usize = ZERO;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut i32 as *mut c_void,
            core::mem::size_of::<i32>(),
            &mut read,
        )
    };
    if ok != RPM_FALSE && read == core::mem::size_of::<i32>() {
        Some(value)
    } else {
        None
    }
}

/// Fault-tolerant f32 read (None on unmapped memory).
///
/// # Safety
///
/// `addr` has NO precondition: any value, including 0, a freed pointer, or a wholly
/// unmapped address, is safe to pass. The read goes through `ReadProcessMemory`, which
/// validates the range in the kernel and returns `FALSE` rather than raising an access
/// violation, so this function cannot fault on a bad address -- that fault-tolerance is
/// the entire reason it exists.
///
/// What the CALLER owns is the meaning of the bytes that come back. A successful read
/// only proves those bytes were mapped at that instant; it does not prove they are a
/// live object of the expected type, and the game may free or overwrite the region on
/// another thread immediately afterwards. Treat the value as a sample, not a borrow.
///
/// The `unsafe` marker is therefore about interpretation, not memory validity, and is
/// retained rather than removed only because dropping it would change the signature of
/// a function every DLL in this workspace calls.
pub unsafe fn safe_read_f32(addr: usize) -> Option<f32> {
    let mut value: f32 = 0.0;
    let mut read: usize = ZERO;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut f32 as *mut c_void,
            core::mem::size_of::<f32>(),
            &mut read,
        )
    };
    if ok != RPM_FALSE && read == core::mem::size_of::<f32>() {
        Some(value)
    } else {
        None
    }
}

/// Fault-tolerant single-byte read (None on unmapped memory).
///
/// # Safety
///
/// `addr` has NO precondition: any value, including 0, a freed pointer, or a wholly
/// unmapped address, is safe to pass. The read goes through `ReadProcessMemory`, which
/// validates the range in the kernel and returns `FALSE` rather than raising an access
/// violation, so this function cannot fault on a bad address -- that fault-tolerance is
/// the entire reason it exists.
///
/// What the CALLER owns is the meaning of the bytes that come back. A successful read
/// only proves those bytes were mapped at that instant; it does not prove they are a
/// live object of the expected type, and the game may free or overwrite the region on
/// another thread immediately afterwards. Treat the value as a sample, not a borrow.
///
/// The `unsafe` marker is therefore about interpretation, not memory validity, and is
/// retained rather than removed only because dropping it would change the signature of
/// a function every DLL in this workspace calls.
pub unsafe fn safe_read_u8(addr: usize) -> Option<u8> {
    let mut value: u8 = 0;
    let mut read: usize = ZERO;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut u8 as *mut c_void,
            core::mem::size_of::<u8>(),
            &mut read,
        )
    };
    if ok != RPM_FALSE && read == core::mem::size_of::<u8>() {
        Some(value)
    } else {
        None
    }
}

/// Fault-tolerant bulk read into `out`. Returns true only if the whole slice was
/// read (None-equivalent for byte buffers). Used by the `.text` AOB scanner so a
/// drifted/unmapped region fails closed instead of faulting.
///
/// # Safety
///
/// `addr` has NO precondition -- see [`safe_read_usize`]; the read is performed by
/// `ReadProcessMemory` and fails closed on an unmapped range instead of faulting. This
/// is what lets the `.text` AOB scanner walk a drifted or partially-unmapped image
/// without crashing the game.
///
/// `out` is an ordinary Rust slice and is only written on a fully successful read, so a
/// `false` return leaves its contents unspecified but initialised. The caller owns the
/// meaning of the bytes, exactly as above.
pub unsafe fn read_bytes(addr: usize, out: &mut [u8]) -> bool {
    if out.is_empty() {
        return true;
    }
    let mut read: usize = ZERO;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            out.as_mut_ptr() as *mut c_void,
            out.len(),
            &mut read,
        )
    };
    ok != RPM_FALSE && read == out.len()
}

/// Resolve the running game image's `.text` section as `(start_va, len)` by parsing
/// the in-memory PE headers. Returns `None` if the headers are unreadable or no
/// `.text` section is found. This is the bound for a fault-safe AOB scan; it makes
/// signature-based function discovery version-agnostic (no hardcoded RVAs).
pub fn module_text_range() -> Option<(usize, usize)> {
    let base = game_module_base().ok()?;
    unsafe {
        // DOS header: e_lfanew (u32) at +0x3C -> PE header offset.
        let mut w4 = [0u8; 4];
        if !read_bytes(base + 0x3C, &mut w4) {
            return None;
        }
        let pe = base + u32::from_le_bytes(w4) as usize;
        let mut sig = [0u8; 4];
        if !read_bytes(pe, &mut sig) || &sig != b"PE\0\0" {
            return None;
        }
        // COFF file header at pe+4: NumberOfSections (u16) at +2, SizeOfOptionalHeader (u16) at +16.
        let mut nsec = [0u8; 2];
        let mut optsz = [0u8; 2];
        if !read_bytes(pe + 6, &mut nsec) || !read_bytes(pe + 20, &mut optsz) {
            return None;
        }
        let num_sections = u16::from_le_bytes(nsec) as usize;
        let opt_size = u16::from_le_bytes(optsz) as usize;
        // Section headers (40 bytes each) begin after the optional header.
        let mut sec = pe + 24 + opt_size;
        for _ in 0..num_sections.min(96) {
            let mut hdr = [0u8; 40];
            if !read_bytes(sec, &mut hdr) {
                return None;
            }
            // name[0..8], VirtualSize[8..12], VirtualAddress[12..16].
            if &hdr[0..8] == b".text\0\0\0" {
                let vsize = u32::from_le_bytes([hdr[8], hdr[9], hdr[10], hdr[11]]) as usize;
                let vaddr = u32::from_le_bytes([hdr[12], hdr[13], hdr[14], hdr[15]]) as usize;
                if vaddr == 0 || vsize == 0 {
                    return None;
                }
                return Some((base + vaddr, vsize));
            }
            sec += 40;
        }
        None
    }
}

/// Fault-tolerant u16 read (None on unmapped memory).
///
/// # Safety
///
/// `addr` has NO precondition: any value, including 0, a freed pointer, or a wholly
/// unmapped address, is safe to pass. The read goes through `ReadProcessMemory`, which
/// validates the range in the kernel and returns `FALSE` rather than raising an access
/// violation, so this function cannot fault on a bad address -- that fault-tolerance is
/// the entire reason it exists.
///
/// What the CALLER owns is the meaning of the bytes that come back. A successful read
/// only proves those bytes were mapped at that instant; it does not prove they are a
/// live object of the expected type, and the game may free or overwrite the region on
/// another thread immediately afterwards. Treat the value as a sample, not a borrow.
///
/// The `unsafe` marker is therefore about interpretation, not memory validity, and is
/// retained rather than removed only because dropping it would change the signature of
/// a function every DLL in this workspace calls.
pub unsafe fn safe_read_u16(addr: usize) -> Option<u16> {
    let mut value: u16 = 0;
    let mut read: usize = ZERO;
    let ok = unsafe {
        ReadProcessMemory(
            CURRENT_PROCESS_PSEUDO_HANDLE,
            addr as *const c_void,
            &mut value as *mut u16 as *mut c_void,
            core::mem::size_of::<u16>(),
            &mut read,
        )
    };
    if ok != RPM_FALSE && read == core::mem::size_of::<u16>() {
        Some(value)
    } else {
        None
    }
}

/// Page granularity for [`safe_read_cstr`]'s walk. Mapping is per-page, so a page is either
/// readable in full or not at all -- which is what makes a page-bounded chunk the largest read
/// that cannot fail merely because of where the string happens to sit.
const PAGE_SIZE: usize = 0x1000;

/// Fault-safe, LENGTH-BOUNDED read of a NUL-terminated C string.
///
/// This exists because `CStr::from_ptr` on a pointer that came from outside our own code is a
/// crash waiting for the right afternoon: it calls `strlen`, `strlen` dereferences, and a
/// non-null-but-garbage pointer takes the process down. A null check does not help -- the
/// pointers that actually killed Elden Ring on two testers on 2026-08-23 were
/// `0x011000010e05acda` and `0x0110000107be5e2c`, both very much non-null (bd
/// `ersc-steam-garbage-key-ptr-crashes-lobby-publish-2026-08-24`).
///
/// Returns the bytes BEFORE the NUL, or `None` if the string is unreadable, if `addr` is null,
/// or if no NUL appears within `max_len`. That last case is deliberately a failure and not a
/// truncation: a readable region with no terminator in range is not a string we have any reason
/// to trust, and silently returning `max_len` bytes of it would launder junk into a value the
/// caller goes on to use.
///
/// # Safety
///
/// `addr` has NO precondition -- see [`safe_read_usize`]. Every read goes through
/// `ReadProcessMemory`, which fails closed on an unmapped page instead of faulting. The reads
/// are page-bounded so a legitimate string ending in the last mapped page of a region is not
/// rejected just because the next page is absent.
///
/// The caller still owns the MEANING of the bytes: a successful read proves only that they were
/// mapped at that instant.
pub unsafe fn safe_read_cstr(addr: usize, max_len: usize) -> Option<Vec<u8>> {
    cstr_walk(addr, max_len, &mut |at, out| unsafe { read_bytes(at, out) })
}

/// The page-bounded walk behind [`safe_read_cstr`], with the actual read injected.
///
/// Split out for one reason: `ReadProcessMemory` does not exist on the host, so a test that
/// called [`safe_read_cstr`] would not link, and the guard would ship with its logic unexercised
/// -- which is how the bug it exists to prevent got out in the first place. `read` stands in for
/// the kernel: it returns `false` for a range that is not fully readable, exactly as
/// [`read_bytes`] does.
fn cstr_walk(
    addr: usize,
    max_len: usize,
    read: &mut dyn FnMut(usize, &mut [u8]) -> bool,
) -> Option<Vec<u8>> {
    if addr == ZERO || max_len == ZERO {
        return None;
    }
    let mut out: Vec<u8> = Vec::new();
    let mut cursor = addr;
    while out.len() < max_len {
        // Never let one read span a page boundary. `ReadProcessMemory` is all-or-nothing, so a
        // read that ran on into an unmapped neighbouring page would report failure for a string
        // that is entirely present in the page it started in.
        let to_page_end = PAGE_SIZE - (cursor & (PAGE_SIZE - 1));
        let want = to_page_end.min(max_len - out.len());
        let mut chunk = vec![0u8; want];
        if !read(cursor, &mut chunk) {
            return None;
        }
        if let Some(nul) = chunk.iter().position(|&byte| byte == 0) {
            out.extend_from_slice(&chunk[..nul]);
            return Some(out);
        }
        out.extend_from_slice(&chunk);
        cursor = cursor.checked_add(want)?;
    }
    None
}

#[cfg(test)]
mod cstr_tests {
    use super::{PAGE_SIZE, cstr_walk};

    /// The two pointers that actually took Elden Ring down on 2026-08-23. Both non-null, which is
    /// the entire point: the guard they defeated was a null check.
    const CRASH_POINTERS: [usize; 2] = [0x0110_0001_0e05_acda, 0x0110_0001_07be_5e2c];

    /// A reader that models ONE mapped page at `base` whose contents start with `bytes`;
    /// everything outside that page is unmapped. The page is a full [`PAGE_SIZE`] because that is
    /// what mapping granularity means -- a stub page shorter than that would reject the
    /// page-bounded chunk the walk legitimately asks for, and test the stub rather than the code.
    fn one_page(base: usize, bytes: &[u8]) -> impl FnMut(usize, &mut [u8]) -> bool {
        let mut page = vec![0xffu8; PAGE_SIZE];
        page[..bytes.len()].copy_from_slice(bytes);
        move |at, out| {
            let Some(start) = at.checked_sub(base) else {
                return false;
            };
            let Some(end) = start.checked_add(out.len()) else {
                return false;
            };
            if end > page.len() {
                return false;
            }
            out.copy_from_slice(&page[start..end]);
            true
        }
    }

    #[test]
    fn an_unreadable_pointer_is_refused_rather_than_dereferenced() {
        for bad in CRASH_POINTERS {
            let mut never = |_: usize, _: &mut [u8]| false;
            assert_eq!(
                cstr_walk(bad, 255, &mut never),
                None,
                "a garbage non-null pointer must fail closed, not walk memory"
            );
        }
    }

    #[test]
    fn null_is_refused_before_any_read_is_attempted() {
        let mut reads = 0usize;
        let mut counting = |_: usize, _: &mut [u8]| {
            reads += 1;
            true
        };
        assert_eq!(cstr_walk(0, 255, &mut counting), None);
        assert_eq!(reads, 0, "null must short-circuit, not reach the reader");
    }

    #[test]
    fn a_terminated_string_comes_back_without_its_nul() {
        let page = b"lobby_key\0trailing junk".to_vec();
        let mut read = one_page(0x1_0000, &page);
        assert_eq!(
            cstr_walk(0x1_0000, 255, &mut read).as_deref(),
            Some(&b"lobby_key"[..])
        );
    }

    #[test]
    fn a_run_with_no_terminator_in_range_is_refused_not_truncated() {
        // one_page pads with 0xff, so nothing in range is a NUL.
        let mut read = one_page(0x1_0000, &[b'A'; 512]);
        assert_eq!(
            cstr_walk(0x1_0000, 64, &mut read),
            None,
            "no NUL within max_len is junk, and truncating it would launder junk into a value"
        );
    }

    #[test]
    fn a_string_at_the_end_of_a_mapped_page_survives_the_unmapped_neighbour() {
        // The string sits in the last bytes of the page, so a read that ran past the page end
        // would fail. Page-bounded chunking is what keeps this case readable.
        let tail = b"lobby_key\0";
        let mut page = vec![b'.'; PAGE_SIZE];
        page[PAGE_SIZE - tail.len()..].copy_from_slice(tail);
        let base = 0x1_0000;
        let mut read = one_page(base, &page);
        let at = base + PAGE_SIZE - tail.len();
        assert_eq!(
            cstr_walk(at, 255, &mut read).as_deref(),
            Some(&b"lobby_key"[..])
        );
    }
}
