// Why the vanilla invasion-bounds popup cannot be backed out of: read the one byte that decides.
//
// # The question this settles and the two answers it separates
//
// `CS::CSPlayerMenuCtrl::StartInvasionFromBoundsPopup` is the step-2 handler for the bounds popup
// ("Attempting to invade another world."), and its first branch is the whole back-out:
//
// ```text
//   1407c2c60: test %dl,%dl
//   1407c2c62: je   1407c2c78
//   1407c2c64: movl $0x5,0x10(%rcx)     <- step 5, the only route to the popup close
// ```
//
// Step 5 is the sole path to `0x1407ee500`, which is the only writer of `CSMenuManImp+0x104 = 0`.
// So if `dl` is zero when the player presses Back, nothing anywhere else can close the dialog.
// What disk evidence cannot say is WHICH of two things is true, and they need different fixes:
//
// - `dl` goes nonzero on the press and the dialog still stands -> the handler is at fault.
// - `dl` stays zero while the popup's own result at `+0x1a0` moves -> the dialog does report the
//   cancel and the ctrl's result-0 branch is the bug: it calls `*0x88(vtable)` and returns with
//   the step still 2, never reaching the close.
// - `dl` stays zero and `+0x1a0` never moves -> Back never reaches the dialog's job at all, and
//   the next hook belongs on that job's input reader rather than here.
//
// # Why every address is followed before it is hooked
//
// About 28 percent of function entries on this build are Arxan stubs: a rel32 jump with the
// original bytes 5..15 left intact. A hook placed on the stub is overwritten rather than
// installed, and the symptom is a site that reports zero hits -- indistinguishable from a
// function that never ran. `follow` is the same fix the Wine XInput thunk needed.
//
// # Addresses
//
// Installed build 1.17.1, every rva below `0xafefe9` so the 1.17.0 deobf image and the live
// process agree. `0x1407c2c50` was byte-checked against `eldenring-deobf-1.17.1.bin` before this
// agent was written, and the check is worth repeating rather than trusting: the pre-existing
// `bounds-popup-oracle.js` carries `0x1407c1dd0` for this same function, which on 1.17.1 lands
// mid-instruction and is not an entry at all.
const RVA = {
  // The step-2 handler. Runs once per frame while the popup is up, which is why everything below
  // reports on CHANGE rather than per call.
  startInvasion: 0x7c2c50,
  // The raiser, so the log says when the popup went up instead of leaving it to be inferred.
  raiser: 0x7c3310,
  // The cancel-prompt twin, present so a press that lands on the OTHER dialog is named rather
  // than counted as silence here.
  confirmCancel: 0x7c2e10,
  // `CS::CSPlayerMenuCtrl`'s per-frame tick, which dispatches on the step and is what calls the
  // handler above. Added after the first live reading, which is the reason it is here: with the
  // popup on screen and Back pressed, the step-2 handler fired EXACTLY ONCE -- at the moment the
  // prompt went up -- and never again. So the interesting question stopped being "what is `dl` on
  // the press" and became "is anything asking". The tick takes the same flag (`movzbl %dl,%esi`
  // at `+0x16`), so hooking it separates a press that never arrives from a press that arrives and
  // is not dispatched.
  ctrlTick: 0x7c2ae0,
  // The menu-event query, and the reason the search ended here.
  //
  // `dl` reaches the tick from `0x140661636`'s caller, which computes it out of exactly two
  // predicates and nothing else:
  //
  // ```text
  //   14066161b: call 0x1403f4a90      ; A: return (this[0x1c5] >> 1) & 1
  //   140661622: jne  0x140661634      ;    -> dl = 1
  //   140661627: call 0x1403f4e10      ; B: query(this->+0x178, eventId=0xf) != 0
  //   14066162e: jne  0x140661634      ;    -> dl = 1
  //   140661630: xor  %edx,%edx        ;    otherwise dl = 0
  //   140661640: call *0x80(%rax)      ; the tick, with that dl
  // ```
  //
  // Both returned zero across every Back press measured, which is why `dl` was never once
  // nonzero. A is a single flag bit; B is this query, asked for event id `0xf`. Hooking it says
  // whether Back fires `0xf` at all, fires a DIFFERENT id, or never reaches the event source --
  // three different bugs that the `dl` reading alone cannot separate.
  menuEventQuery: 0x4fa370,
  // Predicate B itself, and the reason it is hooked rather than inferred.
  //
  // `0xf` turned out to be polled on MANY widget objects every frame -- the first live attempt
  // cached "the source that asked 0xf" and got a different pointer each time, so a mask sampled
  // from it described some other widget. B is the only caller whose answer feeds this dialog's
  // `dl`, so its own `rcx` is the only object that matters, and its return value IS the gate.
  backPredicate: 0x3f4e10,
};

