// Grass lab: read and change, live, which grass types a map piece scatters and what each type looks
// like.
//
// How Elden Ring places grass (1.16.2 dump :8765 names, offsets byte-checked in
// eldenring-deobf-1.17.1.bin and again in eldenring-deobf-1.17.2.bin for 2.7.2.0):
//
//   Each map piece's mapbnd carries a `.grass` file: a triangle mesh whose vertices hold six
//   densities, one per grass slot (SoulsFormats GRASS: vertex = position + float[6]).
//   The slot -> GrassTypeParam row id table is the MSB part's grass config, six u32:
//     CS::CSMsbParts::GetGrassType(CSMsbParts*, slot)  1.16.2 0x140ced5b0, 1.17.1 0x140ceecf0
//                                                      -> 2.7.2.0 0x140cf03e0
//       CSMsbParts +0x18 MsbPart*, MsbPart +0x80 grass config*, config +slot*4 the row id
//     reached from CSGrassMapPart_Geom (owner +0x100, CSMsbParts at +0x30) and
//     CSGrassMapPart_Map (owner +0x100, MapInsData +0x18 msbPartsMap).
//   The row is GrassTypeParam (solo index 63), _Lv1 (64), _Lv2 (65) by quality.
//
// Commands go in target/grass-lab/cmd.json with a new `seq`:
//   stats   {}                         GetGrassType call count and every map part seen, with slots
//   rows    {ids, table?}              decoded rows (table 63/64/65, default all three)
//   setrow  {id, set:{field:value}, tables?}
//                                      write fields; originals kept for `revert`
//   slots   {part, ids:[6]}            rewrite a part's slot -> row table (originals kept)
//   revert  {}                         put back every row field and slot table this agent changed
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
// Every address below is 2.7.2.0, carried from 1.17.1. Each
// code address is a function byte-identical across the two builds apart from relocated operands
// (GetGrassType's jump table moves with it), and each global has the same readers in both. The
// offsets this file walks read unchanged in byte-identical 2.7.2.0 code: CSMsbParts +0x18, part
// +0x80 and slot*4 in GetGrassType; CSGrassManager +0x4d0, +0x5d0 and +0x7c8 in its constructor
// (0x140b971e0); CSGrassMapPart +0x18 and +0x20 in its constructor (0x140ba06b0) and Init, +0x100
// in the _Geom constructor (0x140ba0850); owner +0x30 (_Geom) and +0x18 then +0x8 (_Map) in the two
// GetGrassType thunks (0x140ba1780, 0x140ba17a0 -> 0x14071aad0); the geometry map's head +0x20,
// key +0x20 and value +0x28 in GetGrassMapPartByFieldInsPtr (0x140b9c9a0); the buffer's vector
// pairs +0x20 to +0x88 in its clear (0x140b88e00); the param repository's +0x80/+0x88 stride 0x48
// in GetParamResCap (0x140d500f0, still 0xc2 params) and +0x80 -> +0x80 -> +0x0a in
// GetParamResCapRowCount (0x140d36070). GrassTypeParam is still index 0x3f: all 216 carried
// GetParamResCap call sites pass the same index in both builds; 64 and 65 (_Lv1, _Lv2) have no
// call site with an immediate index, and rest on 0x3f and 0x42 being unchanged. Not confirmed in
// game code: the grass resource map head at manager +0x4d0 +0x28, a resource's name strings at
// +0x98, +0xe8 and +0x138, and the vertex/face/volume strides, which come from the .grass format.
const MSB_GET_GRASS_TYPE = va('0x140cf03e0'); // 1.17.1 0x140ceecf0
// .data rva, 1.17.1 0x3d85f58 -> 2.7.2.0 0x3d88f78 (933 of 961 readers carry onto it; the rest
// are in code Arxan re-mutates per build).
const SOLO_PARAM_REPOSITORY_GLOBAL_RVA = 0x3d88f78;
const CMD_PATH = 'Z:\\home\\banon\\projects\\er-mods-rs\\target\\grass-lab\\cmd.json';

