// Does driving Seamless's own invade action actually make it search?
//
// The question the near+far handoff turns on. `ersc+0x25850` is nine instructions read out of the
// decrypted image -- lock the session, return unless the state reads idle, store 0x0e, unlock --
// so the state write cannot itself be a search. Something consumes that state. This asks whether
// driving the action is enough to make the consumer run, with a query to Steam as the oracle
// rather than the state field we would be writing ourselves.
//
// Read-only until `arm()` is called. `report()` is safe at any time.
//
//   rpc.exports.find()    locate the session, the option-menu object and the matchmaking interface
//   rpc.exports.arm()     hook RequestLobbyList and start sampling the state
//   rpc.exports.drive()   call ersc+0x25850 with the located owner
//   rpc.exports.report()  queries seen, and every distinct state the session passed through

'use strict';

const ERSC_INVADE = 0x25850;
const ERSC_CANCEL = 0x258d0;
const SESSION_STATE = 0x150;
const SESSION_GUARD = 0x14c;
const SESSION_LOCK = 0x100;
const OWNER_SESSION = 0x58;
const STATE_MAX = 0x30;
const REQUEST_LOBBY_LIST_SLOT = 4;

// The tick the state sampler counts in, instead of a timer.
//
// `scripts/frida/pad-frames.js` measured this target: `XInputGetState` is called ~82 times a
// second whether or not a pad is attached, while `FD4PadManager`'s own builders are never called
// at all without one. So the input poll is the only per-frame tick that exists here, and sampling
// every fourth call is the same 20 Hz the 50 ms timer ran at -- with no timer, and with the read
// happening on a thread the game is already running rather than on one of Frida's.
const XINPUT_MODULE = 'XINPUT1_4.dll';
const XINPUT_READ = 'XInputGetState';
// `FD4PadManager`'s builder A, the fallback `pad-frames.js` keeps for a process with no
// `XINPUT1_4.dll` mapped.
const PAD_BUILDER_RVA = 0x240e70;
const SAMPLE_EVERY_TICKS = 4;

const state = {
  ersc: null,
  session: null,
  owner: null,
  matchmaking: null,
  queries: 0,
  filters: 0,
  seen: [],
  log: [],
  hooked: false,
  sampler: null,
  flat: [],
};

// A line for the transcript, and optionally a name for the caller to block on.
//
// `event` rides on the existing `note` payload rather than arriving as a new kind, so a reader
// that only knows about `line` is unaffected. `scripts/er-invade-proof.py` waits on `query`: the
// oracle is a Steam search going out, so the run ends when one does instead of when a clock does.
function note(line, event) {
  state.log.push(line);
  const payload = { kind: 'note', line };
  if (event !== undefined) payload.event = event;
  send(payload);
}

