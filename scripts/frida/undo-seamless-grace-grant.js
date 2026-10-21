// Undo what Seamless Co-op's goods grant added to the live character, through the engine's own
// calls, then get the game to write its save.
//
// # What the grant is, read statically out of ersc.dll 2.0.1 (vendor-archive/seamless)
//
// `ersc+0x86060` is the body of a std::function that ersc's hook installer (`ersc+0x750a0`, the
// block at `0x75b7b`) puts over the game address in its resolved-address table slot `+0x350`. The
// installer writes a bare `e9` over that entry -- no original bytes are kept -- to a 16-byte stub
// (`movabs rax,<stub2>; jmp rax`) whose target is `movabs rax,<impl>; movq xmm5,rax; movabs r11,
// ersc+0x94210; jmp r11`. `impl+0x10` is the lambda, `impl+0x8` the captured ersc context. The
// lambda: (1) for goods 8380001..8380006 and 8380012 (`ersc+0x1c1000`) not already held, give one
// each through the give function at `[ctx+0x68]+0x10`; (2) for goods 0, 10..16, 20, 21
// (`ersc+0x1c101c`) and (3) for 8380007..8380011 (`ersc+0x1c1058` + 0x7fde6b) take the whole
// held quantity away with the adjust function at `[ctx+0x68]+0x40`; then call `ersc+0xaa7f0` and set
// `[[ctx+0x28]+8] = 1`. No event flag is touched by that body.
//
// # Phases
//
// PHASE is edited and the watcher reloads the file: 'read' is read-only, 'remove' removes the
// eight goods, 'flags' clears FLAGS_TO_CLEAR (empty unless static RE names one), 'save' asks the
// game to save. Every phase that writes runs once, on the game's main thread, inside PeekMessageW.
'use strict';

const PHASE = 'read';

// The grant re-runs on every grace rest while ersc.dll is loaded (ResetWorld calls the replaced
// WorldChrMan setter at game+0x50b580 on 1.17.1, game+0x50b800 on 2.7.2.0), and gives back any of
// the seven that are missing. With this
// set, the loop bound at ersc+0x860f6 (`cmp rsi, 7`) is patched to 0 while the agent is loaded, so
// the give list stays empty; everything else in the lambda (its two removal loops, the
// `ersc+0xaa7f0` session call that also raises WorldChrMan+0x1e524, the latch) still runs.
// dispose() puts the 7 back unless KEEP_BLOCK_AFTER_DETACH; an already-patched 0 is accepted.
const BLOCK_REGRANT = true;
const KEEP_BLOCK_AFTER_DETACH = true;
const GRANT_LOOP_CMP_RVA = 0x860f6;   // 48 83 fe 07  cmp rsi, 7

const GAME = 'eldenring.exe';
const ERSC = 'ersc.dll';

// 2.7.2.0 addresses, carried from 1.17.1 with scripts/map-rvas-1171-to-1172.py; the 1.17.1 value is
// in each comment. The 1.17.1 function rvas were below the 0xafefe9 boundary, so 1.16.2 == 1.17.0
// == 1.17.1 (`scripts/map-rvas-1170-to-1171.py`), each disassembled in eldenring-deobf-1.17.1.bin
// first; every function below opens with the same bytes in eldenring-deobf-1.17.2.bin, and every
// global is read by the same instructions at their 2.7.2.0 places.
const GAME_DATA_MAN_GLOBAL_RVA = 0x3d64fb8;   // 1.17.1 0x3d61f98, read off CanMainPlayerUseGoods+0xd
const PLAYER_GAME_DATA_OFFSET = 0x08;
const EQUIP_GAME_DATA_OFFSET = 0x2b0;          // held by value inside PlayerGameData
const GET_EQUIP_INVENTORY_DATA_RVA = 0x247b30;  // unmoved; lea rax,[rcx+0x158]; ret
const GET_ITEM_INVENTORY_IDX_RVA = 0x24c560;    // unmoved; EquipInventoryData::GetItemInventoryIdx(inv, int*)
const GET_QUANTITY_BY_ITEM_ID_RVA = 0x24c1b0;   // unmoved; EquipInventoryData::GetQuantityByItemId(inv, int*)
const REMOVE_ITEM_RVA = 0x248ad0;               // unmoved; EquipGameData::RemoveItem(egd, idx, flag, refresh)
const GET_INVENTORY_ITEM_ENTRY_BY_INDEX_RVA = 0x24e770; // unmoved