// GRASS_TYPE_PARAM_ST, from the Smithbox paramdef via scripts/er-paramdef-offsets.py.
const FIELDS = {
  lodRange: [0x0, 'u16'], lod0ClusterType: [0x2, 'u8'], lod1ClusterType: [0x3, 'u8'], lod2ClusterType: [0x4, 'u8'],
  distributionType: [0x7, 'u8'], baseDensity: [0x8, 'f32'],
  model0Name: [0xc, 'w16'], flatTextureName: [0x2c, 'w32'], billboardTextureName: [0x6c, 'w32'],
  normalInfluence: [0xac, 'u8'], inclinationMax: [0xad, 'u8'], inclinationJitter: [0xae, 'u8'],
  scaleBaseMin: [0xaf, 'u8'], scaleBaseMax: [0xb0, 'u8'], scaleHeightMin: [0xb1, 'u8'], scaleHeightMax: [0xb2, 'u8'],
  colorShade1_r: [0xb3, 'u8'], colorShade1_g: [0xb4, 'u8'], colorShade1_b: [0xb5, 'u8'],
  colorShade2_r: [0xb6, 'u8'], colorShade2_g: [0xb7, 'u8'], colorShade2_b: [0xb8, 'u8'],
  flatSplitType: [0xb9, 'u8'], flatBladeCount: [0xba, 'u8'], flatSlant: [0xbb, 's8'], flatRadius: [0xbc, 'f32'],
  castShadow: [0xc0, 'u8'], windAmplitude: [0xc1, 'u8'], windCycle: [0xc3, 'u8'],
  orientationAngle: [0xc4, 'f32'], orientationRange: [0xc8, 'f32'], spacing: [0xcc, 'f32'], dithering: [0xd0, 'u8'],
  simpleModelName: [0xd4, 'w16'], model1Name: [0xf4, 'w16'],
};

function emit(kind, fields) { send(Object.assign({ kind: kind }, fields || {})); }

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

function rowPtr(index, id) {
  const p = paramBlob(index);
  if (p === null) return null;
  const n = p.blob.add(0x0a).readU16();
  for (let i = 0; i < n; i++) {
    const e = p.blob.add(0x40 + i * 24);
    if ((e.readU32() >>> 0) === id) return p.blob.add(e.add(8).readU64().toNumber());
  }
  return null;
}

function readField(row, f) {
  const [o, t] = f;
  const a = row.add(o);
  if (t === 'u8') return a.readU8();
  if (t === 's8') return a.readS8();
  if (t === 'u16') return a.readU16();
  if (t === 'f32') return Math.round(a.readFloat() * 10000) / 10000;
  if (t === 'w16') return a.readUtf16String(16);
  if (t === 'w32') return a.readUtf16String(32);
  return null;
}

function writeField(row, f, v) {
  const [o, t] = f;
  const a = row.add(o);
  Memory.protect(a, 4, 'rw-');
  if (t === 'u8') a.writeU8(v);
  else if (t === 's8') a.writeS8(v);
  else if (t === 'u16') a.writeU16(v);
  else if (t === 'f32') a.writeFloat(v);
  else throw new Error('cannot write ' + t);
}

function decode(row) {
  const out = {};
  for (const k of Object.keys(FIELDS)) out[k] = readField(row, FIELDS[k]);
  return out;
}

// Every map part GetGrassType has been asked about: name -> {msbPart, config}.
const parts = new Map();
let calls = 0;
const callers = new Map();
const hook = Interceptor.attach(MSB_GET_GRASS_TYPE, {
  onEnter (args) {
    calls++;
    try {
      const part = args[0].add(0x18).readPointer();
      if (part.isNull()) return;
      const key = part.toString();
      if (!parts.has(key)) {
        const cfg = part.add(0x80).readPointer();
        parts.set(key, { name: part.readPointer().readUtf16String(64), part: part, cfg: cfg });
      }
      const ret = this.returnAddress.sub(game.base).toString();
      callers.set(ret, (callers.get(ret) || 0) + 1);
    } catch (e) { /* not a part */ }
  },
});

