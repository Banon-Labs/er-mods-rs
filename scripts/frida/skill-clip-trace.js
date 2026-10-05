// Which skill clips the game picks while the player uses a skill by hand: a passive trace for
// questions like "how does Euporia Vortex reach its 040110 / 040111 clips". Nothing here blocks
// or injects input.
//
//   grant   config `grant: [{ item, gem }]` puts weapons into the inventory on the first player
//           frame, skipped when already carried (the chainsaw driver's grant op: mint 0x140672b30,
//           add by handle 0x140246480, release 0x1406832d0; CSGaitem global 0x143d6d900).
//   clips   every TimeAct the CustomManualSelectorGenerator writer 0x1419bb530 [0x1419b96c0]
//           stores, categories minCat..maxCat (default all, so weapon clips a0xx-a2xx show too)
//           at node+0xec (a<category>_<anim>), with the frame,
//           the right-hand weapon and FP. Repeats of the same clip on consecutive frames are folded.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const cfg = Object.assign({ grant: [], minCat: 0, maxCat: 999, fpMax: null }, globalThis.__ER_FRIDA_CONFIG || {});

const WORLD_CHR_MAN = va('0x143d69ff8');
const GAME_DATA_MAN = va('0x143d61f98');
const GLOBAL_CSGAITEM = va('0x143d6d900');
const PRE_BEHAVIOR_SAFE = va('0x140401f30');
const CMSG_SET_TAE = va('0x1419bb530');
const GET_EQUIP = new NativeFunction(va('0x1406577b0'), 'int', ['pointer', 'int']);
const GET_INV = new NativeFunction(va('0x140247b30'), 'pointer', ['pointer']);
const GET_IDX = new NativeFunction(va('0x14024c560'), 'int', ['pointer', 'pointer']);
const MINT = new NativeFunction(va('0x140672b30'), 'pointer', ['pointer', 'pointer', 'int', 'int']);
const ADD_BY_HANDLE = new NativeFunction(va('0x140246480'), 'int', ['pointer', 'pointer', 'uint32', 'uint8', 'uint8']);
const HANDLE_DTOR = new NativeFunction(va('0x1406832d0'), 'void', ['pointer']);

const S = { frame: 0, granted: false, last: null, lastFrame: -10, clips: 0 };

function mainPlayer () {
  try {
    const w = WORLD_CHR_MAN.readPointer();
    if (w.isNull()) return null;
    const p = w.add(0x1e508).readPointer();
    return p.isNull() ? null : p;
  } catch (e) { return null; }
}
function equipGameData () {
  try {
    const g = GAME_DATA_MAN.readPointer();
    if (g.isNull()) return null;
    const pgd = g.add(0x08).readPointer();
    return pgd.isNull() ? null : pgd.add(0x2b0);
  } catch (e) { return null; }
}
function carried (egd, item) {
  const id = Memory.alloc(4);
  id.writeS32(item);
  return GET_IDX(GET_INV(egd), id) >= 0;
}
function grant (egd, g) {
  const r = { kind: 'grant', item: g.item, gem: g.gem === undefined ? -1 : g.gem, ok: false };
  if (carried(egd, g.item)) { r.ok = true; r.why = 'already'; return r; }
  const h = Memory.alloc(16);
  MINT(GLOBAL_CSGAITEM.readPointer(), h, g.item, r.gem);
  if (h.readU32() === 0) { r.why = 'mint_failed'; return r; }
  r.idx = ADD_BY_HANDLE(egd, h, 1, 1, 1);
  HANDLE_DTOR(h);
  r.ok = carried(egd, g.item);
  if (!r.ok) r.why = 'not_added';
  return r;
}
// The selector's hkbNode name (`SwordArtsStanceNoSyncLoop_CMSG`, `DrawStanceRightLoop_CMSG`, ...), so
// two selectors that pick the same clip can be told apart. The name pointer's offset in the live
// object is not pinned down: the 2018 tagfile has it at +0x48, so +0x38 and +0x48 are both tried and
// the first that reads as a plain identifier wins. Cached per node.
const names = new Map();
function nodeName (node) {
  const k = node.toString();
  if (names.has(k)) return names.get(k);
  let out = null;
  for (const off of [0x38, 0x48, 0x40]) {
    try {
      const s = node.add(off).readPointer().readUtf8String(96);
      if (s && /^[A-Za-z][A-Za-z0-9_]{3,}$/.test(s)) { out = s + '@+0x' + off.toString(16); break; }
    } catch (e) { /* not a string pointer */ }
  }
  names.set(k, out);
  return out;
}
function fp (p) { try { return p.add(0x190).readPointer().readPointer().add(0x148).readS32(); } catch (e) { return null; } }

const hooks = [];
hooks.push(Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    const p = mainPlayer();
    if (p === null || !args[0].equals(p)) return;
    S.frame += 1;
    // `fpMax`: FP held at or below this value every frame, for skills that behave differently on low FP.
    if (cfg.fpMax !== null) { try { const dm = p.add(0x190).readPointer().readPointer(); if (dm.add(0x148).readS32() > cfg.fpMax) dm.add(0x148).writeS32(cfg.fpMax); } catch (e) { /* no data module yet */ } }
    if (!S.granted) {
      S.granted = true;
      const egd = equipGameData();
      if (egd !== null) cfg.grant.forEach(function (g) { try { send(grant(egd, g)); } catch (e) { send({ kind: 'grant', item: g.item, ok: false, why: e.message }); } });
    }
  },
}));
hooks.push(Interceptor.attach(CMSG_SET_TAE, {
  onEnter (args) { this.node = args[0]; },
  onLeave () {
    let tae;
    try { tae = this.node.add(0xec).readS32(); } catch (e) { return; }
    const cat = Math.floor(tae / 1000000);
    if (cat < cfg.minCat || cat > cfg.maxCat) return;
    const fold = tae === S.last && S.frame - S.lastFrame <= 1;
    S.last = tae; S.lastFrame = S.frame;
    if (fold) return;
    const p = mainPlayer();
    S.clips += 1;
    send({ kind: 'clip', frame: S.frame, tae: tae, clip: 'a' + cat + '_' + String(tae % 1000000).padStart(6, '0'), node: this.node.toString(), name: nodeName(this.node), heldR: p === null ? null : GET_EQUIP(p, -1), fp: p === null ? null : fp(p) });
  },
}));

send({ kind: 'armed', cfg: cfg, hooks: hooks.length });

rpc.exports = {
  dispose () { hooks.forEach(function (h) { h.detach(); }); },
};
