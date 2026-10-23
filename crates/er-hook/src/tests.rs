use super::*;

/// A page operation, recorded rather than performed.
#[derive(Debug, PartialEq, Eq)]
enum Op {
    Protect {
        addr: usize,
        len: usize,
        new_protect: u32,
    },
    Store {
        addr: usize,
        value: u8,
    },
    Flush {
        addr: usize,
        len: usize,
    },
}

/// Stands in for a real code page. `original_protect` is what it reports as the page's previous
/// protection, so a test can assert that exact value is handed back on the second call.
struct FakePage {
    ops: Vec<Op>,
    original_protect: u32,
    protect_allowed: bool,
}

impl FakePage {
    fn allowing(original_protect: u32) -> Self {
        Self {
            ops: Vec::new(),
            original_protect,
            protect_allowed: true,
        }
    }

    fn refusing() -> Self {
        Self {
            ops: Vec::new(),
            original_protect: 0,
            protect_allowed: false,
        }
    }
}

impl CodePageOps for FakePage {
    fn protect(
        &mut self,
        addr: usize,
        len: usize,
        new_protect: u32,
        old_protect: &mut u32,
    ) -> bool {
        self.ops.push(Op::Protect {
            addr,
            len,
            new_protect,
        });
        if !self.protect_allowed {
            return false;
        }
        *old_protect = self.original_protect;
        true
    }

    unsafe fn store(&mut self, addr: usize, value: u8) {
        self.ops.push(Op::Store { addr, value });
    }

    fn flush(&mut self, addr: usize, len: usize) {
        self.ops.push(Op::Flush { addr, len });
    }
}

const PAGE_EXECUTE_READ: u32 = 0x20;
const TEST_ADDR: usize = 0x1234_5678;
const TEST_BYTE: u8 = 0xcc;

/// The whole sequence, in order: unlock to RWX, store, relock, flush.
#[test]
fn writes_between_unlocking_and_relocking_then_flushes() {
    let mut page = FakePage::allowing(PAGE_EXECUTE_READ);

    let wrote = unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

    assert!(wrote);
    assert_eq!(
        page.ops,
        vec![
            Op::Protect {
                addr: TEST_ADDR,
                len: ONE_CODE_BYTE,
                new_protect: PAGE_EXECUTE_READWRITE,
            },
            Op::Store {
                addr: TEST_ADDR,
                value: TEST_BYTE,
            },
            Op::Protect {
                addr: TEST_ADDR,
                len: ONE_CODE_BYTE,
                new_protect: PAGE_EXECUTE_READ,
            },
            Op::Flush {
                addr: TEST_ADDR,
                len: ONE_CODE_BYTE,
            },
        ]
    );
}

/// The hazard: a patched page left writable-and-executable for the rest of the process. The
/// relock must name the protection the page actually had, not a guess and not RWX.
#[test]
fn does_not_leave_the_page_executable_and_writable() {
    for original in [PAGE_EXECUTE_READ, 0x02, 0x04, 0x80] {
        let mut page = FakePage::allowing(original);

        unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

        let last_protect = page
            .ops
            .iter()
            .filter_map(|op| match op {
                Op::Protect { new_protect, .. } => Some(*new_protect),
                _ => None,
            })
            .next_back()
            .expect("a protection change");
        assert_eq!(
            last_protect, original,
            "page relocked to the wrong protection"
        );
        assert_ne!(last_protect, PAGE_EXECUTE_READWRITE, "page left RWX");
    }
}

/// A refused protection change must abort before the store. Writing anyway would fault, or
/// worse, succeed on a page that was already writable and hide the refusal.
#[test]
fn refused_protection_change_writes_nothing() {
    let mut page = FakePage::refusing();

    let wrote = unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

    assert!(!wrote);
    assert_eq!(
        page.ops,
        vec![Op::Protect {
            addr: TEST_ADDR,
            len: ONE_CODE_BYTE,
            new_protect: PAGE_EXECUTE_READWRITE,
        }],
        "nothing may follow a refused VirtualProtect"
    );
}