const GAME_MAN_GLOBAL_RVA = 0x3d709a8;          // 1.17.1 0x3d6d988, read off RequestSave+0xf
const REQUEST_SAVE_RVA = 0x67b820;              // 1.17.1 0x67b370
const SAVE_DISABLED_BYTE_RVA = 0x3d8c740;       // 1.17.1 0x3d89720
const EVENT_FLAG_MAN_GLOBAL_RVA = 0x3d6f4d8;    // 1.17.1 0x3d6c4b8
const GET_EVENT_FLAG_RVA = 0x5fa700;           // 1.17.1 0x5fa250; int GetEventFlag(man, u32)
const SET_EVENT_FLAG_RVA = 0x5d3410;           // 1.17.1 0x5d2f60; SetEventFlag(man, u32*, u8 state, caller*, u8 sync)
const SET_EVENT_FLAG_BIT_RVA = 0x5faef0;       // 1.17.1 0x5faa40
const GET_EVENT_FLAG_PTR_RVA = 0x5d2630;       // 1.17.1 0x5d2180

const GOODS = 0x40000000;
const TARGETS = [8380001, 8380002, 8380003, 8380004, 8380005, 8380006, 8380012, 9153];
// Everything else the grant body touches, read so the report says what it found.
const ALSO_READ = [8380007, 8380008, 8380009, 8380010, 8380011, 0, 10, 11, 12, 13, 14, 15, 16, 20, 21];
// Every flag ersc 2.0.1 writes by literal id (all clears, in its per-tick update ersc+0x81430), the
// flag that gates one of them, and the vanilla flags tied to the 9153 tutorial in common.emevd
// (event 1100 slot 53: args 9153, 10530, 0, 510530) and m11_00/m11_05 (DirectlyGivePlayerItem
// goods 9153 under flag 710880; 9150 under 710850; area flag 69480).
const FLAGS_TO_READ = [11102790, 11102845, 11102848, 11100020, 105, 6951,
  9153, 10530, 510530, 710880, 9150, 10500, 510500, 710850, 69480];
const FLAGS_TO_CLEAR = [];

const GRANT_LAMBDA_RVA = 0x86060;
const STUB_DISPATCH_RVA = 0x94210;

const game = Process.getModuleByName(GAME);
const ersc = Process.findModuleByName(ERSC);

function hex (v) { return '0x' + (v >>> 0).toString(16); }
function inGame (p) { return p.compare(game.base) >= 0 && p.compare(game.base.add(game.size)) < 0; }
function inErsc (p) { return ersc !== null && p.compare(ersc.base) >= 0 && p.compare(ersc.base.add(ersc.size)) < 0; }
function rel (p) {
  if (inGame(p)) return 'game+0x' + p.sub(game.base).toString(16);
  if (inErsc(p)) return 'ersc+0x' + p.sub(ersc.base).toString(16);
  return p.toString();
}
function bytes (p, n) {
  return Array.from(new Uint8Array(p.readByteArray(n))).map((b) => b.toString(16).padStart(2, '0')).join('');
}

function pdataSection () {
  const pe = game.base.add(game.base.add(0x3c).readU32());
  const count = pe.add(6).readU16();
  const first = pe.add(24).add(pe.add(20).readU16());
  for (let i = 0; i < count; i++) {
    const s = first.add(i * 40);
    let name = '';
    for (let b = 0; b < 8; b++) { const c = s.add(b).readU8(); if (c === 0) break; name += String.fromCharCode(c); }
    if (name === '.pdata') return { rva: s.add(12).readU32(), size: s.add(8).readU32() };
  }
  return null;
}

