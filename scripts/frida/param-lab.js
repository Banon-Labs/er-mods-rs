// Param lab: read and write any solo param row in memory, so a regulation tweak is a runtime edit
// instead of a regulation.bin edit. Nothing on disk changes; restarting the game restores vanilla.
//
// The row lookup is the same arithmetic grass-lab.js and goods-row-live.js use:
//   ParamResCap*    repo + 0x88 + (index * 9) * 8, FD4ParamResCap at +0x80, blob at +0x80
//   row table       blob + 0x40, 24 bytes per row: u32 id, u64 data offset at +8; count u16 at +0x0a
// Param indices are the ones in fromsoftware-rs `crates/eldenring/src/cs/solo_param_repository.rs`
// (GrassLodRangeParam 66, PartsDrawParam 68, LoadBalancerDrawDistScaleParam 93,
// LoadBalancerNewDrawDistScaleParam_win64 100, SpeedtreeParam 123).
//
// Commands go in target/param-lab/cmd.json with a new `seq`; the watcher must be started with
// `--command-file target/param-lab/cmd.json`, which posts each new one here as a `cmd` message:
//   dump   {index, size, ids?}           every row (or the listed ids) as hex, `size` bytes each
//   write  {index, edits:[{id, off, type, value}]}
//                                        type u8 s8 u16 s16 u32 s32 f32; originals kept for revert
//   revert {index?}                      put back every byte this agent changed (one param or all)
// scripts/er-param-csv-apply.py drives this from a Smithbox CSV and the paramdef.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
// 2.7.2.0 .data rva, carried from 1.17.1 0x3d85f58 (933 of
// its 961 readers carry onto it; the rest are in code Arxan re-mutates per build). The repository
// layout is unchanged in eldenring-deobf-1.17.2.bin: GetParamResCap (0x140d500f0) still bounds the
// index at 0xc2 params and reads +0x80 + index * 0x48 and +0x88, GetParamResCapRowCount
// (0x140d36070) still walks +0x80 -> +0x80 -> +0x0a, and all 216 carried GetParamResCap call sites
// pass the same index in both builds (0x3f, 0x42 and 0x44 among them, and indices up to 0xc0).
// 93, 100 and 123 have no call site with an immediate index to compare, so those three rest on the
// unchanged count and the unchanged indices around them.
const SOLO_PARAM_REPOSITORY_GLOBAL_RVA = 0x3d88f78;
// Host-side path the watcher forwards from; reported in `ready` so a reader knows where to write.
const CMD_FILE = 'target/param-lab/cmd.json';
const SIZES = { u8: 1, s8: 1, u16: 2, s16: 2, u32: 4, s32: 4, f32: 4 };

function emit(kind, fields) { send(Object.assign({ kind: kind }, fields || {})); }

function paramBlob(index) {
  const repo = game.base.add(SOLO_PARAM_REPOSITORY_GLOBAL_RVA).readPointer();
  if (repo.isNull()) return null;
  if (repo.add(0x80 + index * 72).readS32() <= 0) return null;
  const cap = repo.add(0x88 + index * 9 * 8).readPointer();
  if (cap.isNull()) return null;
  const fd4 = cap.add(0x80).readPointer();
  if (fd4.isNull()) return null;
  return fd4.add(0x80).readPointer();
}

function rows(index) {
  const blob = paramBlob(index);
  if (blob === null) throw new Error('param ' + index + ' not loaded');
  const n = blob.add(0x0a).readU16();
  const out = [];
  for (let i = 0; i < n; i++) {
    const e = blob.add(0x40 + i * 24);
    out.push({ id: e.readU32() >>> 0, ptr: blob.add(e.add(8).readU64().toNumber()) });
  }
  return out;
}

function hexOf(p, size) {
  const bytes = new Uint8Array(p.readByteArray(size));
  return Array.from(bytes, (b) => ('0' + b.toString(16)).slice(-2)).join('');
}

function writeValue(p, type, v) {
  if (type === 'u8') p.writeU8(v);
  else if (type === 's8') p.writeS8(v);
  else if (type === 'u16') p.writeU16(v);
  else if (type === 's16') p.writeS16(v);
  else if (type === 'u32') p.writeU32(v);
  else if (type === 's32') p.writeS32(v);
  else if (type === 'f32') p.writeFloat(v);
  else throw new Error('cannot write ' + type);
}

// "index:id:off" -> original bytes (hex) and the address, first write wins.
const originals = new Map();

function run(cmd) {
  if (cmd.op === 'dump') {
    const want = cmd.ids ? new Set(cmd.ids) : null;
    const out = rows(cmd.index).filter((r) => !want || want.has(r.id)).map((r) => ({ id: r.id, hex: hexOf(r.ptr, cmd.size) }));
    emit('dump', { index: cmd.index, size: cmd.size, rows: out });
  } else if (cmd.op === 'write') {
    const byId = new Map(rows(cmd.index).map((r) => [r.id, r.ptr]));
    const done = [];
    const missing = [];
    for (const e of cmd.edits) {
      const row = byId.get(e.id);
      if (!row) { missing.push(e.id); continue; }
      const a = row.add(e.off);
      const size = SIZES[e.type];
      const key = cmd.index + ':' + e.id + ':' + e.off;
      if (!originals.has(key)) originals.set(key, { addr: a, hex: hexOf(a, size), index: cmd.index });
      Memory.protect(a, size, 'rw-');
      writeValue(a, e.type, e.value);
      done.push(key);
    }
    emit('write', { index: cmd.index, written: done.length, missing: missing });
  } else if (cmd.op === 'revert') {
    let n = 0;
    for (const [key, o] of originals) {
      if (cmd.index !== undefined && o.index !== cmd.index) continue;
      const bytes = o.hex.match(/../g).map((h) => parseInt(h, 16));
      Memory.protect(o.addr, bytes.length, 'rw-');
      o.addr.writeByteArray(bytes);
      originals.delete(key);
      n++;
    }
    emit('revert', { reverted: n });
  } else {
    emit('error', { message: 'unknown op ' + cmd.op });
  }
}

// Each command arrives as a `cmd` message posted by er-frida-watch.py when the command file gets
// a new seq. recv() takes one message, so the handler re-arms itself first.
let listening = true;
function listen() {
  recv('cmd', (message) => {
    if (!listening) return;
    listen();
    const cmd = message.payload;
    try { run(cmd); } catch (e) { emit('error', { seq: cmd.seq, message: String(e) }); }
  });
}
listen();

emit('ready', { cmd: CMD_FILE });

rpc.exports = { dispose () { listening = false; } };