// The id the bounds popup's back-out is gated on. Every other id is asked here too, so it is
// named rather than filtered out: an id that fires on Back while `0xf` stays silent IS the
// finding.
const BACK_EVENT_ID = 0xf;

// `CSMenuManImp`. The popup hangs off `+0x80` and its result is `+0x1a0`, which is what the
// handler consumes; `+0x1a4` sits beside it and is captured so a result that moves in the
// neighbouring field is not read as no movement at all.
const MENU_MAN_RVA = 0x3d6f820;
const POPUP_OFFSET = 0x80;
const RESULT_OFFSET = 0x1a0;

const base = Process.getModuleByName('eldenring.exe').base;

// The keystate bitmap the front-end actually reads, and the one a DLL in this repo can read too.
//
// `inputmgr = *(base + 0x3d6b7b0)` (CS::CSMenuMan), events at `+0x90 + eventId`, edge-triggered on
// bit 0 -- the same channel `er-input-harness`'s `tap_menu_event` writes. This is a DIFFERENT
// namespace from the 3-digit ids `0x1404fa370` answers, and it is the one the product fix has to
// use, so the Back press has to be named in THIS bitmap rather than in that one.
const INPUT_MANAGER_GLOBAL_RVA = 0x3d6b7b0;
const INPUTMGR_BITMAP_OFFSET = 0x90;
const INPUTMGR_BITMAP_SPAN = 0x80;

function inputManager () {
  const slot = readPtr(base.add(INPUT_MANAGER_GLOBAL_RVA));
  return slot === null || slot.isNull() ? null : slot;
}

// Which event ids in that bitmap are currently held, as a sorted list of hex strings.
function heldMenuEvents () {
  const im = inputManager();
  if (im === null) return null;
  const held = [];
  for (let id = 0; id < INPUTMGR_BITMAP_SPAN; id++) {
    let byte;
    try {
      byte = im.add(INPUTMGR_BITMAP_OFFSET + id).readU8();
    } catch (e) {
      return null;
    }
    if ((byte & 1) !== 0) held.push('0x' + id.toString(16));
  }
  return held;
}

function follow (address) {
  try {
    return address.readU8() === 0xe9
      ? address.add(5).add(address.add(1).readS32())
      : address;
  } catch (e) {
    return address;
  }
}

// Fault-closed throughout. A pointer chain through a menu object that is being torn down faults,
// and a faulting oracle that takes the game with it destroys the evidence it was installed for.
function readPtr (address) {
  try {
    return address.readPointer();
  } catch (e) {
    return null;
  }
}

function readU32 (address) {
  try {
    return address.readU32();
  } catch (e) {
    return null;
  }
}

function popupResult () {
  const man = readPtr(base.add(MENU_MAN_RVA));
  if (man === null || man.isNull()) return { popup: null, result: null, beside: null };
  const popup = readPtr(man.add(POPUP_OFFSET));
  if (popup === null || popup.isNull()) return { popup: null, result: null, beside: null };
  return {
    popup: popup.toString(),
    result: readU32(popup.add(RESULT_OFFSET)),
    beside: readU32(popup.add(RESULT_OFFSET + 4)),
  };
}

