# er-invasion-warp: cancel window, hunt mode, and the Redmane place name

Static investigation only, 2026-09-13. No build, no launch, no runtime probe. Every claim below is
tagged **VERIFIED** (read out of the source or out of git history in this session) or **HYPOTHESIS**
(needs a live run to settle). Line numbers are against the tree at commit `01387de1`.

Sources consulted before the source tree, per `AGENTS.md`: bd memories
`ersc-retry-constant-not-statically-recoverable-two-negatives-2026-08-06`,
`ersc-v200-repin-addresses-and-enum-shift-2026-09-02`,
`ersc-201-is-one-0x150-shift-code-and-fields-unchanged-2026-09-02`,
`dll-users-only-pool-via-lobby-key-substitution-2026-08-06`.

---

## Item 2 -- "See if we can adjust the Seamless timeout window to cancel sooner"

### Verdict

The timers split cleanly in two, and the split is the whole answer.

| timer | where it lives | can we change it |
|---|---|---|
| stall detection threshold, 5 s | ours, `stall_watchdog.rs:125` | yes, one constant |
| which states are timed at all | ours, `stall_watchdog.rs:76` | yes -- **and it is currently wrong**, see below |
| rejection re-arm window, 600 ticks | ours, `local_invasion_filter.rs:1962` | yes |
| fast-fail backoff 1 s / 1 s / 8 s | ours, `restart_backoff.rs:41,44,49` | yes |
| dead-join / torn-down grace, 650 ms / 8 s | ours, `local_invasion_filter.rs:1468,1477` | yes |
| **Seamless's no-match retry dwell (~15 s, 600 frames)** | inside `ersc.dll`, Themida-virtualised | **no**, not without writing session state or patching bytes |
| **Seamless's post-cancel `joinCheck` countdown, 30.0 s** | inside `ersc.dll`, f32 seconds | **no**, same |

So: the thing the player experiences as "it did not give up when I asked" is **Seamless's**, not ours.
But there is real, owned work adjacent to it, and one of those owned constants is presently a live bug.

### The decisive evidence that the long wait is ersc's

`crates/er-invasion-warp/src/local_invasion_filter/actions.rs:330-344`, a comment written from a
measured run:

> Measured on run `br-20260910-012622-fd23`: four cancels, each driven immediately after a
> `join-progress` line, and all four of those lines read `Progressing`. Two settled in ~1.6s and two
> took ~30.2s [...] **The 30s is not spent deciding whether to cancel; it is spent inside `0x23`
> afterwards.** The two fast cancels passed through `lobby=7 proto=1 joinCheck=30.0 -> proto=2
> joinCheck=29.7` and left `0x23` three tenths of a second into that countdown; the two slow ones
> never reached `lobby=7` at all and sat out its full 30.0s. `joinCheck`/`waitInit` are **f32
> seconds**, which is why no `30000` immediate was ever found in ersc's `.text`.

**VERIFIED.** Our cancel already fires at the first opportunity -- `cancel_match` calls Seamless's own
`Cancel search` option callback (`ersc+0x258d0`) directly, the same one the player's click invokes,
and that callback's entire body is: lock `session+0x100`, compare `session+0x14c`, write `0x23` to
`session+0x150`, unlock. There is no delay of ours between the decision and the write. The wait the
player sees is ersc unwinding state `0x23`.