// CSGrassManager, taken from the first Grass_BatchUpdate (1.16.2 0x140b97f40, 1.17.1 0x140b99660
// -> 2.7.2.0 0x140b99b20, whole function byte-identical). +0x7c8 Vector<CSGrassMapPart*> (start
// +0x8, end +0x10).
// CSGrassMapPart: +0x18 grass buffer, +0x20 u32[6] the slot -> row ids cached by
// CSGrassMapPart::Init (1.16.2 0x140b9fc30, 1.17.1 0x140ba1350 -> 2.7.2.0 0x140ba1810) from
// GetGrassType, +0x100 owner. This line used to give Init as 1.17.1 0x140ba12e0, which is the _Map
// part's GetGrassType thunk (2.7.2.0 0x140ba17a0); Init is the one that opens
// `lea r14, [rdi+0x20]` after calling GetGrassResourceManager.
let grassMan = null;
const batchHook = Interceptor.attach(va('0x140b99b20'), {
  onEnter (args) { if (grassMan === null) grassMan = args[0]; },
});

// MSVC std::map: head node, root at head+0x8; node +0 left, +0x10 right, +0x19 is-nil, value +0x20.
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

// The live parts: CSGrassManager +0x5d0 map<WorldBlockInfo*, CSGrassGeomManager*> (head +0x5d8),
// and each CSGrassGeomManager +0x18 map<FieldInsBase*, CSGrassMapPart*> (head +0x20). The vector
// at +0x7c8 is empty in play.
function mapParts() {
  if (grassMan === null) return [];
  // The manager is rebuilt on an area reload, so a stale capture has a null tree head; drop it and
  // let the next Grass_BatchUpdate capture the live one (seen 2026-10-17 after the user warped).
  if (grassMan.add(0x5d8).readPointer().isNull()) { grassMan = null; throw new Error('grass manager was stale; recaptured on the next frame, send again'); }
  const mps = [];
  walkTree(grassMan.add(0x5d8).readPointer(), (blk) => {
    const geomMan = blk.add(0x28).readPointer();
    if (geomMan.isNull()) return;
    walkTree(geomMan.add(0x20).readPointer(), (n) => mps.push(n.add(0x28).readPointer()));
  });
  const out = [];
  for (const mp of mps) {
    if (mp.isNull()) continue;
    const cls = rttiName(mp) || '';
    const owner = mp.add(0x100).readPointer();
    let msbParts = null;
    if (cls.indexOf('_Map') >= 0) msbParts = owner.add(0x20);  // owner +0x18 MapInsData, +0x8 CSMsbParts (1.16.2 0x1407197d0)
    else if (cls.indexOf('_Geom') >= 0) msbParts = owner.add(0x30);
    let name = null;
    let msbIds = null;
    try {
      const part = msbParts.add(0x18).readPointer();
      name = part.readPointer().readUtf16String(64);
      const cfg = part.add(0x80).readPointer();
      if (!cfg.isNull()) msbIds = slotsOf(cfg);
    } catch (e) { /* unnamed */ }
    out.push({ mp: mp, cls: cls, name: name, ids: slotsOf(mp.add(0x20)), msbIds: msbIds });
  }
  return out;
}

const rttiCache = new Map();
function rttiName(obj) {
  try {
    const vtbl = obj.readPointer();
    const key = vtbl.toString();
    if (rttiCache.has(key)) return rttiCache.get(key);
    const col = vtbl.sub(8).readPointer();
    const name = game.base.add(col.add(0xc).readU32()).add(0x10).readCString();
    rttiCache.set(key, name);
    return name;
  } catch (e) { return null; }
}

const savedCache = new Map();  // mp ptr -> {name, ids}
const savedDensity = new Map(); // buffer ptr -> {name, vb, bytes}
const savedRes = new Map();     // 'res:off' -> {at, value}
const savedRows = new Map();   // 'table:id:field' -> original value
const savedSlots = new Map();  // cfg ptr -> {name, ids}

function slotsOf(cfg) { const ids = []; for (let i = 0; i < 6; i++) ids.push(cfg.add(i * 4).readU32() >>> 0); return ids; }

