// Is a dialog on screen? Ask the function the game polls while one is.
//
// # Why a press is not an answer
//
// Pressing pad B and printing "dialog cleared" is a receipt for an injected button, not a reading
// of the screen. The two come apart exactly when it matters: a dialog that swallowed the press is
// still up, and every later press lands in it, so the run reports "the item raised nothing".
//
// `FUN_1407ee550(popup, &result, &row)` is the game's own answer reader. While a dialog is open it
// is called every frame and writes `-1` into `result`, meaning not answered yet; when the dialog
// closes, the calls stop. So the CALL RATE is the oracle, and the answer value tells an open
// dialog apart from one that was just answered.

'use strict';

const POPUP_RESULT_READER = ptr('0x1407ee550');

const out = { calls: 0, lastResult: null, lastRow: null, answers: 0, forced: 0 };

// One answer, written into the reader's own out-parameter on its next call.
//
// The bounds prompt has two rows and no cancel row, so pad B is read and discarded and a prompt
// raised by a driver that then walked away cannot be closed by any button at all. The reader is
// where the answer enters the game, so writing it there is the same event the two rows produce.
let pendingAnswer = null;

// About 28 percent of function entries on this build open with an Arxan healing stub, and a hook on
// the stub never fires.
function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

Interceptor.attach(follow(POPUP_RESULT_READER), {
  onEnter (args) { this.result = args[1]; this.row = args[2]; },
  onLeave (retval) {
    if (retval.toInt32() === 0) return;
    out.calls += 1;
    try {
      out.lastResult = this.result.readS32();
      out.lastRow = this.row.readS32();
    } catch (e) {
      return;
    }
    if (out.lastResult !== -1) {
      out.answers += 1;
      return;
    }
    if (pendingAnswer === null) return;
    try {
      this.result.writeS32(pendingAnswer);
      out.lastResult = pendingAnswer;
      out.forced += 1;
      send({ kind: 'forced-answer', line: 'wrote result=' + pendingAnswer + ' into the reader' });
    } catch (e) {
      send({ kind: 'forced-answer', line: 'could not write the answer: ' + e });
    }
    pendingAnswer = null;
  },
});

rpc.exports = {
  // Calls since the last call to this, so a caller measures a window rather than a total.
  sample: function () {
    const calls = out.calls;
    out.calls = 0;
    return { calls: calls, lastResult: out.lastResult, lastRow: out.lastRow,
      answers: out.answers };
  },
  report: function () { return out; },
  // Answer the open dialog once. `0` is the close-without-acting value, `1` is Nearby only and
  // `2` is Both near and far, measured on this build.
  answer: function (value) {
    pendingAnswer = value;
    return { ok: true, pending: pendingAnswer };
  },
};
console.log('dialog-open-oracle: reading');