const info = {};
const hits = { startInvasion: 0, raiser: 0, confirmCancel: 0, ctrlTick: 0 };
// The tick runs every frame, so it reports on CHANGE plus a heartbeat. The heartbeat is not
// noise: without it, "the tick is running and nothing is changing" and "the hook is dead" look
// identical, and the first live reading of this agent could not tell them apart.
let lastTick = null;
let tickHeartbeat = 0;
const TICK_HEARTBEAT_CALLS = 900;
// One caller capture, not one per frame.
let callerSaid = false;
// Armed by hand for the forced-open experiment below, and spent on its first use.
let forceOnce = true;
// The `CSPlayerMenuCtrl` the bounds popup is actually sitting in, learned from the tick. The
// forced open needs it: it is what tells one menu owner from the four others being polled.
let boundsCtrl = null;
// Armed for the direct step-5 store, and spent on its first use.
let forceClose = false;
// Last-seen set of held menu events, so a press is one line rather than one per frame.
let lastHeld = null;
// Only a change is worth a line: the step-2 handler runs every frame the popup is on screen, so
// an unconditional send is tens of thousands of identical messages and the one that matters is
// buried in them.
let last = null;
let dlEverNonZero = false;

function arm (name, onEnter) {
  const entry = base.add(RVA[name]);
  const body = follow(entry);
  info[name] = {
    entry: entry.toString(),
    body: body.toString(),
    stubbed: !body.equals(entry),
  };
  Interceptor.attach(body, {
    onEnter: function (args) {
      hits[name] += 1;
      onEnter.call(this, args);
    },
  });
}

arm('startInvasion', function (args) {
  const ctrl = args[0];
  const dl = args[1].toUInt32() & 0xff;
  const step = readU32(ctrl.add(0x10));
  const popup = popupResult();
  if (dl !== 0) dlEverNonZero = true;
  const signature = [dl, step, popup.result, popup.beside].join('/');
  if (signature === last) return;
  last = signature;
  send({
    kind: 'startInvasion',
    dl: dl,
    step: step,
    ctrl: ctrl.toString(),
    popup: popup.popup,
    result: popup.result,
    beside: popup.beside,
    calls: hits.startInvasion,
    line: 'step-2 handler: dl=' + dl + ' step=' + step +
      ' popupResult=' + popup.result + ' beside=' + popup.beside +
      ' (call #' + hits.startInvasion + ')' +
      (dl !== 0 ? '  <- this call writes step 5 and closes the dialog' : ''),
  });
});

arm('raiser', function (args) {
  // `(ctrl, u8, u8, messageId, u8, kind)`; kind 4 is the two-option bounds prompt, 1 is yes/no.
  const message = args[3].toUInt32();
  const kind = args[5].toUInt32();
  // A new prompt is a new question: let the next handler call report even if its fields match the
  // previous prompt's.
  last = null;
  send({
    kind: 'raiser',
    message: message,
    promptKind: kind,
    line: 'raiser: message=' + message + ' kind=' + kind +
      (kind === 4 ? '  <- the two-option bounds prompt' : ''),
  });
});

// Whoever writes the popup's answer, named by instruction rather than guessed at.
//
// The first live reading said `dl` is zero on every call and `+0x1a0` never leaves `-1`, while
// the player can HEAR the Back press being consumed. So the press is reaching something; the
// question is whether that something ever writes the field this handler reads. A watchpoint
// answers it with the writer's own address.
//
// `Thread.setHardwareWatchpoint` and NOT `MemoryAccessMonitor`, which AGENTS.md forbids on a live
// game object for a measured reason: it revokes a whole 4 KB page and turns every access by every
// thread into a fault, and on this target one such guard page killed the game with `0xc0000005`
// inside ersc's own reader. A hardware watchpoint changes no protection and traps only these
// eight bytes.
let watchArmed = false;

function armResultWatchpoint (threadId) {
  if (watchArmed) return;
  const popup = popupResult();
  if (popup.popup === null) return;
  const address = ptr(popup.popup).add(RESULT_OFFSET);
  try {
    // Measured 2026-09-17 on this build: `Thread.setHardwareWatchpoint` is `not a function` here,
    // so this path only ever reports its own absence. Kept because a refusal that says so is the
    // difference between "nothing wrote the field" and "nothing was watching it" -- and the first
    // reading of this agent would otherwise have been read as the former.
    Thread.setHardwareWatchpoint(threadId, address, 8, 'w');
    watchArmed = true;
    send({
      kind: 'watchpoint',
      address: address.toString(),
      threadId: threadId,
      line: 'watchpoint armed on the popup answer at ' + address + ' (8 bytes, writes) on the ' +
        'menu thread ' + threadId + ' -- the next line naming an address is whoever answers this ' +
        'dialog',
    });
  } catch (e) {
    send({
      kind: 'watchpoint',
      error: String(e),
      line: 'watchpoint REFUSED on ' + address + ': ' + e +
        ' -- the field is unwatched, so silence below proves nothing',
    });
    // Latched either way: a refusal that retries every frame is a refusal printed every frame.
    watchArmed = true;
  }
}

