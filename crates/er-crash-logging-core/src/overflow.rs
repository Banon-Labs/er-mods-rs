//! The call chain of a thread that ran out of stack, read by a thread that has not.
//!
//! A stack overflow is reported on the thread that overflowed, which has a few KB left at most, so
//! the fault record used to stop at the registers and write
//! `stack_note=stack-overflow; skipped stack scan/backtrace`. That threw away the one thing an
//! overflow is good evidence of: the frames are all still there, in the thread's own stack memory
//! between `rsp` and the stack base, and a loop shows up as the same few return addresses repeated
//! hundreds of times. Measured on a user's machine 2026-10-26: `STATUS_STACK_OVERFLOW` in
//! `nvwgf2umx.dll` 1.4 s after start on every launch, only with `er_invasion_warp.dll` loaded, and
//! the record could not say which modules were on the stack.
//!
//! So the overflowing thread does no scanning. It stores its `rsp` and stack base, wakes a helper
//! thread started at install, and waits on an event until the helper has walked the stack on its own
//! stack and left the formatted lines behind. Unparking and the wait each cost a few hundred bytes;
//! the walk costs whatever it costs, somewhere else. If the helper does not answer within
//! `HANDOFF_TIMEOUT_MS`, the record says so and goes on without it.

/// Frames listed from the innermost end of the stack: what was running when it ran out.
pub(crate) const INNER_FRAMES: usize = 48;
/// Frames listed from the outermost end: what started the thread.
pub(crate) const OUTER_FRAMES: usize = 24;
/// Distinct return addresses listed by how often they occur. A recursion loop is the top of this.
pub(crate) const HOT_FRAMES: usize = 16;

/// The lines a stack overflow record carries, from return addresses listed innermost first.
///
/// `frames` holds one label per return address found on the stack, nearest `rsp` first. Repeats are
/// kept: the counts are the evidence.
pub(crate) fn summarize(frames: &[String], stack_bytes: usize, stack_qwords: usize) -> String {
    use std::fmt::Write as _;

    let mut out = String::new();
    let _ = writeln!(
        out,
        "overflow_stack=span=0x{stack_bytes:x} qwords={stack_qwords} return_addresses={}",
        frames.len()
    );
    let _ = writeln!(
        out,
        "overflow_inner=[{}]",
        collapse_runs(frames.iter().take(INNER_FRAMES)).join(",")
    );
    let outer_start = frames.len().saturating_sub(OUTER_FRAMES);
    let _ = writeln!(
        out,
        "overflow_outer=[{}]",
        collapse_runs(frames[outer_start..].iter()).join(",")
    );
    let _ = writeln!(out, "overflow_hot=[{}]", hottest(frames).join(","));
    out
}

/// Consecutive identical labels folded into one, with `x<n>` when there was more than one.
fn collapse_runs<'a>(frames: impl Iterator<Item = &'a String>) -> Vec<String> {
    let mut out: Vec<(String, usize)> = Vec::new();
    for frame in frames {
        match out.last_mut() {
            Some((last, count)) if last == frame => *count += 1,
            _ => out.push((frame.clone(), 1)),
        }
    }
    out.into_iter()
        .map(|(frame, count)| {
            if count > 1 {
                format!("{frame}x{count}")
            } else {
                frame
            }
        })
        .collect()
}

/// The most frequent labels, most frequent first, ties in order of first appearance.
fn hottest(frames: &[String]) -> Vec<String> {
    let mut counts: Vec<(&String, usize)> = Vec::new();
    for frame in frames {
        match counts.iter_mut().find(|(seen, _)| *seen == frame) {
            Some((_, count)) => *count += 1,
            None => counts.push((frame, 1)),
        }
    }
    // A stable sort keeps first-appearance order among equal counts.
    counts.sort_by_key(|entry| std::cmp::Reverse(entry.1));
    counts
        .into_iter()
        .take(HOT_FRAMES)
        .map(|(frame, count)| format!("{frame}x{count}"))
        .collect()
}

