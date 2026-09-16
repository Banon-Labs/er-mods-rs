// Clear `disable_offline` on the vanilla invasion goods, and watch `CanUseGoods` flip.
//
// Measured: `CanUseGoods` (1.16.2 0x14068e010) greys the Use row through one long AND, and the
// term that fails here is `disable_offline != 0 -> IsInOnlineMode()`. Seamless runs its own
// netcode with the game's online flag clear. It rewrites nothing in the param -- the live rows are
// byte-identical to the installed regulation -- and `CanUseBreakInItem` already returns 1.
//
// `IsInOnlineMode` (0x14067a030) is inlined into this caller on 1.17, which is why hooking and
// stubbing that 15-byte function caught nothing and changed nothing. Clearing the bit removes the
// test instead of lying about its input. It is bit 5 of byte 0x48 in `_EQUIP_PARAM_GOODS_ST`,
// below the paramdef drift at 0x4d.
//
// Diagnostic write into the loaded param table. The product shape is the same edit applied through
// the game's own param load, not a correction made behind it.
const CAN_USE_GOODS_RVA = 0x68ee60; // 1.17, unique 43B signature, +0xe50 from 1.16.2
const REPO_RVA = 0x3d85f58; // 1.17 .data, from rva-map-1162-to-1170.data.tsv
const GOODS_INDEX = 3;
const FLAG_OFFSET = 0x48;
const DISABLE_OFFLINE_BIT = 1 << 5;
const GOODS = [102, 111, 112]; // Bloody Finger, Festering Bloody Finger, Recusant Finger

const game = Process.findModuleByName('eldenring.exe');
const rows = new Map();
const seen = new Map();
let patched = false;

// The `GetParamResCap` arithmetic, reproduced rather than called -- see map_piece_live.rs.
const repo = game.base.add(REPO_RVA).readPointer();
const cap = repo.add(0x88 + GOODS_INDEX * 9 * 8).readPointer();
const fd4 = cap.add(0x80).readPointer();
const blob = fd4.add(0x80).readPointer();
const rowCount = blob.add(0x0a).readU16();

for (let i = 0; i < rowCount; i += 1) {
  const entry = blob.add(0x40 + i * 24);
  const id = entry.readU32();
  if (GOODS.includes(id)) {
    rows.set(id, blob.add(entry.add(8).readU64().toNumber()));
  }
}

const flags = (id) => rows.get(id).add(FLAG_OFFSET).readU8();
for (const id of rows.keys()) {
  console.log(`offline-gate: row ${id} byte0x48=0x${flags(id).toString(16)} disable_offline=${(flags(id) >> 5) & 1}`);
}

Interceptor.attach(game.base.add(CAN_USE_GOODS_RVA), {
  onEnter(args) { this.goodsId = args[0].toInt32(); },
  onLeave(retval) {
    if (!GOODS.includes(this.goodsId)) return;
    const answer = retval.toInt32() & 0xff;
    const key = `${patched}:${this.goodsId}=${answer}`;
    if (seen.has(key)) return;
    seen.set(key, true);
    console.log(`offline-gate: CanUseGoods(${this.goodsId}) = ${answer}  (${patched ? 'AFTER clearing the bit' : 'before'})`);
  },
});

// Written at load rather than on a timer: this watcher's Frida runtime never ran the timer
// callback, so a `setTimeout` here silently does nothing at all.
for (const id of rows.keys()) {
  const at = rows.get(id).add(FLAG_OFFSET);
  at.writeU8(at.readU8() & ~DISABLE_OFFLINE_BIT);
  console.log(`offline-gate: row ${id} -> 0x${flags(id).toString(16)} disable_offline=${(flags(id) >> 5) & 1}`);
}
patched = true;

console.log('offline-gate: bit cleared; CanUseGoods returns below are post-patch');
