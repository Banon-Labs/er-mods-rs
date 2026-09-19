// Which ERSC action does accepting the Lynchpin's dialog invoke, and who calls it?
//
// # Why these five addresses
//
// The decrypted runtime dump gives them exactly, so nothing here is a scan. Each is a tiny,
// fully readable function of identical shape -- read `[rcx+0x58]` as the session, take the
// `std::mutex` at `session+0x100`, write one constant to `session+0x150`, unlock:
//
//   0x180025850  state -> 0x0e   the invade action  (guarded: returns unless state == 1)
//   0x1800258d0  state -> 0x23   cancel
//   0x180024ef0  state -> 0x02
//   0x1800257d0  state -> 0x07
//   0x1800259d0  state -> 0x23
//
// The RETURN ADDRESS is the finding. A caller inside `ersc.dll` names Seamless's own menu handler
// -- which is the function the vanilla finger's `Both near and far` has to reach in order to do
// what the Lynchpin does. Vanilla's own path instead dead-ends in `?NetworkMessage?`, an FMG id
// with no entry, because the game's network layer is the one Seamless replaced.
const ACTIONS = {
  '0x25850 invade': 0x25850,
  '0x258d0 cancel': 0x258d0,
  '0x24ef0 state2': 0x24ef0,
  '0x257d0 state7': 0x257d0,
  '0x259d0 state23': 0x259d0,
};
const SESSION_OWNER_OFFSET = 0x58;
const SESSION_STATE_OFFSET = 0x150;

const out = { hooked: [], trail: [], counts: {} };
const ersc = Process.findModuleByName('ersc.dll');

function where (address) {
  const m = Process.findModuleByAddress(address);
  return m === null ? String(address) : m.name + '+0x' + address.sub(m.base).toString(16);
}

if (ersc !== null) {
  for (const name of Object.keys(ACTIONS)) {
    const fn = ersc.base.add(ACTIONS[name]);
    out.hooked.push(name);
    out.counts[name] = 0;
    Interceptor.attach(fn, {
      onEnter (args) {
        out.counts[name] += 1;
        let state = '?';
        try {
          state = '0x' + args[0].add(SESSION_OWNER_OFFSET).readPointer()
            .add(SESSION_STATE_OFFSET).readU32().toString(16);
        } catch (e) { /* fault-closed: the owner may not be a session */ }
        // The first hit of each action gets a full chain, because one return address names the
        // immediate caller and the question is which MENU path reached it.
        let chain = '';
        if (out.counts[name] === 1) {
          try {
            chain = '\n      ' + Thread.backtrace(this.context, Backtracer.FUZZY)
              .slice(0, 12).map(where).join('\n      ');
          } catch (e) { /* fault-closed: a fuzzy walk can fail on a packed frame */ }
        }
        const line = name + '  this=' + args[0] + ' state=' + state
          + '  <- ' + where(this.returnAddress) + chain;
        out.trail.push(line);
        send({ kind: 'action', line: line });
      },
    });
  }
}

rpc.exports = { report: function () { return out; } };
console.log('ersc-action-trace: armed on ' + out.hooked.length + ' action(s)');