arm('ctrlTick', function (args) {
  const ctrl = args[0];
  const dl = args[1].toUInt32() & 0xff;
  const step = readU32(ctrl.add(0x10));
  // Only while the bounds popup is the thing on screen, and only on the thread that ticks it.
  if (step === 2) {
    armResultWatchpoint(this.threadId);
    boundsCtrl = ctrl;
  }
  // What the player is holding, in the bitmap a DLL can read, while the popup is up. Reported on
  // change only, so a press is one line and a quiet frame is none. This is the measurement the
  // product fix depends on: it names the Back press in the namespace `er-input-harness` already
  // speaks, rather than in the 3-digit one whose `0xf` never fires.
  if (step === 2) {
    const held = heldMenuEvents();
    const shown = held === null ? 'unreadable' : (held.join(',') || 'none');
    if (shown !== lastHeld) {
      const previous = lastHeld;
      lastHeld = shown;
      send({
        kind: 'menuKeystate',
        held: shown,
        line: 'menu keystate held: ' + previous + ' -> ' + shown +
          '  (inputmgr+0x90+id, the channel the product fix can read)',
      });
    }
  }
  // Write the step the back-out arm would have written, directly.
  //
  // Forcing predicate B's return value did not reach the tick -- no tick ever saw `dl = 1` -- so
  // the `A || B` caller is not the frame that feeds this ctrl, whatever the static read says.
  // This skips the argument entirely and performs the one store `0x1407c2c64` performs:
  // `movl $0x5, 0x10(%rcx)`. Step 5 is what the tick dispatches to `0x1407ee500`, the popup
  // close, so this is the game's own teardown reached by its own value, not a new path.
  //
  // One shot, on the menu thread, inside the tick that is about to read the field. Nothing else
  // is written.
  if (forceClose && step === 2) {
    forceClose = false;
    try {
      ctrl.add(0x10).writeU32(5);
      send({
        kind: 'forcedClose',
        ctrl: ctrl.toString(),
        line: 'FORCED step 5 into ' + ctrl + '+0x10 -- the same store the back-out arm makes. ' +
          'The next tick dispatches step 5 to the popup close.',
      });
    } catch (e) {
      send({
        kind: 'forcedClose',
        error: String(e),
        line: 'could not write step 5 into ' + ctrl + '+0x10: ' + e,
      });
    }
  }
  // The pending-event bitmap the popup's own gate reads, sampled once a frame. Reported on change
  // only: a bitmap that moves while Back is pressed means the press reaches this source and the
  // gate's exact-id match is what rejects it; a bitmap that never moves means the press never
  // arrives here at all.
  if (backSource !== null) {
    const mask = readU32(backSource.add(0x28));
    const high = readU32(backSource.add(0x2c));
    const both = (high === null ? 'x' : high.toString(16)) + ':' +
      (mask === null ? 'x' : mask.toString(16));
    if (both !== lastMask) {
      const previous = lastMask;
      lastMask = both;
      send({
        kind: 'pendingMask',
        mask: both,
        previous: previous,
        line: 'pending-event mask at ' + backSource + '+0x28 moved: ' + previous + ' -> ' + both +
          '  (the bucket the 0xf gate tests)',
      });
    }
  }
  // Who calls this tick, once. `dl` arrives as an argument and is zero on every call including
  // across a dozen Back presses, so the byte is decided ABOVE this frame and the caller is the
  // only thing that names where. One capture, because a backtrace per frame is a backtrace nobody
  // reads.
  if (step === 2 && !callerSaid) {
    callerSaid = true;
    let chain = [];
    try {
      chain = Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 16).map(function (a) {
        const m = Process.findModuleByAddress(a);
        return m === null ? String(a) : m.name + '+0x' + a.sub(m.base).toString(16);
      });
    } catch (e) { /* fault-closed: a fuzzy walk can fail on a packed frame */ }
    send({
      kind: 'ctrlTickCaller',
      chain: chain,
      line: 'ctrl tick caller chain: ' + chain.join(' <- '),
    });
  }
  const signature = dl + '/' + step;
  tickHeartbeat += 1;
  const beat = tickHeartbeat >= TICK_HEARTBEAT_CALLS;
  if (signature === lastTick && !beat) return;
  if (beat) tickHeartbeat = 0;
  lastTick = signature;
  if (dl !== 0) dlEverNonZero = true;
  send({
    kind: 'ctrlTick',
    dl: dl,
    step: step,
    ctrl: ctrl.toString(),
    calls: hits.ctrlTick,
    heartbeat: beat,
    line: 'ctrl tick: dl=' + dl + ' step=' + step + ' (call #' + hits.ctrlTick + ')' +
      (beat ? '  [heartbeat -- the tick is alive and nothing changed]' : '') +
      (dl !== 0 ? '  <- the press reached the dispatch' : ''),
  });
});

