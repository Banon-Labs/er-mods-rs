// Which term refuses the invasion fingers in a zone the vanilla game calls non-pvp, and can the
// refusal be lifted without touching anything else?
//
// `CS::PlayerIns::CanUseBreakInItem` is the term `crates/er-invasion-warp/src/can_use_goods_gate.rs`
// deliberately keeps: when it says no, that module hands the engine's refusal back and the fingers
// stay greyed. Read out of `eldenring-deobf-1.17.1.bin`, its whole body is three calls:
//
//   1.17.1 0x140657d50  CanUseBreakInItem(player)
//     call 0x14065f6a0                     IsInSafePosRange(player)
//     call 0x14050a950                     CanStartBreakIn(*0x143d69ff8)
//     lea rcx,[rbx+0x6e8]                  &player->playRegionId
//     call 0x140a61980                     IsBreakInLimitedByEventFlagId(&playRegionId)
//
//   1.17.1 0x14050a950  CanStartBreakIn(worldChrMan)
//     mov rax,[rcx+0x1e508]                worldChrMan->mainPlayerIns, null -> refuse
//     call 0x1404fa370                     HasSpecialEffectWithStateInfo(player->specialEffect, 0x1a2)
//     mov ecx,[*0x143d6d248 + 0xe8]        FieldArea->playRegionParamId
//     call 0x140a61980                     IsBreakInLimitedByEventFlagId(&playRegionParamId)
//
// So `0x140a61980` is the region question, asked twice with two different region ids, and its sense
// is inverted against its name: both callers refuse when it returns false, so true means "invading
// is permitted in this region". That is the candidate for the zone gate Seamless disagrees with --
// Seamless turns zones into pvp zones that vanilla flags off.
//
// # What this agent does, in two states
//
// `FORCE` off: every call is counted and its first distinct shape reported -- which caller, which
// region id, which answer. That names the refusing term instead of assuming it.
//
// `FORCE` on: a `false` from `0x140a61980` is answered `true`, and nothing else is touched. A
// `true` is handed back untouched, so a region that already permits invading stays the engine's
// call. The switch is this constant, because the resident watcher has no rpc channel and editing
// the file is the only switch it has.
// Measured with `FORCE` off, on the user's live session, walking between two regions:
//
//   region 1400011  isInSafePosRange=1 canStartBreakIn=1 isBreakInLimitedByEventFlagId=1 canUseBreakInItem=1
//   region 1400000  isInSafePosRange=1 canStartBreakIn=0 isBreakInLimitedByEventFlagId=0 canUseBreakInItem=0
//
// One term moved. `IsInSafePosRange` holds at 1 in both, and `CanStartBreakIn` collapses only
// because it asks the same region predicate about `FieldArea->playRegionParamId`. So the region
// gate is the whole refusal, it is asked twice about the same region id, and forcing it is the
// smallest change that can lift the grey.
const FORCE = true;

// Arxan stubs a function's entry with `jmp rel32` to a healed copy allocated below the image, so a
// hook at the entry address catches nothing -- the whole explanation for the zero-call reading in
// the first attempt at `CanUseGoods`. Read the first byte and follow it.
function followStub (address) {
  const first = address.readU8();
  if (first !== 0xe9) return { address, followed: false, firstByte: first };
  return { address: address.add(5).add(address.add(1).readS32()), followed: true, firstByte: first };
}

const game = Process.findModuleByName('eldenring.exe');

// Every address below is an rva into `eldenring.exe`, read off the disassembly quoted above. All
// are under the `0xafefe9` boundary, so 1.17.0 and 1.17.1 agree and no mapping step applies.
const RVA = {
  isBreakInLimitedByEventFlagId: 0xa61980,
  canUseBreakInItem: 0x657d50,
  isInSafePosRange: 0x65f6a0,
  canStartBreakIn: 0x50a950,
};

const out = {
  force: FORCE,
  region: { calls: 0, forced: 0, shapes: {} },
  terms: {},
  bodies: {},
};

// Where a return address lands, as an rva when it is inside the game image and the raw pointer when
// Arxan has moved the caller's body out of it. Naming the caller is what decides whether forcing
// this predicate touches only the two invasion sites or something wider.
function siteOf (returnAddress) {
  if (game !== null && returnAddress.compare(game.base) >= 0
      && returnAddress.compare(game.base.add(game.size)) < 0) {
    return 'eldenring+0x' + returnAddress.sub(game.base).toString(16);
  }
  return 'off-image ' + returnAddress.toString();
}

