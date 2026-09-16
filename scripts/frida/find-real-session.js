// Let ersc.dll point at its own session, instead of a heuristic sweep guessing one.
//
// `ersc+0x25850` is four instructions of work -- require `[session+0x150] == 1`, lock
// `session+0x100`, set the state to `0x0e`, unlock -- and our worker has been parked inside that
// lock since it was called. `_Thread_id` and `_Count` both read zero while it waits, which is what
// an object that is not really a live `_Mtx_internal_imp_t` looks like: every field our scan checks
// is plausible and the lock underneath is not one.
//
// So do not check the object harder. Watch `_Mtx_lock` and record what ersc itself passes: the
// session's mutex is locked by Seamless's own code on its own schedule, and `rcx - 0x100` is then
// the session by construction rather than by resemblance.
const MTX_LOCK_RVA = 0xf9828;
const INVADE_RVA = 0x25850;
const SESSION_MUTEX_OFFSET = 0x100;
const SESSION_STATE_OFFSET = 0x150;
const SESSION_GUARD_OFFSET = 0x14c;
const OWNER_SESSION_OFFSET = 0x58;
// The state codes this build writes. `0x01` idle and `0x0e` searching are the two that matter here.
const STATES = [0x00, 0x01, 0x0e, 0x0f, 0x10, 0x12, 0x15, 0x16, 0x22, 0x23, 0x24];

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  console.log('find-session: ersc.dll is not loaded');
}

const seen = new Set();
const candidates = new Map();

function noteCandidate(session, why) {
  const key = session.toString();
  if (candidates.has(key)) {
    return;
  }
  let state = null;
  let guard = null;
  try {
    state = session.add(SESSION_STATE_OFFSET).readU32();
    guard = session.add(SESSION_GUARD_OFFSET).readS32();
  } catch (e) {
    return;
  }
  if (!STATES.includes(state)) {
    return;
  }
  candidates.set(key, { session, state, guard });
  // `_Type` is the comparison that matters. Our own session reads `0x100003`, which our scan
  // accepted; an object ersc demonstrably locks shows what this build's real mutexes look like, so
  // the two side by side say whether ours is a live mutex or an impostor that passed the shape
  // test and then hung the first thread to touch it.
  let shape = 'unreadable';
  try {
    const mutex = session.add(SESSION_MUTEX_OFFSET);
    shape =
      `_Type=0x${mutex.readU32().toString(16)} ` +
      `_Thread_id=0x${mutex.add(0x48).readU32().toString(16)} ` +
      `_Count=${mutex.add(0x4c).readS32()}`;
  } catch (e) {
    shape = `unreadable (${e.message.split(' ')[0]})`;
  }
  console.log(
    `find-session: candidate ${session} state=0x${state.toString(16)} guard=${guard} ${shape} (${why})`
  );
}

if (ersc !== null) {
  const lock = ersc.base.add(MTX_LOCK_RVA);
  console.log(`find-session: watching _Mtx_lock @${lock}`);
  Interceptor.attach(lock, {
    onEnter(args) {
      const mutex = args[0];
      const key = mutex.toString();
      if (seen.has(key) || seen.size > 256) {
        return;
      }
      seen.add(key);
      noteCandidate(mutex.sub(SESSION_MUTEX_OFFSET), `ersc locked ${mutex}`);
    },
  });
}

// Is anything calling it at all?
//
// The first pass printed no candidates, and a silent hook is not evidence until something in the
// same attach is shown to fire. Two additions: a hit counter on `_Mtx_lock` itself, so "ersc never
// locks anything" and "the hook did not take" stop looking alike, and a hook on the lock BODY at
// `ersc+0xf98c0` -- the frame our parked worker is actually sitting in, so that address is known
// to be live code on the path.
const LOCK_BODY_RVA = 0xf98c0;
let lockHits = 0;
let bodyHits = 0;
if (ersc !== null) {
  Interceptor.attach(ersc.base.add(MTX_LOCK_RVA), {
    onEnter() {
      lockHits += 1;
      if (lockHits === 1 || lockHits === 500) {
        console.log(`find-session: _Mtx_lock has fired ${lockHits}x -- the hook is live`);
      }
    },
  });
  Interceptor.attach(ersc.base.add(LOCK_BODY_RVA), {
    onEnter(args) {
      bodyHits += 1;
      if (bodyHits === 1 || bodyHits === 500) {
        console.log(`find-session: lock body has fired ${bodyHits}x`);
      }
    },
  });
  console.log(`find-session: counters armed on _Mtx_lock and the lock body`);
}

// And the session our own DLL settled on, for comparison.
const OURS = ptr('0x45da73c8');
try {
  console.log(
    `find-session: our session ${OURS} state=0x${OURS.add(SESSION_STATE_OFFSET).readU32().toString(16)} ` +
      `mutex _Type=0x${OURS.add(SESSION_MUTEX_OFFSET).readU32().toString(16)} ` +
      `_Thread_id=0x${OURS.add(SESSION_MUTEX_OFFSET + 0x48).readU32().toString(16)} ` +
      `_Count=${OURS.add(SESSION_MUTEX_OFFSET + 0x4c).readS32()}`
  );
} catch (e) {
  console.log(`find-session: our session unreadable: ${e.message}`);
}

// Every distinct mutex ersc locks, unfiltered.
//
// The state filter above admitted exactly one object in 500 lock calls, and our own session was not
// among them at all -- ersc never locks it. That is the strongest evidence yet that the sweep
// picked the wrong object, but "one candidate" is also what a too-narrow filter produces, so print
// the landscape instead of a filtered view of it.
const allSeen = new Set();
if (ersc !== null) {
  Interceptor.attach(ersc.base.add(MTX_LOCK_RVA), {
    onEnter(args) {
      const mutex = args[0];
      const key = mutex.toString();
      if (allSeen.has(key) || allSeen.size >= 24) {
        return;
      }
      allSeen.add(key);
      const session = mutex.sub(SESSION_MUTEX_OFFSET);
      let state = 'unreadable';
      let type = 'unreadable';
      try {
        state = `0x${session.add(SESSION_STATE_OFFSET).readU32().toString(16)}`;
        type = `0x${mutex.readU32().toString(16)}`;
      } catch (e) {
        state = 'unreadable';
      }
      console.log(
        `find-session: ersc locks ${mutex} (#${allSeen.size}) _Type=${type} ` +
          `-> ${session}+0x150 = ${state}`
      );
    },
  });
  console.log('find-session: listing every distinct mutex ersc locks, up to 24');
}
