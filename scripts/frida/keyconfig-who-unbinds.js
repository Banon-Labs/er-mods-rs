// Who clears the player's d-pad binding while they are playing?
//
// # The gap this closes
//
// Action 0x0f of `CSPcKeyConfig` is "switch right armament". On 2026-09-19 a live process was
// measured with its pad code at `-1` while the game's own default table two tables over said
// `2003`, and the player's d-pad right did nothing all session. A launch started minutes later
// read `2003` from the same save -- no container had been written in between -- so the `-1` was
// not loaded from disk. Something clears it during play.
//
// The candidate is the key-config deserializer at rva `0x242ee0`, which is not a field write but a
// bulk copy: it walks a save stream and overwrites the first 26 rows of the current table, five
// dwords at a time, then sets the active-device word and rebuilds the pad manager. One call with a
// stream whose row 0x0f carries `-1` unbinds the button, and nothing else in the table has to move
// for the symptom to appear. That is exactly the shape the measurement found: one pad code in 54
// differing from the defaults.
//
// # What this records
//
//   * every call to the deserializer -- the caller's module and return address, the row 0x0f pad
//     code before and after, and the active-device word it installs;
//   * `row()`, pulled by the driver, so the binding can be sampled between calls without a hook on
//     a hot accessor. If the code goes to `-1` while the call count has not moved, the writer is
//     somewhere else and this agent is wrong rather than silent.
//
//   * a direct sample of the row every 250 ms, which reports a move whether or not either hook ran,
//     and writes the game's own default back so a player who hits this keeps the button.
//
// No watchpoint is armed. The field is a candidate, not a confirmed writer, and a debug register
// would be the wrong instrument for a bulk copy anyway; `MemoryAccessMonitor` over the page has
// already been measured killing this game. The only write is the restore described above.
'use strict';

const MODULE = 'eldenring.exe';

// Byte-verified 1.16.2 -> 1.17.0 at delta 0, and the rva is below the 1.17.0 -> 1.17.1 boundary
// (`0xafefe9`), so this address is the running build's.
const DESERIALIZE_RVA = 0x242ee0;

// The only code in the game that writes this table, and the one this agent was missing.
//
// Measured 2026-09-19 on the 1.17 dump: of the 45 functions that reference the `CSPcKeyConfig`
// singleton across 98 references, exactly two compute `cfg + 0x440` -- `FUN_14023f220`, which bulk
// copies 26 rows of five dwords each FROM a serialized stream INTO the table, and `FUN_14023f280`,
// which copies the same 26 rows back OUT. Every other one of the 45 reaches a row through
// `GetAssign` at rva 0x242ab0, which returns a copy on the caller's stack and so cannot write
// anything at all.
//
// Action 0x0f is index 15, inside the 26 rows both functions cover. So the row is never edited in
// place during play: a stored configuration carrying `-1` is loaded over the live table wholesale.
// The hook on `DESERIALIZE_RVA` above would have sat silent through exactly that event, which is
// the failure this pairing exists to prevent -- an agent hooked next to the writer reports the
// same nothing as an agent hooked on dead code.
//
// Both rvas are below the 1.17.0 -> 1.17.1 boundary `0xafefe9`, so the installed build shares them.
const SAVE_LOAD_APPLY_RVA = 0x23f220;

// The serializer. It is hooked because it names the moment the damage became durable: a `-1` read
// out of the table here is a `-1` about to be written down, and the call that does it is upstream
// of every later session that loads it back.
const SAVE_LOAD_STORE_RVA = 0x23f280;

// The funnel, and the reason this agent does not rest on the candidate above. Every path in this
// module that changes bindings ends by rebuilding the pad manager from the table: the deserializer
// calls it, load-defaults (rva 0x243030) calls it, restore-defaults (0x243330) calls it. Hooking
// the funnel as well as the candidate means a writer this agent did not predict still shows up,
// instead of the agent going quiet and the silence reading as "nothing wrote it".
const REBUILD_RVA = 0x243200;

// `qword ptr [0x143d61f08]`, the key-config singleton on 1.17.
const KEY_CONFIG_GLOBAL_RVA = 0x3d61f08;

// `lea rcx,[rcx + idx*0x14 + 0x440]` in the accessor at rva 0x242ab0.
const CURRENT_TABLE_OFFSET = 0x440;
const ROW_STRIDE = 0x14;

// Switch right armament. Default pad code 2003.
const WATCHED_ACTION = 0x0f;

// What a row with no button on it holds. The symptom is this value in the pad field.
const UNBOUND = -1;