function hookTerm (name, rva) {
  const resolved = followStub(game.base.add(rva));
  out.bodies[name] = {
    body: resolved.address.toString(),
    followed: resolved.followed,
    firstByte: '0x' + resolved.firstByte.toString(16),
  };
  out.terms[name] = { calls: 0, returns: {} };
  Interceptor.attach(resolved.address, {
    onLeave (retval) {
      const term = out.terms[name];
      term.calls += 1;
      // `bool` comes back in `al` and neither tail of these functions clears the upper 24 bits of
      // `eax`, so an unmasked read gives values like `0x10d200` for a false. Mask, or every
      // comparison against zero silently never matches -- the trap that cost the first reading of
      // the multiplayer-row predicate.
      const answer = retval.toUInt32() & 0xff;
      const first = term.returns[answer] === undefined;
      term.returns[answer] = (term.returns[answer] || 0) + 1;
      // Each distinct answer is announced the first time the engine produces it, which is what
      // makes the force provable. The direct probe below cannot prove it: Frida suppresses
      // interceptors re-entered from inside an interceptor callback, so a call issued there runs
      // the unforced function and reports the engine's untouched verdict. Measured -- with `FORCE`
      // on, the engine's own call logged `0 -> 1` while the probe in the same frame still read 0.
      // That makes the probe the ground truth and this line the proof, and neither substitutes for
      // the other.
      if (first) {
        send({ kind: 'term', line: `${name} returned ${answer} for the first time ` +
                                   `(call #${term.calls}, force=${FORCE})` });
      }
    },
  });
}

for (const name of ['canUseBreakInItem', 'isInSafePosRange', 'canStartBreakIn']) {
  hookTerm(name, RVA[name]);
}

const regionResolved = followStub(game.base.add(RVA.isBreakInLimitedByEventFlagId));
out.bodies.isBreakInLimitedByEventFlagId = {
  body: regionResolved.address.toString(),
  followed: regionResolved.followed,
  firstByte: '0x' + regionResolved.firstByte.toString(16),
};

Interceptor.attach(regionResolved.address, {
  onEnter (args) {
    // The argument is a pointer to the region id, not the id itself: one caller passes
    // `&player->playRegionId` and the other a stack copy of `FieldArea->playRegionParamId`.
    this.arg = args[0];
    this.site = siteOf(this.returnAddress);
    this.regionId = null;
    if (!this.arg.isNull()) {
      try {
        this.regionId = this.arg.readU32();
      } catch (e) {
        this.regionId = null;
      }
    }
  },
  onLeave (retval) {
    out.region.calls += 1;
    const answer = retval.toUInt32() & 0xff;
    const shape = `${this.site} region=${this.regionId} -> ${answer}`;
    if (!out.region.shapes[shape]) {
      out.region.shapes[shape] = 0;
      send({ kind: 'shape', line: 'first seen ' + shape });
    }
    out.region.shapes[shape] += 1;
    if (!FORCE || answer !== 0) return;
    retval.replace(ptr(1));
    out.region.forced += 1;
    if (out.region.forced % 500 === 1) {
      send({
        kind: 'forced',
        line: `IsBreakInLimitedByEventFlagId 0 -> 1 at ${this.site} region=${this.regionId} `
              + `(#${out.region.forced})`,
      });
    }
  },
});

// The heartbeat counts calls rather than seconds. A timer reports on a clock the game does not
// share, and `scripts/check-no-timeouts.py` bans timer APIs here for that reason. Counting calls
// means every line below was caused by a gate actually running, so a silent log means the gate is
// not reached rather than that the watcher died.
const REPORT_EVERY_CALLS = 200;
let sinceReport = 0;

Interceptor.attach(followStub(game.base.add(RVA.canUseBreakInItem)).address, {
  onLeave () {
    sinceReport += 1;
    if (sinceReport % REPORT_EVERY_CALLS !== 0) return;
    const terms = Object.keys(out.terms)
      .map((name) => `${name}=${JSON.stringify(out.terms[name].returns)}`)
      .join(' ');
    send({
      kind: 'report',
      line: `force=${FORCE} regionCalls=${out.region.calls} forced=${out.region.forced} ${terms}`,
    });
  },
});

// # Asking, instead of waiting to be asked
//
// The passive hooks above only speak when the game asks, and the game asks `CanUseBreakInItem`
// from the item menu. Waiting for that means waiting on the player to open a menu, and a silent
// log then cannot tell "the gate allows" from "nobody asked". So the predicates are also invoked
// directly with the live `PlayerIns`, which answers the same question in the current frame.
//
// The call is issued from inside a `PeekMessageW` hook. The game pumps its message queue on its
// main thread every frame, so the callback runs on the thread that owns the player and the menu
// rather than on Frida's own thread -- these predicates read singletons and event flags, and the
// wrong thread is the difference between a read and a race.

// Both globals are read off the rip-relative operands in the 1.17.1 disassembly quoted at the top
// of this file, not from `crates/er-game-base/src/rva.rs`. That file carries `0x3d65f88` for
// `WorldChrMan` and `0x3d691d8` for `FieldArea`, each exactly `0x4070` below what 1.17.1 loads at
// these two call sites, so those constants are 1.16.2 addresses. Both are printed below so the
// disagreement is measured here rather than argued about.
const WORLD_CHR_MAN_GLOBAL_RVA = 0x3d69ff8;
const WORLD_CHR_MAN_GLOBAL_RVA_FROM_RVA_RS = 0x3d65f88;
const FIELD_AREA_GLOBAL_RVA = 0x3d6d248;
const WORLD_CHR_MAN_PLAYER_INS_OFFSET = 0x1e508;
const PLAYER_INS_PLAY_REGION_ID_OFFSET = 0x6e8;
const FIELD_AREA_PLAY_REGION_PARAM_ID_OFFSET = 0xe8;

