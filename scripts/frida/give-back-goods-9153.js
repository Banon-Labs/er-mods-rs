// Give goods 9153 ("About Marika's Effigy at the Roundtable") back to the live character, once.
//
// It is vanilla: m11_00 event 11002697 / m11_05 event 11052697 award it with 2003[43]
// DirectlyGivePlayerItem [3, 9153, 710880, 1], and undo-seamless-grace-grant.js removed it by
// mistake. The call is the one that EMEVD command ends in: `AddOrRemoveItem` (1.16.2 0x1405ea980)
// gives a not-yet-held item with `CS::EquipGameData::AddInventoryEquipByItemId(egd, &id, quantity,
// true, true)`. 0x246840 is below the 0xafefe9 boundary, so 1.16.2 == 1.17.1, and
// scripts/map-rvas-1171-to-1172.py leaves it unmoved in 2.7.2.0; its prologue was read in
// eldenring-deobf-1.17.1.bin and eldenring-deobf-1.17.2.bin and is checked again here before the
// call. Of the addresses below only the GameDataMan global moved (1.17.1 0x3d61f98 -> 2.7.2.0
// 0x3d64fb8); the four inventory functions are unmoved and open with the same bytes.
//
// Runs on the game's main thread inside PeekMessageW, with a latch so it happens exactly once per
// load of this file. It refuses if 9153 is already held.
'use strict';

const game = Process.getModuleByName('eldenring.exe');

const GAME_DATA_MAN_GLOBAL_RVA = 0x3d64fb8; // 1.17.1 0x3d61f98
const PLAYER_GAME_DATA_OFFSET = 0x08;
const EQUIP_GAME_DATA_OFFSET = 0x2b0;
const ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA = 0x246840;
const ADD_PROLOGUE = '40574883ec4048c7442438feffffff';
const GET_EQUIP_INVENTORY_DATA_RVA = 0x247b30;
const GET_ITEM_INVENTORY_IDX_RVA = 0x24c560;
const GET_QUANTITY_BY_ITEM_ID_RVA = 0x24c1b0;

const GOODS = 0x40000000;
const GIVE = 9153;
const WATCH = [9153, 8380001, 8380002, 8380003, 8380004, 8380005, 8380006, 8380012];

function bytes (p, n) {
  return Array.from(new Uint8Array(p.readByteArray(n))).map((b) => b.toString(16).padStart(2, '0')).join('');
}

function work () {
  const gdm = game.base.add(GAME_DATA_MAN_GLOBAL_RVA).readPointer();
  const pgd = gdm.isNull() ? NULL : gdm.add(PLAYER_GAME_DATA_OFFSET).readPointer();
  if (pgd.isNull()) { send({ kind: 'refused', line: 'no PlayerGameData' }); return; }
  const egd = pgd.add(EQUIP_GAME_DATA_OFFSET);
  const getInv = new NativeFunction(game.base.add(GET_EQUIP_INVENTORY_DATA_RVA), 'pointer', ['pointer']);
  const getIdx = new NativeFunction(game.base.add(GET_ITEM_INVENTORY_IDX_RVA), 'int', ['pointer', 'pointer']);
  const getQty = new NativeFunction(game.base.add(GET_QUANTITY_BY_ITEM_ID_RVA), 'int', ['pointer', 'pointer']);
  const cell = Memory.alloc(4);
  const read = () => {
    const inv = getInv(egd);
    const out = {};
    for (const row of WATCH) {
      cell.writeS32(GOODS + row);
      const idx = getIdx(inv, cell);
      cell.writeS32(GOODS + row);
      out[row] = { idx: idx, qty: getQty(inv, cell) };
    }
    return out;
  };
  const report = { before: read() };
  const prologue = bytes(game.base.add(ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA), 15);
  report.prologue = prologue;
  if (prologue !== ADD_PROLOGUE) {
    report.refused = 'AddInventoryEquipByItemId prologue mismatch';
  } else if (report.before[GIVE].qty > 0) {
    report.refused = 'already held';
  } else {
    const add = new NativeFunction(game.base.add(ADD_INVENTORY_EQUIP_BY_ITEM_ID_RVA), 'int',
      ['pointer', 'pointer', 'uint32', 'int', 'int']);
    const id = Memory.alloc(4);
    id.writeS32(GOODS + GIVE);
    report.returned = add(egd, id, 1, 1, 1);
  }
  report.after = read();
  send({ kind: 'state', report: report });
}

const peek = Process.getModuleByName('user32.dll').getExportByName('PeekMessageW');
let mainThread = -1;
let done = false;
const listener = Interceptor.attach(peek, {
  onEnter () {
    if (mainThread === -1) { mainThread = Process.getCurrentThreadId(); return; }
    if (done || Process.getCurrentThreadId() !== mainThread) return;
    done = true;
    try { work(); } catch (e) { send({ kind: 'failed', message: e.message, stack: e.stack }); }
  },
});

rpc.exports = { dispose () { listener.detach(); } };
send({ kind: 'armed' });