// Decode ersc's two-stub detour: entry `e9` -> `48 b8 imm64 ff e0` -> `48 b8 <impl> 66 48 0f 6e e8
// 49 bb <dispatch> 41 ff e3`. Returns null for anything else.
function decodeErscDetour (entry) {
  if (entry.readU8() !== 0xe9) return null;
  const stub1 = entry.add(5).add(entry.add(1).readS32());
  if (stub1.readU16() !== 0xb848 || stub1.add(10).readU16() !== 0xe0ff) return null;
  const stub2 = stub1.add(2).readPointer();
  if (stub2.readU16() !== 0xb848 || bytes(stub2.add(10), 5) !== '66480f6ee8' || stub2.add(15).readU16() !== 0xbb49) return null;
  const impl = stub2.add(2).readPointer();
  const dispatch = stub2.add(17).readPointer();
  return { impl: impl, dispatch: dispatch, fn: impl.add(0x10).readPointer(), ctx: impl.add(0x8).readPointer() };
}

function survey () {
  const out = { ersc: ersc === null ? null : ersc.base.toString(), hooks: [], grant: null };
  const pdata = pdataSection();
  const entries = Math.floor(pdata.size / 12);
  for (let i = 0; i < entries; i++) {
    const start = game.base.add(pdata.rva + i * 12).readU32();
    if (start === 0) continue;
    const at = game.base.add(start);
    let d = null;
    try { d = decodeErscDetour(at); } catch (e) { d = null; }
    if (d === null) continue;
    const row = { game: hex(start), fn: rel(d.fn), dispatch: rel(d.dispatch), ctx: d.ctx.toString() };
    out.hooks.push(row);
    if (inErsc(d.fn) && d.fn.sub(ersc.base).toInt32() === GRANT_LAMBDA_RVA) out.grant = { row: row, ctx: d.ctx };
  }
  return out;
}

function ctxReport (ctx) {
  const r = {};
  const table = ctx.readPointer();
  r.table = table.toString();
  const known = {};
  known[SET_EVENT_FLAG_RVA] = 'SetEventFlag'; known[SET_EVENT_FLAG_BIT_RVA] = 'SetEventFlagBit';
  known[GET_EVENT_FLAG_RVA] = 'GetEventFlag'; known[GET_EVENT_FLAG_PTR_RVA] = 'GetEventFlag(int*)';
  known[EVENT_FLAG_MAN_GLOBAL_RVA] = 'CSEventFlagMan global';
  known[GAME_DATA_MAN_GLOBAL_RVA] = 'GameDataMan global';
  r.eventFlagSlots = [];
  r.tableGamePtrs = 0;
  for (let off = 0; off < 0x1000; off += 8) {
    let p;
    try { p = table.add(off).readPointer(); } catch (e) { break; }
    if (!inGame(p)) continue;
    r.tableGamePtrs += 1;
    const rva = p.sub(game.base).toInt32();
    if (known[rva] !== undefined) r.eventFlagSlots.push(hex(off) + '=' + known[rva]);
  }
  const fnTable = ctx.add(0x68).readPointer();
  r.fnTable = [];
  try {
    const give = fnTable.add(0x10).readPointer();
    r.giveObjectGlobal = rel(give.readPointer());
    r.giveFn = rel(give.add(0x18).readPointer());
  } catch (e) { r.giveFn = 'unreadable ' + e.message; }
  for (let off = 0; off <= 0x48; off += 8) {
    const p = fnTable.add(off).readPointer();
    let deref = '';
    try { if (!p.isNull() && !inGame(p) && !inErsc(p)) deref = ' -> ' + rel(p.readPointer()); } catch (e) {}
    r.fnTable.push(hex(off) + ':' + rel(p) + deref);
  }
  try { r.latch = ctx.add(0x28).readPointer().add(8).readU8(); } catch (e) { r.latch = 'unreadable'; }
  return r;
}

