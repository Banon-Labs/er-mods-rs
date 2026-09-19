// The port, prototyped: the vanilla finger's bounds prompt, answered, drives Seamless's invade.
//
// # What is being replaced
//
// Confirming the vanilla prompt reaches `FUN_1407c2e10`, which reads the answer through
// `FUN_1407ee550(popup, &result, &row)` and then closes the dialog.  The game's own request goes
// into the network layer Seamless replaced, so it dead-ends in `?NetworkMessage?` and no invasion
// happens.  The Lynchpin instead reaches `ersc+0x25850` -- measured twice, `state=0x1` on entry,
// called from `ersc+0x8276e` on the MENU thread.  So this hooks the vanilla confirm and makes the
// same call from the same thread.
//
// # Why the call is made here and not from a game task
//
// `ersc+0x25850` takes the session mutex.  Driven from the product DLL's own game task it PARKED
// and never returned (bd never-call-ersc-invade-from-inside-a-game-function-hook).  The Lynchpin's
// working call comes off the menu-accept thread, which is the thread this hook runs on.
//
// # The row
//
// `row` is the second out-parameter of the reader, and it is what the game itself uses to tell the
// two options apart.  It has only ever been observed as 0, because no input tried so far moves the
// highlight.  So `REDIRECT_ROW` is configurable and defaults to -1, meaning "any confirming
// answer", which proves the mechanism without needing the highlight solved first.  Setting it to 1
// is the whole difference between the prototype and the product rule.
const POPUP_RESULT_READER = ptr('0x1407ee550');
const ERSC_INVADE = 0x25850;
const SESSION_OWNER = 0x58;
const SESSION_STATE = 0x150;
const SESSION_STATE_IDLE = 1;
// An unanswered poll reads -1; anything else is an answer. `result` was assumed to be a
// confirm/dismiss flag with 1 meaning dismiss -- measured 2026-09-16, a confirm that turned the
// finger ON reported `result=1 row=0`, so that reading was wrong and the filter built on it
// discarded the only answer that acts. `result` is the answer itself.
const NOT_ANSWERED = -1;
// The two rows of the bounds prompt, as the popup reports them. Measured 2026-09-16 by pressing
// D-pad RIGHT before the confirm in one run and pressing the confirm alone in the others: the run
// that moved right reported 2, every run that did not reported 1. The rows are laid out LEFT/RIGHT
// with the cursor starting on the left, so:
const ANSWER_NEARBY_ONLY = 1;
const ANSWER_BOTH_NEAR_AND_FAR = 2;

function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

const ersc = Process.findModuleByName('ersc.dll');
const out = { armed: ersc !== null, owner: null, redirectRow: ANSWER_BOTH_NEAR_AND_FAR,
  fired: 0, log: [], seen: [] };
let armed = false;

function note (line) {
  out.log.push(line);
  send({ kind: 'redirect', line: line });
}

function sessionState (owner) {
  try { return owner.add(SESSION_OWNER).readPointer().add(SESSION_STATE).readU32(); }
  catch (e) { return null; }
}

if (ersc !== null) {
  Interceptor.attach(follow(POPUP_RESULT_READER), {
    onEnter (args) { this.result = args[1]; this.row = args[2]; },
    onLeave (retval) {
      if (retval.toInt32() === 0 || !armed || out.owner === null) return;
      let result = -1;
      let row = -1;
      try { result = this.result.readS32(); row = this.row.readS32(); } catch (e) { return; }
      // Every observation is recorded. A silent early return here is indistinguishable from the
      // hook never firing, and that ambiguity already cost one run.
      out.seen.push('result=' + result + ' row=' + row);
      if (out.seen.length > 12) out.seen.shift();
      if (result === NOT_ANSWERED) return;
      // The branch the whole feature is. `Both near and far` goes to Seamless; `Nearby only` is
      // deliberately NOT redirected -- it belongs to this repo's own block-based nearby filter,
      // and taking it over here would be the feature choosing for the player.
      if (result === ANSWER_NEARBY_ONLY && out.redirectRow === ANSWER_BOTH_NEAR_AND_FAR) {
        note('answer ' + result + ' is Nearby only -- left to the local block-based filter');
        return;
      }
      if (out.redirectRow !== -1 && result !== out.redirectRow) {
        note('answer ' + result + ' is not the redirect answer ' + out.redirectRow + '; left alone');
        return;
      }
      const owner = ptr(out.owner);
      const state = sessionState(owner);
      if (state !== SESSION_STATE_IDLE) {
        note('confirm result=' + result + ' row=' + row
          + ' -- refusing: the Seamless session state is ' + state + ', not idle');
        return;
      }
      armed = false;   // one shot per arming, so a wedged dialog cannot spam the session
      out.fired += 1;
      note('confirm result=' + result + ' row=' + row + ' -- calling ersc+0x25850 on this thread');
      try {
        new NativeFunction(ersc.base.add(ERSC_INVADE), 'void', ['pointer'])(owner);
        note('ersc+0x25850 returned; session state is now ' + sessionState(owner));
      } catch (e) {
        note('ersc+0x25850 threw: ' + e.message);
      }
    },
  });
}

rpc.exports = {
  report: function () { return out; },
  // The owner `ersc+0x25850` takes: its `+0x58` is the session. Learned from a real call rather
  // than guessed, and validated before every use.
  arm: function (ownerHex, row) {
    const owner = ptr(ownerHex);
    const state = sessionState(owner);
    if (state === null) return { ok: false, why: 'that owner has no readable session' };
    out.owner = String(owner);
    out.redirectRow = (row === undefined || row === null) ? ANSWER_BOTH_NEAR_AND_FAR : row;
    armed = true;
    return { ok: true, owner: out.owner, sessionState: state, redirectRow: out.redirectRow };
  },
  disarm: function () { armed = false; return { ok: true }; },
};
console.log('finger-redirect-to-seamless: loaded' + (ersc === null ? ' (ersc.dll missing!)' : ''));