/// True when the bytes just before a return address end in a `call`.
///
/// `before[i]` is the byte at `return_address - 8 + i`, so the last byte sits immediately before
/// the return address. A stack holds plenty of pointers into code that are not return addresses --
/// function pointers, vtable entries spilled to locals -- and counting those would bury a loop
/// under noise.
pub(crate) fn follows_call(before: &[u8; 8]) -> bool {
    let at = |back: usize| before[8 - back];
    // `call rel32`: E8 xx xx xx xx.
    if at(5) == 0xe8 {
        return true;
    }
    // `call r/m64`: FF /2, two to seven bytes long with any modrm, sib and displacement, plus an
    // optional REX prefix ahead of the FF.
    (2..=7).any(|back| at(back) == 0xff && (at(back - 1) >> 3) & 7 == 2)
}

#[cfg(windows)]
pub(crate) use windows::{request, start};

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;
    use std::sync::Mutex;
    use std::sync::OnceLock;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread::Thread;

    use super::super::{
        CURRENT_PROCESS, MIN_VALID_PTR, ReadProcessMemory, loaded_modules, module_for_addr,
    };

    /// How long the overflowing thread waits for the helper before writing its record without it.
    const HANDOFF_TIMEOUT_MS: u32 = 5_000;
    /// Stack bytes the helper reads at most, from `rsp` up. Larger than any thread stack this game
    /// creates; the cap only bounds a garbage `rsp`/base pair.
    const MAX_STACK_BYTES: usize = 16 * 1024 * 1024;
    /// Bytes per `ReadProcessMemory` call while copying the stack.
    const CHUNK_BYTES: usize = 64 * 1024;

    const IDLE: usize = 0;
    const CLAIMED: usize = 1;
    const REQUESTED: usize = 2;
    const DONE: usize = 3;

    static STATE: AtomicUsize = AtomicUsize::new(IDLE);
    static REQUEST_RSP: AtomicUsize = AtomicUsize::new(0);
    static REQUEST_BASE: AtomicUsize = AtomicUsize::new(0);
    static RESULT: Mutex<String> = Mutex::new(String::new());
    static HELPER: OnceLock<Thread> = OnceLock::new();
    /// Auto-reset event the helper signals when its lines are in `RESULT`. A kernel wait costs the
    /// overflowing thread one call frame, where a `Thread` handle would mean touching its TLS.
    static DONE_EVENT: AtomicUsize = AtomicUsize::new(0);

    const WAIT_OBJECT_0: u32 = 0;

    unsafe extern "system" {
        fn CreateEventW(
            attributes: *mut c_void,
            manual_reset: i32,
            initial_state: i32,
            name: *const u16,
        ) -> isize;
        fn SetEvent(event: isize) -> i32;
        fn WaitForSingleObject(handle: isize, milliseconds: u32) -> u32;
    }

    /// Start the helper. Called once, from `install`, while there is stack to spare.
    pub(crate) fn start() {
        let event = unsafe { CreateEventW(std::ptr::null_mut(), 0, 0, std::ptr::null()) };
        if event == 0 {
            return;
        }
        DONE_EVENT.store(event as usize, Ordering::SeqCst);
        let _ = std::thread::Builder::new()
            .name("er-crash-overflow-scribe".to_owned())
            .spawn(|| {
                let _ = HELPER.set(std::thread::current());
                loop {
                    std::thread::park();
                    if STATE.load(Ordering::SeqCst) != REQUESTED {
                        continue;
                    }
                    let rsp = REQUEST_RSP.load(Ordering::SeqCst);
                    let base = REQUEST_BASE.load(Ordering::SeqCst);
                    let lines = describe(rsp, base);
                    if let Ok(mut slot) = RESULT.lock() {
                        *slot = lines;
                    }
                    STATE.store(DONE, Ordering::SeqCst);
                    unsafe { SetEvent(DONE_EVENT.load(Ordering::SeqCst) as isize) };
                }
            });
    }

    /// The overflowing thread's half: hand off `rsp` and this thread's stack base, wait, and
    /// return the helper's lines. `None` when there is no helper, another request is in flight,
    /// or the helper did not answer in time.
    pub(crate) fn request(rsp: usize) -> Option<String> {
        let helper = HELPER.get()?;
        STATE
            .compare_exchange(IDLE, CLAIMED, Ordering::SeqCst, Ordering::SeqCst)
            .ok()?;
        REQUEST_RSP.store(rsp, Ordering::SeqCst);
        REQUEST_BASE.store(current_stack_base(), Ordering::SeqCst);
        STATE.store(REQUESTED, Ordering::SeqCst);
        helper.unpark();
        let event = DONE_EVENT.load(Ordering::SeqCst) as isize;
        let signalled = unsafe { WaitForSingleObject(event, HANDOFF_TIMEOUT_MS) } == WAIT_OBJECT_0;
        if !signalled || STATE.load(Ordering::SeqCst) != DONE {
            // Left in REQUESTED: a helper that finishes late must not hand its lines to the next
            // request, and an overflow is the end of the process anyway.
            return None;
        }
        let lines = RESULT
            .lock()
            .map(|mut slot| std::mem::take(&mut *slot))
            .ok();
        STATE.store(IDLE, Ordering::SeqCst);
        lines
    }

    /// `NT_TIB.StackBase` of the calling thread: the highest address of its stack.
    #[inline(always)]
    fn current_stack_base() -> usize {
        let base: usize;
        unsafe {
            core::arch::asm!(
                "mov {base}, gs:[0x8]",
                base = out(reg) base,
                options(nostack, preserves_flags),
            );
        }
        base
    }

    /// Runs on the helper: copy the stack, keep the qwords that are return addresses into a loaded
    /// module, and format them.
    fn describe(rsp: usize, base: usize) -> String {
        if rsp < MIN_VALID_PTR || base <= rsp {
            return format!("overflow_stack=unreadable rsp=0x{rsp:x} base=0x{base:x}\n");
        }
        let bytes = (base - rsp).min(MAX_STACK_BYTES);
        let words = copy_words(rsp, bytes);
        let modules = loaded_modules();
        let mut frames = Vec::new();
        for value in words.iter().copied() {
            if value < MIN_VALID_PTR {
                continue;
            }
            // A return address outside every module is kept when its page is executable: hook
            // trampolines and relays live in anonymous allocations, and a loop between two hooks
            // passes through them.
            let label = match module_for_addr(value, &modules) {
                Some((name, offset)) => format!("{name}+0x{offset:x}"),
                None if is_executable(value) => format!("0x{value:x}"),
                None => continue,
            };
            let Some(before) = read_eight(value.wrapping_sub(8)) else {
                continue;
            };
            if super::follows_call(&before) {
                frames.push(label);
            }
        }
        let mut out = super::summarize(&frames, bytes, words.len());
        out.push_str(&format!("overflow_stack_range=0x{rsp:x}..0x{base:x}\n"));
        out
    }

    /// The qwords from `start` up, read in chunks; a chunk that fails to read ends the copy.
    fn copy_words(start: usize, bytes: usize) -> Vec<usize> {
        let mut words = Vec::with_capacity(bytes / 8);
        let mut chunk = vec![0usize; CHUNK_BYTES / 8];
        let mut at = start;
        let end = start + bytes;
        while at < end {
            let want = (end - at).min(CHUNK_BYTES) / 8 * 8;
            if want == 0 {
                break;
            }
            let mut read = 0usize;
            let ok = unsafe {
                ReadProcessMemory(
                    CURRENT_PROCESS,
                    at as *const _,
                    chunk.as_mut_ptr().cast(),
                    want,
                    &mut read,
                )
            };
            if ok == 0 || read != want {
                break;
            }
            words.extend_from_slice(&chunk[..want / 8]);
            at += want;
        }
        words
    }

    /// `MEMORY_BASIC_INFORMATION` on x64, as far as `Protect`.
    #[repr(C)]
    struct MemoryBasicInformation {
        base_address: usize,
        allocation_base: usize,
        allocation_protect: u32,
        partition_id: u16,
        region_size: usize,
        state: u32,
        protect: u32,
        kind: u32,
    }

    const MEM_COMMIT: u32 = 0x1000;
    /// `PAGE_EXECUTE`, `PAGE_EXECUTE_READ`, `PAGE_EXECUTE_READWRITE`, `PAGE_EXECUTE_WRITECOPY`.
    const PAGE_EXECUTE_ANY: u32 = 0x10 | 0x20 | 0x40 | 0x80;

    unsafe extern "system" {
        fn VirtualQuery(
            address: *const c_void,
            buffer: *mut MemoryBasicInformation,
            length: usize,
        ) -> usize;
    }

    fn is_executable(addr: usize) -> bool {
        let mut info = std::mem::MaybeUninit::<MemoryBasicInformation>::zeroed();
        let written = unsafe {
            VirtualQuery(
                addr as *const c_void,
                info.as_mut_ptr(),
                std::mem::size_of::<MemoryBasicInformation>(),
            )
        };
        if written == 0 {
            return false;
        }
        let info = unsafe { info.assume_init() };
        info.state == MEM_COMMIT && info.protect & PAGE_EXECUTE_ANY != 0
    }

    fn read_eight(addr: usize) -> Option<[u8; 8]> {
        let mut out = [0u8; 8];
        let mut read = 0usize;
        let ok = unsafe {
            ReadProcessMemory(
                CURRENT_PROCESS,
                addr as *const _,
                out.as_mut_ptr().cast(),
                out.len(),
                &mut read,
            )
        };
        (ok != 0 && read == out.len()).then_some(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn labels(names: &[&str]) -> Vec<String> {
        names.iter().map(|name| (*name).to_owned()).collect()
    }

    #[test]
    fn a_recursion_loop_tops_the_hot_list() {
        let mut frames = labels(&["nvwgf2umx.dll+0x9cec7c"]);
        for _ in 0..300 {
            frames.extend(labels(&["a.dll+0x10", "b.dll+0x20"]));
        }
        frames.extend(labels(&["kernel32.dll+0x1234", "ntdll.dll+0x5678"]));
        let text = summarize(&frames, 0x10_0000, 0x2_0000);
        assert!(text.contains("overflow_hot=[a.dll+0x10x300,b.dll+0x20x300,"));
        assert!(text.contains("kernel32.dll+0x1234,ntdll.dll+0x5678]"));
        assert!(text.contains("return_addresses=603"));
    }

    #[test]
    fn consecutive_repeats_fold_into_one_entry() {
        let frames = labels(&["x.dll+0x1", "x.dll+0x1", "x.dll+0x1", "y.dll+0x2"]);
        let text = summarize(&frames, 0x100, 0x20);
        assert!(text.contains("overflow_inner=[x.dll+0x1x3,y.dll+0x2]"));
    }

    #[test]
    fn an_empty_stack_still_writes_every_line() {
        let text = summarize(&[], 0, 0);
        assert!(text.contains("overflow_inner=[]"));
        assert!(text.contains("overflow_outer=[]"));
        assert!(text.contains("overflow_hot=[]"));
    }

    #[test]
    fn call_encodings_are_recognised() {
        // call rel32
        assert!(follows_call(&[0, 0, 0, 0xe8, 1, 2, 3, 4]));
        // call rax (FF D0)
        assert!(follows_call(&[0, 0, 0, 0, 0, 0, 0xff, 0xd0]));
        // call qword ptr [rip+disp32] (FF 15 disp32)
        assert!(follows_call(&[0, 0, 0xff, 0x15, 1, 2, 3, 4]));
        // call qword ptr [rax+0xb8] (FF 90 disp32)
        assert!(follows_call(&[0, 0, 0xff, 0x90, 0xb8, 0, 0, 0]));
        // call r8 (41 FF D0)
        assert!(follows_call(&[0, 0, 0, 0, 0, 0x41, 0xff, 0xd0]));
        // jmp rax (FF E0) is not a call
        assert!(!follows_call(&[0, 0, 0, 0, 0, 0, 0xff, 0xe0]));
        assert!(!follows_call(&[0x90; 8]));
    }
}