/// What the `ONE_CODE_BYTE` doc claims: the protection, the store and the flush cover the same
/// one byte at the same address. A length that disagreed would unlock or flush a range the
/// caller never asked about.
#[test]
fn every_page_operation_covers_the_same_single_byte() {
    let mut page = FakePage::allowing(PAGE_EXECUTE_READ);

    unsafe { write_code_byte_with(&mut page, TEST_ADDR, TEST_BYTE) };

    assert_eq!(ONE_CODE_BYTE, size_of::<u8>());
    for op in &page.ops {
        let (addr, len) = match op {
            Op::Protect { addr, len, .. } | Op::Flush { addr, len } => (*addr, *len),
            Op::Store { addr, .. } => (*addr, ONE_CODE_BYTE),
        };
        assert_eq!(addr, TEST_ADDR, "{op:?} touched a different address");
        assert_eq!(len, ONE_CODE_BYTE, "{op:?} covered a different length");
    }
}

// ------------------------------------------------------------------
// Registry ownership. The recording half needs `dll_base` (a Win32 call), so these drive the
// pure decision + rendering halves and inject the offset formatter. What they pin is the
// defect from run `br-20260831-160354-2513`: a bare detour colliding with a union-owned
// address reported `already hooked by detour(s) []`.
// ------------------------------------------------------------------

/// Stand-in for `as_dll_off` that does not touch `GetModuleHandleExW`.
fn fake_off(p: usize) -> String {
    format!("dll+0x{p:x}")
}

const INCUMBENT_DETOUR: usize = 0xdef60;
const NEWCOMER_DETOUR: usize = 0xdf1d0;

#[test]
fn a_union_incumbent_is_named_rather_than_reported_as_an_empty_set() {
    let prior = [(INCUMBENT_DETOUR, HookOwner::Union)];
    assert_eq!(
        registry_verdict(
            &prior,
            NEWCOMER_DETOUR,
            HookOwner::Bare,
            MH_STATUS::MH_ERROR_ALREADY_CREATED,
        ),
        RegistryVerdict::Collision
    );
    let off: &dyn Fn(usize) -> String = &fake_off;
    assert_eq!(
        render_prior_owners(&prior, off),
        "union handler dll+0xdef60",
        "the incumbent's installer must be named, not just its offset"
    );
}

#[test]
fn a_bare_incumbent_still_renders_as_the_plain_offset() {
    let prior = [(INCUMBENT_DETOUR, HookOwner::Bare)];
    let off: &dyn Fn(usize) -> String = &fake_off;
    assert_eq!(render_prior_owners(&prior, off), "dll+0xdef60");
}

#[test]
fn one_owner_installing_twice_is_a_duplicate_not_a_collision() {
    let prior = [(INCUMBENT_DETOUR, HookOwner::Bare)];
    assert_eq!(
        registry_verdict(
            &prior,
            INCUMBENT_DETOUR,
            HookOwner::Bare,
            MH_STATUS::MH_ERROR_ALREADY_CREATED,
        ),
        RegistryVerdict::Duplicate
    );
}

#[test]
fn the_same_detour_under_a_different_installer_is_a_collision() {
    // Same function pointer, but one came through the union and one through a bare `MhHook`:
    // they are contesting the MinHook slot, so calling it a duplicate would say "nothing is
    // lost" about a case where something is.
    let prior = [(INCUMBENT_DETOUR, HookOwner::Union)];
    assert_eq!(
        registry_verdict(
            &prior,
            INCUMBENT_DETOUR,
            HookOwner::Bare,
            MH_STATUS::MH_ERROR_ALREADY_CREATED,
        ),
        RegistryVerdict::Collision
    );
}

#[test]
fn an_uncontested_first_registration_logs_nothing() {
    assert_eq!(
        registry_verdict(&[], NEWCOMER_DETOUR, HookOwner::Bare, MH_STATUS::MH_OK),
        RegistryVerdict::Fresh
    );
}

#[test]
fn already_created_with_no_recorded_owner_is_still_a_collision() {
    // MinHook knows an owner this registry never saw -- another DLL's instance, or a hook
    // installed before the log sink existed. An empty owner list now means exactly that.
    assert_eq!(
        registry_verdict(
            &[],
            NEWCOMER_DETOUR,
            HookOwner::Bare,
            MH_STATUS::MH_ERROR_ALREADY_CREATED,
        ),
        RegistryVerdict::Collision
    );
}

