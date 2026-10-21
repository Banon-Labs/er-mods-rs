// LOD shadow: the "LOD Restored" GrassLodRangeParam / PartsDrawParam values without changing a
// single param byte. Seamless Co-op only matches players whose params are identical (user,
// 2026-10-17), so the tables stay vanilla and the game is handed a private copy of the rows instead.
//
// Where the rows are consumed (static RE, 1.16.2 :8765 names, 1.17.1 addresses byte-checked in
// eldenring-deobf-1.17.1.bin). The agent runs on 2.7.2.0: each 1.17.1 address below was carried
// with scripts/map-rvas-1171-to-1172.py, and each function is byte-identical in
// eldenring-deobf-1.17.2.bin apart from relocated operands, so the param indices 0x42 and 0x44 and
// both prologues are unchanged. 1.17.1 -> 2.7.2.0: GetPartsDrawParam 0x140d44790 -> 0x140d45e80,
// LookupGrassLodRangeParam 0x140d3c6a0 -> 0x140d3dd90, resource builder 0x140ba1b50 ->
// 0x140ba2010, resource creation 0x140b8f8d0 -> 0x140b8fd90, RendMan 0x143d7f130 -> 0x143d82150
// (its readers still walk +0x10 then +0x730), SoloParamRepository 0x143d85f58 -> 0x143d88f78.
// CSGrassManager +0x4d0 is confirmed by GetGrassResourceManager (2.7.2.0 0x140b9ca50); the map head
// at +0x28 of it and the LOD floats at resource +0x08 have no 2.7.2.0 witness beyond the
// byte-identical builder.
//   GetPartsDrawParam(out, id)          1.17.1 0x140d44790 (1.16.2 0x140d42f40), the only caller of
//                                       GetParamResCap(0x44) in the image. Writes {id, row*} to out;
//                                       callers keep the row pointer (CSModelDispEntity +0x278) and
//                                       read it every frame (fade factor 1.16.2 0x140d428d0, max
//                                       render range 0x140d42ec0). So the hook swaps out+8 for the
//                                       shadow row, and the shadow has to outlive the script.
//   LookupGrassLodRangeParam(out, id)   1.17.1 0x140d3c6a0 (1.16.2 0x140d3aee0), the only caller of
//                                       GetParamResCap(0x42). Its one caller is the grass resource
//                                       builder (1.17.1 0x140ba1b50), which converts the row into
//                                       [r0, r0+p0, r1, r1+p1, r2, r2+p2] at resource +0x08 once,
//                                       when a grass type is first requested.
//   grass resource creation             1.17.1 0x140b8f8d0 (1.16.2 0x140b8e1b0), args[2] = resource.
//   existing grass resources            [eldenring+0x3d82150] (1.17.1 +0x3d7f130) RendMan +0x10
//                                       StageRend +0x730
//                                       CSGrassManager +0x4d0 CSGrassResourceManager +0x28 map head,
//                                       node +0x28 resource; LOD floats at resource +0x08..+0x1f.
//
// The shadow tables live in a VirtualAlloc block at a fixed address, so the memory survives a
// reload or unload of this script and a reattach reuses it. Dispose turns the swap off, rewrites
// the shadow with vanilla bytes (anything still holding a shadow row now reads vanilla values) and
// puts the original floats back in every grass resource this agent changed. Objects built before
// the agent loaded keep the vanilla row until they are rebuilt (an area reload or fast travel).
//
// Commands in target/lod-shadow/cmd.json with a new `seq`, forwarded as `cmd` messages by
// `er-frida-watch.py --command-file target/lod-shadow/cmd.json`:
//   stats                    hook counters, table hashes now vs at load
//   probe {index, id}        call the game's own getter for the row; report real vs returned
//   grass                    every grass resource's LOD floats
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const GET_PARTS_DRAW_PARAM = va('0x140d45e80'); // 1.17.1 0x140d44790
const LOOKUP_GRASS_LOD_RANGE = va('0x140d3dd90'); // 1.17.1 0x140d3c6a0
const GRASS_RESOURCE_CREATE = va('0x140b8fd90'); // 1.17.1 0x140b8f8d0
const RENDMAN_GLOBAL_RVA = 0x3d82150; // 1.17.1 0x3d7f130
const SOLO_PARAM_REPOSITORY_GLOBAL_RVA = 0x3d88f78; // 1.17.1 0x3d85f58
const PLAN_PATH = 'Z:\\home\\banon\\projects\\er-mods-rs\\target\\lod-shadow\\plan.json';
// The two getters share their generated prologue; a different build refuses rather than hooks.
const GETTER_PROLOGUE = '40574883ec4048c7442420feffffff48';
const CREATE_PROLOGUE = '40534881ec5003000048c7442430feff';

