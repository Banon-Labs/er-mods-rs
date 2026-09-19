// What does the vanilla finger's bounds prompt hand back, and what does the game do with it?
//
// # The chain, read out of the 1.17.0 image
//
//   FUN_1407c2ae0   the per-tick state machine on the goods dialog; `dialog+0x10` is the step
//                   (1 = raise, 2 = prompt is up, 0 = done)
//   step 2, case 4  role == 0x1e && CSSessionManager[0xc] == 3 ? FUN_1407c2ec0 : FUN_1407c2e10
//   FUN_1407c2e10   the START path: `FUN_1407ee550(popup, &result, &row)`, then on a confirming
//                   result it calls `dialog->vtable[0x88]` -- the invasion request itself
//
// So `vtable[0x88]` is the one function the finger's answer reaches, and the row the player chose
// arrives in the second out-parameter.  Both are recorded here because the detour needs the row to
// decide between our own nearby filter and Seamless's invade, and needs the virtual to replace.
const CONSUMER_START = ptr('0x1407c2e10');
const CONSUMER_CANCEL = ptr('0x1407c2ec0');
const POPUP_RESULT = ptr('0x1407ee550');
const CONFIRM_VSLOT = 0x88;

function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

function where (address) {
  const m = Process.findModuleByAddress(address);
  return m === null ? String(address) : m.name + '+0x' + address.sub(m.base).toString(16);
}

const out = { events: [], confirmVirtual: null };

Interceptor.attach(follow(CONSUMER_START), {
  onEnter (args) {
    const dialog = args[0];
    let virt = null;
    try { virt = where(dialog.readPointer().add(CONFIRM_VSLOT).readPointer()); } catch (e) {}
    out.confirmVirtual = virt;
    const line = 'START-path consumer  dialog=' + dialog + ' arg2=' + args[1].toInt32()
      + '  vtable[0x88]=' + virt;
    out.events.push(line);
    send({ kind: 'consumer', line: line, confirmVirtual: virt });
  },
});

Interceptor.attach(follow(CONSUMER_CANCEL), {
  onEnter () {
    out.events.push('CANCEL-path consumer');
    send({ kind: 'consumer', line: 'CANCEL-path consumer' });
  },
});

// The two out-parameters are the finding: the first is the confirm/dismiss result, the second is
// the row. A driver that presses without reading these is confirming blind.
// The popup control the result is read out of. Kept so the highlighted row can be found by
// diffing the object across a D-pad press -- `row` above is 0 on both rows, so it is not it, and
// without a highlight oracle a confirm is a coin flip between the two options.
let popupCtrl = null;

Interceptor.attach(follow(POPUP_RESULT), {
  onEnter (args) { popupCtrl = args[0]; this.result = args[1]; this.row = args[2]; },
  onLeave (retval) {
    let r = null;
    let row = null;
    try { r = this.result.readS32(); row = this.row.readS32(); } catch (e) {}
    if (retval.toInt32() === 0) return;   // nothing was answered on this tick
    const line = 'popup answered  result=' + r + ' row=' + row;
    out.events.push(line);
    send({ kind: 'answer', result: r, row: row, line: line });
  },
});

rpc.exports = {
  report: function () { return out; },
  popup: function () { return popupCtrl === null ? null : String(popupCtrl); },
  // A dword window of the popup control, for diffing across a cursor press.
  window: function (size) {
    if (popupCtrl === null) return null;
    const words = [];
    for (let i = 0; i < (size || 0x400) / 4; i++) {
      try { words.push(popupCtrl.add(i * 4).readU32()); } catch (e) { words.push(null); }
    }
    return words;
  },
};
console.log('finger-answer-consumer: armed on the start path, the cancel path and the result reader');