// ------------------------------------------------------------------
// The five-argument union. Everything below runs on the host, where `MH_CreateHook` does not
// exist, so it drives the two halves that do not need it: [`union_admission`] (the decision,
// including the arity refusal) and [`union_dispatch5`] + [`chain_append`] (the wiring and the
// call itself). What no host test can prove is the Microsoft x64 stack forwarding of the fifth
// argument -- `extern "system"` on this host is SysV, which passes five arguments entirely in
// registers. The protection against getting that wrong is not a test but the type: the
// dispatcher and the handler are both declared `fn(usize, usize, usize, usize, usize)`, so
// rustc emits the caller side, and there is no hand-written `[rsp+0x20]` anywhere to be wrong.
//
// `UNION_HEADS` is process-wide and the harness runs tests in parallel threads, so each test
// below owns a distinct slot index and its own statics.
// ------------------------------------------------------------------

/// A test handler's address, taken the way the registrar takes one: through the fn-pointer
/// type the union will call it by. A direct fn-item cast is a lint error, and rightly -- it is
/// the step at which an arity could be lost with no diagnostic at all.
fn addr_of(f: UnionFn5) -> usize {
    f as usize
}

// Deliberately not a real game address. This used to be `0x1_4092_0c90`, the Quit row
// cloner's `AddCancelButton` -- flavour, since these tests only manipulate the admission
// table and never hook anything. It was also a second literal declaration of an address
// `er-title-flow` already owns, which `scripts/check-rva-alias-drift.py` reads as two claims
// about one function. A value below the image's `.text` cannot be either.
const FAKE_TARGET: usize = 0x1_4000_0c90;
const OTHER_TARGET: usize = 0x1_4074_6e80;
const HANDLER_A: usize = 0xaaa0;
const HANDLER_B: usize = 0xbbb0;
const FAKE_TRAMPOLINE: usize = 0x7ffe_0000;

/// A table entry as `register_union_hook_resolved_with` would have left it after one
/// registration, without the `MH_CreateHook` that produced the trampoline.
fn entry_with(target: usize, arity: UnionArity, handler: usize) -> UnionEntry {
    followed_entry_with(target, target, arity, handler)
}

/// A table entry whose registrant named `entry` and whose detour went to `target`, the shape a
/// followed jump leaves.
fn followed_entry_with(
    entry: usize,
    target: usize,
    arity: UnionArity,
    handler: usize,
) -> UnionEntry {
    static SOLE_ORIG: AtomicUsize = AtomicUsize::new(0);
    let mut owned = UnionEntry {
        entry,
        target,
        trampoline: FAKE_TRAMPOLINE,
        arity,
        handlers: Vec::new(),
    };
    chain_append(&mut owned, handler, &SOLE_ORIG);
    owned
}

// -------- naming an entry and letting a locator say where the detour goes --------

/// Where the jump at `FAKE_TARGET` lands in the tests below: a relay outside the image.
const RELAY: usize = 0x1_3ff8_0fd3;

/// A locator that must not be asked: the table already owns the entry.
fn never_asked(address: usize) -> Option<usize> {
    panic!("the locator ran for 0x{address:x}, which the table already owns")
}

/// The failure this guards, as measured on 2026-10-22: this image's own detour is the jump at
/// the entry, and a locator that follows jumps would send the second registrant onto this
/// image's own relay -- a second dispatcher on one function. The table answers first.
#[test]
fn an_owned_entry_chains_without_asking_the_locator() {
    let unions = [entry_with(FAKE_TARGET, UnionArity::Four, HANDLER_A)];
    assert_eq!(
        located_admission(
            &unions,
            FAKE_TARGET,
            &never_asked,
            HANDLER_B,
            UnionArity::Four
        ),
        Some((UnionAdmission::Chain(0), FAKE_TARGET))
    );
}