const BLOCK_AT = ptr('0x310000000');
const BLOCK_SIZE = 0x100000;
const BLOCK_MAGIC = 0x4c4f4453;
// +0x00 magic, +0x40 hook state for param 68 (Range below), shadows from +0x1000.
const RANGE68 = 0x40;

function emit(kind, fields) { send(Object.assign({ kind: kind }, fields || {})); }
const hex = (p, n) => Array.from(new Uint8Array(p.readByteArray(n)), (b) => ('0' + b.toString(16)).slice(-2)).join('');

for (const [name, at, want] of [['GetPartsDrawParam', GET_PARTS_DRAW_PARAM, GETTER_PROLOGUE],
  ['LookupGrassLodRangeParam', LOOKUP_GRASS_LOD_RANGE, GETTER_PROLOGUE],
  ['grass resource create', GRASS_RESOURCE_CREATE, CREATE_PROLOGUE]]) {
  const have = hex(at, want.length / 2);
  if (have !== want) throw new Error(name + ' prologue ' + have + ' != ' + want + ': not 2.7.2.0, refusing to hook');
}

const k32 = Process.getModuleByName('kernel32.dll');
const VirtualAlloc = new NativeFunction(k32.getExportByName('VirtualAlloc'), 'pointer', ['pointer', 'uint64', 'uint32', 'uint32']);
let block = VirtualAlloc(BLOCK_AT, uint64(BLOCK_SIZE), 0x1000 | 0x2000, 0x04);
// Already reserved by an earlier load of this agent: commit it again and reuse it.
if (block.isNull()) block = VirtualAlloc(BLOCK_AT, uint64(BLOCK_SIZE), 0x1000, 0x04);
if (block.isNull()) throw new Error('lod-shadow: could not map the shadow block at ' + BLOCK_AT);
const reused = block.readU32() === BLOCK_MAGIC;
block.writeU32(BLOCK_MAGIC);

function paramBlob(index) {
  const repo = game.base.add(SOLO_PARAM_REPOSITORY_GLOBAL_RVA).readPointer();
  if (repo.isNull()) return null;
  if (repo.add(0x80 + index * 72).readS32() <= 0) return null;
  const cap = repo.add(0x88 + index * 9 * 8).readPointer();
  if (cap.isNull()) return null;
  const fd4 = cap.add(0x80).readPointer();
  if (fd4.isNull()) return null;
  return { blob: fd4.add(0x80).readPointer(), size: fd4.add(0x78).readU64().toNumber() };
}

function rowsOf(blob) {
  const n = blob.add(0x0a).readU16();
  const out = new Map();
  for (let i = 0; i < n; i++) {
    const e = blob.add(0x40 + i * 24);
    out.set(e.readU32() >>> 0, e.add(8).readU64().toNumber());
  }
  return out;
}

const sha = (p, n) => Checksum.compute('sha256', p.readByteArray(n));

