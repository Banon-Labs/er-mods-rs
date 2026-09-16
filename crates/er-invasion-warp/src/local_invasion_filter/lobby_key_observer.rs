//! Watch Seamless derive the lobby key, without touching it.
//!
//! The one string that decides whether two Seamless players can see each other at all. It is read
//! only: the observer reports the value and never writes one, because a key this process invents
//! removes the player from every other player's matchmaking pool rather than joining them to it.
//!
//! Split out of `local_invasion_filter` because it is independently failable -- a Seamless build
//! that moved the builder should cost the comparison and nothing else -- and because the parent
//! crossed this repo's hard Rust file-size limit.

use super::*;

/// Trampoline for the lobby-key observer.
static ORIG_BUILD_LOBBY_KEY: AtomicUsize = AtomicUsize::new(0);
/// One-shot latch for the `ctx`-shape probe above.
static CTX_SHAPE_PROBED: AtomicBool = AtomicBool::new(false);
/// Whether the lobby-key observer is installed.
static LOBBY_KEY_HOOK_INSTALLED: AtomicUsize = AtomicUsize::new(0);
/// FNV-1a of the last key reported, so a re-key is one line and a steady key is silent.
static LAST_LOBBY_KEY_HASH: AtomicUsize = AtomicUsize::new(0);
/// How many times the key has been derived, and how many distinct values were seen.
static LOBBY_KEY_DERIVATIONS: AtomicUsize = AtomicUsize::new(0);
static LOBBY_KEY_CHANGES: AtomicUsize = AtomicUsize::new(0);

/// Read an MSVC `std::string` as ASCII, or `None` if it is not the shape we expect.
///
/// Every read is fault-closed. The value is [`ersc::LOBBY_KEY_HEX_LEN`] characters, far past what
/// the inline buffer holds, so the heap branch is the only one that can carry it -- but the inline
/// branch is handled anyway rather than assumed away, because an assumption here would silently
/// print nothing on a build whose string differs.
#[cfg(windows)]
fn read_std_string(at: usize) -> Option<String> {
    let size = unsafe { er_game_base::mem::safe_read_usize(at + ersc::STD_STRING_SIZE_OFFSET) }?;
    let capacity =
        unsafe { er_game_base::mem::safe_read_usize(at + ersc::STD_STRING_CAPACITY_OFFSET) }?;
    // A key is 16 characters. Anything wildly longer is not the string this was written for, and
    // reading it would be a walk through memory on a guess.
    // A SHA-256 hex digest. Anything else is not the string this was written for, and reading it
    // would be a walk through memory on a guess.
    if size != ersc::LOBBY_KEY_HEX_LEN || capacity < size {
        return None;
    }
    let data = if capacity >= ersc::STD_STRING_HEAP_CAPACITY {
        unsafe { er_game_base::mem::safe_read_usize(at) }?
    } else {
        at
    };
    let mut out = String::with_capacity(size);
    for index in 0..size {
        let byte = unsafe { er_game_base::mem::safe_read_u8(data + index) }?;
        // Printable ASCII only: the value is hex digits, and refusing anything else keeps a wrong
        // pointer from spraying control bytes into the log.
        if !(0x20..0x7f).contains(&byte) {
            return None;
        }
        out.push(char::from(byte));
    }
    Some(out)
}

