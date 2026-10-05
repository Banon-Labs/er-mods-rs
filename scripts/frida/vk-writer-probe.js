// Which inputs reach which layer, for choosing where the chainsaw driver injects.
//
//   vk      the one writer of FD4PadDevice+0x88, 1.17.1 0x142665d20 [1.16.2 0x1426634a0]. Keyboard
//           input never reached it (every key pressed, 0 calls, 2026-10-04).
//   action  CSChrActionRequestModule::UpdateFromManipulator, 1.17.1 0x140408190 [0x140407c60]: on
//           entry +0x10 holds the raw action bits the manipulator wrote this frame (+0x18 previous,
//           +0x40 disabled), whichever device produced them.
//
// Reported every 60 action-module updates while anything is set. Read-only.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const WRITER = va('0x142665d20');
const UPDATE_FROM_MANIPULATOR = va('0x140408190');

const devices = new Map();
const S_owner = new Map();
const WCM = game.base.add(0x3d69ff8);
function mainPlayer () { try { const w = WCM.readPointer(); return w.isNull() ? null : w.add(0x1e508).readPointer().toString(); } catch (e) { return null; } }
let ticks = 0;
let vk = {};
let action = {};

const hooks = [
  Interceptor.attach(WRITER, {
    onEnter (args) {
      const d = args[0].toString();
      if (!devices.has(d)) devices.set(d, devices.size);
      const k = 'dev' + devices.get(d) + ':' + args[1].toUInt32();
      vk[k] = (vk[k] || 0) + 1;
    },
  }),
  Interceptor.attach(UPDATE_FROM_MANIPULATOR, {
    onEnter (args) {
      ticks += 1;
      if (ticks % 60 === 0) report();
      if (!S_owner.has(args[0].toString())) { let o = null; try { o = args[0].add(8).readPointer().toString(); } catch (e) { o = e.message; } S_owner.set(args[0].toString(), o); send({ kind: 'module', module: args[0].toString(), owner8: o, player: mainPlayer() }); }
      const raw = args[0].add(0x10).readU64();
      if (raw.equals(0)) return;
      const k = args[0].toString() + ':0x' + raw.toString(16);
      action[k] = (action[k] || 0) + 1;
    },
  }),
];

send({ kind: 'armed', writer: WRITER.toString(), update: UPDATE_FROM_MANIPULATOR.toString() });

// Reported every 60 action-module updates (one per character with a manipulator, per frame), on
// the game thread.
function report () {
  if (Object.keys(vk).length > 0 || Object.keys(action).length > 0) send({ kind: 'down', vk: vk, action: action });
  vk = {};
  action = {};
}

rpc.exports = {
  dispose () {
    hooks.forEach(function (h) { h.detach(); });
  },
};