// The last answer reported, so a sample that says the same thing as the previous one is silent.
let lastAnswer = null;

function readPointerOrNull (rva) {
  try {
    const value = game.base.add(rva).readPointer();
    return value.isNull() ? null : value;
  } catch (e) {
    return null;
  }
}

function askDirectly () {
  const lines = [];
  const fromRvaRs = readPointerOrNull(WORLD_CHR_MAN_GLOBAL_RVA_FROM_RVA_RS);
  lines.push(`rva.rs WorldChrMan global +0x${WORLD_CHR_MAN_GLOBAL_RVA_FROM_RVA_RS.toString(16)} `
             + `-> ${fromRvaRs}`);

  const worldChrMan = readPointerOrNull(WORLD_CHR_MAN_GLOBAL_RVA);
  lines.push(`1.17.1 WorldChrMan global +0x${WORLD_CHR_MAN_GLOBAL_RVA.toString(16)} `
             + `-> ${worldChrMan}`);
  if (worldChrMan === null) {
    send({ kind: 'direct', line: lines.join(' | ') + ' | no WorldChrMan, asked nothing' });
    return;
  }

  const player = worldChrMan.add(WORLD_CHR_MAN_PLAYER_INS_OFFSET).readPointer();
  if (player.isNull()) {
    send({ kind: 'direct', line: lines.join(' | ') + ' | no local PlayerIns, asked nothing' });
    return;
  }
  lines.push(`PlayerIns ${player}`);
  lines.push(`player->playRegionId `
             + `${player.add(PLAYER_INS_PLAY_REGION_ID_OFFSET).readU32()}`);

  const fieldArea = readPointerOrNull(FIELD_AREA_GLOBAL_RVA);
  if (fieldArea !== null) {
    lines.push('FieldArea->playRegionParamId '
               + `${fieldArea.add(FIELD_AREA_PLAY_REGION_PARAM_ID_OFFSET).readU32()}`);
  }

  // Each predicate is called through the same resolved body the passive hooks use, so a stubbed
  // entry is followed once and the direct call and the hook cannot disagree about which code ran.
  const call = (name, argument) => {
    try {
      const fn = new NativeFunction(ptr(out.bodies[name].body), 'bool', ['pointer']);
      lines.push(`${name}=${fn(argument)}`);
    } catch (e) {
      lines.push(`${name} threw ${e.message}`);
    }
  };
  call('isInSafePosRange', player);
  call('canStartBreakIn', worldChrMan);
  call('isBreakInLimitedByEventFlagId', player.add(PLAYER_INS_PLAY_REGION_ID_OFFSET));
  call('canUseBreakInItem', player);

  // Reported when the answer changes, not on every sample. The player walks between regions, and
  // a reading taken where invading is permitted says nothing about the region where it is not --
  // so crossing the boundary is the measurement, and the log records which term flipped with no
  // menu involved and nothing for the player to drive.
  const answer = lines.join(' | ');
  if (answer === lastAnswer) return;
  lastAnswer = answer;
  send({ kind: 'direct', line: answer });
}

// Sampled on the game's own main thread, throttled by frame count rather than by a clock:
// `scripts/check-no-timeouts.py` bans timer APIs, and a frame counter also follows the game rather
// than a clock it does not share. There is no detach, because detaching from inside the callback
// is not safe; a hot reload replaces the agent wholesale instead.
//
// Frida 17 removed the module-static `Module.getExportByName(module, name)` that older agents in
// this directory still call -- it raises `TypeError: not a function`, which is why
// `scripts/frida/breakin-gate.js` cannot run as written. The export is resolved through the module
// object instead, with the global lookup as the fallback.
function exportByName (moduleName, symbol) {
  const owner = Process.findModuleByName(moduleName);
  if (owner !== null) {
    const found = owner.findExportByName(symbol);
    if (found !== null) return found;
  }
  return Module.getGlobalExportByName(symbol);
}

const SAMPLE_EVERY_FRAMES = 60;
let frames = 0;

Interceptor.attach(exportByName('user32.dll', 'PeekMessageW'), {
  onEnter () {
    frames += 1;
    if (frames % SAMPLE_EVERY_FRAMES !== 0) return;
    try {
      askDirectly();
    } catch (e) {
      const failure = 'direct call failed: ' + e.message;
      if (failure === lastAnswer) return;
      lastAnswer = failure;
      send({ kind: 'direct', line: failure });
    }
  },
});

rpc.exports = {
  report () { return out; },
};

console.log('breakin-region-gate: ' + JSON.stringify(out.bodies));