/// `BuildLobbyKey(ctx, out)` -- observed, never altered.
///
/// Runs the original first, then reads the string it produced. Reading before the call would see
/// an uninitialised buffer; reading after is the only ordering that can work, and it also means a
/// fault in our read cannot affect what Seamless publishes.
#[cfg(windows)]
unsafe extern "system" fn build_lobby_key_observer(
    ctx: usize,
    out: usize,
    c: usize,
    d: usize,
) -> usize {
    // Stamp the thread, so the owner id in `report_lock_preconditions` has a name to resolve to
    // rather than staying a bare number.
    let _scope = enter_handler(HANDLER_ERSC_LOBBY_KEY);
    let _ersc = enter_ersc_callback();
    let orig = ORIG_BUILD_LOBBY_KEY.load(Ordering::SeqCst);
    if orig == 0 {
        return 0;
    }
    // SAFETY: the trampoline MinHook produced for a byte-verified prologue; same four-argument
    // shape the union dispatcher uses everywhere else in this module.
    let result = unsafe { core::mem::transmute::<usize, ErscActionFn>(orig)(ctx, out, c, d) };

    LOBBY_KEY_DERIVATIONS.fetch_add(1, Ordering::SeqCst);

    // Can this detour replace the `show` one? That is the whole question keeping the
    // local-invasion filter alive, so it is asked here rather than argued about.
    //
    // `show` is the only thing this DLL patches that faults -- armed alone it dies at
    // 0x140010043 in ~25s, while this detour armed alone ran clean. But `show` is currently the
    // only source of `OSM`, and `resolve_session` needs `OSM` solely to reach
    // `[OSM + NEXT_OBJECT_OFFSET]`, the session. The session is self-identifying: `read_session_state`
    // returns `Some` only for a known state code at a known offset. So any pointer that reaches it
    // is as good as `OSM`, and this detour's first argument is a candidate nobody has tested.
    //
    // Two shapes are checked, once, and only reported: `ctx` being the session itself, and `ctx`
    // standing where `OSM` stands (session one hop away). A hit means the filter can be rebuilt on
    // a detour that does not crash; a miss rules this route out instead of leaving it as a hope.
    if !CTX_SHAPE_PROBED.swap(true, Ordering::SeqCst)
        && let Some(abi) = resolve_ersc_abi()
    {
        let direct = read_session_state(abi, ctx);
        let hop = unsafe { er_game_base::mem::safe_read_usize(ctx + ersc::NEXT_OBJECT_OFFSET) }
            .filter(|next| *next != 0)
            .and_then(|next| read_session_state(abi, next).map(|state| (next, state)));
        crate::standalone_log(format_args!(
            "local-invasion: lobby-key ctx=0x{ctx:x} -- is it the session? direct_state={direct:?}              one_hop={hop:?}. If either is Some, the filter can resolve its session WITHOUT the              `show` detour, which is the hook that crashes the game at 0x140010043 in ~25s. If              both are None this route is dead and the session must be found another way."
        ));
    }
    if let Some(key) = read_std_string(out) {
        let hash = fnv1a64(key.as_bytes()) as usize;
        if LAST_LOBBY_KEY_HASH.swap(hash, Ordering::SeqCst) != hash {
            let changes = LOBBY_KEY_CHANGES.fetch_add(1, Ordering::SeqCst) + 1;
            crate::standalone_log(format_args!(
                "local-invasion: LOBBY KEY = {key} (derivation #{}, distinct value \
                 #{changes}). ONE key serves both the lobby search filter and the publish, so \
                 whatever it partitions applies to co-op and invasions alike -- there is no \
                 invasion-only key in readable code. Two players whose keys differ never see each \
                 other; compare this line with your friend's. Observed only; nothing here \
                 publishes or alters a key.",
                LOBBY_KEY_DERIVATIONS.load(Ordering::SeqCst)
            ));
        }
    } else if LOBBY_KEY_DERIVATIONS.load(Ordering::SeqCst) == 1 {
        // Say what was actually there. "Could not read it" invites a guess; the length and
        // capacity say immediately whether the layout moved or the digest size changed.
        let size =
            unsafe { er_game_base::mem::safe_read_usize(out + ersc::STD_STRING_SIZE_OFFSET) };
        let capacity =
            unsafe { er_game_base::mem::safe_read_usize(out + ersc::STD_STRING_CAPACITY_OFFSET) };
        crate::standalone_log(format_args!(
            "local-invasion: the lobby key was derived but did not read back as {} hex characters \
             (size={size:?} capacity={capacity:?}) -- the std::string this build's lobby-key \
             builder wrote is not the shape expected, so the comparison is UNAVAILABLE rather \
             than wrong. Do not treat a missing line as 'the key did not change'.",
            ersc::LOBBY_KEY_HEX_LEN,
        ));
    }
    result
}

/// Install the lobby-key observer. Idempotent; returns 1 on success.
///
/// Separate from the `show` observer because it can fail independently: a Seamless build that moved
/// this function should cost the comparison, not the filter.
#[cfg(windows)]
pub(super) fn install_lobby_key_observer() -> usize {
    if LOBBY_KEY_HOOK_INSTALLED.load(Ordering::SeqCst) != 0 {
        return 0;
    }
    let Some(base) = ersc_module_base() else {
        return 0; // Seamless not loaded yet -- retry next tick
    };
    let Some(abi) = resolve_ersc_abi() else {
        // `resolve_ersc_abi` already said, once, which builds are known and that none matched.
        LOBBY_KEY_HOOK_INSTALLED.store(1, Ordering::SeqCst);
        return 0;
    };
    let address = base + abi.build_lobby_key_rva;
    if !prologue_matches(address, abi.build_lobby_key_prologue) {
        if LOBBY_KEY_HOOK_INSTALLED.swap(1, Ordering::SeqCst) == 0 {
            crate::standalone_log(format_args!(
                "local-invasion: ersc.dll @0x{base:x} was recognised as Seamless Co-op v{} but does not carry \
                 that build's lobby-key builder at ersc+0x{:x} -- NOT touching it. The lobby-key \
                 comparison is unavailable; everything else is unaffected.",
                abi.version, abi.build_lobby_key_rva,
            ));
        }
        return 0;
    }
    if LOBBY_KEY_HOOK_INSTALLED.swap(1, Ordering::SeqCst) != 0 {
        return 0;
    }
    match unsafe {
        er_hook::register_union_hook(
            address,
            build_lobby_key_observer as er_hook::UnionFn,
            &ORIG_BUILD_LOBBY_KEY,
        )
    } {
        Ok(()) => {
            crate::standalone_log(format_args!(
                "local-invasion: observing ersc lobby-key builder @0x{address:x} (read-only). It \
                 reports the one string that decides whether two Seamless players can see each \
                 other at all."
            ));
            1
        }
        Err(error) => {
            crate::standalone_log(format_args!(
                "local-invasion: could not observe the ersc lobby-key builder: {error:?}"
            ));
            0
        }
    }
}
