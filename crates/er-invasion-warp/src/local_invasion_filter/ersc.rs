//! ERSC entry points, session-object layout and state codes -- one [`Abi`] per Seamless Co-op
//! build, chosen at runtime by byte-checking the module that is actually loaded.
//!
//! # Why this is a table rather than four constants
//!
//! `ersc.dll` is third-party. The user installs it, updates it whenever they like, and may
//! downgrade; the Seamless launcher leaves the previous build in `_SeamlessCoop/` beside the new
//! one, so both are usually present on disk. v2.0.0 shipped 2026-09-02 and moved every address
//! this module pins AND the fields those addresses operate on. A single constant set can only
//! ever be right about one build, so there is one [`Abi`] per build and [`super::resolve_ersc_abi`]
//! picks between them -- refusing, as before, on a module that matches neither.
//!
//! # What v2.0.0 moved, and how each address was identified
//!
//! A byte match was not accepted as an identification for any of these. The measurements are
//! reproducible with `scripts/ersc-disas.py` against the two DLLs, read in place.
//!
//! | | v1.9.9 | v2.0.0 | what identifies the v2.0.0 address |
//! |---|---|---|---|
//! | `show` | `0x22d30` | `0x241a0` | unique masked-body match; `.pdata` index 296 -> 301; function SIZE identical (`0xa0a`) |
//! | invade action | `0x243e0` | `0x25850` | `.pdata` index 314 -> 319 at the same `+5` shift that holds unbroken over 78 consecutive functions; the only action of its shape writing `0xe`, the `+1` counterpart of `0xd` |
//! | cancel action | `0x24460` | `0x258d0` | unique masked-body match, AND `.pdata` index 315 -> 320 at that same `+5`, AND size identical (`0x64`) |
//! | `BuildLobbyKey` | `0xabc20` | `0xad590` | loads the SHA-256 IV and a 32-byte `.rdata` salt at the same internal spacing (`0x23` bytes apart); its two callers sit in ONE function that is byte-size-identical across the builds (`0xf18`) and calls it twice `0xaf` apart, exactly as v1.9.9's does; and its opening decodes instruction-for-instruction against v1.9.9's, including `movzx edx,[rcx+CTX]; shr edx,2; and edx,1` |
//!
//! The two failures the previous measurement hit are both explained, which is what turns them
//! from "the function is gone" into "the function is right here":
//!
//! * **The invade action matched no body signature at any length.** The compiler INVERTED its
//!   idle guard -- `cmp [rdi+0x110],0; je <continue>` became `cmp [rdi+0x150],1; jne <return>` --
//!   and `je` and `jne` differ in the OPCODE byte, which a masked search keeps. The divergence is
//!   at offset 21, inside even the shortest signature on the ladder.
//! * **`BuildLobbyKey`'s 19-byte prologue matched exactly one v2.0.0 address, and it was the
//!   wrong function.** `0x1a6d0` merely happens to frame `0x148` too. It sits at `.pdata` index
//!   193, some 1568 entries from where `BuildLobbyKey`'s neighbourhood is -- so a unique prologue
//!   hit really is a code SHAPE and nothing more.
//!
//! And the two candidates its masked BODY search returned instead are worth naming, because one of
//! them is not a coincidence at all: `0xab5f0` is `BuildLobbyKey`'s own CALLER -- the lobby
//! search-and-publish function, `0xaa2f0` in v1.9.9, the same `0xf18` bytes in both builds. A body
//! signature taken from the top of a big-frame MSVC function matches the top of another big-frame
//! MSVC function in the same source file; that it landed one level up the call graph rather than
//! somewhere random is exactly why "two candidates" had to be resolved by reading, not by picking.
//!
//! # The session ABI moved as one block
//!
//! The mutex sub-object, the guard and the state field all shifted by exactly `+0x40` and kept
//! their relative spacing (`guard = mutex+0x4c`, `state = mutex+0x50`). That is one finding with
//! three consequences rather than three separate measurements.
//!
//! # `0xd` was not replaced -- the whole enum was renumbered `+1`
//!
//! "No v2.0.0 action writes `0xd`" was correct and led to the wrong conclusion. Scanning every
//! `mov dword [reg+STATE], imm32` in each build's real code finds SEVEN distinct values whose
//! site counts match one-for-one under a uniform `+1`: `0x1`x1, `0x3`x1, `0x6`x1, `0x9`x1,
//! `0xd`x1, `0x22`x7, `0x23`x1 become `0x2`, `0x4`, `0x7`, `0xa`, `0xe`, `0x23`x7, `0x24`. The
//! "idle" sentinel the guarded actions compare against moved `0` -> `1` in the same shift. So
//! `0xd` became `0xe`, and a new state was inserted at the bottom of the enum.
//!
//! That renumber makes leaving a state code alone the DANGEROUS option, not the safe one:
//! v1.9.9's `0x12` progress marker is, under the shift, a fast-fail state in v2.0.0.