function run(cmd) {
  if (cmd.op === 'stats') {
    const list = [];
    for (const p of parts.values()) list.push({ name: p.name, msbPart: p.part.toString(), cfg: p.cfg.toString(), slots: p.cfg.isNull() ? null : slotsOf(p.cfg) });
    const rets = Array.from(callers.entries()).map(([r, n]) => ({ rva: '0x' + (parseInt(r, 16) + 0x140000000).toString(16), n: n }));
    emit('stats', { calls: calls, parts: list, callers: rets });
  } else if (cmd.op === 'rows') {
    const tables = cmd.table ? [cmd.table] : [63, 64, 65];
    const out = [];
    for (const t of tables) for (const id of cmd.ids) {
      const row = rowPtr(t, id);
      out.push(Object.assign({ table: t, id: id, ptr: row ? row.toString() : null }, row ? (cmd.fields ? Object.fromEntries(cmd.fields.map((k) => [k, readField(row, FIELDS[k])])) : decode(row)) : {}));
    }
    emit('rows', { rows: out });
  } else if (cmd.op === 'list') {
    // Every row id of a table with its model names.
    const p = paramBlob(cmd.table || 63);
    const n = p.blob.add(0x0a).readU16();
    const out = [];
    for (let i = 0; i < n; i++) {
      const e = p.blob.add(0x40 + i * 24);
      const row = p.blob.add(e.add(8).readU64().toNumber());
      out.push([e.readU32() >>> 0, readField(row, FIELDS.model0Name), readField(row, FIELDS.model1Name), readField(row, FIELDS.baseDensity)]);
    }
    emit('list', { table: cmd.table || 63, rows: out });
  } else if (cmd.op === 'setrow') {
    const tables = cmd.tables || [63, 64, 65];
    const ids = cmd.ids || [cmd.id];
    const done = [];
    for (const t of tables) for (const id of ids) {
      const row = rowPtr(t, id);
      if (!row) { done.push({ table: t, id: id, missing: true }); continue; }
      for (const k of Object.keys(cmd.set)) {
        const key = t + ':' + id + ':' + k;
        const before = readField(row, FIELDS[k]);
        if (!savedRows.has(key)) savedRows.set(key, { row: row, field: k, value: before });
        writeField(row, FIELDS[k], cmd.set[k]);
        done.push({ table: t, id: id, field: k, before: before, after: readField(row, FIELDS[k]) });
      }
    }
    emit('setrow', { changed: done });
  } else if (cmd.op === 'slots') {
    const hits = [];
    for (const p of parts.values()) {
      if (p.name !== cmd.part || p.cfg.isNull()) continue;
      const key = p.cfg.toString();
      const before = slotsOf(p.cfg);
      if (!savedSlots.has(key)) savedSlots.set(key, { name: p.name, cfg: p.cfg, ids: before });
      Memory.protect(p.cfg, 0x18, 'rw-');
      cmd.ids.forEach((v, i) => p.cfg.add(i * 4).writeU32(v));
      hits.push({ name: p.name, cfg: key, before: before, after: slotsOf(p.cfg) });
    }
    emit('slots', { part: cmd.part, changed: hits });
  } else if (cmd.op === 'peek') {
    // Discovery: qwords at grassMan (or `at`) + path..., with RTTI of each pointer.
    let base = cmd.at ? ptr(cmd.at) : grassMan;
    for (const o of cmd.path || []) base = base.add(o).readPointer();
    const rows = [];
    for (let o = cmd.from || 0; o < (cmd.to || 0x40); o += 8) {
      let q;
      try { q = base.add(o).readPointer(); } catch (e) { rows.push('0x' + o.toString(16) + ' fault'); break; }
      rows.push('0x' + o.toString(16) + ' ' + q + ' ' + (q.compare(ptr('0x10000')) > 0 && q.compare(ptr('0x7fffffffffff')) < 0 ? (rttiName(q) || '') : ''));
    }
    emit('peek', { base: base.toString(), rows: rows });
  } else if (cmd.op === 'res' || cmd.op === 'resf') {
    // The per-type resource the renderer uses: CSGrassResourceManager (grassMan +0x4d0) +0x28 the
    // head of map<type id, resource*>; the resource's +0x8 holds the values copied out of the
    // GrassTypeParam row when the type was first requested (1.16.2 0x140ba0430), so a param write
    // after that changes nothing until the type is requested again. `resf {id, off, value}`
    // writes one float at resource +off.
    const head = grassMan.add(0x4d0 + 0x28).readPointer();
    let res = null;
    walkTree(head, (n) => { if ((n.add(0x20).readU32() >>> 0) === cmd.id) res = n.add(0x28).readPointer(); });
    if (res === null) { emit('error', { op: cmd.op, error: 'no resource for type ' + cmd.id }); return; }
    if (cmd.op === 'resf') {
      const a = res.add(cmd.off);
      const key = res.toString() + ':' + cmd.off;
      const before = a.readFloat();
      if (!savedRes.has(key)) savedRes.set(key, { at: a, value: before });
      a.writeFloat(cmd.value);
      emit('resf', { id: cmd.id, res: res.toString(), off: cmd.off, before: before, after: a.readFloat() });
      return;
    }
    const rows = [];
    for (let o = cmd.from || 0; o < (cmd.to || 0x100); o += 4) {
      const u = res.add(o).readU32() >>> 0;
      const f = res.add(o).readFloat();
      rows.push('0x' + o.toString(16) + ' ' + ('00000000' + u.toString(16)).slice(-8) + ' ' + (Math.abs(f) > 1e-6 && Math.abs(f) < 1e6 ? Math.round(f * 10000) / 10000 : ''));
    }
    let names = [];
    try { for (const o of [0x98, 0xe8, 0x138]) { const len = res.add(o + 0x10).readU64().toNumber(); names.push(len < 64 ? (res.add(o + 0x18).readU64().toNumber() > 7 ? res.add(o).readPointer() : res.add(o)).readUtf16String(len) : null); } } catch (e) { names = [e.message]; }
    emit('res', { id: cmd.id, res: res.toString(), names: names, rows: rows });
  } else if (cmd.op === 'mapparts') {
    const match = cmd.match ? new RegExp(cmd.match) : null;
    const rows = mapParts().filter((p) => !match || (p.name && match.test(p.name)));
    emit('mapparts', { grassMan: grassMan ? grassMan.toString() : null, n: rows.length, parts: rows.map((p) => ({ mp: p.mp.toString(), cls: p.cls, name: p.name, ids: p.ids, msbIds: p.msbIds })) });
  } else if (cmd.op === 'cache') {
    // Rewrite the slot -> row ids a loaded CSGrassMapPart cached at Init.
    const hits = [];
    for (const p of mapParts()) {
      if (p.name !== cmd.part) continue;
      const key = p.mp.toString();
      if (!savedCache.has(key)) savedCache.set(key, { name: p.name, mp: p.mp, ids: p.ids });
      cmd.ids.forEach((v, i) => p.mp.add(0x20 + i * 4).writeU32(v));
      hits.push({ name: p.name, mp: key, before: p.ids, after: slotsOf(p.mp.add(0x20)) });
    }
    emit('cache', { part: cmd.part, changed: hits });
  } else if (cmd.op === 'fill') {
    // `fill {part, groups: [[vertex index...]], radius}`: give each group of vertices (the ground
    // under a hidden rock, from scripts/er-rock-eruption-scan.py) the mean live density of the
    // vertices around it, those within `radius` m (x/z) of the group and not in it.
    const radius = cmd.radius || 3;
    const out = [];
    for (const p of mapParts()) {
      if (p.name !== cmd.part) continue;
      const buf = p.mp.add(0x18).readPointer();
      const vb = buf.add(0x40).readPointer();
      const nv = buf.add(0x48).readPointer().sub(vb).toInt32() / 0x24;
      const key = buf.toString();
      if (!savedDensity.has(key)) savedDensity.set(key, { name: p.name, vb: vb, bytes: vb.readByteArray(nv * 0x24) });
      Memory.protect(vb, nv * 0x24, 'rw-');
      const xz = [];
      for (let i = 0; i < nv; i++) xz.push([vb.add(i * 0x24).readFloat(), vb.add(i * 0x24 + 8).readFloat()]);
      for (const group of cmd.groups) {
        const inside = new Set(group.filter((i) => i < nv));
        const mean = [0, 0, 0, 0, 0, 0];
        let n = 0;
        for (let i = 0; i < nv; i++) {
          if (inside.has(i)) continue;
          let near = false;
          for (const j of inside) {
            const dx = xz[i][0] - xz[j][0];
            const dz = xz[i][1] - xz[j][1];
            if (dx * dx + dz * dz <= radius * radius) { near = true; break; }
          }
          if (!near) continue;
          for (let k = 0; k < 6; k++) mean[k] += vb.add(i * 0x24 + 12 + k * 4).readFloat();
          n++;
        }
        if (n === 0) { out.push({ size: inside.size, ring: 0 }); continue; }
        for (let k = 0; k < 6; k++) mean[k] /= n;
        for (const j of inside) for (let k = 0; k < 6; k++) vb.add(j * 0x24 + 12 + k * 4).writeFloat(mean[k]);
        out.push({ size: inside.size, ring: n, mean: mean.map((x) => Math.round(x * 1000) / 1000) });
      }
    }
    emit('fill', { part: cmd.part, groups: out });
  } else if (cmd.op === 'buffer' || cmd.op === 'density') {
    // The part's loaded .grass: CSGrassBuffer (mp +0x18) vectors begin/end at +0x20/+0x28 volumes
    // (0x14), +0x40/+0x48 vertices (0x24: position, float[6] slot densities), +0x60/+0x68 faces
    // (0x18), +0x80/+0x88 boxes. `density` rewrites every vertex: new[j] = sum_i mix[j][i] * old[i],
    // optionally limited to block-frame x/z bounds `box: [x0, z0, x1, z1]`.
    const out = [];
    for (const p of mapParts()) {
      if (p.name !== cmd.part) continue;
      const buf = p.mp.add(0x18).readPointer();
      const vb = buf.add(0x40).readPointer();
      const nv = buf.add(0x48).readPointer().sub(vb).toInt32() / 0x24;
      const counts = {
        volumes: buf.add(0x28).readPointer().sub(buf.add(0x20).readPointer()).toInt32() / 0x14,
        vertices: nv,
        faces: buf.add(0x68).readPointer().sub(buf.add(0x60).readPointer()).toInt32() / 0x18,
      };
      const key = buf.toString();
      if (cmd.op === 'density') {
        if (!savedDensity.has(key)) savedDensity.set(key, { name: p.name, vb: vb, bytes: vb.readByteArray(nv * 0x24) });
        Memory.protect(vb, nv * 0x24, 'rw-');
      }
      // `file`: the part's extracted .grass. Its vertex block is the baseline the mix is applied
      // to, so a repeated `density` does not compound and identity mix is a revert. The file must
      // match the loaded buffer (count, and positions checked below).
      let orig = null;
      if (cmd.file) {
        const fb = File.readAllBytes(cmd.file);
        const dv = new DataView(fb);
        const fvol = dv.getInt32(0xc, true);
        const fnv = dv.getInt32(0x14, true);
        if (fnv !== nv) { emit('error', { op: cmd.op, error: 'file has ' + fnv + ' vertices, buffer ' + nv }); return; }
        orig = { dv: dv, off: 0x28 + fvol * 0x14 };
        for (let i = 0; i < nv; i++) {
          const o = orig.off + i * 0x24;
          const v = vb.add(i * 0x24);
          if (Math.abs(orig.dv.getFloat32(o, true) - v.readFloat()) > 1e-3 || Math.abs(orig.dv.getFloat32(o + 8, true) - v.add(8).readFloat()) > 1e-3) {
            emit('error', { op: cmd.op, error: 'vertex ' + i + ' position differs from the file' }); return;
          }
        }
      }
      const sum = [0, 0, 0, 0, 0, 0];
      const nz = [0, 0, 0, 0, 0, 0];
      let touched = 0;
      for (let i = 0; i < nv; i++) {
        const v = vb.add(i * 0x24);
        const d = [];
        const o = orig ? orig.off + i * 0x24 : 0;
        for (let k = 0; k < 6; k++) d.push(orig ? orig.dv.getFloat32(o + 12 + k * 4, true) : v.add(12 + k * 4).readFloat());
        if (cmd.op === 'density') {
          const x = v.readFloat();
          const z = v.add(8).readFloat();
          let inBox = !cmd.box || (x >= cmd.box[0] && x <= cmd.box[2] && z >= cmd.box[1] && z <= cmd.box[3]);
          // `rbox: {origin:[x,z], yaw, centre:[x,z], half:[hx,hz]}`: a box in a part's own frame
          // (geom-lab's convention, dx = x cos + z sin, dz = -x sin + z cos), so a square can
          // follow a building's walls.
          if (cmd.rbox) {
            const c = Math.cos(cmd.rbox.yaw);
            const s = Math.sin(cmd.rbox.yaw);
            const dx = x - cmd.rbox.origin[0];
            const dz = z - cmd.rbox.origin[1];
            const xl = dx * c - dz * s;
            const zl = dx * s + dz * c;
            inBox = Math.abs(xl - cmd.rbox.centre[0]) <= cmd.rbox.half[0] && Math.abs(zl - cmd.rbox.centre[1]) <= cmd.rbox.half[1];
          }
          if (inBox) {
            // `set: [6]` writes those densities outright (null keeps the mixed value).
            const nd = cmd.mix ? cmd.mix.map((row) => row.reduce((acc, m, k) => acc + m * d[k], 0)) : d.slice();
            if (cmd.set) cmd.set.forEach((val, k) => { if (val !== null) nd[k] = val; });
            for (let k = 0; k < 6; k++) { d[k] = cmd.max !== undefined ? Math.min(nd[k], cmd.max) : nd[k]; v.add(12 + k * 4).writeFloat(d[k]); }
            touched++;
          }
        }
        for (let k = 0; k < 6; k++) { sum[k] += d[k]; if (d[k] > 0) nz[k]++; }
      }
      out.push({ name: p.name, buffer: key, counts: counts, ids: p.ids, densitySum: sum.map((s) => Math.round(s * 10) / 10), nonzero: nz, touched: touched });
    }
    emit(cmd.op, { part: cmd.part, buffers: out });
  } else if (cmd.op === 'revert') {
    let n = 0;
    for (const s of savedDensity.values()) {
      try { if (mapParts().some((p) => p.mp.add(0x18).readPointer().add(0x40).readPointer().equals(s.vb))) { s.vb.writeByteArray(s.bytes); n++; } } catch (e) { /* gone */ }
    }
    savedDensity.clear();
    for (const s of savedCache.values()) {
      try { if (mapParts().some((p) => p.mp.equals(s.mp))) { s.ids.forEach((v, i) => s.mp.add(0x20 + i * 4).writeU32(v)); n++; } } catch (e) { /* gone */ }
    }
    savedCache.clear();
    for (const s of savedRows.values()) { writeField(s.row, FIELDS[s.field], s.value); n++; }
    for (const s of savedSlots.values()) { s.ids.forEach((v, i) => s.cfg.add(i * 4).writeU32(v)); n++; }
    savedRows.clear();
    savedSlots.clear();
    emit('revert', { restored: n });
  } else {
    emit('error', { error: 'unknown op ' + cmd.op });
  }
}