// Build one shadow table: a byte copy of the whole param file with the plan applied to its rows.
const plan = JSON.parse(File.readAllText(PLAN_PATH));
const tables = new Map();
let cursor = 0x1000;
for (const p of plan.params) {
  const b = paramBlob(p.index);
  if (b === null) throw new Error('param ' + p.index + ' not loaded');
  const shadow = block.add(cursor);
  cursor += (b.size + 0xfff) & ~0xfff;
  if (cursor > BLOCK_SIZE) throw new Error('shadow block too small for param ' + p.index);
  const vanillaHash = sha(b.blob, b.size);
  Memory.copy(shadow, b.blob, b.size);
  const rows = rowsOf(b.blob);
  const missing = [];
  let values = 0;
  let changed = 0;
  for (const id of Object.keys(p.rows)) {
    const off = rows.get(Number(id));
    if (off === undefined) { missing.push(Number(id)); continue; }
    for (const [fo, v] of p.rows[id]) {
      const a = shadow.add(off + fo);
      if (Math.abs(a.readFloat() - v) > 1e-4) changed++;
      a.writeFloat(v);
      values++;
    }
  }
  const unplanned = Array.from(rows.keys()).filter((id) => !(String(id) in p.rows));
  tables.set(p.index, { blob: b.blob, size: b.size, shadow: shadow, rows: rows, rowSize: p.rowSize,
    vanillaHash: vanillaHash, values: values, changed: changed, missing: missing, unplanned: unplanned });
}
const t66 = tables.get(66);
const t68 = tables.get(68);

// PartsDraw is looked up whenever a drawable is built, so the swap runs in C.
const range68 = block.add(RANGE68);
range68.writePointer(t68.blob);
range68.add(0x08).writePointer(t68.blob.add(t68.size));
range68.add(0x10).writePointer(t68.shadow);
range68.add(0x18).writeU32(1);
range68.add(0x20).writeU64(0);
range68.add(0x28).writeU64(0);
const cm = new CModule(`
#include <gum/guminterceptor.h>
#include <stdint.h>

/* +0x00 lo, +0x08 hi, +0x10 shadow, +0x18 enabled, +0x20 calls, +0x28 swaps */
void on_enter (GumInvocationContext * ic) {
  void ** slot = GUM_IC_GET_INVOCATION_DATA (ic, void *);
  *slot = gum_invocation_context_get_nth_argument (ic, 0);
}

void on_leave (GumInvocationContext * ic) {
  uint8_t * r = GUM_IC_GET_FUNC_DATA (ic, uint8_t *);
  uint8_t * out = *GUM_IC_GET_INVOCATION_DATA (ic, uint8_t *);
  *(uint64_t *) (r + 0x20) += 1;
  if (*(uint32_t *) (r + 0x18) == 0 || out == 0) return;
  uint64_t p = *(uint64_t *) (out + 8);
  uint64_t lo = *(uint64_t *) r, hi = *(uint64_t *) (r + 8);
  if (p >= lo && p < hi) {
    *(uint64_t *) (out + 8) = *(uint64_t *) (r + 0x10) + (p - lo);
    *(uint64_t *) (r + 0x28) += 1;
  }
}
`);
const partsHook = Interceptor.attach(GET_PARTS_DRAW_PARAM, { onEnter: cm.on_enter, onLeave: cm.on_leave }, range68);

// Grass: the lookup and the resource build are once per grass type, so plain JS is enough, and
// recording each resource built under the swap lets dispose put its vanilla floats back.
const derived = (base, off) => {
  const f = (o) => base.add(off + o).readFloat();
  return [f(0), f(0) + f(4), f(8), f(8) + f(12), f(16), f(16) + f(20)].map(Math.fround);
};
const same = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 1e-3);
const readLod = (res) => [0, 4, 8, 12, 16, 20].map((o) => res.add(8 + o).readFloat());
const writeLod = (res, v) => v.forEach((x, i) => res.add(8 + i * 4).writeFloat(x));

let grassEnabled = true;
let grassLookups = 0;
let grassSwaps = 0;
const lastLodId = new Map();
// resource -> { vanilla floats, written floats, how }
const grassTouched = new Map();
// Whether the last table check found both tables as loaded; a change is reported once.
let tablesReported = true;