// An export sitting behind a `jmp rel32` thunk is followed to the real entry, because attaching to
// the five bytes of the thunk itself can fail for want of room.
function follow(address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

// The per-frame call this agent samples on, named so the report says which one it got.
function gameTick() {
  const xinput = Process.findModuleByName(XINPUT_MODULE);
  if (xinput !== null) {
    return {
      at: follow(xinput.getExportByName(XINPUT_READ)),
      what: XINPUT_MODULE + '!' + XINPUT_READ,
    };
  }
  const game = Process.findModuleByName('eldenring.exe');
  if (game === null) return null;
  return {
    at: follow(game.base.add(PAD_BUILDER_RVA)),
    what: 'eldenring.exe+0x' + PAD_BUILDER_RVA.toString(16),
  };
}

// Every distinct state the session passes through, recorded the moment the game next polls its
// pad. Reads one dword, so it costs the game nothing measurable.
function sampleState() {
  if (state.session === null) return;
  const st = readU32(state.session.add(SESSION_STATE));
  if (st === null) return;
  if (state.seen.length === 0 || state.seen[state.seen.length - 1] !== st) {
    state.seen.push(st);
    note(`session state -> 0x${st.toString(16)}`);
  }
}

function readU32(p) {
  try {
    return p.readU32();
  } catch (e) {
    return null;
  }
}

function readPtr(p) {
  try {
    return p.readPointer();
  } catch (e) {
    return null;
  }
}

// The states the four option-row actions read and write, read out of the decrypted image: 1 idle,
// 2 and 7 from the other two rows, 4 and 6 from the handler at ersc+0x73630, 0x0e searching,
// 0x12 connecting, 0x16 in world, 0x23 cancelling, 0x24 beside it.
//
// `0` is NOT among them, and excluding it is the whole correction. A first attempt accepted any
// value <= 0x30, latched a zeroed object, and reported `state: 0` -- against which `ersc+0x25850`
// returns on its second instruction, so the drive did nothing and the run proved nothing. Zeroed
// memory is the most common thing in a writable section, and this repo has latched onto it before
// (bd: the scan concluded an invasion attempt was permanently in flight).
const SESSION_STATES = [1, 2, 4, 6, 7, 0x0e, 0x0f, 0x12, 0x16, 0x23, 0x24];

// A session, by shape: a state at +0x150 drawn from that set, a guard at +0x14c that is not the
// poisoned sentinel, and a lock at +0x100 whose owner reads free or a plausible thread id.
function looksLikeSession(p) {
  const st = readU32(p.add(SESSION_STATE));
  if (st === null || SESSION_STATES.indexOf(st) < 0) return false;
  const guard = readU32(p.add(SESSION_GUARD));
  if (guard === null || guard === 0x7ffffffe) return false;
  // `+0x148` is the lock's owner-thread field and `+0x14c` its recursion count, the pair
  // `ersc+0xf9830` decrements and clears. A free lock reads owner `0xffffffff`, count 0.
  const owner = readU32(p.add(0x148));
  if (owner === null) return false;
  if (owner !== 0xffffffff && (owner === 0 || owner > 0x100000)) return false;
  // A session at rest reads a FREE lock: owner `0xffffffff`, recursion count 0. Requiring both is
  // what makes the identification stable -- without it the scan returned a different address on
  // every run (0x16d40038, then 0xb7b94824), and a session that moves between two reads of the
  // same process is not a session, it is coincidence passing a loose filter.
  const count = readU32(p.add(SESSION_GUARD));
  if (owner === 0xffffffff && count !== 0) return false;
  const lock = readPtr(p.add(SESSION_LOCK));
  if (lock === null || lock.isNull()) return false;
  // `+0x110` is the handle the unlock path passes to the OS. A session carries something there.
  const os_lock = readPtr(p.add(0x110));
  if (os_lock === null) return false;
  return true;
}

rpc.exports = {
  find() {
    const ersc = Process.findModuleByName('ersc.dll');
    if (!ersc) return { ok: false, why: 'ersc.dll is not loaded' };
    state.ersc = ersc;

    // Seamless parks the session pointer in its own writable data. Scan exactly those ranges --
    // `protection: 'rw-'` matches AT LEAST those bits and would sweep the rwx packed section as
    // code, which is how an earlier scan in this repo reported instruction bytes as pointers.
    const ranges = Process.enumerateRanges('rw-').filter(
      (r) => r.base.compare(ersc.base) >= 0 && r.base.compare(ersc.base.add(ersc.size)) < 0,
    );
    // Scan for the OWNER, not the session, and accept a session only through one.
    //
    // A session-shaped object alone is not an identification: four separate runs against this
    // process returned four different addresses (0x1d2b85af0, 0x16d40038, 0xb7b94824, 0x45ba80),
    // and a session that moves between two reads of one process is a filter matching noise. The
    // pair is a far stronger constraint, and it is the pair the action actually needs:
    // `ersc+0x25850` reads `[rcx+0x58]`, and `ersc+0x241a0` locks `[rcx+0x120]`, so a real
    // option-menu object carries its own lock at +0x120 AND points at a session-shaped object at
    // +0x58. Two independent structural facts about the same candidate, both read out of the
    // decrypted image rather than guessed.
    const candidates = [];
    for (const r of ranges) {
      for (let off = 0; off + 8 <= r.size; off += 8) {
        const owner = r.base.add(off);
        const sess = readPtr(owner.add(OWNER_SESSION));
        if (sess === null || sess.isNull()) continue;
        if (!looksLikeSession(sess)) continue;
        // The owner's own lock, the one `show` takes. `+0x16c` is its guard, checked against the
        // same poisoned sentinel the session's is.
        const own_guard = readU32(owner.add(0x16c));
        if (own_guard === null || own_guard === 0x7ffffffe) continue;
        const own_lock = readPtr(owner.add(0x120));
        if (own_lock === null) continue;
        candidates.push({ at: owner, session: sess, state: readU32(sess.add(SESSION_STATE)) });
        if (candidates.length >= 64) break;
      }
      if (candidates.length >= 64) break;
    }
    state.candidates = candidates.slice(0, 8).map((c) => ({
      owner: c.at.toString(),
      session: c.session.toString(),
      state: c.state,
    }));
    state.candidate_count = candidates.length;
    if (candidates.length === 0) {
      return {
        ok: false,
        why: 'no session-shaped object in ersc writable data -- Seamless has no session in this process',
        ranges: ranges.length,
      };
    }
    // Prefer one that reads idle: that is the only state `ersc+0x25850` acts on, so a candidate in
    // any other state cannot answer the question even if it is the real session.
    // Rank by how many owners point at the same session, not by scan order.
    //
    // This is the identification. A coincidence is pointed at once, by the one window of bytes
    // that happened to encode it; a real session is referenced by every object that holds it.
    // Measured on run br-20260916-221431-aa0a: twenty candidates, and five separate owners all
    // named `0xb7ba4824` while every other session address appeared exactly once. Taking the
    // first idle candidate instead picked `0x3c0038`, a singleton, which is how the previous
    // four runs each came back with a different address.
    const byCount = {};
    for (const c of candidates) {
      const key = c.session.toString();
      byCount[key] = (byCount[key] || 0) + 1;
    }
    const ranked = candidates
      .filter((c) => c.state === 1)
      .sort((a, b) => byCount[b.session.toString()] - byCount[a.session.toString()]);
    const pick = ranked[0] || candidates[0];
    state.session = pick.session;
    state.owner = pick.at;
    state.references = byCount[pick.session.toString()] || 1;

    // The matchmaking interface, so the oracle can be installed on its vtable.
    const iface = readPtr(ersc.base.add(0x21b610));
    if (iface && !iface.isNull()) state.matchmaking = iface;

    return {
      ok: true,
      ersc: ersc.base.toString(),
      session: state.session.toString(),
      session_state: readU32(state.session.add(SESSION_STATE)),
      owner: state.owner ? state.owner.toString() : null,
      matchmaking: state.matchmaking ? state.matchmaking.toString() : null,
      candidates: state.candidate_count,
      references: state.references,
      top: state.candidates,
    };
  },

  // Install the oracle and start sampling. Hooks Steam's vtable, NOT ersc.dll -- an Interceptor
  // inside ersc has killed this process before.
  arm() {
    if (!state.matchmaking) return { ok: false, why: 'no matchmaking interface located' };
    if (!state.hooked) {
      const vt = readPtr(state.matchmaking);
      if (!vt) return { ok: false, why: 'matchmaking vtable unreadable' };
      const request = readPtr(vt.add(REQUEST_LOBBY_LIST_SLOT * 8));
      const filter = readPtr(vt.add(5 * 8));
      if (!request) return { ok: false, why: 'RequestLobbyList slot unreadable' };
      Interceptor.attach(request, {
        onEnter() {
          state.queries += 1;
          note(`RequestLobbyList #${state.queries}`, 'query');
        },
      });
      if (filter) {
        Interceptor.attach(filter, {
          onEnter() {
            state.filters += 1;
          },
        });
      }
      // Also hook every lobby-list path `steam_api64.dll` exports by name.
      //
      // Without this a zero is not a zero. The vtable hook above covers ONE interface object, the
      // one cached at `ersc+0x21b610`, and if Seamless asks Steam through a different pointer --
      // another `SteamInternal_CreateInterface` result, or the flat C exports -- the slot never
      // fires and the run reports "no query" for a search that happened. The flat exports cannot
      // be dodged that way: they are the module's own entry points.
      const steam = Process.findModuleByName('steam_api64.dll');
      for (const exp of steam ? steam.enumerateExports() : []) {
        if (exp.type !== 'function') continue;
        if (exp.name.indexOf('RequestLobbyList') < 0 && exp.name.indexOf('RequestInternetServer') < 0) {
          continue;
        }
        try {
          Interceptor.attach(exp.address, {
            onEnter() {
              state.queries += 1;
              note(`${exp.name} #${state.queries}`, 'query');
            },
          });
          state.flat.push(exp.name);
        } catch (e) {
          note(`could not hook ${exp.name}: ${e}`);
        }
      }
      state.hooked = true;
    }
    if (!state.sampler) {
      const tick = gameTick();
      if (tick === null) {
        note('no per-frame tick is reachable, so the state sampler is not running');
      } else {
        let ticks = 0;
        Interceptor.attach(tick.at, {
          onEnter() {
            ticks += 1;
            if (ticks % SAMPLE_EVERY_TICKS !== 0) return;
            sampleState();
          },
        });
        state.sampler = tick.what;
        sampleState();
      }
    }
    return {
      ok: true,
      hooked: state.hooked,
      flat_exports_hooked: state.flat,
      sampler: state.sampler,
      state: readU32(state.session.add(SESSION_STATE)),
    };
  },

  // Call the action the option menu's Invade row calls. Called from Frida's own thread, which is
  // neither the game task nor an ersc callback -- the two frames this repo has measured parking on
  // the session mutex.
  drive() {
    if (!state.owner) return { ok: false, why: 'no owner object located' };
    // Re-verify the pair at call time and refuse rather than risk the park.
    //
    // `ersc+0x25850` takes the session lock through `ersc+0xf9850`, which ends in
    // `lea rcx,[rbx+0x10]; call [rip+0xfc7fa]` -- an unbounded acquire, not a try-lock. The `edx`
    // this passes is a flag, not a timeout. So calling it on an object that is not really a
    // session hands a garbage pointer to the OS lock and the thread never comes back: measured
    // 2026-09-16, the call did not return, and Frida's transport to that process stayed dead
    // afterwards, costing a relaunch. A refusal costs nothing.
    const linked = readPtr(state.owner.add(OWNER_SESSION));
    if (linked === null || !linked.equals(state.session)) {
      return { ok: false, why: 'owner no longer points at the session' };
    }
    const lock_owner = readU32(state.session.add(0x148));
    const lock_count = readU32(state.session.add(SESSION_GUARD));
    const st = readU32(state.session.add(SESSION_STATE));
    if (lock_owner !== 0xffffffff || lock_count !== 0) {
      return { ok: false, why: `session lock is held: owner=${lock_owner} count=${lock_count}` };
    }
    if (st !== 1) {
      // The action's second instruction returns unless the state reads idle, so a call here
      // proves nothing even when it is safe.
      return { ok: false, why: `session is not idle (state 0x${(st || 0).toString(16)})` };
    }
    const before = readU32(state.session.add(SESSION_STATE));
    const fn = new NativeFunction(state.ersc.base.add(ERSC_INVADE), 'void', [
      'pointer',
      'int',
      'int',
      'int',
    ]);
    const queriesBefore = state.queries;
    fn(state.owner, 0, 1, 1);
    const after = readU32(state.session.add(SESSION_STATE));
    note(`drive: state 0x${(before || 0).toString(16)} -> 0x${(after || 0).toString(16)}`);
    return { ok: true, before, after, queries_before: queriesBefore, queries: state.queries };
  },

  cancel() {
    if (!state.owner) return { ok: false, why: 'no owner object located' };
    const fn = new NativeFunction(state.ersc.base.add(ERSC_CANCEL), 'void', [
      'pointer',
      'int',
      'int',
      'int',
    ]);
    fn(state.owner, 0, 1, 1);
    return { ok: true, state: readU32(state.session.add(SESSION_STATE)) };
  },

  // Make Seamless hand the object over, instead of guessing at it.
  //
  // Every scan in this session found a shape and none found a session: four runs returned four
  // addresses, and the best-ranked candidate -- eleven objects naming the same value -- had
  // garbage where its lock belongs, so `0xb7ba4824` is a repeated constant in ersc's data rather
  // than a pointer to anything. This repo already wrote that lesson down and it was ignored:
  // "Do not re-run a pointer scan expecting a different answer."
  //
  // The hand-over point is a GAME function, which is what makes it safe to hook here: ersc calls
  // `CS::CSMenuMan::OpenConversationChoicesMenu` to raise its own dialog, and at `ersc+0x241cb`
  // it has just done `lea r14,[rcx+0x120]`, so `r14 - 0x120` IS the option-menu object. Detouring
  // inside ersc.dll has killed this process before; detouring the game function it calls has not.
  //
  // 1.16.2 `0x140e9e4f0` -> 1.17.1 `0x140ea0360`, the translation this repo's own DLL logs.
  watchHandover() {
    const game = Process.enumerateModules().find((m) => m.name.toLowerCase() === 'eldenring.exe');
    if (!game) return { ok: false, why: 'eldenring.exe not found' };
    const target = ptr('0x140ea0360');
    try {
      Interceptor.attach(target, {
        onEnter(args) {
          const r14 = this.context.r14;
          if (!r14 || r14.isNull()) return;
          const osm = r14.sub(0x120);
          const sess = readPtr(osm.add(OWNER_SESSION));
          if (sess === null || sess.isNull()) return;
          const st = readU32(sess.add(SESSION_STATE));
          const lock_owner = readU32(sess.add(0x148));
          note(
            `handover: osm=${osm} session=${sess} state=0x${(st || 0).toString(16)} ` +
              `lock_owner=0x${(lock_owner || 0).toString(16)}`,
          );
          // Only adopt an object whose session passes the same shape check the scan used. The
          // hand-over is far stronger evidence, but a bad r14 must not overwrite a good pick.
          if (looksLikeSession(sess)) {
            state.owner = osm;
            state.session = sess;
            state.handover = true;
            note(`handover ADOPTED: owner=${osm} session=${sess}`);
          }
        },
      });
    } catch (e) {
      return { ok: false, why: `could not hook the handover: ${e}` };
    }
    return { ok: true, hooked: target.toString(), game: game.base.toString() };
  },

  report() {
    return {
      queries: state.queries,
      filters: state.filters,
      handover: state.handover === true,
      owner: state.owner ? state.owner.toString() : null,
      session: state.session ? state.session.toString() : null,
      states_seen: state.seen.map((s) => '0x' + s.toString(16)),
      state_now: state.session ? readU32(state.session.add(SESSION_STATE)) : null,
      log: state.log.slice(-40),
    };
  },
};