/// A slot that followed a jump is still found by the entry its registrant named, and the
/// address reported is where the detour went.
#[test]
fn a_followed_slot_is_found_by_its_entry() {
    let unions = [followed_entry_with(
        FAKE_TARGET,
        RELAY,
        UnionArity::Four,
        HANDLER_A,
    )];
    assert_eq!(
        located_admission(
            &unions,
            FAKE_TARGET,
            &never_asked,
            HANDLER_B,
            UnionArity::Four
        ),
        Some((UnionAdmission::Chain(0), RELAY))
    );
    assert_eq!(
        union_admission(&unions, FAKE_TARGET, HANDLER_A, UnionArity::Four),
        UnionAdmission::AlreadyPresent,
        "the first registrant naming its entry again is a retry, not a second handler"
    );
}

/// An entry nothing owns goes where the locator says, and a locator that lands on a slot the
/// table already owns chains onto it.
#[test]
fn an_unowned_entry_goes_where_the_locator_says() {
    let to_relay = |_: usize| Some(RELAY);
    assert_eq!(
        located_admission(&[], FAKE_TARGET, &to_relay, HANDLER_A, UnionArity::Four),
        Some((UnionAdmission::Create(0), RELAY))
    );
    let relay_owned = [entry_with(RELAY, UnionArity::Four, HANDLER_A)];
    assert_eq!(
        located_admission(
            &relay_owned,
            FAKE_TARGET,
            &to_relay,
            HANDLER_B,
            UnionArity::Four
        ),
        Some((UnionAdmission::Chain(0), RELAY))
    );
}

/// A locator that refuses is a refusal, never a slot.
#[test]
fn a_refusing_locator_admits_nothing() {
    let refuse = |_: usize| None;
    assert_eq!(
        located_admission(&[], FAKE_TARGET, &refuse, HANDLER_A, UnionArity::Four),
        None
    );
}

/// The arity rule holds for an entry found through its followed slot too.
#[test]
fn an_owned_entry_at_another_arity_is_refused() {
    let unions = [followed_entry_with(
        FAKE_TARGET,
        RELAY,
        UnionArity::Seven,
        HANDLER_A,
    )];
    assert_eq!(
        located_admission(
            &unions,
            FAKE_TARGET,
            &never_asked,
            HANDLER_B,
            UnionArity::Four
        ),
        Some((UnionAdmission::ArityConflict(UnionArity::Seven), RELAY))
    );
}

#[test]
fn an_unclaimed_address_takes_the_next_slot_at_either_arity() {
    assert_eq!(
        union_admission(&[], FAKE_TARGET, HANDLER_A, UnionArity::Five),
        UnionAdmission::Create(0)
    );
    let taken = [entry_with(OTHER_TARGET, UnionArity::Four, HANDLER_B)];
    assert_eq!(
        union_admission(&taken, FAKE_TARGET, HANDLER_A, UnionArity::Five),
        UnionAdmission::Create(1),
        "a five-argument target draws from the same slot index space as a four-argument one"
    );
}

#[test]
fn a_second_handler_at_the_same_arity_chains() {
    let unions = [entry_with(FAKE_TARGET, UnionArity::Five, HANDLER_A)];
    assert_eq!(
        union_admission(&unions, FAKE_TARGET, HANDLER_B, UnionArity::Five),
        UnionAdmission::Chain(0)
    );
}

#[test]
fn the_same_five_argument_handler_registering_twice_is_a_no_op() {
    let unions = [entry_with(FAKE_TARGET, UnionArity::Five, HANDLER_A)];
    assert_eq!(
        union_admission(&unions, FAKE_TARGET, HANDLER_A, UnionArity::Five),
        UnionAdmission::AlreadyPresent,
        "appending a handler to its own chain would make it call itself"
    );
}

/// The rule this whole arity split exists to hold: one prologue, one dispatcher. A second
/// arity on a claimed address must be refused where it can still be reported, not discovered
/// as an `MH_ERROR_ALREADY_CREATED` after one of the two has already lost.
#[test]
fn a_target_claimed_at_one_arity_is_refused_at_the_other() {
    let four = [entry_with(FAKE_TARGET, UnionArity::Four, HANDLER_A)];
    assert_eq!(
        union_admission(&four, FAKE_TARGET, HANDLER_B, UnionArity::Five),
        UnionAdmission::ArityConflict(UnionArity::Four)
    );
    let five = [entry_with(FAKE_TARGET, UnionArity::Five, HANDLER_A)];
    assert_eq!(
        union_admission(&five, FAKE_TARGET, HANDLER_B, UnionArity::Four),
        UnionAdmission::ArityConflict(UnionArity::Five),
        "the refusal has to run in both directions or it is just install-order luck"
    );
}