// The command file is read on the game's own frame, as geom-lab.js does: CS::ChrIns::PreBehaviorSafe
// runs once per character per frame, so filtering on the local player gives one call a frame, and
// every POLL_FRAMES-th one reads the file. Commands then run on the game thread between frames, the
// same thread that owns the grass parts and param rows they touch. WorldChrMan 0x143d69ff8 ->
// 0x143d6d018 and the hook 0x140401f30 -> 0x140401fc0, as in scripts/frida/arena-extents.js.
const WORLD_CHR_MAN = va('0x143d6d018');
const PRE_BEHAVIOR_SAFE = va('0x140401fc0');
const POLL_FRAMES = 15;
let frame = 0;
let lastSeq = null;
try { lastSeq = JSON.parse(File.readAllText(CMD_PATH)).seq; } catch (e) { /* no file yet */ }
const frameHook = Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    let player;
    try { player = WORLD_CHR_MAN.readPointer().add(0x1e508).readPointer(); } catch (e) { return; }
    if (player.isNull() || !args[0].equals(player) || ++frame % POLL_FRAMES !== 0) return;
    let cmd;
    try { cmd = JSON.parse(File.readAllText(CMD_PATH)); } catch (e) { return; }
    if (cmd.seq === lastSeq) return;
    lastSeq = cmd.seq;
    try { run(cmd); } catch (e) { emit('error', { op: cmd.op, error: e.message, stack: e.stack }); }
  },
});

emit('ready', { cmd: CMD_PATH, hook: MSB_GET_GRASS_TYPE.toString() });
rpc.exports = { dispose () { frameHook.detach(); hook.detach(); batchHook.detach(); } };