// Every `*_PROLOGUE` below is assembled from NAMED `iced-x86` instructions by this crate's
// `build.rs`, which additionally checks each one against a real copy of the build it claims to
// describe -- located by the Seamless version string inside the file rather than by which
// directory it sits in. Hand-typing them is what the generator exists to prevent: one wrong
// byte and every check here silently fails closed, which looks exactly like "Seamless is not
// loaded".
include!(concat!(env!("OUT_DIR"), "/generated_ersc_prologues.rs"));

/// Everything that differs between two Seamless builds, in one record.
///
/// Splitting it up is what made the previous breakage silent-adjacent: a correct v2.0.0
/// ADDRESS used with v1.9.9 field offsets would read and write the wrong fields of a live
/// multiplayer session, and this module's failure mode is cancelling other players'
/// invasions. Address, layout and codes travel together or not at all.
pub struct Abi {
    /// Human-readable build name, for the log line that says which one was recognised.
    pub version: &'static str,
    /// `show(void* OSM, int groupId)` -- the option-menu builder. Read to learn OSM, and the
    /// one function in Seamless this module hooks.
    pub show_rva: usize,
    /// The "Invade world" option action. Reads `rcx` only.
    pub invade_action_rva: usize,
    /// The "Cancel search" option action. Reads `rcx` only.
    pub cancel_action_rva: usize,
    /// `BuildLobbyKey(ctx, std::string* out)` -- produces the `lobby_key` string.
    ///
    /// # Why this one matters more than it looks
    ///
    /// Seamless finds worlds with a Steam lobby-list query carrying exactly TWO match terms:
    /// `lobby_type == "yknx3_seamless_master_lobby"` and `lobby_key == <this string>`, the
    /// latter with `k_ELobbyComparisonEqual`. An exhaustive decode of the XOR-obfuscated
    /// string idiom across every plaintext function in `ersc.dll` finds only those two keys in
    /// the whole module -- no map, block, region, coordinate or radius anywhere. So two
    /// players who derive DIFFERENT `lobby_key` values are simply invisible to each other, no
    /// matter how well their levels, weapon levels or locations line up.
    ///
    /// The value is [`LOBBY_KEY_HEX_LEN`] lowercase hex characters -- a SHA-256 digest, from
    /// an IV this function loads out of `.rdata` together with a 32-byte salt. **v2.0.0
    /// rotated that salt** (`gcEz!NaR-*y7?kSPXJ8Ll7wVtspRo?EZ` -> `n7iAOMaYxNItSTYeAIIFB2+AlM8GQoXs`),
    /// which by itself makes v1.9.9 and v2.0.0 clients invisible to each other -- worth
    /// knowing before anyone reads a key mismatch as a bug in this module.
    ///
    /// The moment it re-derives is jittered, so a comparison between two machines must use the
    /// key that was live when the offers arrived, not merely the first one printed. Observed,
    /// never altered: publishing a key of our own would change what every other Seamless
    /// client matches, which is not ours to do.
    pub build_lobby_key_rva: usize,
    pub show_prologue: &'static [u8],
    /// Also the version DISCRIMINATOR -- see [`SUPPORTED`].
    pub invade_prologue: &'static [u8],
    pub cancel_prologue: &'static [u8],
    pub build_lobby_key_prologue: &'static [u8],
    /// Session state, the field every option action writes.
    pub session_state_offset: usize,
    /// Guard field. [`SESSION_GUARD_POISON`] is the value every action refuses to proceed
    /// past -- they take a fatal-error branch instead -- so the filter refuses too.
    pub session_guard_offset: usize,
    /// Idle: the state a cancelled search settles back to, and the state `invade` requires.
    pub state_idle: u32,
    /// The state the "Invade world" action writes -- the one and only site in the whole
    /// unpacked `.text` that puts this value in the field, in either build.
    pub state_searching: u32,
    /// The state "Cancel search" writes.
    ///
    /// NOT usable as a "the user cancelled" signal, which is what it was briefly used for: the
    /// static scan found SEVEN sites writing this value and only one is the Cancel action, so
    /// every internal abort looked like a user cancel. (Still seven in v2.0.0, at the
    /// renumbered value.) It survives as a label for the trace.
    pub state_cancelling: u32,
    /// The first state past the fast-fail path, our own progress marker for the restart
    /// backoff. See `note_attempt_progress`, its only reader, for what it gates.
    ///
    /// v1.9.9's `0x12` was measured at runtime; v2.0.0's is that value carried through the
    /// proven enum-wide `+1`, and is the one number in this table that is INFERRED rather than
    /// read out of an instruction. Leaving it alone would have been worse than shifting it:
    /// under the renumber, v1.9.9's `0x12` is a fast-fail state in v2.0.0, so an unshifted
    /// marker would clear the backoff penalty on exactly the attempts that earned it.
    pub state_offer_received: u32,
}