#[test]
fn a_full_table_is_exhausted_rather_than_indexing_past_the_pool() {
    let unions: Vec<UnionEntry> = (0..MAX_UNION_SLOTS)
        .map(|i| entry_with(FAKE_TARGET + i, UnionArity::Four, HANDLER_A))
        .collect();
    assert_eq!(
        union_admission(&unions, OTHER_TARGET, HANDLER_B, UnionArity::Five),
        UnionAdmission::Exhausted
    );
}

// -------- the dispatcher, called for real --------

/// Slot 95 belongs to `forwards_all_five_arguments`, 94 to `a_null_head_returns_zero`, 93 to
/// the chaining test. Production never reaches them in a test binary, which installs no hooks.
const DISPATCH_SLOT: usize = 95;
const NULL_HEAD_SLOT: usize = 94;
const CHAIN_SLOT: usize = 93;

static SEEN: [AtomicUsize; 5] = [const { AtomicUsize::new(0) }; 5];
const SOLE_HANDLER_RETURN: usize = 0x5011;

unsafe extern "system" fn record_five(a: usize, b: usize, c: usize, d: usize, e: usize) -> usize {
    for (cell, value) in SEEN.iter().zip([a, b, c, d, e]) {
        cell.store(value, Ordering::SeqCst);
    }
    SOLE_HANDLER_RETURN
}

/// The fifth argument is the whole point: a four-argument dispatcher drops it, and for
/// `AddCancelButton` the dropped value is the keyguide function pointer the game then calls.
#[test]
fn the_five_argument_dispatcher_forwards_all_five_arguments() {
    UNION_HEADS[DISPATCH_SLOT].store(addr_of(record_five), Ordering::Release);

    let returned = unsafe { union_dispatch5::<DISPATCH_SLOT>(11, 22, 33, 44, 55) };

    assert_eq!(returned, SOLE_HANDLER_RETURN);
    let seen: Vec<usize> = SEEN.iter().map(|c| c.load(Ordering::SeqCst)).collect();
    assert_eq!(seen, vec![11, 22, 33, 44, 55]);
    UNION_HEADS[DISPATCH_SLOT].store(0, Ordering::Release);
}

/// An unarmed slot returns 0 without calling anything, exactly as [`union_dispatch`] does.
#[test]
fn a_null_five_argument_head_returns_zero() {
    UNION_HEADS[NULL_HEAD_SLOT].store(0, Ordering::Release);
    assert_eq!(
        unsafe { union_dispatch5::<NULL_HEAD_SLOT>(1, 2, 3, 4, 5) },
        0
    );
}

// -------- two handlers on one five-argument target --------

static OUTER_ORIG: AtomicUsize = AtomicUsize::new(0);
static INNER_ORIG: AtomicUsize = AtomicUsize::new(0);
static CALL_ORDER: Mutex<Vec<&'static str>> = Mutex::new(Vec::new());
static GAME_SAW: [AtomicUsize; 5] = [const { AtomicUsize::new(0) }; 5];

const GAME_RETURN: usize = 0x6a3e;

unsafe extern "system" fn fake_game(a: usize, b: usize, c: usize, d: usize, e: usize) -> usize {
    CALL_ORDER.lock().unwrap().push("game");
    for (cell, value) in GAME_SAW.iter().zip([a, b, c, d, e]) {
        cell.store(value, Ordering::SeqCst);
    }
    GAME_RETURN
}

unsafe extern "system" fn inner_handler(a: usize, b: usize, c: usize, d: usize, e: usize) -> usize {
    CALL_ORDER.lock().unwrap().push("inner");
    let orig: UnionFn5 =
        unsafe { std::mem::transmute::<usize, UnionFn5>(INNER_ORIG.load(Ordering::SeqCst)) };
    unsafe { orig(a, b, c, d, e) }
}