// Every id this query answers YES to, once each, plus the fact that `0xf` was asked at all.
//
// The query runs for many ids every frame, so an unconditional report is a firehose and a
// report filtered to `0xf` alone cannot see the one outcome that would explain everything -- Back
// firing some other id. Reporting each id's first YES, and `0xf`'s first ASK, covers both without
// the volume.
const eventYes = {};
let backAsked = false;
// The source the bounds popup polls for `0xf`, kept so its pending-event mask can be watched.
//
// `0x1404fa370` tests `rdx & [source+0x28]`, where `rdx` is a nibble mask chosen by
// `4 * ((id % 15) + (id != 0))` -- so `+0x28` is a 64-bit bitmap of sixteen pending-event
// buckets, and it is the field a Back press would have to reach. A hardware watchpoint is the
// right instrument and is unavailable here (`not a function`), so it is polled from the tick
// instead: the tick already runs every frame on the menu thread, and a bitmap that never changes
// across a press says the press never arrives at this source.
let backSource = null;
let lastMask = null;

Interceptor.attach(follow(base.add(RVA.menuEventQuery)), {
  onEnter: function (args) {
    // `(source, eventId)`; the id arrives in `dx` as a 16-bit value.
    this.eventId = args[1].toUInt32() & 0xffff;
    // The SOURCE is carried too, because the ids that fire and the id the popup waits on may
    // simply be coming from different objects. Measured 2026-09-17: Back fires `0x131`, `0x12f`,
    // `0x130`, `0x19c` and `0x1a4` and never `0xf`, so the press reaches an event source -- the
    // open question is whether it is THIS source.
    this.source = args[0];
    if (this.eventId === BACK_EVENT_ID) backSource = this.source;
    if (this.eventId === BACK_EVENT_ID && !backAsked) {
      backAsked = true;
      send({
        kind: 'menuEventAsked',
        eventId: this.eventId,
        source: this.source.toString(),
        line: 'menu-event query: id 0x' + BACK_EVENT_ID.toString(16) +
          ' IS being asked, on source ' + this.source +
          ' -- the back-out path is live and only its answer is missing',
      });
    }
  },
  onLeave: function (retval) {
    if ((retval.toInt32() & 0xff) === 0) return;
    const id = this.eventId;
    // Keyed by (source, id) and re-armed after a quiet second, so a press shows up as its own
    // event instead of being swallowed by the first one of its kind. The previous rule -- first
    // YES per id for the whole session -- answered "which ids exist" and could not answer "does
    // THIS press fire one", which is the question left.
    const key = this.source + '/' + id;
    const now = Date.now();
    if (eventYes[key] && now - eventYes[key] < 1000) {
      eventYes[key] = now;
      return;
    }
    eventYes[key] = now;
    send({
      kind: 'menuEventFired',
      eventId: id,
      source: this.source.toString(),
      line: 'menu-event FIRED: id 0x' + id.toString(16) + ' on source ' + this.source +
        (id === BACK_EVENT_ID
          ? '  <- this is the back-out id; the popup should now close'
          : '  <- not the back-out id (0x' + BACK_EVENT_ID.toString(16) + ')'),
    });
  },
});