The ~20 s figure in `crates/er-invasion-warp-core/src/reject_notice.rs:8` ("Seamless retries roughly
every 20 seconds") is the same family: it is ersc's own no-match retry dwell, not a value we set.

bd `ersc-retry-constant-not-statically-recoverable-two-negatives-2026-08-06` is the definitive
negative result on ever reading that constant statically, and it should be read before anyone tries
again. Summary of what it proves: the dwell is a **frame counter of 600 ticks** (nine consecutive
samples, zero variance -- ~10 s focused, ~20 s alt-tabbed through ER's unfocused present throttle),
it is compared and decremented **inside a Themida-virtualised function**, all 40 VM stub bodies were
scanned for any surviving instruction touching `[reg+0x110]` as a dword and there were **zero hits**,
and two independent immediate-hunting techniques came back empty. A clock-based patch would have been
wrong anyway, since the dwell is frames.

### What shortening ersc's own timer would actually require

Three options, stated plainly so nobody proposes the second by accident:

1. **Force the state transition instead of finding the timer.** This is the unshipped proposal
   already recorded in that bd memory, and it is the only one that does not touch ersc's bytes: take
   the session mutex via `ersc_base+0x0f4868`, check `dword[session+SESSION_GUARD_OFFSET] != 0x7fffffff`,
   write the target state to `dword[session+0x150]`, unlock via `ersc_base+0x0f4870`. The template is
   ersc's own invade action, fully plaintext. The memory records why it was not taken: ersc only ever
   writes `0x0d`/`0x0e` from idle, so forcing a mid-search state to idle is a transition Seamless never
   performs, and the virtualised code may hold a pending Steam lobby-query handle that assumes the
   dwell -- a leaked or duplicated query we cannot observe. Track record on this exact state is 0 for 1.
2. **Patch `ersc.dll` bytes.** Saying it plainly, because the task asked me to: this would mean
   **modifying a third-party, Themida-protected DLL the user installs and updates independently**.
   It is also technically dead on arrival here -- the compare/decrement is inside the virtualised
   region, so there are no plaintext bytes at the site to patch. I am **not** proposing it. It would
   additionally collide with the repo's standing rule that `ersc.dll` is never copied, staged or
   modified, and with the one-supported-build contract (`ERSC_SUPPORTED_VERSION` in
   `build-support/prologue_build.rs`, enforced by `scripts/check-ersc-version-supported.py`).
3. **Runtime memory diff of the dwell counter.** The only remaining way to read the constant, per the
   bd memory. That is a runtime experiment, out of scope for this pass.

### The half that IS in our control: what the player perceives

"Stop showing and waiting immediately" is entirely ours and needs no ersc change at all. The state
after a driven cancel is already `AUTO_SEARCH_ARMED`/`PENDING_REINVADE` bookkeeping we own
(`actions.rs:368-369`), the banner is ours (`reject_notice.rs`), and the map/warp gate reads our own
`publish_invasion_attempt_state` (`local_invasion_filter.rs:2083`). Nothing in the player-facing
surface is forced to sit through ersc's 30 s.

**HYPOTHESIS** (needs one live run to confirm the user's complaint is actually this): what the player
calls "a window before Seamless gives up" is the 30.0 s `joinCheck` countdown observed above, during
which our own surfaces keep reporting an attempt in flight because `read_session_state` still reads
`0x23`. If so, the fix is to treat `state_cancelling` as "already over" for every player-visible
surface while leaving the session alone, and it costs nothing in ersc.

### The bug found while looking: `TIMED_STATES` is still in Seamless v1.9.9 numbering

This is the one genuinely actionable finding in item 2, and it is **VERIFIED** three independent ways.

`crates/er-invasion-warp/src/stall_watchdog.rs:76-80`:

```rust
const TIMED_STATES: [u32; 3] = [
    0x0e, // search -> connect handoff
    0x12, // connect
    0x13, // connect
];
```

`crates/er-invasion-warp/src/stall_watchdog.rs:38-51` still declares:

```rust
pub mod state {
    pub const IDLE: u32 = 0x00;
    pub const SEARCHING: u32 = 0x0d;
    pub const RETRYING: u32 = 0x11;
    pub const CANCELLING: u32 = 0x22;
    pub const CANCEL_SETTLING: u32 = 0x23;
    pub const CONNECTING: u32 = 0x12;
}
```

Against the supported build, `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs:199-202`:

```rust
    state_idle: 0x01,
    state_searching: 0x0e,
    state_cancelling: 0x23,
    state_offer_received: 0x13,
```

1. **The numbers disagree.** `IDLE` is `0x00` in the watchdog and `0x01` in the ABI; `SEARCHING` is
   `0x0d` there and `0x0e` here; `CANCELLING` is `0x22` there and `0x23` here. That is the uniform
   `+1` enum renumber Seamless v2.0.0 introduced, recorded in bd
   `ersc-v200-repin-addresses-and-enum-shift-2026-09-02` and proven there by scanning every
   `mov dword [reg+STATE], imm32` site in both builds. The watchdog never received it.
2. **Nothing translates.** `actions.rs:916-928` (`watch_for_stall`) feeds the raw value straight
   through: `read_session_state(session.abi, session.session)` -> `guard.observe(state, now_ms)`.
   No mapping, no normalisation.
3. **Git says it was never touched.** `git log -L76,81:crates/er-invasion-warp/src/stall_watchdog.rs`
   returns exactly two commits: `c705c975` (the crate's original landing, under v1.9.9) which
   introduced `[0x0e, 0x12, 0x13, 0x22, 0x23]`, and `0e084240` which only removed the last two. The
   three survivors are original v1.9.9 values.

**Consequence.** Under v2.0.1 numbering, `0x0e` **is `state_searching`** -- the one state this file's
own documentation and its own test `a_long_search_is_never_a_stall` say must never be timed. And
`0x12` is where v1.9.9's `0x11 RETRYING` landed after the `+1`, which is precisely the state the file
records as having shipped a regression that made the mod "strictly worse than not existing":

> `0x11 -> 0x0d     0 times   <- Seamless's own retry, extinct`
> `0x11 -> 0x22    33 times   <- this detector cancelling it instead`

There is already a live sighting of it firing on `0x0e`, in a comment at
`local_invasion_filter.rs:1355-1361`:

> run `br-20260908-212740-ea7c` resolved a session whose first read was already `0x0e`, armed the
> loop off that, **timed five seconds, called the handshake stalled and cancelled it** -- before the
> player had used an invasion item.

That comment attributes the event to arming off a first read, and fixes that. The first read is a
real contributing cause; it is not the whole one. The watchdog was willing to time `0x0e` at all
because `0x0e` is in `TIMED_STATES`, and it still is.

**HYPOTHESIS, the part that needs a run:** how often this bites in practice. If Seamless leaves
`0x0e` quickly and dwells in `0x12` instead, then the 5 s threshold is cancelling the retry cycle;
if it dwells in `0x0e`, it is cancelling the search itself. Either is the documented catastrophic
shape. The log line to look for is `connection stalled at state 0x0e` or `... 0x12`
(`actions.rs:879-883`).

### Plan for item 2

Ordered, smallest first. Steps 1-2 are the real deliverable; 3 is the perception fix; 4 is optional.

1. **Re-derive `TIMED_STATES` against the supported build.** Do not guess the `+1` -- re-measure the
   walk. `scripts/ersc-disas.py` reads the installed DLL in place and has a `states` mode that dumps
   every `mov dword [reg+STATE], imm32` site; that is what identified the renumber the first time.
   The correct set is the states measured to complete in under ~1 s, expressed in v2.0.1 numbering,
   and it must exclude `abi.state_searching` and the retry dwell by construction.
2. **Make it impossible to drift again.** The root cause is that `stall_watchdog::state` is a
   hand-maintained second copy of a table that already exists in `ersc::Abi`, deliberately duplicated
   so the module stays host-testable (`stall_watchdog.rs:35-37`). Keep the purity; remove the
   duplication of *values*. Two shapes work:
   - pass the states in -- `StallWatchdog::new(timed: &'static [u32])`, constructed at the one call
     site from `abi`, so the host tests still supply their own list; or
   - keep the constant but add a host test in `local_invasion_filter/tests.rs` asserting
     `!TIMED_STATES.contains(&abi.state_searching)` and
     `!TIMED_STATES.contains(&abi.state_cancelling)` and `stall_watchdog::state::IDLE == abi.state_idle`.
     The second is cheaper and catches exactly this class; there is already precedent for it in
     `ersc.rs`'s `the_guard_and_state_offsets_are_the_ones_the_cancel_action_addresses`, which
     recovers offsets out of the generated pin bytes rather than trusting hand-typed constants.
   Either way the gate must fail on the next renumber instead of running with stale numbers.
3. **Decouple the player-visible surfaces from `state_cancelling`.** Once a cancel has been driven,
   stop reporting an attempt in flight: `publish_invasion_attempt_state`
   (`local_invasion_filter.rs:2083-2090`) currently calls an attempt live for any state that is
   neither `0` nor `state_idle`, which includes the whole 30 s `0x23` unwind. Excluding
   `state_cancelling` there makes the mod's own answer to "are you still searching" flip the instant
   we cancel, which is "cancel sooner" from the player's seat and touches nothing in ersc. Pair it
   with `RejectNotice::reset()` so the next hunt speaks up.
4. **Only then consider forcing the transition** (option 1 above), behind a throttle, with a watch
   for duplicated Steam queries. This is the option bd explicitly parked; do not start here.

**Do not** shorten `STALL_THRESHOLD_MS` below 5 s as a way to "cancel sooner". Its own pinning test
(`stall_watchdog.rs:444-457`) requires it to sit strictly between the slowest measured healthy
handshake (2 s, n=8) and the observed 30 s stall, and lowering it makes a mis-numbered `TIMED_STATES`
fire faster rather than fixing anything.

---

## Item 3 -- "Hunt mode means what?"

This is a question, so here is the answer rather than a plan. It is documented, and the documentation
is accurate; the feature's behaviour is narrower than its name suggests in one specific way.

### In one sentence

`hunt = true` attaches **one extra Steam lobby-list filter** to the query Seamless is about to send,
asking for hosts whose lobby advertises `er_invasion_warp_map == "<one block id>"`, so the invasion
arrives at the right place on the first try instead of arriving anywhere and being cancelled.

### Mechanically, step by step (all VERIFIED)

**What it narrows.** Seamless's own invasion search issues a Steam `RequestLobbyList` with five
filters already attached (`lobby_publish.rs:16-21`):

```text
  AddRequestLobbyListStringFilter("lobby_breakin_lobby_ykssr_199_6",       "true")
  AddRequestLobbyListStringFilter("matchmaking_breakin_lobby_ykssr_199_6", "4_3")
  AddRequestLobbyListStringFilter("lobby_type", "yknx3_seamless_master_lobby")
  AddRequestLobbyListNumericalFilter("ykssr_dlc", 1)
  AddRequestLobbyListStringFilter("lobby_key", "<sha256>")
```

None carries location. Hunt adds a sixth. The detour is on `ISteamMatchmaking::RequestLobbyList`,
vtable slot 4, **in `steamclient64.dll`, not in `ersc.dll`** (`lobby_publish.rs:211-216`,
`install_hunt_hook` at `lobby_publish.rs:1205-1246`). It has to be on that call because Steam
accumulates filters and consumes them at request time, and only Seamless knows when its last
`AddRequestLobbyList*` has gone out. The hook runs our `add_string_filter` *before* the original
(`request_lobby_list_hook`, `lobby_publish.rs:1142-1178`).

**What key it filters on.** `LOBBY_MAP_KEY = "er_invasion_warp_map"` (`lobby_publish.rs:82`), a key
**this DLL invents and this DLL publishes**. Its value is the host's current block spelled the
engine's own debug way, e.g. `m60_51_36_00` (`map_value`, `lobby_publish.rs:189-191`). The host half
is `publish_current_map`, which republishes whenever the host's block changes, because Seamless writes
its whole advertisement once at `CreateLobby` and never again -- measured, 7 `SetLobbyData` calls at
creation and zero after (`lobby_publish.rs:42-49`).

**What it filters out.** Everyone who is not running this DLL. A Steam lobby that lacks a filtered key
is excluded from the results -- measured 2026-08-06, a baseline of 13 lobbies went to 0 when one filter
on an unpublished key was added, reproduced twice (`lobby_publish.rs:29-31`). So with `hunt = true` you
see **only** hosts who also run `er_invasion_warp`, and you see them only if they are in the one block
you asked for. This is the cost, it is entirely the invader's own, and it is why the option is off by
default and why both shipped example configs tell you to leave it off.

**Which single location it asks for** (`hunt_filter_value`, `lobby_publish.rs:246-267`):

- exactly one marked block, not excluded -> that one;
- no marked blocks -> the block you are standing in, unless you have excluded it;
- two or more marked -> `None`, no filter added, and a one-time log line explains why.

The two-or-more case is a transport limit, not a policy: a Steam string filter is an equality test
and there is no OR, so asking for two values matches nobody. `hunt_refusal`
(`lobby_publish.rs:276-317`) exists solely so that case is explained in words rather than failing
silently. Exclusions bind hunt as well as the reject filter, so the query cannot aim at a place the
filter is standing by to cancel.

**What changes for the player, `true` vs `false`:**

| | `hunt = false` (default) | `hunt = true` |
|---|---|---|
| who you can match | every Seamless player in your bracket | only players also running this DLL, in one block |
| how the right place is reached | rejection sampling -- take a match, judge it, cancel if wrong, search again | the query only returns the right place |
| expected tries | ~13 draws for one specific host (measured, 13 hosts worldwide in one bracket) | one |
| hostility to strangers | none | you become invisible to, and they to you |
| several marked places | handled, slowly, by the reject filter | refused with an explanation; falls back to unfiltered |

The reject filter (`local_invasion_filter`) and hunt are **independent and complementary**: hunt
narrows what arrives, the filter judges what did. Turning hunt on does not turn the filter off.

**What the counters mean** (`hunt_tally`, `lobby_publish.rs:1259-1264`; published as oracles by
`oracles.rs:166,173`):

- `oracle_invasion_warp_hunt_hooked` (bool) -- the detour on `RequestLobbyList` is **live**. It is
  deliberately a different flag from `HUNT_HOOK_INSTALLED`, which only means "an install was
  attempted". `false` with `hunt = true` in the config means hunt is inert and every query went out
  unfiltered, which from outside looks identical to "nobody was online".
- `oracle_invasion_warp_hunt_filters` (count) -- how many outgoing queries we actually added the filter
  to. Hooked-but-zero means `hunt_target()` declined every time: hunt off, or a refusal, or no
  readable block. `oracles.rs:508` also flags the contradiction case (filters recorded with no hook).

### Does the name match the feature

No, and the decision taken 2026-09-15 is to rename the key to `prefilter`.

"Hunt" reads as "go looking harder", and what it does is the opposite -- it **narrows** the search so
you are shown fewer worlds, and it costs you every host who is not running this mod.

The names that suggest themselves first are all wrong for one reason worth writing down, because it
is not obvious: `target_one_location`, `search_one_location` and `one_location_only` all describe
*which place* is chosen, and that is already what `mode` does. `hunt_filter_value`
(`lobby_publish.rs:246`) aims at the single marked block, or the block the player is standing in --
the same block `LocalInvasionMode::ExactOnly` judges candidates against. Two config keys whose names
both mean "one location" cannot be told apart in a settings panel.

The distinction that matters is **when** each acts, not what it selects:

| key | acts on | every host visible | mechanism |
|---|---|---|---|
| `mode` | the answer Steam returned | yes, then declines the wrong ones | judge `ServerPushJoinData+0x00` at `SetMultiplayJoinData` |
| `prefilter` (was `hunt`) | the question | no -- only mod users publish the key | Steam lobby-list string filter on `RequestLobbyList` |

Read as a pair they become "`mode` decides what you accept; `prefilter` decides what you are
offered". Runner-up name considered and rejected: `narrow_the_search`, plainer but it does not pair.

Sweep cost: about 220 `hunt*` identifier sites. Two facts make it cheaper than it looks --
`settings_panel.rs:284` derives the in-game panel label from the raw key string, so the rename
reaches the UI in the same edit, and `local_invasion_config.rs:517` surfaces an unknown key as a
reported issue rather than dropping it, so a stale `hunt = true` complains instead of silently
reverting. An alias is still worth the one line.

### The escalation ladder (user directive 2026-09-15)

The goal the user stated is to **remove the need to search a location and cancel** -- rejection
sampling is the thing to get rid of, not to tune. The shape asked for is a ladder that widens on its
own and reports where it is:

| rung | filter asked of Steam | who can answer |
|---|---|---|
| 1 | the player's exact block | mod users in that tile |
| 2 | each neighbouring tile in turn, one per query round | mod users in the ring |
| 3 | no filter at all (opt-in, after the ring is exhausted) | everybody, including vanilla hosts |

Rung 2 works because our detour recomputes the filter value on every `RequestLobbyList`
(`lobby_publish.rs:1195-1221`), so successive rounds can name successive tiles. One query can still
only carry one value -- a Steam string filter is equality with no or, and several filters and
together -- so the ring is covered over rounds, never in one shot.

**Rung 3 is why the reject filter must not be deleted.** With no string filter the answer set is the
whole population again, and the only thing that can tell where a candidate would actually land is
`mode` judging the server-sent destination. Deleting the reject filter would leave rung 3 accepting
anything, anywhere.

**The banner has to say which rung it is on**, or "no invasions found" stops meaning anything: the
player cannot distinguish an empty ring from a ring we have not finished asking about. The agreed
shape names the place as well as the count, because a player recognises a place name and has no idea
what tile 3 of 8 is:

```
Could not find an invasion in Liurnia Lake Shore -- searching 3 of 8 nearby locations
```

This is the same failure `hunt_refusal` already exists to prevent (a silent `None` conflating "off"
with "cannot express that"), relocated from configuration to timing, so it wants the same treatment:
a per-value tally -- which tile was asked for, how many lobbies came back, how many rounds until the
ring closed -- not a log line nobody reads.

**Naming a neighbour tile must not depend on the player having opened the map** (user directive
2026-09-15: snapshot on load, or at least before the map is opened). The first shape considered --
snapshot the table when the world map first builds -- is rejected: a player who never opens the map
would get `?PlaceName?` in the banner, and the banner is the thing that makes the ladder legible.

The way out is that the pin rows are not where the names live. `nearest_place_name_in_area`
(`map_hooks.rs:981`) reads each pin row's `ROW_PARAM_POINTER_OFFSET` and then
`PARAM_LABEL_KIND_BASE` / `PARAM_LABEL_TEXT_ID_BASE` **out of the param row it points at**, so the
name is param data that the map UI merely renders. Build the id-to-name table from the param table
directly at DLL load and the map never enters the picture.

The param is **`BonfireWarpParam`**, and its layout is already written down here -- the module doc
of `er-invasion-warp-core/src/param_row.rs` tabulates every field the pin constructor copies, and
`map_seams.rs:181` names the lookup (`BonfireWarpParamLookup`, `0x140d25c30`, param table index
`0x2B`). So the "which param" question is answered; an earlier draft of this section said it was
written down nowhere, which was wrong.

Getting from a block id to map coordinates is also already solved in live memory, and deliberately
independent of the map UI: `legacy_map_regions.rs` reads `CS::WorldMapLegacyConverter`, whose entry
per legacy block carries the overworld block it projects into and the map-space origin of that
projection, and which the engine keeps resident because the map has to draw dungeons the player has
never entered. Overworld blocks get their origin from `WorldGridAreaInfo::GetWorldAreaInfoCoordinates`
(`0x1406338d0`, recorded in `invasion_warp.rs:35`).

**The one genuinely unmeasured link is a grace's position without the map.** `BonfireWarpParam`
carries the entity id, the cleared-event flag, the icon, the category bits and the eight labels --
no coordinates. The pin rows have coordinates because the map's own construction path resolves
them: `nearest_place_name_in_area` reads the position out of the pin row at `+0x10` / `+0x14`, and
`project_to_map` calls `CS::WorldMapAreaConverter::ConvertMsbCoordsToMapCoords` against converters
held on the map **view model**. Both are map-construction artefacts, so neither survives a session
where the map was never opened.

### The lead that was read, and what it ruled out (2026-09-15)

`CS::CSMapPlaceNameOverrideRegionMan` is the only `PlaceName`-named symbol in the whole 1.16.2
dump (`0x140a73790`), and a region manager is the right shape for "what is this position called",
so it was the obvious candidate for a map-free name source. Two facts came out of reading it:

- **It is constructed at world load, not at map open.** Its single call site is `FUN_14061e800` at
  `0x14061f243` -- a `FieldArea` initialiser that allocates `WorldAreaTime`, `WorldMapManImp` and a
  row of sibling region managers (`CSPlayRegionPointMan`, `CSRideJumpRegionMan`,
  `CSOpenChrActivateThresholdRegionMan`) and stores each in a global. So
  `GLOBAL_CSMapPlaceNameOverrideRegionMan` is live from the moment a world is up, which is the
  property the banner needs.
- **It is an `Override` table, so it is not the base mapping.** The name says what it holds:
  regions that *replace* a place name, the exceptions. A tile with no override has no entry, so
  this manager alone cannot name an arbitrary neighbour.

### The base source, found on the param side (2026-09-15)

The manager overrides a param, and that param is `WorldMapPlaceNameParam`. Reading the param-name
table out of `eldenring-deobf.bin` -- a flat array of `{name pointer, table index}` pairs at
`0x143b3c000` -- puts three map params next to each other on 1.16.2:

| param | name string | table index |
|---|---|---|
| `WorldMapPointParam` | `0x142bb3400` | `0x57` |
| `WorldMapPieceParam` | `0x142bb3428` | `0x58` |
| `WorldMapPlaceNameParam` | `0x142bb3480` | `0x5a` |

That index is the same currency this repo already spends: `map_seams.rs:181` reaches
`BonfireWarpParamLookup` at table index `0x2B`, so the machinery for getting at a param by index is
written and working.

This supersedes the grace-position approach entirely. A param is resident from load, carries no
dependency on the map view model, and `WorldMapPlaceNameParam` is by construction the mapping from
a piece of the map to the name shown on it -- which is the table the banner wants, without a single
pin row.

**And then the row counts falsified it.** `WorldMapPlaceNameParam` has **10 rows** in the installed
regulation (`python3 scripts/regulation-params.py WorldMapPlaceNameParam`). Ten rows cannot name a
world. Its neighbours are no better: `WorldMapPieceParam` has 34, `WorldMapPointParam` 472 with
coordinate-shaped ids. So the name-string table gave the right *neighbourhood* and the wrong param,
and the index `0x5a` above is correct about what it indexes and useless for this purpose.

### `MapGdRegionInfoParam` is the table, and its row id IS the block id

293 rows, and the id packing is the one this repo already uses. `invasion_warp.rs:51` records
`BlockKey` as `[index, region, block, area]`; a `MapGdRegionInfoParam` id read as decimal digits is
the same four fields:

```text
60081002  ->  area 60   block 08   region 10   index 02     (m60_08_10_02)
10000000  ->  area 10   block 00   region 00   index 00     (m10_00_00_00)
```

184 of the 293 rows are area 60 -- the overworld -- which is exactly the coverage a per-tile name
table needs and exactly what `WorldMapPlaceNameParam`'s ten rows could never provide. A block id
therefore addresses a row **directly**, with no coordinates, no converter, no map view model and no
pin. That is the whole difficulty dissolved: the lookup the banner needs is an integer reinterpreted
as decimal digits.

**What is established, and what is not.** Established: the param, its row count, its overworld
coverage, and that its row id is the block id's decimal digit packing -- all read out of the
installed regulation, offline. Not established: which field of the row carries the `PlaceName` text
id (the reader used here needs no paramdef and so reports ids, not fields), and whether a region
with no row falls back to a coarser tile or to nothing. Both are static reads. No code should be
written against a guessed field offset -- the 10-row detour above is what that costs.

`nearest_place_name_text_id` (`map_hooks.rs:969`) stays as it is -- it is the map-pin path's
resolver and it is correct there. The banner wants a separate, coordinate-free lookup from block id
to name.

The `-1` case still needs a fallback in the banner text: that function's own doc records that an
unresolvable id renders as the literal `?PlaceName?`, not as an empty string.

### Documentation status

Documented in three places, all accurate:

- `crates/er-invasion-warp-core/src/local_invasion_config.rs:72-85` -- the `DEFAULT_CONFIG_TOML` the
  DLL writes on first run, so every user gets it. Explains the mechanism, the cost, and the
  one-value-no-OR limit.
- `docs/er-invasion-warp.invader-example.toml:33-39` -- the opposite emphasis, correctly: "LEAVE THIS
  FALSE to invade people who do NOT have this DLL [...] It is off here on purpose, not by oversight."
- `docs/invasion-warp-second-player-setup.md:87,136-138` -- a "Why `hunt` stays off" section.

There is **no crate README** for `er-invasion-warp` or `er-invasion-warp-core` (checked; neither
directory has one).

**One doc bug worth closing, unrelated to hunt itself.** `docs/er-invasion-warp.invader-example.toml:3`
tells the user to install the file as `...\ELDEN RING\Game\er-invasion-warp-core.toml`, but
`CONFIG_FILE_NAME` is `er-invasion-warp.toml` (`local_invasion_config.rs:38`). A user following that
line drops the file where nothing reads it and concludes the mod ignores its own config. The same
wrong name appears in `local_invasion_config.rs:1` (module doc) and in the example's own section
header comment. One-line fix in the example plus the module doc.

**Correction to the brief:** `crates/er-invasion-warp-core/src/select.rs` is not part of hunt mode. It
ranks warp targets (`nearest` / `next`) for the hotkey warp and the map cursor; it has no Steam, lobby
or matchmaking content. `lobby_pool.rs` is likewise a different feature -- `dll_users_only`, which
rewrites Seamless's `lobby_key` to move a player into a separate matchmaking pool. Hunt and the pool
are often confused because both end up narrowing who you meet, but hunt adds a key of ours while the
pool rewrites a key of Seamless's, and the pool is symmetric (it hides you from vanilla too) while
hunt is not (vanilla hosts can still be invaded by you when hunt is off).

---

## Item 8 -- "Impassable Greatbridge should say Redmane"

### Verdict

**The mod is picking the wrong source, which is a superset of "picking the wrong region id".** It never
asks for a region's name at all. The string itself is the game's own and is correct for what was asked.

### Where the name comes from (all VERIFIED)

**The string is not ours.** A search over the whole repo for `Greatbridge`, `Redmane` and `Caelid`
returns **zero hits** in any file. There is no place-name table in this repo to fix.

**It is resolved from the game's own `PlaceName` FMG at display time.**
`crates/er-invasion-warp/src/place_name.rs:69-116` calls `FUN_140d10b60(MsgRepositoryImp*, id)` --
the `PlaceName` getter, RVA `0xd1_0b60`, byte-checked against a pinned prologue -- and reads the
`wchar_t*` it returns. The getter tries `PlaceName`, then `PlaceName_dlc01`, then `PlaceName_dlc02`,
and returns the literal `?PlaceName?` on a miss, which this module treats as "no name" rather than
passing through. So the only thing the mod chooses is the **text id**.

**The text id is chosen by nearest Site of Grace, not by region.** This is the root cause, and it is
stated outright in the function's own docs -- `crates/er-invasion-warp/src/map_hooks.rs:934-978`:

> The `PlaceName` text id of the shipped warp row **nearest** `coords` [...] 225 of the shipped warp
> rows in areas 60/61 carry a valid `PlaceName` text id, and they are already sitting in the list
> being appended to, already projected into map space by the engine. **Naming a pin after the nearest
> one costs a walk over resident memory and no engine calls at all.**

The implementation is `nearest_place_name_in_area` (`map_hooks.rs:982-1033`): walk every existing row
in the world-map pin list, take its `BonfireWarpParam` pointer, require label kind 0 (a `PlaceName`,
not an `NpcName`), require a positive text id, and keep the one with the smallest squared 2D distance
`(x, z)`. The area byte is a preference, tried first and then dropped:
`nearest_place_name_text_id` (`map_hooks.rs:969-978`) calls the area-locked pass, then the unrestricted
pass, then `-1`.

The chosen id is then recorded per block by `record_place_name(block, id)`
(`map_hooks.rs:1437` -> `map_hooks/msb_catalog.rs:441-453`), stored in `PLACE_NAMES_BY_BLOCK`, and read
back later by `registry_place_names_for_block` (`msb_catalog.rs:459-468`) ->
`place_name_for_block` (`place_name.rs:133-137`), which sorts the ids and takes the **lowest** so a
block with several names always prints the same one.

### Why that produces "Impassable Greatbridge"

`BonfireWarpParam` rows are **Sites of Grace**, and a grace's `PlaceName` is the **grace's own name**,
not the name of the region containing it. "Impassable Greatbridge" is the grace on the bridge that is
the approach to Redmane Castle. Any invasion pin whose projected map coordinates are closer to that
grace than to any other warpable grace inherits its label. The region "Redmane Castle" is a different
piece of game data that this code path never consults.

This is the same failure class the function was written to fix, one step along: before it existed,
every one of the 365 pins read "Godrick the Grafted", because they all copied the donor row's label.
Nearest-grace was a large improvement over one-name-for-everything; it is still the wrong table.

So, to answer the question the task poses directly: **not a wrong region id, and not a wrong string --
a wrong source table.** There is no region id in play to be wrong. The mod asks "which grace is
nearest" and correctly gets the answer to that question.

### Where the wrong name is visible

Two surfaces, both fed from the same recorded ids:

- the world-map invasion pin's label, written into the synthetic param row at injection
  (`map_hooks.rs:1430-1471`);
- the rejection / success / arrival banner, e.g. `Rejected Impassable Greatbridge (another world)` --
  `reject_notice.rs:163-175`, with the name supplied by `place_name_for_block`.

`mode = "area"` in the local-invasion filter also **judges** on these ids (`named_location_text_ids`,
the "five names, five places to look" rule), so a mis-attributed name is not only cosmetic: marking
"this place" with `Shift+Insert` stores the grace's id, and the filter then accepts every block whose
nearest grace happens to be the same one.

### What the correct source is

`CS::WorldMapPlaceNameParam` exists in the image -- `docs/recon/deobf-rtti-classmap.tsv:2617`,
`0x142ad61d0 .?AVWorldMapPlaceNameParam@CS@@` -- alongside `CS::WorldMapPointParam`
(`:1376`), `CS::CSWorldMapPointMan` / `CSWorldMapPointManImplement` (`:4267`) and
`CS::CSMsbPointWorldMapPoint` (`:5394`). That is the family the engine itself uses to answer "which
named area is this world position in", which is what draws the big area-name banner when the player
crosses into Redmane Castle. **This repo does not reference any of them** (searched; the only hits are
the RTTI classmap rows above).

**HYPOTHESIS, and the thing to establish before writing any code:** that `WorldMapPlaceNameParam`
carries, per row, a map region plus a bounding shape plus a `PlaceName` text id, such that a point
query over it answers "Redmane Castle" for a coordinate inside the castle and something coarser
outside it. That is what the class name and the in-game behaviour imply; it has not been read in this
session and must not be assumed.

### Plan for item 8

1. **Read `WorldMapPlaceNameParam` before touching anything.** Ghidra MCP on `:8765` (1.16.2, the only
   named dump) for the RTTI-adjacent code: `getXrefsTo` the vtable at `0x142ad61d0`, find the
   constructor and the consumer, and decompile whatever does the point-in-region test. Then carry the
   addresses to 1.17 through `scripts/map-rvas-1162-to-1170.py` -> `scripts/map-rvas-1170-to-1171.py`
   and **read the 1.17.1 function before hooking anything** -- the installed game is 1.17.1 and every
   address in the named dump describes 1.16.2. Also check whether the param is already reachable
   host-side through `crates/soulsformats` + `tools/er-param-inspect`, which would let the mapping be
   validated offline against `regulation.bin` with no game at all.
2. **Confirm the specific case offline.** With the param readable, dump the row whose text id is the
   one currently recorded for the Redmane block and the row that ought to win. Two facts settle it:
   the text id `place_name_for_block` returns for that block today, and the id
   `WorldMapPlaceNameParam` would return for the same coordinates. If they differ as expected, the
   diagnosis above is proven rather than inferred.
3. **Add a region-first resolver, keeping nearest-grace as the fallback.** New function beside
   `nearest_place_name_text_id`: ask the region param for the pin's projected coordinates; on a hit,
   use that id; on a miss, fall through to the existing nearest-grace walk. The fallback must stay,
   for the reason `map_hooks.rs:943-955` documents at length: a pin whose eight label text ids are all
   negative is **not drawn at all** (`CS::WorldMapPinData::UpdateVisible` at `0x14087afa0` reduces the
   visible flag through `FUN_14088bcd0`, a loop that returns false unless some `param+0x30+12i >= 0`),
   so returning `-1` where nearest-grace would have returned something turns a mislabelled pin into an
   invisible one. That is a strict regression and is the single easiest way to get this fix wrong.
4. **Do not invent an id.** The existing rule holds: an id that resolves in no FMG renders the literal
   `?PlaceName?` on the pin. Only ids read out of real game data are ever used.
5. **Host tests.** The resolver's *selection* logic (region hit beats grace, miss falls through,
   negative never returned) is pure and belongs in `er-invasion-warp-core` with host tests, the same
   seam `select.rs` and `reject_notice.rs` already use. The memory reads stay in `map_hooks`.
6. **Runtime proof, when it comes to that.** The oracle is not a screenshot. Record, per named block,
   both the old nearest-grace id and the new region id, and assert in telemetry that the Redmane block
   resolves to the region name. Per `AGENTS.md`, a rendered-label claim needs a pixel or RAM oracle,
   not "the pin was placed".

### Scope note

This changes the ids stored in `PLACE_NAMES_BY_BLOCK`, which `mode = "area"` and
`named_location_text_ids` are written in terms of. A user's existing
`named_location_text_ids = [...]` in `er-invasion-warp.toml` holds grace ids; after this change the
same places resolve to region ids and those saved entries stop matching. That is a config migration,
not a silent behaviour change, and it needs a log line at minimum.

---

## Summary of file references

| item | primary files |
|---|---|
| 2 | `crates/er-invasion-warp/src/stall_watchdog.rs:38-51,76-80,125`; `crates/er-invasion-warp/src/local_invasion_filter/actions.rs:238-376,330-344,890-930`; `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs:180-229`; `crates/er-invasion-warp/src/restart_backoff.rs:41-49`; `crates/er-invasion-warp/src/local_invasion_filter.rs:1355-1371,1962,2083-2090`; `crates/er-invasion-warp-core/src/reject_notice.rs:8` |
| 3 | `crates/er-invasion-warp/src/lobby_publish.rs:16-21,82,189-191,211-216,246-317,1142-1264`; `crates/er-invasion-warp/src/lib.rs:272-315`; `crates/er-invasion-warp-core/src/oracles.rs:161-173,402,473,508-520`; `crates/er-invasion-warp-core/src/local_invasion_config.rs:38,72-85`; `docs/er-invasion-warp.invader-example.toml:3,33-39`; `docs/invasion-warp-second-player-setup.md:136-138` |
| 8 | `crates/er-invasion-warp/src/place_name.rs:1-137`; `crates/er-invasion-warp/src/map_hooks.rs:930-1039,1430-1471`; `crates/er-invasion-warp/src/map_hooks/msb_catalog.rs:425-483`; `docs/recon/deobf-rtti-classmap.tsv:1376,2617,4267,5394` |

---

## Adversarial validation (2026-09-13)

Second pass, written to refute rather than confirm. Static only: no build, no launch, no runtime
probe. Every verdict below was earned by opening the primary source, not by reading the section
above. Where this pass disagrees, the original text is left standing and corrected here.

### Verdict table

| # | claim | verdict |
|---|---|---|
| 1 | `TIMED_STATES` is stale and times the wrong states | **CONFIRMED** as a live defect, on stronger grounds than given -- but two supporting sub-claims downgraded, and the plan's safety argument is wrong in the player's favour |
| 2 | 30.0s `joinCheck` is ersc's f32; our cancel is immediate | **CONFIRMED**, verbatim |
| 3 | the retry dwell is not statically recoverable | **PLAUSIBLE-BUT-UNPROVEN** (exhaustive negative); the 600-frame measurement inside it is **CONFIRMED** |
| 4 | hunt adds one `RequestLobbyList` string filter in `steamclient64.dll` | mechanism **CONFIRMED**; the table's "you become invisible to [strangers]" row is **REFUTED** |
| 5 | nearest-grace naming; `-1` makes the pin invisible | **CONFIRMED**, and the `-1` claim is a documented RE finding, not an assumption |
| 6 | `CS::WorldMapPlaceNameParam` is at `0x142ad61d0` | **REFUTED for the installed game.** That address is a different class in 1.17 |
| 7 | doc names the config `er-invasion-warp-core.toml` | **CONFIRMED**, and undercounted |
| -- | `select.rs` / `lobby_pool.rs` are unrelated to hunt | **CONFIRMED** |

---

### Claim 1 -- `TIMED_STATES` (CONFIRMED, with corrections)

**The defect is real, and it can be proven without leaving the tree.** The plan argues it through
a `bd` memory about a Seamless renumber. That detour is unnecessary and it weakens the finding.
Three lines in this repository contradict each other on their own terms:

- `crates/er-invasion-warp/src/local_invasion_filter/ersc.rs:200` -- `state_searching: 0x0e`, for
  the one supported build.
- `crates/er-invasion-warp/src/stall_watchdog.rs:76-77` -- `TIMED_STATES` contains `0x0e`.
- `crates/er-invasion-warp/src/stall_watchdog.rs:23-26` -- "`SEARCHING` is UNBOUNDED by nature
  [...] Timing that state would fire a stall on an perfectly healthy empty search, which is the
  single most obvious way to get this wrong."

`crates/er-invasion-warp/src/local_invasion_filter/actions.rs:916-928` passes the raw read into
`guard.observe` with no mapping, exactly as the plan says. So the file times the state its own
documentation forbids timing. No external evidence is needed to call that a bug.

**It is worse than the plan states, because the loop arms on the same value it then times.**
`local_invasion_filter.rs:1362-1364` sets `AUTO_SEARCH_ARMED` when `state == abi.state_searching`
(`0x0e`), and `watch_for_stall` returns early unless that flag is set (`actions.rs:901`). So the
watchdog is switched on by entering `0x0e` and then cancels `0x0e` five seconds later. A quiet
bracket -- the documented normal case, "three consecutive filtered queries returned 0, 0, then 1
lobby" -- is cancelled on a five-second clock.

**New defect the plan missed, and it inverts the plan's own safety argument.** The plan writes that
`0x0e` is "the one state this file's own documentation and its own test
`a_long_search_is_never_a_stall` say must never be timed." The test says no such thing. It is at
`stall_watchdog.rs:252-256` and it observes `state::SEARCHING`, which is `0x0d`
(`stall_watchdog.rs:40`) -- a value absent from `TIMED_STATES`. The test therefore **passes
vacuously** while the real searching state is timed, and has done since the v2.0.x re-pin.

Measured scope of the stale `state` module: `stall_watchdog::state` is referenced nowhere outside
`stall_watchdog.rs`, and the test module begins at line 221, so **every** use of `IDLE`/`SEARCHING`/
`RETRYING` is inside tests. Two consequences the plan should have drawn:

- the `IDLE = 0x00` vs `state_idle: 0x01` disagreement the plan lists as evidence point 1 has **no
  runtime effect at all** -- it is test-only. `TIMED_STATES` (line 76) is the sole line with
  runtime consequence, because `is_transient` (line 116) is its only consumer;
- the stale test constants are not cosmetic: they are what allows the guard test to keep passing.
  That makes the plan's step 2 (assert `!TIMED_STATES.contains(&abi.state_searching)`) the
  **necessary** deliverable, not the cheaper of two options.

**Sub-claim DOWNGRADED: "`0x12` is where v1.9.9's `0x11 RETRYING` landed after the `+1`."** The
renumber evidence does not reach this state. `bd ersc-v200-repin-addresses-and-enum-shift-2026-09-02`
proves the `+1` by scanning every `mov dword [reg+STATE], imm32` site in both builds, and the set it
recovered is seven values: `{0x1, 0x3, 0x6, 0x9, 0xd, 0x22, 0x23}` -> `{0x2, 0x4, 0x7, 0xa, 0xe,
0x23, 0x24}`. `0x0e`, `0x11`, `0x12` and `0x13` are **not in it** -- and `bd
ersc-retry-constant-not-statically-recoverable-two-negatives-2026-08-06` says why, in terms:
"no plaintext site in ersc.dll writes or compares states 0x0E/0x11/0x12/0x13/0x14". Those states are
written from virtualised code and were never measured on either side of the update. The same memory
records the repin author choosing the shift rather than measuring it -- "our runtime-measured
progress marker 0x12 -> 0x13 (shifting it is the SAFE option)". So:

- `state_searching = 0x0e` under v2.0.x is **measured** (`0xd` -> `0xe` is in the scanned set).
  The `0x0e` half of the bug is proven.
- `0x12 == retrying` and `0x13 == offer_received` under v2.0.x are **inferred** from a uniform `+1`.
  Treat the two remaining `TIMED_STATES` entries as unknown, which under this file's own
  fail-closed rule means they should not be timed either.

The plan's step 1 (re-measure with `scripts/ersc-disas.py states`) is the right response and is
strengthened by this: a `states` dump cannot recover `0x0e`/`0x12`/`0x13` at all, because they have
no plaintext write sites. Expect that mode to come back without them, and do not read an empty
result as "no change needed". The honest v2.0.x set is *unknown*, and the fail-closed answer is a
**shorter** list, not a shifted one.

Git history **CONFIRMED** exactly as described: `git log -L76,81:.../stall_watchdog.rs` returns two
commits, `c705c975` (2026-08-13, crate landing, `[0x0e, 0x12, 0x13, 0x22, 0x23]`) and `0e084240`
(2026-09-08, removes the last two). The landing predates Seamless v2.0.0 (2026-09-02), so the values
are original v1.9.9 numbering.

One corroboration the plan did not use, and the cleanest single proof the enum moved: the comment at
`stall_watchdog.rs:88-89` records a measured walk of `0x23 -> 0x24 -> idle`, while the module's own
doc walk at line 20 reads `0x22 -> 0x23 -> 0x00 IDLE`. The same file contains a pre-renumber and a
post-renumber observation of the same transition.

Terminology: the supported build is **v2.0.1** (`ERSC_SUPPORTED_VERSION` in
`build-support/prologue_build.rs:135`), not v2.0.0. The plan mixes both spellings.

### Claim 2 -- CONFIRMED

`actions.rs:330-344` reads verbatim as quoted, including "`joinCheck`/`waitInit` are f32 seconds".
The cancel is immediate and unconditional two lines later: `actions.rs:346`,
`unsafe { cancel(owner, 0, 1, 1) }` -- a direct call into ersc's own option callback, no polling, no
deferral, no queue. Nothing between the decision and the write.

### Claim 3 -- PLAUSIBLE-BUT-UNPROVEN, mixed

The memory is two different kinds of evidence and the plan flattens them into one.

- **Positive, CONFIRMED:** the dwell is a 600-tick frame counter, "nine consecutive, zero
  variance", with the focused/unfocused framerate explaining the ~10s/~20s spread. That is a
  runtime measurement and it stands.
- **Negative:** unrecoverability. It is an *exhaustive* negative over a bounded space -- all 40 VM
  stub bodies scanned for any instruction touching `[reg+0x110]` as a dword, zero hits, plus two
  independent immediate hunts -- which is much stronger than an abandoned search. But it is still
  the absence of a finding, and the memory itself names the untried routes ("Only devirtualisation
  or a runtime memory diff will produce the constant"). The plan calling it "the definitive negative
  result on ever reading that constant statically" overstates it by one notch. Nothing actionable
  turns on the difference; the recommendation not to look again is sound.

### Claim 4 -- mechanism CONFIRMED, one user-facing consequence REFUTED

Mechanism verified. `lobby_publish.rs:71`: "`RequestLobbyList` is detoured -- in
`steamclient64.dll`'s interface vtable, not in `ersc.dll`." `install_hunt_hook`
(`lobby_publish.rs:1205-1246`) reads the interface pointer, walks to
`REQUEST_LOBBY_LIST_SLOT` (`= 4`, line 216) and registers a union hook. One filter, one slot.

Minor: the module installs **three** detours in total (`lobby_publish.rs:54`) -- `SetLobbyData`,
`AddRequestLobbyListStringFilter` and `RequestLobbyList`. Only the last is hunt's, so the plan's
count is right for hunt and its heading "exactly one" would be wrong for the module.

**REFUTED -- the comparison table's "hostility to strangers" row.** It reads "`hunt = true` -> you
become invisible to, and they to you". The first half is false and the module doc says so directly
(`lobby_publish.rs:33-34`): "a vanilla Seamless invader never filters on our key, so publishing it
changes nothing for them -- they still match this host exactly as before", and line 38, "The cost
falls entirely on an INVADER who chooses to filter." Filtering narrows **your own result set**; it
does not remove you from anyone else's. The cost is one-directional. The plan's own closing
paragraph (line 330) states this correctly -- the table contradicts the prose, and the table is the
part a user would be shown. Fix the table, not the prose.

**"Only one location" -- CONFIRMED in code, the reason is asserted.** `hunt_filter_value`
(`lobby_publish.rs:260-266`) returns `None` for two or more wanted blocks, and `hunt_refusal`
(`:310-314`) explains it. But "a Steam string filter tests ONE value with no OR" is a statement
about the Steam API, not something this repo measured -- the measured fact is only the 13-lobbies
-> 0 exclusion test (`lobby_publish.rs:29-31`). It is very likely right, and it is still an
assertion. Not noted anywhere: a *sequence* of queries, one per marked block, would express the OR
at a level above the filter. That is a real design option the plan closes off by calling the limit
a transport limit.

### Claim 5 -- CONFIRMED

`map_hooks.rs:982-1033` is exactly as described: walk every existing pin row, take
`ROW_PARAM_POINTER_OFFSET`, require label kind 0, require `text_id > 0`, keep the smallest
`(x-x')^2 + (z-z')^2` from `row+0x10`/`row+0x14`. `nearest_place_name_text_id` (`:969-977`) tries
area-locked, then unrestricted, then `.unwrap_or(-1)`. No region param is touched.
`place_name.rs` is 182 lines and contains no region path -- only `place_name_for_text_id` and
`place_name_for_block`, as the plan says.

**The `-1` behaviour is NOT an assumption** -- contrary to the framing this pass was asked to test.
It is documented at `map_hooks.rs:943-949` with a named mechanism and addresses:
`CS::WorldMapPinData::UpdateVisible` (`0x14087afa0`) computes `row+0x0c` as `A && B && C && D`,
where `D` reduces to `FUN_14088bcd0`, a loop over the 8 labels returning false unless some
`param+0x30+12i >= 0`. The comment even records that it *replaced* the earlier assumption ("the
comment that used to sit here [...] was describing a label that never gets the chance to be empty").
That is a prior session's RE finding, recorded, and it is the strongest class of evidence available
without a run. Caveat: like every address in that comment it carries **no build tag** -- see claim 6.

### Claim 6 -- REFUTED for the installed game

The plan gives `0x142ad61d0` for `CS::WorldMapPlaceNameParam` and names no build. Resolved the RTTI
directly out of each flat image (`VA = 0x140000000 + file_offset`; vtable`-8` -> COL -> TypeDescriptor
-> name):

```text
eldenring-deobf.bin        (1.16.x)  0x142ad61d0 -> .?AVWorldMapPlaceNameParam@CS@@
eldenring-deobf-1.17.bin   (1.17.0)  0x142ad61d0 -> .?AVKeywordViewModel@CS@@
eldenring-deobf-1.17.1.bin (1.17.1)  0x142ad61d0 -> .?AVKeywordViewModel@CS@@
```

**On the installed 1.17.1 game that address is a different class entirely.** The real one, found by
searching each image for the mangled name and walking TypeDescriptor -> COL -> vtable:

```text
1.16.x  : TypeDescriptor 0x143cabbe0  COL 0x143301918  vtable 0x142ad61d0
1.17.1  : TypeDescriptor 0x143cafc40  COL 0x143304bd8  vtable 0x142ad9250   (+0x3080)
```

Corroborated through the Ghidra daemons: `getXrefsTo 0x142ad61d0` returns 14 data refs from
`FUN_14087b0d0` on `:8765` (1.16.2) and 4 refs from a different function, `FUN_1408631e0`, on
`:8767` (1.17.0). Different neighbourhood, different class.

Three defects follow:

1. **The address is quoted with no build.** Per `AGENTS.md` that alone is worth reporting, and here
   it is not academic -- following it into 1.17 lands on `KeywordViewModel`.
2. **Plan step 1's carry route is a category error.** It says to carry the address to 1.17 through
   `scripts/map-rvas-1162-to-1170.py` -> `scripts/map-rvas-1170-to-1171.py`. Those tools match
   `.text` functions by masked instruction bytes; they cannot carry an `.rdata` vtable, and the
   1.17.1 mapper only models the `.text` `+0x70` shift at all. The measured `.rdata` drift for this
   object is `+0x3080`, which neither tool would ever produce. Re-resolve the RTTI in the target
   image instead -- it is a ten-line script and it is an identity check rather than an inference.
3. **Provenance conflict, unresolved.** `docs/recon/deobf-rtti-classmap.README.md:3` says the
   harvest is from Elden Ring **1.16.1**; `AGENTS.md` says `eldenring-deobf.bin` is **1.16.2**. One
   of the two is stale. The plan treats the TSV as interchangeable with the `:8765` (1.16.2) dump
   without noticing. Worth closing before anyone pins a hook off that file.

"This repo never references it" -- **CONFIRMED**. A scan of `crates/`, `scripts/`, `docs/`,
`tools/` for `WorldMapPlaceNameParam|WorldMapPointParam|CSWorldMapPointMan|CSMsbPointWorldMapPoint`
returns only the four RTTI rows plus this plan's own citations.

The class does exist and the hypothesis about its contents remains untested, so the substance of
item 8's plan survives. Only the address and the carry route are wrong.

### Claim 7 -- CONFIRMED, and undercounted

`crates/er-invasion-warp-core/src/local_invasion_config.rs:38` --
`pub const CONFIG_FILE_NAME: &str = "er-invasion-warp.toml";`, consumed at
`local_invasion_filter.rs:327-328`. The wrong name appears in **four** places, not the two the plan
names:

- `docs/er-invasion-warp.invader-example.toml:1` (the file's own title line);
- `docs/er-invasion-warp.invader-example.toml:3` (the install instruction -- the damaging one);
- `crates/er-invasion-warp-core/src/local_invasion_config.rs:1` (module doc);
- `crates/er-invasion-warp-core/src/local_invasion_config.rs:250` (**missed by the plan**) --
  "Parse `er-invasion-warp-core.toml` text."

### The brief's `select.rs` / `lobby_pool.rs` pointer -- dismissal CONFIRMED

`crates/er-invasion-warp-core/src/select.rs` is 406 lines and contains **zero** matches for
`hunt|RequestLobbyList|lobby_key|SetLobbyData|[Ss]team|matchmak`. It ranks `ResolvedTarget`s by
distance. Unrelated to hunt, as the plan says.

`crates/er-invasion-warp-core/src/lobby_pool.rs` is the `lobby_key` substitution feature, and its
own module doc (lines 25-28) independently confirms the plan's distinction, including the asymmetry
point the plan's own hunt table got wrong: the pool "moves a player into a different pool entirely
and SYMMETRICALLY. They cannot see vanilla lobbies and vanilla cannot see theirs". Hunt is not
symmetric; the pool is.

---

### What this pass changes about what to do

1. **Item 2, step 1 changes shape.** Do not "re-derive the `+1`". `0x0e`/`0x12`/`0x13` have no
   plaintext write sites in either build, so `scripts/ersc-disas.py states` cannot recover them and
   a shifted list would be a second guess dressed as a measurement. Remove `abi.state_searching`
   from `TIMED_STATES` now -- that one is proven -- and drop `0x12`/`0x13` under the file's own
   fail-closed rule until a run identifies what they are. A shorter list is strictly safer: the
   worst case is a stall that is not auto-recovered, against a current worst case of every quiet
   search being cancelled at five seconds.
2. **Item 2, step 2 is mandatory, not the cheaper option.** The existing guard test passes
   vacuously because `state::SEARCHING` is the stale `0x0d`. Fix the test constants in the same
   commit or the new assertion inherits the same blind spot.
3. **Item 8, step 1: replace the address and the carry route.** Use `0x142ad9250` for 1.17.1, or
   better, re-resolve the RTTI in whichever image is being read. Do not route an `.rdata` address
   through the `.text` RVA mappers.
4. **Item 3: fix the comparison table's "hostility to strangers" row** before anyone quotes it to a
   user. Publishing the key costs a host nothing; only the filtering invader narrows their own view.
5. **Item 3's doc fix has four sites, not two.**