/// Every Seamless build this module knows how to drive.
///
/// [`super::resolve_ersc_abi`] requires EXACTLY ONE of these to match
/// the loaded module and refuses otherwise -- zero matches means an unrecognised build, and
/// two would mean the discriminator does not discriminate. That is not a theoretical
/// guard: measured 2026-09-02, each [`Abi::invade_prologue`] occurs exactly once in the
/// build it describes and NOT AT ALL in the other, so the "two matched" arm is unreachable
/// for these two entries and stays as the check that keeps a third entry honest.
///
/// The discriminator is the INVADE action rather than `show` for two independent reasons.
/// `show` is byte-identical between the two builds -- it is the same code at a different
/// address, so it cannot tell them apart at all. And this module HOOKS `show`, so once the
/// detour is installed those bytes are our own; a fingerprint taken there measures our patch
/// and concludes Seamless is a stranger, which is a bug this module has already had once.
pub const SUPPORTED: &[Abi] = &[
    Abi {
        version: "Seamless Co-op v1.9.9",
        show_rva: V199_SHOW_RVA,
        invade_action_rva: V199_INVADE_ACTION_RVA,
        cancel_action_rva: V199_CANCEL_ACTION_RVA,
        build_lobby_key_rva: V199_BUILD_LOBBY_KEY_RVA,
        show_prologue: V199_SHOW_PROLOGUE,
        invade_prologue: V199_INVADE_PROLOGUE,
        cancel_prologue: V199_CANCEL_PROLOGUE,
        build_lobby_key_prologue: V199_BUILD_LOBBY_KEY_PROLOGUE,
        session_state_offset: V199_SESSION_STATE_OFFSET,
        session_guard_offset: V199_SESSION_GUARD_OFFSET,
        state_idle: 0x00,
        state_searching: 0x0d,
        state_cancelling: 0x22,
        state_offer_received: 0x12,
    },
    Abi {
        version: "Seamless Co-op v2.0.0",
        show_rva: V200_SHOW_RVA,
        invade_action_rva: V200_INVADE_ACTION_RVA,
        cancel_action_rva: V200_CANCEL_ACTION_RVA,
        build_lobby_key_rva: V200_BUILD_LOBBY_KEY_RVA,
        show_prologue: V200_SHOW_PROLOGUE,
        invade_prologue: V200_INVADE_PROLOGUE,
        cancel_prologue: V200_CANCEL_PROLOGUE,
        build_lobby_key_prologue: V200_BUILD_LOBBY_KEY_PROLOGUE,
        session_state_offset: V200_SESSION_STATE_OFFSET,
        session_guard_offset: V200_SESSION_GUARD_OFFSET,
        state_idle: 0x01,
        state_searching: 0x0e,
        state_cancelling: 0x23,
        state_offer_received: 0x13,
    },
];