function natives () {
  return {
    getInv: new NativeFunction(game.base.add(GET_EQUIP_INVENTORY_DATA_RVA), 'pointer', ['pointer']),
    getIdx: new NativeFunction(game.base.add(GET_ITEM_INVENTORY_IDX_RVA), 'int', ['pointer', 'pointer']),
    getQty: new NativeFunction(game.base.add(GET_QUANTITY_BY_ITEM_ID_RVA), 'int', ['pointer', 'pointer']),
    remove: new NativeFunction(game.base.add(REMOVE_ITEM_RVA), 'int', ['pointer', 'int', 'uint32', 'int']),
    getFlag: new NativeFunction(game.base.add(GET_EVENT_FLAG_RVA), 'int', ['pointer', 'uint32']),
    setFlag: new NativeFunction(game.base.add(SET_EVENT_FLAG_RVA), 'void', ['pointer', 'pointer', 'uint8', 'pointer', 'uint8']),
  };
}

function egdOrNull () {
  const gdm = game.base.add(GAME_DATA_MAN_GLOBAL_RVA).readPointer();
  if (gdm.isNull()) return null;
  const pgd = gdm.add(PLAYER_GAME_DATA_OFFSET).readPointer();
  if (pgd.isNull()) return null;
  return pgd.add(EQUIP_GAME_DATA_OFFSET);
}

function readGoods (n, egd, rows) {
  const inv = n.getInv(egd);
  const cell = Memory.alloc(4);
  const out = {};
  for (const row of rows) {
    cell.writeS32(GOODS + row);
    const idx = n.getIdx(inv, cell);
    cell.writeS32(GOODS + row);
    const qty = n.getQty(inv, cell);
    out[row] = { idx: idx, qty: qty };
  }
  return out;
}

function readFlags (n, ids) {
  const man = game.base.add(EVENT_FLAG_MAN_GLOBAL_RVA).readPointer();
  const out = {};
  for (const id of ids) out[id] = n.getFlag(man, id);
  return out;
}

// Where a flag's bit lives in CSEventFlagMan's block memory (bd great-jar-sign-flags-1171-2026-10-06:
// +0x1c flags per block, +0x20 bytes per block, +0x28 blocks base, +0x38 std::map head whose nodes
// carry +0x19 isNil, +0x20 group, +0x28 mode (1 = index), +0x30 location; bit 7-(n&7)).
function flagLocation (id) {
  const man = game.base.add(EVENT_FLAG_MAN_GLOBAL_RVA).readPointer();
  const perBlock = man.add(0x1c).readU32();
  const blockBytes = man.add(0x20).readU32();
  const blocks = man.add(0x28).readPointer();
  const group = Math.floor(id / perBlock);
  const head = man.add(0x38).readPointer();
  let node = head.add(0x8).readPointer();
  while (node.add(0x19).readU8() === 0) {
    const key = node.add(0x20).readU32();
    if (key === group) {
      const mode = node.add(0x28).readU32();
      const location = node.add(0x30).readU64().toNumber();
      if (mode !== 1) return { id: id, group: group, mode: mode };
      const within = id % perBlock;
      const offset = location * blockBytes + Math.floor(within / 8);
      return { id: id, group: group, mode: 1, location: location, blockOffset: offset, bit: 7 - (within % 8),
        byte: blocks.add(offset).readU8() };
    }
    node = node.add(key > group ? 0x0 : 0x10).readPointer();
  }
  return { id: id, group: group, missing: true };
}

function blockAnchor () {
  const man = game.base.add(EVENT_FLAG_MAN_GLOBAL_RVA).readPointer();
  const blocks = man.add(0x28).readPointer();
  // Several 64-byte windows spread over the block memory, for locating the same bytes in the save.
  const out = [];
  // The low blocks (system and progress groups) are dense; most of the rest is zero.
  for (const off of [0x0, 0x40, 0x340, 0x440, 0x980, 0x2c80, 0x3680]) out.push({ off: off, hex: bytes(blocks.add(off), 64) });
  return { blocks: blocks.toString(), perBlock: man.add(0x1c).readU32(), blockBytes: man.add(0x20).readU32(), windows: out };
}