const grassHook = Interceptor.attach(LOOKUP_GRASS_LOD_RANGE, {
  onEnter (args) { this.out = args[0]; },
  onLeave () {
    grassLookups++;
    const id = this.out.readU32() >>> 0;
    lastLodId.set(Process.getCurrentThreadId(), id);
    if (!grassEnabled) return;
    const p = this.out.add(8).readPointer();
    if (p.compare(t66.blob) >= 0 && p.compare(t66.blob.add(t66.size)) < 0) {
      this.out.add(8).writePointer(t66.shadow.add(p.sub(t66.blob)));
      grassSwaps++;
    }
  },
});
const createHook = Interceptor.attach(GRASS_RESOURCE_CREATE, {
  onEnter (args) { this.res = args[2]; lastLodId.delete(Process.getCurrentThreadId()); },
  onLeave (ret) {
    if ((ret.toUInt32() & 0xff) === 0) return;
    const id = lastLodId.get(Process.getCurrentThreadId());
    const off = id === undefined ? undefined : t66.rows.get(id);
    if (off !== undefined) {
      const now = readLod(this.res);
      grassTouched.set(this.res.toString(), { res: this.res, vanilla: derived(t66.blob, off), written: now, how: 'built', lodId: id });
      emit('grass-built', { res: this.res.toString(), lodId: id, lod: now });
    }
    afterGrassBuild();
  },
});

// A grass type whose build was already under way while the hooks went in finishes without the
// lookup hook having seen it -- that is the `id === undefined` case above -- so every completed
// build, the event that the resource map changed, sweeps the map for vanilla resources. The same
// event re-checks the param tables, since a table that moved or changed would leave the shadow
// describing rows the game no longer reads.
function afterGrassBuild() {
  const late = patchExistingGrass();
  if (late.patched > 0) emit('grass-late', late);
  const s = tableState();
  const identical = s[66].identical && s[68].identical;
  if (!identical && tablesReported) emit('table-changed', stats());
  tablesReported = identical;
}

function walkTree(head, visit) {
  const stack = [];
  let node = head.add(0x8).readPointer();
  let n = 0;
  while ((stack.length > 0 || node.add(0x19).readU8() === 0) && n < 20000) {
    if (node.add(0x19).readU8() === 0) { stack.push(node); node = node.readPointer(); } else {
      node = stack.pop(); visit(node); n++; node = node.add(0x10).readPointer();
    }
  }
}

function grassResources() {
  const rend = game.base.add(RENDMAN_GLOBAL_RVA).readPointer();
  if (rend.isNull()) return [];
  const stage = rend.add(0x10).readPointer();
  if (stage.isNull()) return [];
  const man = stage.add(0x730).readPointer();
  if (man.isNull()) return [];
  const out = [];
  walkTree(man.add(0x4d0 + 0x28).readPointer(), (n) => out.push({ type: n.add(0x20).readU32() >>> 0, res: n.add(0x28).readPointer() }));
  return out;
}

// Resources built before this agent loaded hold vanilla floats. Identify each one's row by its
// floats; it is changed only when every row with those floats maps to the same new floats.
function patchExistingGrass() {
  const byVanilla = [];
  for (const [id, off] of t66.rows) byVanilla.push({ id: id, vanilla: derived(t66.blob, off), shadow: derived(t66.shadow, off) });
  let patched = 0;
  const unmatched = [];
  for (const { type, res } of grassResources()) {
    if (grassTouched.has(res.toString())) continue;
    const now = readLod(res);
    const hits = byVanilla.filter((r) => same(r.vanilla, now));
    if (hits.length === 0) { if (!byVanilla.some((r) => same(r.shadow, now))) unmatched.push({ type: type, lod: now }); continue; }
    if (!hits.every((h) => same(h.shadow, hits[0].shadow))) { unmatched.push({ type: type, lod: now, ambiguous: hits.map((h) => h.id) }); continue; }
    writeLod(res, hits[0].shadow);
    grassTouched.set(res.toString(), { res: res, vanilla: now, written: hits[0].shadow, how: 'patched', type: type });
    patched++;
  }
  return { patched: patched, unmatched: unmatched };
}