unsafe extern "system" fn outer_handler(a: usize, b: usize, c: usize, d: usize, e: usize) -> usize {
    CALL_ORDER.lock().unwrap().push("outer");
    let orig: UnionFn5 =
        unsafe { std::mem::transmute::<usize, UnionFn5>(OUTER_ORIG.load(Ordering::SeqCst)) };
    let from_chain = unsafe { orig(a, b, c, d, e) };
    CALL_ORDER
        .lock()
        .unwrap()
        .push(if from_chain == GAME_RETURN {
            "outer-saw-game-return"
        } else {
            "outer-saw-something-else"
        });
    // The outermost handler writes the return value last, so this is what the game's caller
    // gets. Deliberately not `from_chain`, or the assertion below could not tell the two apart.
    OUTER_RETURN
}

const OUTER_RETURN: usize = 0x0075e4;

/// Two handlers on one five-argument address: strictly nested, first registrant outermost,
/// every argument reaching the game unchanged, and the outermost handler's return value the
/// one that survives.
///
/// The wiring is done by [`chain_append`], the same function the registrar calls -- only the
/// `MH_CreateHook` that would have produced the trampoline is stood in for.
#[test]
fn two_handlers_on_one_five_argument_target_nest_with_the_first_outermost() {
    let mut entry = UnionEntry {
        entry: FAKE_TARGET,
        target: FAKE_TARGET,
        trampoline: addr_of(fake_game),
        arity: UnionArity::Five,
        handlers: Vec::new(),
    };
    // First registrant: becomes the head, and its orig is the game.
    assert_eq!(
        chain_append(&mut entry, addr_of(outer_handler), &OUTER_ORIG),
        1
    );
    UNION_HEADS[CHAIN_SLOT].store(addr_of(outer_handler), Ordering::Release);
    // Second registrant: the first now calls it, and its own orig becomes the game.
    assert_eq!(
        chain_append(&mut entry, addr_of(inner_handler), &INNER_ORIG),
        2
    );

    assert_eq!(
        OUTER_ORIG.load(Ordering::SeqCst),
        addr_of(inner_handler),
        "the first registrant must now call the second, not the game"
    );
    assert_eq!(INNER_ORIG.load(Ordering::SeqCst), addr_of(fake_game));
    assert_eq!(
        UNION_HEADS[CHAIN_SLOT].load(Ordering::SeqCst),
        addr_of(outer_handler),
        "chaining must not move the head off the first registrant"
    );

    let returned = unsafe { union_dispatch5::<CHAIN_SLOT>(0x11, 0x22, 0x33, 0x44, 0x55) };

    assert_eq!(
        *CALL_ORDER.lock().unwrap(),
        vec!["outer", "inner", "game", "outer-saw-game-return"],
        "the chain must be strictly nested, not a fan-out"
    );
    let game_saw: Vec<usize> = GAME_SAW.iter().map(|c| c.load(Ordering::SeqCst)).collect();
    assert_eq!(
        game_saw,
        vec![0x11, 0x22, 0x33, 0x44, 0x55],
        "all five arguments must survive two handlers"
    );
    assert_eq!(
        returned, OUTER_RETURN,
        "the first registrant writes the return value last"
    );
    UNION_HEADS[CHAIN_SLOT].store(0, Ordering::Release);
}

// ------------------------------------------------------------------ electing the hub
// The rule is a pure function over a candidate list precisely so it can be asserted here,
// on a host with no loader list, no modules and no MinHook. What stays behind `cfg(windows)`
// is `loaded_module_bases` and the `GetProcAddress` that fills `exports_registrar` in -- the
// enumeration, not the decision.

/// Three module bases spread far enough apart to read as distinct addresses, deliberately
/// declared out of numeric order so a test that passes by list position rather than by base
/// cannot hide.
const HIGH_BASE: usize = 0x7fff_0000;
const LOW_BASE: usize = 0x1800_0000;
const MIDDLE_BASE: usize = 0x4000_0000;
/// A base belonging to none of the candidates: the caller is a companion that exports nothing
/// and does not appear in the lists below.
const COMPANION_BASE: usize = 0x6000_0000;

fn hub(base: usize) -> UnionCandidate {
    UnionCandidate {
        base,
        exports_registrar: true,
    }
}

fn plain(base: usize) -> UnionCandidate {
    UnionCandidate {
        base,
        exports_registrar: false,
    }
}