function work () {
  const n = natives();
  const egd = egdOrNull();
  if (egd === null) { send({ kind: 'refused', line: 'no PlayerGameData' }); return; }
  const report = { phase: PHASE, egd: egd.toString() };
  report.prologues = {
    removeItem: bytes(game.base.add(REMOVE_ITEM_RVA), 12),
    getIdx: bytes(game.base.add(GET_ITEM_INVENTORY_IDX_RVA), 12),
    getQty: bytes(game.base.add(GET_QUANTITY_BY_ITEM_ID_RVA), 12),
    getFlag: bytes(game.base.add(GET_EVENT_FLAG_RVA), 12),
    setFlag: bytes(game.base.add(SET_EVENT_FLAG_RVA), 12),
  };
  try {
    const first = game.base.add(GET_EVENT_FLAG_RVA);
    if (first.readU8() === 0xe9) report.getFlagDetourTo = rel(first.add(5).add(first.add(1).readS32()));
  } catch (e) {}
  report.before = readGoods(n, egd, TARGETS);
  report.alsoRead = readGoods(n, egd, ALSO_READ);
  report.flagsBefore = readFlags(n, FLAGS_TO_READ.concat(FLAGS_TO_CLEAR));
  try {
    report.flagLocations = FLAGS_TO_READ.map(flagLocation);
    report.blockAnchor = blockAnchor();
  } catch (e) { report.flagLocationError = e.message; }

  // Census of the live carried inventory: every entry GetInventoryItemEntryByIndex hands back, as
  // [index, itemId, quantity] (InventoryItemEntry: handle +0x0, itemId +0x4, quantity +0x8).
  try {
    const inv = n.getInv(egd);
    const getEntry = new NativeFunction(game.base.add(GET_INVENTORY_ITEM_ENTRY_BY_INDEX_RVA), 'pointer', ['pointer', 'uint32']);
    const top = inv.add(0x80).readU32();
    const census = [];
    for (let i = 0; i <= top; i++) {
      const e = getEntry(inv, i);
      if (e.isNull()) continue;
      census.push([i, e.add(4).readU32(), e.add(8).readU32()]);
    }
    report.census = { top: top, count: census.length, entries: census };
  } catch (e) { report.censusError = e.message; }
  if (PHASE === 'remove') {
    const removed = [];
    // One at a time, re-resolving the index each time: a removal reindexes the inventory.
    for (const row of TARGETS) {
      const cell = Memory.alloc(4);
      cell.writeS32(GOODS + row);
      const inv = n.getInv(egd);
      const idx = n.getIdx(inv, cell);
      if (idx < 0) { removed.push({ row: row, skipped: 'not held' }); continue; }
      const ok = n.remove(egd, idx, 1, 1);
      removed.push({ row: row, idx: idx, returned: ok });
    }
    report.removed = removed;
    report.after = readGoods(n, egd, TARGETS);
  }
  if (PHASE === 'flags' && FLAGS_TO_CLEAR.length > 0) {
    const man = game.base.add(EVENT_FLAG_MAN_GLOBAL_RVA).readPointer();
    const cell = Memory.alloc(4);
    for (const id of FLAGS_TO_CLEAR) { cell.writeU32(id); n.setFlag(man, cell, 0, NULL, 0); }
    report.flagsAfter = readFlags(n, FLAGS_TO_READ.concat(FLAGS_TO_CLEAR));
  }
  if (PHASE === 'save') {
    // `RequestSave(bool throttled)`, 1.16.2 0x14067a520 carried +0xe50 to 1.17 (unique 43-byte
    // signature), then 1.17.1 0x14067b370 -> 2.7.2.0 0x14067b820, and read in both de-Arxan'd
    // images: with cl=0 it skips the time throttle and, unless the byte at 1.17.1 0x143d89720 /
    // 2.7.2.0 0x143d8c740 is set, writes GameMan+0xb73=1 and (when +0xac0 != -1)
    // +0xb72=1, the saveRequested byte the MoveMapStep tick's ShouldSave polls.
    const gameMan = game.base.add(GAME_MAN_GLOBAL_RVA).readPointer();
    const peekSave = () => ({ b72: gameMan.add(0xb72).readU8(), b73: gameMan.add(0xb73).readU8(),
      b80: gameMan.add(0xb80).readU8(), ac0: gameMan.add(0xac0).readS32(),
      disabled: game.base.add(SAVE_DISABLED_BYTE_RVA).readU8() });
    report.saveBefore = peekSave();
    report.requestSavePrologue = bytes(game.base.add(REQUEST_SAVE_RVA), 12);
    if (report.requestSavePrologue !== '4881ec9800000084c90f8434') {
      report.saveRefused = 'RequestSave prologue mismatch';
    } else {
      new NativeFunction(game.base.add(REQUEST_SAVE_RVA), 'void', ['uint8'])(0);
      report.saveAfter = peekSave();
    }
  }
  send({ kind: 'state', report: report });
}