const game = Process.findModuleByName(MODULE);
if (game === null) {
  send({ tag: 'fatal', reason: MODULE + ' not loaded' });
} else {
  const counts = { calls: 0, rebuilds: 0, flips: 0 };

  function configBase() {
    try {
      const base = game.base.add(KEY_CONFIG_GLOBAL_RVA).readPointer();
      return base.isNull() ? null : base;
    } catch (error) {
      return null;
    }
  }

  function padCode() {
    const cfg = configBase();
    if (cfg === null) return null;
    try {
      return cfg.add(CURRENT_TABLE_OFFSET + WATCHED_ACTION * ROW_STRIDE).readS32();
    } catch (error) {
      return null;
    }
  }

  function callerOf(context) {
    try {
      const home = Process.findModuleByAddress(context.returnAddress);
      return {
        module: home === null ? 'unknown' : home.name,
        at: home === null
          ? context.returnAddress.toString()
          : '+0x' + context.returnAddress.sub(home.base).toString(16),
      };
    } catch (error) {
      return { module: 'unreadable', at: '?' };
    }
  }

  const target = game.base.add(DESERIALIZE_RVA);
  Interceptor.attach(target, {
    onEnter(args) {
      // The deserializer takes the config as its first argument, so a call on some other object
      // is worth seeing rather than assuming: it would mean the table this reads is not the table
      // that call writes.
      this.arg0 = args[0];
      this.before = padCode();
      this.who = callerOf(this.context);
    },
    onLeave() {
      counts.calls += 1;
      const after = padCode();
      if (this.before !== after) counts.flips += 1;
      const cfg = configBase();
      send({
        tag: 'deserialize',
        n: counts.calls,
        caller: this.who.module,
        at: this.who.at,
        cfg_arg: this.arg0.toString(),
        cfg_global: cfg === null ? null : cfg.toString(),
        pad_before: this.before,
        pad_after: after,
        flipped: this.before !== after,
      });
    },
  });

  const rebuild = game.base.add(REBUILD_RVA);
  Interceptor.attach(rebuild, {
    onEnter() {
      this.before = padCode();
      this.who = callerOf(this.context);
    },
    onLeave() {
      counts.rebuilds += 1;
      const after = padCode();
      if (this.before === after) return;
      counts.flips += 1;
      send({
        tag: 'rebuild-flip',
        n: counts.rebuilds,
        caller: this.who.module,
        at: this.who.at,
        pad_before: this.before,
        pad_after: after,
      });
    },
  });

  // The writer itself. Every call is reported whether or not the row moved, because a load that
  // installs the correct value is the control case: it dates the moment the stored configuration
  // was still good, and without it a session with no report cannot be told from a session where
  // the load never ran.
  counts.applies = 0;
  Interceptor.attach(game.base.add(SAVE_LOAD_APPLY_RVA), {
    onEnter() {
      this.before = padCode();
      this.who = callerOf(this.context);
    },
    onLeave() {
      counts.applies += 1;
      const after = padCode();
      if (this.before !== after) counts.flips += 1;
      send({
        tag: 'save-load-apply',
        n: counts.applies,
        caller: this.who.module,
        at: this.who.at,
        pad_before: this.before,
        pad_after: after,
        // The whole point of this hook: a load that brought the dead value in with it.
        installed_unbound: after === UNBOUND,
        flipped: this.before !== after,
      });
    },
  });

  // The serializer, reported only when it is about to write the dead value down. A store of the
  // correct value is the normal case and says nothing.
  counts.stores = 0;
  Interceptor.attach(game.base.add(SAVE_LOAD_STORE_RVA), {
    onEnter() {
      counts.stores += 1;
      const pad = padCode();
      if (pad !== UNBOUND) return;
      send({
        tag: 'save-load-store-unbound',
        n: counts.stores,
        caller: callerOf(this.context),
        pad: pad,
        // Where the stack was when the damage was committed to storage. This is the call chain
        // that makes the next session start broken, so it is worth the cost here and nowhere else.
        stack: Thread.backtrace(this.context, Backtracer.ACCURATE)
          .slice(0, 12)
          .map(DebugSymbol.fromAddress)
          .map(String),
      });
    },
  });

  // # The half the hooks cannot cover
  //
  // Two interceptors can only report a writer they were pointed at. The measured symptom -- one pad
  // code in 54 sitting at `-1` -- was found by reading the table, not by catching a call, and a
  // launch minutes later read the default back, so whatever does it is rare and may be in neither
  // hooked function. A detector that only counts its own hits reports that case as silence.
  //
  // So the row is checked at the one moment it decides anything: the accessor the game calls to
  // fetch a binding row. A timer would be a poll against a clock nobody is reading, and it can only
  // ever find the damage afterwards; this fires when the game asks, on the game's own frame, and a
  // restore done here lands before the caller reads the value -- so the press the player just made
  // still works. On a move the whole table is diffed against the game's own defaults in the same
  // pass, because a bulk copy and a single-field poke look identical in one row and completely
  // different across 54.
  //
  // The default is then written back. That is a write into the game, and it is deliberate: it is
  // the same value `scripts/er-keybind-repair.py --restore-pad 0x0f` already installs through
  // `/proc/<pid>/mem`, taken from the table the game itself built two tables over, so it restores
  // rather than invents. Without it the player loses the button for the rest of the session and the
  // only way to get it back is to notice and ask. The event is still reported in full -- restoring
  // it does not hide it, and `flips` keeps counting.
  //
  // `cmp r8d,0x35; movsxd rax,r8d; lea r8,[rax + rax*4]` at rva 0x242ab9 on the 1.17 image: the
  // action index is the third integer argument and the config object is the first, so the object
  // consulted is read from the call rather than from the global, and a call against some other
  // config would show up as a mismatch instead of passing silently.
  const ROW_ACCESSOR_RVA = 0x242ab0;

  // `CSFeManImp::Update`, the per-frame owner of the quick-slot switch this binding drives.
  // 1.16.2 `0x140771bd0` carried forward by `scripts/map-rvas-1162-to-1170.py` (unique, 40-byte
  // signature, 32 fixed) to `0x140772a50`, and the rva is below the 1.17.0 -> 1.17.1 boundary
  // `0xafefe9` so the installed build shares it. Read before hooking: it loads a float global and
  // branches on it before doing any work, which is the shape of an update taking a frame delta.
  const FRAME_UPDATE_RVA = 0x772a50;
  const DEFAULT_TABLE_OFFSET = 0x008;
  const PAD_FIELD_OFFSET = 0x00;
  const ROW_COUNT = 0x36;

  function rowAddress(cfg, table, action) {
    return cfg.add(table + action * ROW_STRIDE);
  }

  function defaultPad(cfg, action) {
    try {
      return rowAddress(cfg, DEFAULT_TABLE_OFFSET, action).add(PAD_FIELD_OFFSET).readS32();
    } catch (error) {
      return null;
    }
  }

  // Every row whose current pad code differs from the default one. One entry means a single-field
  // write; a run of them means something copied a block in.
  function padDiff(cfg) {
    const rows = [];
    for (let action = 0; action < ROW_COUNT; action += 1) {
      try {
        const now = rowAddress(cfg, CURRENT_TABLE_OFFSET, action).add(PAD_FIELD_OFFSET).readS32();
        const was = rowAddress(cfg, DEFAULT_TABLE_OFFSET, action).add(PAD_FIELD_OFFSET).readS32();
        if (now !== was) rows.push({ action: action, pad: now, expected: was });
      } catch (error) {
        rows.push({ action: action, pad: 'unreadable', expected: null });
      }
    }
    return rows;
  }

  counts.lookups = 0;
  counts.watchedLookups = 0;
  counts.frames = 0;
  counts.restores = 0;
  let lastSampled = padCode();

  // The invariant, checked wherever a caller gives us a config object: this row matches the table
  // the game built from its own defaults. Comparing against the last value this agent saw instead
  // would make an already-damaged process look healthy, because the damage is what the agent would
  // have seeded itself with. Measured 2026-09-19: attaching to a process whose row was already
  // `-1` reported no move and restored nothing.
  function enforce(cfg, where, context) {
    if (cfg === null) return;
    let pad;
    try {
      pad = rowAddress(cfg, CURRENT_TABLE_OFFSET, WATCHED_ACTION).add(PAD_FIELD_OFFSET).readS32();
    } catch (error) {
      return;
    }
    const want = defaultPad(cfg, WATCHED_ACTION);
    if (want === null) return;
    if (pad === want && pad === lastSampled) return;

    const diff = padDiff(cfg);
    const hooksRan = counts.calls > 0 || counts.rebuilds > 0;
    counts.flips += 1;

    let restored = false;
    if (pad !== want) {
      try {
        rowAddress(cfg, CURRENT_TABLE_OFFSET, WATCHED_ACTION).add(PAD_FIELD_OFFSET).writeS32(want);
        restored =
          rowAddress(cfg, CURRENT_TABLE_OFFSET, WATCHED_ACTION)
            .add(PAD_FIELD_OFFSET)
            .readS32() === want;
        if (restored) counts.restores += 1;
      } catch (error) {
        restored = false;
      }
    }

    const global = configBase();
    send({
      tag: 'row-moved',
      seen_at: where,
      action: WATCHED_ACTION,
      pad_before: lastSampled,
      pad_after: pad,
      expected: want,
      cfg_arg: cfg.toString(),
      cfg_global: global === null ? null : global.toString(),
      caller: context === undefined ? null : callerOf(context),
      // The case this whole block exists for: the binding changed and neither hooked function had
      // run, which means both hooks are on the wrong code and the writer is still unnamed.
      no_hook_had_run: !hooksRan,
      deserialize_calls: counts.calls,
      rebuild_calls: counts.rebuilds,
      // A block copy shows up here as several rows at once; a targeted poke as exactly one.
      rows_off_default: diff,
      restored_to_default: restored,
      counts: Object.assign({}, counts),
    });

    try {
      lastSampled = rowAddress(cfg, CURRENT_TABLE_OFFSET, WATCHED_ACTION)
        .add(PAD_FIELD_OFFSET)
        .readS32();
    } catch (error) {
      lastSampled = pad;
    }
  }

  // The enforcement point. One call per frame, and it is the owner of the quick-slot switch this
  // binding drives, so the check runs on the game's own clock with no timer involved and a restore
  // lands before the frame that would have read the dead row.
  //
  // Two sessions ended while this was armed on 2026-09-19, at about eight and eleven minutes, and
  // it was nearly deleted twice over it. Both were the game shutting itself down, not a fault:
  // `er-quickload-crash-log.txt` for `br-20260919-034556-4b2e` records
  //
  //   [+661234ms] process-exit via ExitProcess code=0x0 handle=0x0 callers=[...]
  //
  // with `throw_records=0 fault_records=0 fatal_reported=0` in the breadcrumb beside it. The first
  // session left no fault record either, which was read at the time as a process lost inside a
  // trampoline -- where the faulting frame is not one the game's handler can unwind -- because a
  // clean `ExitProcess` line had not been looked for. It was there.
  //
  // So the standard this comment used to carry, twice-with-it-armed, was met by count and refuted
  // by the record. Count nothing: read `er-quickload-crash-log.txt` for the `process-exit` line,
  // and if the exit was `ExitProcess code=0x0` the hook is not implicated no matter how many times
  // it happens.
  Interceptor.attach(game.base.add(FRAME_UPDATE_RVA), {
    onEnter() {
      counts.frames += 1;
      // Said once, because a hook on the wrong address is silent in exactly the way a hook on the
      // right address is when nothing has happened yet, and the first of those reads as an answer.
      if (counts.frames === 1) {
        send({ tag: 'frame-hook-live', rva: '0x' + FRAME_UPDATE_RVA.toString(16) });
      }
      enforce(configBase(), 'frame', this.context);
    },
  });

  // Kept as a probe, not as enforcement. Measured 2026-09-19 on a live session: with the row
  // sitting at `-1` and the player in world, this never fired for action 0x0f, so the pad path does
  // not consult it -- it is the config surface's reader. It stays because the counter is the
  // evidence for that sentence, and a future session that sees it fire has learned something.
  Interceptor.attach(game.base.add(ROW_ACCESSOR_RVA), {
    onEnter(args) {
      counts.lookups += 1;
      if (args[2].toInt32() !== WATCHED_ACTION) return;
      counts.watchedLookups += 1;
      if (counts.watchedLookups === 1) {
        send({ tag: 'accessor-live', action: WATCHED_ACTION, lookups_before: counts.lookups });
      }
      enforce(args[0], 'accessor', this.context);
    },
  });

  // Pulled by the driver rather than pushed on a timer: a timer prints the same line against a
  // clock nobody is reading, and the question here is only ever asked when the driver has a reason
  // to ask it. `seen` is the last value either hook observed, so a poll that finds the code changed
  // while neither counter moved is reported as such -- the case where both hooks are on the wrong
  // functions, which is the one an agent that only counted its own hits would show as silence.
  let seen = padCode();
  rpc.exports = {
    row: function () {
      const pad = padCode();
      const missed = pad !== seen && counts.calls === 0 && counts.rebuilds === 0;
      seen = pad;
      return {
        action: WATCHED_ACTION,
        pad: pad,
        counts: Object.assign({}, counts),
        changed_with_no_hook_hit: missed,
      };
    },
  };

  send({
    tag: 'ready',
    module: MODULE,
    base: game.base.toString(),
    target: target.toString(),
    pad_now: padCode(),
  });
}