/// One exporter among several modules: that one, whatever its position in the list and
/// whatever bases the non-exporters hold. A lower base that exports nothing must not win --
/// the walk hands over every loaded module, most of which are the game's own.
#[test]
fn the_single_exporter_is_the_hub_however_low_the_other_bases_are() {
    let candidates = [plain(LOW_BASE), hub(HIGH_BASE), plain(MIDDLE_BASE)];

    assert_eq!(
        elect_union_host(&candidates, COMPANION_BASE),
        Some(HIGH_BASE)
    );
}

/// Several exporters: the lowest base wins. This is the clause that lets every image decide
/// alone -- a caller reaching a different answer here would file its handlers in a second
/// MinHook instance, which is the whole failure.
#[test]
fn several_exporters_elect_the_lowest_base() {
    let candidates = [
        hub(HIGH_BASE),
        plain(0x1000_0000),
        hub(LOW_BASE),
        hub(MIDDLE_BASE),
    ];

    assert_eq!(
        elect_union_host(&candidates, COMPANION_BASE),
        Some(LOW_BASE)
    );
}

/// No exporter: the caller owns the address on its own union. A standalone companion run, and
/// also the state a companion sees while me3 is still mapping the rest of the profile -- which
/// is why the resolver keeps polling on this answer rather than settling.
#[test]
fn no_exporter_leaves_the_caller_on_its_own_union() {
    let candidates = [plain(LOW_BASE), plain(MIDDLE_BASE), plain(HIGH_BASE)];

    assert_eq!(elect_union_host(&candidates, COMPANION_BASE), None);
}

/// An empty list is the same answer, reached without an exporter to compare against. The walk
/// returns this when the loader list cannot be read at all.
#[test]
fn an_empty_candidate_list_is_not_a_hub() {
    assert_eq!(elect_union_host(&[], COMPANION_BASE), None);
}

/// Self-exclusion: the elected hub gets `None` for itself rather than its own export address,
/// so it registers straight into its local table instead of round-tripping through a C ABI
/// back into the lock it already holds.
#[test]
fn the_hub_does_not_elect_itself() {
    let candidates = [hub(LOW_BASE), plain(MIDDLE_BASE), hub(HIGH_BASE)];

    assert_eq!(elect_union_host(&candidates, LOW_BASE), None);
}

/// Self-exclusion is a test on the winner, not a filter on the input -- so an exporter that
/// loses the election still delegates, to the winner rather than to itself.
///
/// Removing this image from the candidates instead would invert exactly this case: the lowest
/// hub would elect the next one up while that one elected the lowest back, and each would file
/// its handlers in the other's table. Two instances on one prologue, which is the condition
/// this whole path exists to prevent.
#[test]
fn an_exporter_that_loses_still_delegates_to_the_winner() {
    let candidates = [hub(LOW_BASE), hub(HIGH_BASE)];

    assert_eq!(elect_union_host(&candidates, HIGH_BASE), Some(LOW_BASE));
}

/// The configuration the election was written for: a non-product shell exports the registrar,
/// `er_armament_icons.dll` does not, and no product is loaded. Under the old name-only lookup
/// the companion saw nothing and took `HookRoute::LocalUnion`, so two MinHook instances landed
/// on `TITLE_SCALEFORM_FILE_OPEN_RVA`.
///
/// The shell that made it a configuration anyone could install was `er_quit_rows.dll`, deleted
/// 2026-09-20, and no artifact in this workspace exports the registrar today except the
/// product -- measured on the live module table, not read off the source tree, on run
/// br-20260920-211125-8c75. The test stays and no longer names that shell: the property is
/// about a candidate list, any future shell carrying `mh.rs` produces such a list again, and a
/// test deleted because its last real configuration cleared is a test rewritten after the next
/// incident.
#[test]
fn a_companion_beside_a_hub_with_no_product_finds_the_hub() {
    let hub_shell = hub(LOW_BASE);
    let armament_icons = plain(COMPANION_BASE);
    let candidates = [hub_shell, armament_icons];

    assert_eq!(
        elect_union_host(&candidates, armament_icons.base),
        Some(hub_shell.base),
        "the companion must route to the shell that exports the registrar"
    );
    assert_eq!(
        elect_union_host(&candidates, hub_shell.base),
        None,
        "and that shell must own the prologue itself, so exactly one instance exists"
    );
}