// The survey only reads code and ersc's own heap, so it runs here, on load.
try {
  const s = survey();
  const msg = { kind: 'survey', ersc: s.ersc, hookCount: s.hooks.length, hooks: s.hooks };
  if (s.grant !== null) {
    msg.grant = s.grant.row;
  }
  if (s.hooks.length > 0) {
    const ctx = ptr(s.hooks[0].ctx);
    msg.ctx = ctxReport(ctx);
    // The installer reads its targets out of `[ctx] + 0x300..0x400`; slot 0x350 is the grant's.
    const table = ctx.readPointer();
    msg.slots = [];
    for (let off = 0x300; off < 0x400; off += 8) {
      const p = table.add(off).readPointer();
      let row = hex(off) + ':' + rel(p);
      try {
        row += ' [' + bytes(p, 8) + ']';
        const d = decodeErscDetour(p);
        if (d !== null) row += ' -> fn ' + rel(d.fn) + ' dispatch ' + rel(d.dispatch);
      } catch (e) { row += ' unreadable'; }
      msg.slots.push(row);
    }
  }
  send(msg);
} catch (e) {
  send({ kind: 'survey-failed', message: e.message, stack: e.stack });
}

const peek = Process.getModuleByName('user32.dll').getExportByName('PeekMessageW');
let done = false;
const listener = Interceptor.attach(peek, {
  onEnter () {
    if (done) return;
    if (Process.getCurrentThreadId() !== mainThread) return;
    done = true;
    try { work(); } catch (e) { send({ kind: 'failed', message: e.message, stack: e.stack }); }
  },
});
// The first thread to pump PeekMessageW is taken as the main thread; filled in by the probe below.
let mainThread = -1;
const probe = Interceptor.attach(peek, {
  onEnter () {
    if (mainThread === -1) mainThread = Process.getCurrentThreadId();
  },
});

let patched = null;
if (BLOCK_REGRANT && ersc !== null) {
  const at = ersc.base.add(GRANT_LOOP_CMP_RVA);
  const was = bytes(at, 4);
  if (was === '4883fe00') {
    patched = at;
    send({ kind: 'regrant-blocked', at: rel(at), before: was, after: was, note: 'already patched' });
  } else if (was === '4883fe07') {
    Memory.patchCode(at.add(3), 1, (code) => { code.writeU8(0x00); });
    patched = at;
    send({ kind: 'regrant-blocked', at: rel(at), before: was, after: bytes(at, 4) });
  } else {
    send({ kind: 'regrant-block-refused', at: rel(at), bytes: was });
  }
}

rpc.exports = {
  dispose () {
    listener.detach(); probe.detach();
    // KEEP_BLOCK_AFTER_DETACH leaves the patched bound in place when the watcher ends, so a grace
    // rest keeps not re-granting for the rest of this game process; ersc.dll is mapped fresh on the
    // next launch.
    if (patched !== null && !KEEP_BLOCK_AFTER_DETACH) Memory.patchCode(patched.add(3), 1, (code) => { code.writeU8(0x07); });
  },
};
send({ kind: 'armed', phase: PHASE });