// The addresses and field offsets are NAMED CONSTANTS rather than literals inside the table
// above, and that is not a style choice: `scripts/check-expression-constants.py` derives its
// gated population from `const` declarations whose name carries `RVA`, and a value written as
// a bare struct-literal field is invisible to it. Inlining these once already dropped six
// names out of that population, which the coverage floor caught. They keep the version prefix
// because two builds are described here and an unprefixed `SHOW_RVA` could only be one of
// them.

/// Seamless v1.9.9, measured 2026-08-05.
pub const V199_SHOW_RVA: usize = 0x2_2d30;
pub const V199_INVADE_ACTION_RVA: usize = 0x2_43e0;
pub const V199_CANCEL_ACTION_RVA: usize = 0x2_4460;
pub const V199_BUILD_LOBBY_KEY_RVA: usize = 0xa_bc20;
pub const V199_SESSION_STATE_OFFSET: usize = 0x110;
pub const V199_SESSION_GUARD_OFFSET: usize = 0x10c;

/// Seamless v2.0.0, measured 2026-09-02. See this module's docs for what identifies each one.
pub const V200_SHOW_RVA: usize = 0x2_41a0;
pub const V200_INVADE_ACTION_RVA: usize = 0x2_5850;
pub const V200_CANCEL_ACTION_RVA: usize = 0x2_58d0;
pub const V200_BUILD_LOBBY_KEY_RVA: usize = 0xa_d590;
pub const V200_SESSION_STATE_OFFSET: usize = 0x150;
pub const V200_SESSION_GUARD_OFFSET: usize = 0x14c;

/// `LOBBY_KEY_HEX_LEN` lowercase hex characters: a SHA-256 digest, so 32 bytes rendered as 64
/// characters. Unchanged across the update -- both builds load the same SHA-256 IV.
pub const LOBBY_KEY_HEX_LEN: usize = 64;
/// MSVC `std::string`: `{ union { char buf[16]; char* ptr; }; size_t size; size_t capacity; }`.
/// A capacity of 16 or more means the bytes are on the heap and the first field is a pointer --
/// which is always the case here, because a 64-character value cannot fit the inline buffer.
pub const STD_STRING_SIZE_OFFSET: usize = 0x10;
pub const STD_STRING_CAPACITY_OFFSET: usize = 0x18;
pub const STD_STRING_HEAP_CAPACITY: usize = 0x10;

/// `OSM+0x58` is the session object. UNCHANGED in v2.0.0: all five of its option actions
/// still open `mov rdi,[rcx+0x58]`, and `show`'s body is byte-identical, so nothing about the
/// OSM layout moved.
///
/// A `.data` singleton holding OSM would have let this module hook NOTHING in Seamless. One
/// was looked for and not found: the only `.data` global that is loaded and then dereferenced
/// at `+0x58` is `ersc+0x21b228`, and it is read at 121 sites, written by a pair of adjacent
/// CRT-shaped setters, and non-zero in the file -- a locale/allocator global that the search
/// matched by coincidence, not the session. The `seamless` tag likewise lives inside longer
/// strings (`seamless buddy system`, source paths), so there is no constructor to trace back
/// to a singleton either. Recorded here so the next reader does not repeat the hunt.
pub const NEXT_OBJECT_OFFSET: usize = 0x58;
/// OSM carries the ASCII tag `seamless` here. Measured live 2026-08-04 in v1.9.9, reported as
/// a diagnostic and believed by nothing -- see `show_observer` for the day it was a gate.
pub const OSM_TAG_OFFSET: usize = 0x68;
pub const OSM_TAG: &[u8] = b"seamless";
/// The value the guard field holds when the session is unusable. Unchanged in v2.0.0; every
/// option action in both builds still compares against it.
pub const SESSION_GUARD_POISON: u32 = 0x7fff_ffff;
/// The highest plausible session state, used to reject a pointer that is not a session at all.
/// Comfortably above the largest code either build writes (`0x24`).
pub const SESSION_STATE_MAX: u32 = 0xff;