// Predicate B, whose answer is half of `dl` and the readable half. Reported on change plus a
// heartbeat, for the same reason the tick is: it runs every frame.
let lastB = null;
let bHeartbeat = 0;

Interceptor.attach(follow(base.add(RVA.backPredicate)), {
  onEnter: function (args) {
    this.owner = args[0];
    this.source = readPtr(args[0].add(0x178));
    this.mask = this.source === null || this.source.isNull()
      ? null
      : readU32(this.source.add(0x28));
  },
  onLeave: function (retval) {
    // One forced YES, and then never again.
    //
    // Everything measured says this predicate's answer is the entire back-out: `dl` is `A || B`,
    // A is a flag bit that stays clear, and B is this. Reading more of the input path can only
    // keep narrowing which id Back posts; replacing this return value once answers the question
    // that actually matters -- is this gate the whole mechanism -- by making the dialog close.
    //
    // Deliberately a ONE-SHOT and deliberately loud. It is an intervention in a live game: it
    // hands the ctrl tick the same `dl = 1` the real Back press would have produced, on the next
    // frame, and nothing else. If the popup closes, the gate is proven and the fix belongs at
    // whatever should have set it; if the popup stays, `dl` was never the whole story and the
    // step-5 write has another precondition nobody has read yet.
    // Only the owner that actually drives the bounds popup.
    //
    // The first attempt forced the first caller it saw and nothing happened, for a reason worth
    // writing down: predicate B runs for EVERY menu owner every frame -- five distinct owners in
    // one second of samples -- and the tick's `this` is not the owner's. The caller reaches the
    // tick through `call *0x80([rsi+0x6a0])`, so the owner that belongs to a given
    // `CSPlayerMenuCtrl` is the one whose `+0x6a0` points at it. Forcing any other owner's answer
    // hands `dl = 1` to a menu that was not asking.
    const owned = readPtr(this.owner.add(0x6a0));
    const isBounds = boundsCtrl !== null && owned !== null && owned.equals(boundsCtrl);
    if (forceOnce && isBounds && this.source !== null) {
      forceOnce = false;
      retval.replace(ptr(1));
      send({
        kind: 'forcedOpen',
        owner: this.owner.toString(),
        source: this.source.toString(),
        line: 'FORCED the back-out gate to 1 once, on owner ' + this.owner +
          ' -- if the popup closes now, this predicate is the whole mechanism',
      });
      return;
    }
    const answer = retval.toInt32() & 0xff;
    const signature = answer + '/' + (this.source === null ? 'null' : this.source.toString()) +
      '/' + (this.mask === null ? 'x' : this.mask.toString(16));
    bHeartbeat += 1;
    const beat = bHeartbeat >= TICK_HEARTBEAT_CALLS;
    if (signature === lastB && !beat) return;
    if (beat) bHeartbeat = 0;
    lastB = signature;
    send({
      kind: 'backPredicate',
      answer: answer,
      owner: this.owner.toString(),
      source: this.source === null ? null : this.source.toString(),
      mask: this.mask === null ? null : this.mask.toString(16),
      heartbeat: beat,
      line: 'back predicate: answer=' + answer + ' owner=' + this.owner +
        ' source=' + this.source + ' mask=0x' + (this.mask === null ? '?' : this.mask.toString(16)) +
        (beat ? '  [heartbeat]' : '') +
        (answer !== 0 ? '  <- the gate opened; the dialog should close now' : ''),
    });
  },
});

arm('confirmCancel', function (args) {
  const dl = args[1].toUInt32() & 0xff;
  send({
    kind: 'confirmCancel',
    dl: dl,
    line: 'cancel-prompt handler: dl=' + dl + ' (call #' + hits.confirmCancel + ')',
  });
});

rpc.exports = {
  info: function () { return info; },
  hits: function () { return hits; },
  // The verdict in one call, so it does not have to be reconstructed from the stream.
  verdict: function () {
    return { dlEverNonZero: dlEverNonZero, hits: hits, popup: popupResult() };
  },
};

console.log('bounds-popup-back-out: armed at ' +
  Object.keys(info).map(function (n) {
    return n + '=' + info[n].body + (info[n].stubbed ? ' (followed an Arxan stub)' : '');
  }).join(', '));