function tableState() {
  const out = {};
  for (const [index, t] of tables) {
    const b = paramBlob(index);
    out[index] = { vanillaHash: t.vanillaHash, nowHash: b ? sha(b.blob, b.size) : null,
      blobMoved: b ? !b.blob.equals(t.blob) : true, size: t.size };
    out[index].identical = out[index].nowHash === t.vanillaHash && !out[index].blobMoved;
  }
  return out;
}

function stats() {
  return {
    tables: tableState(),
    parts: { calls: range68.add(0x20).readU64().toNumber(), swaps: range68.add(0x28).readU64().toNumber() },
    grass: { lookups: grassLookups, swaps: grassSwaps, touched: grassTouched.size },
  };
}

const lookupResult = Memory.alloc(0x20);
const getParts = new NativeFunction(GET_PARTS_DRAW_PARAM, 'void', ['pointer', 'uint32']);
const getGrass = new NativeFunction(LOOKUP_GRASS_LOD_RANGE, 'pointer', ['pointer', 'uint32']);

function run(cmd) {
  if (cmd.op === 'stats') {
    emit('stats', stats());
  } else if (cmd.op === 'probe') {
    const t = tables.get(cmd.index);
    const off = t.rows.get(cmd.id);
    if (off === undefined) { emit('error', { op: 'probe', error: 'no row ' + cmd.id }); return; }
    lookupResult.writeByteArray(new Array(0x20).fill(0));
    (cmd.index === 68 ? getParts : getGrass)(lookupResult, cmd.id);
    const got = lookupResult.add(8).readPointer();
    const n = Math.min(t.rowSize, 0x48) / 4;
    const floats = (p) => Array.from({ length: n }, (_, i) => Math.round(p.add(i * 4).readFloat() * 1000) / 1000);
    emit('probe', { index: cmd.index, id: cmd.id, realRow: t.blob.add(off).toString(), returned: got.toString(),
      returnedIsShadow: got.equals(t.shadow.add(off)), real: floats(t.blob.add(off)), seen: floats(got) });
  } else if (cmd.op === 'grass') {
    emit('grass', { resources: grassResources().map(({ type, res }) => ({ type: type, res: res.toString(), lod: readLod(res),
      touched: grassTouched.has(res.toString()) ? grassTouched.get(res.toString()).how : null })) });
  } else {
    emit('error', { op: cmd.op, error: 'unknown op' });
  }
}

const existing = patchExistingGrass();
emit('ready', {
  block: block.toString(), reused: reused,
  hooks: { parts: GET_PARTS_DRAW_PARAM.toString(), grass: LOOKUP_GRASS_LOD_RANGE.toString(), create: GRASS_RESOURCE_CREATE.toString() },
  plan: Array.from(tables, ([index, t]) => ({ index: index, rows: t.rows.size, values: t.values, changedFromVanilla: t.changed,
    missing: t.missing, unplanned: t.unplanned.length, shadow: t.shadow.toString() })),
  existingGrass: existing,
  tables: tableState(),
});

// Commands arrive as `cmd` messages posted by er-frida-watch.py when the command file gets a new
// seq. recv() takes one message, so the handler re-arms itself first.
let listening = true;
function listen() {
  recv('cmd', (message) => {
    if (!listening) return;
    listen();
    const cmd = message.payload;
    try { run(cmd); } catch (e) { emit('error', { op: cmd.op, error: e.message, stack: e.stack }); }
  });
}
listen();

rpc.exports = {
  dispose () {
    listening = false;
    range68.add(0x18).writeU32(0);
    grassEnabled = false;
    partsHook.detach();
    grassHook.detach();
    createHook.detach();
    Interceptor.flush();
    // Anything still holding a shadow row reads vanilla from here on.
    for (const t of tables.values()) Memory.copy(t.shadow, t.blob, t.size);
    let restored = 0;
    for (const g of grassTouched.values()) {
      try {
        if (same(readLod(g.res), g.written)) { writeLod(g.res, g.vanilla); restored++; }
      } catch (e) { /* resource gone */ }
    }
    send({ kind: 'disposed', grassRestored: restored, grassTouched: grassTouched.size });
  },
};
