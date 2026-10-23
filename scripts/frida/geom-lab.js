// Map-asset lab: list, hide, spawn and swap placed assets (CSWorldGeomIns) around the player.
//
// Prototype for a building-restoration slider. Every operation runs on the game thread, inside the
// player's own behavior update (the hook player-where.js uses), and is driven from the host by a
// command file the agent polls:
//
//   target/geom-lab/cmd.json   {"seq": 1, "op": "survey", "radius": 60}
//
// The agent acts once per new `seq`. Operations:
//
//   survey  {radius, match?}          assets in the player's block within radius metres
//   toggle  {match, enable, draw?, collision?}
//                                     set draw and/or collision on every part whose name matches
//   spawn   {asset, dx?, dy?, dz?, rot?, scale?}
//                                     spawn a model next to the player
//   swap    {from, to}                hide part `from` (draw and collision) and spawn model `to`
//                                     at its MSB transform: one slider step
//   restore {}                        re-enable everything this agent hid, hide what it spawned
//   model   {names?, ptrs?, match?, all?}
//                                     the FLVER each part draws, its materials (FLVER name, with
//                                     the matbin in parentheses when it differs), LOD load state
//                                     and `ready`; `all: true` adds shader and per-mesh counts.
//                                     See modelOf for the pointer path.
//   peek    {name, path, from, to}    discovery: qwords of GeomIns->path..., RTTI and UTF-16 noted
//   sweep   {match, mat?, keep?, stop?, block?, notBlock?, enable?}
//                                     hide (or show) matching parts in every loaded block; `mat`
//                                     also requires a matbin of the drawn FLVER to match
//   state   {match}                   enable bit and update flags of matching parts, all blocks
//   treescan {match?}                 parts whose FLVER has SpeedTree/WorldTree materials
//   matdump {file}                    every drawn FLVER's matbins and per-block on/off counts
//
// Distant forests (measured 2026-10-17, bd er-distant-lod-trees-mechanism-1171-2026-10-17): each
// m60 _00 tile has one forest impostor, AEG091_<n>, placed in the _02 LOD tile's MSB as
// "m60_XX_YY_00-AEG091_<n>_7000" (shader M[AMSN][XZ_BillBoard]_Edge, matbin AET997_600 = the
// AET802_000 tree billboard atlas, plus AET005_201). It draws while its _00 tile is not loaded, so
// those trees vanish as the player approaches. It is an ordinary GeomIns, so `sweep` hides it.
//
// The game calls, all 1.17.1 VAs, each read out of the 1.16.2 dump and byte-checked against
// eldenring-deobf-1.17.1.bin (rva below 0xafefe9, so 1.17.0 == 1.17.1):
//
//   0x1406c9760  set enable(GeomIns*, bool). 1.16.2 0x1406c8910, which EMEVD 2005[3]
//                ChangeAssetEnableState reaches through SetDrawEnable (0x1405f4f30) and the
//                CSEventObjInsProxy lambda (0x1405f7da0 -> 0x1405e59b0). Its state is bit 1 of
//                GeomIns+0x263.
//   0x1405e6770  set collision(CSEventObjInsProxy*, bool). 1.16.2 0x1405e5920, reached from
//                0x1405f4800 through lambda 0x1405f7de0. The proxy is {static GeomIns*, dynamic
//                GeomIns*}, dynamic taking precedence.
//   0x1401db5f0  GeometrySpawnRequest init(req*, 5), and 0x1406a5ed0 SpawnGeometry(blockData*,
//                req*) -> GeomIns*, as fromsoftware-rs world_geom_man.rs calls them.
//
// Layouts from fromsoftware-rs world_geom_man.rs, every one of them checked by the survey's own
// output rather than trusted: block data +0x288 the geom vector (first +0x290, last +0x298);
// GeomIns +0x8 the field-ins handle, +0x48 the MsbPart, whose +0x0 is the part name. The MsbPart
// position offset is a guess the survey reports raw so it can be confirmed against the MSB.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
// 2.7.2.0 addresses. The 1.17.1 ones named above were carried by the 1.17.1 -> 2.7.2.0 segment
// table, except set enable, which no carried segment covered: its
// first 20 bytes occur once in eldenring-deobf-1.17.2.bin, at 0x1406c9c10. The other three
// functions' first 16 bytes match across both images.
const WORLD_CHR_MAN = va('0x143d6d018');
const WORLD_GEOM_MAN = va('0x143d70c38');
const PRE_BEHAVIOR_SAFE = va('0x140401fc0');

const setEnable = new NativeFunction(va('0x1406c9c10'), 'void', ['pointer', 'uint32']);
const setCollision = new NativeFunction(va('0x1405e6c20'), 'void', ['pointer', 'uint32']);
const initSpawnRequest = new NativeFunction(va('0x1401db5f0'), 'void', ['pointer', 'uint32']);
const spawnGeometry = new NativeFunction(va('0x1406a6380'), 'pointer', ['pointer', 'pointer']);

const CMD_PATH = 'Z:\\home\\banon\\projects\\er-mods-rs\\target\\geom-lab\\cmd.json';
const POLL_FRAMES = 15;
// MSB part entry: position, rotation, scale at +0x20, +0x2c, +0x38 in the file layout. Confirmed
// or refuted by the survey (posGuessDist against the player).
const MSB_POS = 0x20;

function emit(kind, fields) { send(Object.assign({ kind: kind }, fields || {})); }

function walkMap(head, visit) {
  let n = 0;
  const stack = [];
  let node = head.add(0x8).readPointer();
  while ((stack.length > 0 || node.add(0x19).readU8() === 0) && n < 20000) {
    if (node.add(0x19).readU8() === 0) {
      stack.push(node);
      node = node.readPointer();
    } else {
      node = stack.pop();
      visit(node);
      n += 1;
      node = node.add(0x10).readPointer();
    }
  }
  return n;
}

const rttiCache = new Map();
function rttiName(obj) {
  try {
    const vtbl = obj.readPointer();
    const key = vtbl.toString();
    if (rttiCache.has(key)) return rttiCache.get(key);
    const col = vtbl.sub(8).readPointer();
    const td = game.base.add(col.add(0xc).readU32());
    const name = td.add(0x10).readCString();
    rttiCache.set(key, name);
    return name;
  } catch (e) { return null; }
}

function vec3(p) { return [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()]; }
function round(v) { return v.map((x) => Math.round(x * 100) / 100); }
function hex(n) { return '0x' + (n >>> 0).toString(16); }

function playerState(player) {
  const ph = player.add(0x190).readPointer().add(0x68).readPointer();
  return { block: player.add(0x30).readU32() >>> 0, pos: vec3(ph.add(0x70)) };
}

function blockData(blockId) {
  let found = null;
  walkMap(WORLD_GEOM_MAN.readPointer().add(0x20).readPointer(), (node) => {
    if ((node.add(0x20).readU32() >>> 0) === blockId) found = node.add(0x28).readPointer();
  });
  return found;
}

function partName(geom) {
  try {
    const part = geom.add(0x48).readPointer();
    if (part.isNull()) return null;
    return part.readPointer().readUtf16String(64);
  } catch (e) { return null; }
}

function describe(geom, origin) {
  const name = partName(geom);
  const part = geom.add(0x48).readPointer();
  const d = {
    ptr: geom.toString(),
    name: name,
    cls: rttiName(geom),
    handle: hex(geom.add(0xc).readU32()) + '_' + hex(geom.add(0x8).readU32()),
    enabled: ((geom.add(0x263).readU8() >> 1) & 1) === 1,
  };
  if (!part.isNull()) {
    const pos = vec3(part.add(MSB_POS));
    d.msbPos = round(pos);
    d.msbRot = round(vec3(part.add(MSB_POS + 0xc)));
    d.msbScale = round(vec3(part.add(MSB_POS + 0x18)));
    if (origin) d.dist = Math.round(Math.hypot(pos[0] - origin[0], pos[1] - origin[1], pos[2] - origin[2]) * 10) / 10;
  }
  return d;
}

function geomsOf(data) {
  const out = [];
  const first = data.add(0x290).readPointer();
  const last = data.add(0x298).readPointer();
  const n = last.sub(first).toInt32() / 8;
  for (let i = 0; i < n && i < 20000; i++) {
    const g = first.add(i * 8).readPointer();
    if (!g.isNull()) out.push(g);
  }
  return out;
}

// DLString<wchar_t>: allocator +0x0, inline buffer or heap pointer +0x8, length +0x18, capacity
// +0x20 (in characters); the buffer is inline while the capacity is under 8.
function dlwstr(p) {
  try {
    const len = p.add(0x18).readU64().toNumber();
    const cap = p.add(0x20).readU64().toNumber();
    if (len > 512 || cap < len) return null;
    return (cap >= 8 ? p.add(0x8).readPointer() : p.add(0x8)).readUtf16String(len);
  } catch (e) { return null; }
}

// The model a placed asset draws, and the materials of that model. Pointer path, 1.17.1, read
// from the 1.16.2 dump's types (CSWorldGeomIns, CSWorldGeomResProxy, CSModelIns, CSFD4ModelItem,
// CSFD4FlverModelData, GXModelData, GXModelMaterialSet, GeombndResCap, FD4ResCapHolderItem) and
// proven live 2026-10-17 on AEG004_501_1200 and AEG004_504_1200, every hop checked by RTTI:
//
//   GeomIns +0x1d0  CSGeomModelIns*        (worldGeomResProxy at +0x1a8, its +0x28)
//     +0x10         CSFD4ModelItem*
//     +0x68         CSFD4FlverModelData*   the FLVER actually drawn
//       +0x608      DLString resName       "AEG004_501" (GXModelData +0x4c8 holds the same name)
//       +0x318      GXModelMaterialSet; +0x328/+0x330 begin/end of GXFlverMaterial* (one per mesh)
//         GXFlverMaterial +0x38 DLString   FLVER material name, "AEG004_501_ID01"
//         GXFlverMaterial +0xa8            GXMaterialData*: +0x18 DLString name (the matbin's),
//                                          +0x48 DLString shader, "M[AMSN_V][Mb2][Ov_AN]"
//   GeomIns +0x210  GeombndResCap*         +0x10 DLString "AEG004_501" (the geombnd), and from
//     +0x98         FlverResCap* per LOD   ("AEG004_501", "AEG004_501_1"), each FD4ResCap with
//                                          +0x10 DLString name and +0x78 its CSFD4FlverModelData*
//                                          (null until the FLVER has loaded). The modelItem's
//                                          FLVER equals LOD 0's +0x78, which ties the two paths.
//
// `ready` is the oracle's loaded test: the model instance, its FLVER and at least one material
// are all present, and the geombnd's LOD-0 FlverResCap points at that same FLVER.
//
// The matbin name is the FLVER material's matxml basename, checked against the file: the
// AEG004_423 FLVER (er-extract .../aeg004_423-geombnd-dcx/AEG004_423.flver) has a material named
// AEG004_501_ID01 whose matxml is AEG004_423_ID01.matxml, and the live spawn of AEG004_423 reports
// exactly that pair, "AEG004_501_ID01 (AEG004_423_ID01)".
function modelOf(geom, all) {
  const out = {};
  const okPtr = (p) => !p.isNull() && p.compare(ptr('0x10000')) >= 0 && p.compare(ptr('0x7fffffffffff')) <= 0;
  let fmd = null;
  try {
    const mi = geom.add(0x1d0).readPointer();
    if (okPtr(mi)) {
      const item = mi.add(0x10).readPointer();
      if (okPtr(item)) fmd = item.add(0x68).readPointer();
    }
  } catch (e) { out.error = e.message; }
  if (fmd !== null && !okPtr(fmd)) fmd = null;
  out.flver = fmd ? dlwstr(fmd.add(0x608)) : null;
  const mats = [];
  if (fmd) {
    const first = fmd.add(0x328).readPointer();
    const n = Math.min(fmd.add(0x330).readPointer().sub(first).toInt32() / 8, 256);
    for (let i = 0; i < n; i++) {
      const m = first.add(i * 8).readPointer();
      if (!okPtr(m)) continue;
      const data = m.add(0xa8).readPointer();
      mats.push({ name: dlwstr(m.add(0x38)), matbin: okPtr(data) ? dlwstr(data.add(0x18)) : null, shader: okPtr(data) ? dlwstr(data.add(0x48)) : null });
    }
  }
  // One row per distinct material (a FLVER repeats a material across meshes).
  const seen = new Map();
  for (const m of mats) {
    const key = m.name + '|' + m.matbin + '|' + m.shader;
    if (seen.has(key)) seen.get(key).meshes++; else seen.set(key, Object.assign({ meshes: 1 }, m));
  }
  out.meshes = mats.length;
  out.materials = all ? Array.from(seen.values()) : Array.from(seen.values()).map((m) => (m.matbin && m.matbin !== m.name ? m.name + ' (' + m.matbin + ')' : m.name));
  try {
    const bnd = geom.add(0x210).readPointer();
    if (okPtr(bnd)) {
      out.geombnd = dlwstr(bnd.add(0x10));
      out.lods = [];
      for (let o = 0x98; o < 0xb8; o += 8) {
        const res = bnd.add(o).readPointer();
        if (!okPtr(res) || (rttiName(res) || '').indexOf('FlverResCap') < 0) break;
        const data = res.add(0x78).readPointer();
        out.lods.push({ flver: dlwstr(res.add(0x10)), loaded: okPtr(data), drawn: fmd !== null && data.equals(fmd) });
      }
    }
  } catch (e) { out.bndError = e.message; }
  out.ready = fmd !== null && mats.length > 0 && !!out.lods && out.lods.length > 0 && out.lods[0].drawn;
  return out;
}

// What this agent changed, so `restore` can put it back.
const hidden = new Map();     // ptr -> {draw, collision}
const spawned = [];
const kept = [];              // `place` commands to replay after a block reload
const pending = [];           // `keep` places waiting for their anchor's block to load
let keptAnchor = null;        // the anchor GeomIns they were placed against, as a string

// `sweep` hides every part matching a pattern in every loaded block, not only the player's, and
// a kept sweep runs again every replay tick, so blocks that stream in later (walking, warping,
// the view widening) are caught too. Kept sweeps are saved beside the command file so a hot
// reload of this agent does not drop them.
const SWEEPS_PATH = CMD_PATH.replace(/cmd\.json$/, 'sweeps.json');
// `--config-json '{"noSweeps": true}'` leaves the saved sweeps alone: none are loaded or replayed,
// and a `sweep` command does not rewrite the file, so work in another part of the map does not
// inherit a restoration experiment's hidden parts.
const NO_SWEEPS = !!((globalThis.__ER_FRIDA_CONFIG || {}).noSweeps);
let sweeps = [];
if (!NO_SWEEPS) {
  try { sweeps = JSON.parse(File.readAllText(SWEEPS_PATH)); } catch (e) { /* none yet */ }
}

function allBlocks() {
  const out = [];
  walkMap(WORLD_GEOM_MAN.readPointer().add(0x20).readPointer(), (node) => {
    const data = node.add(0x28).readPointer();
    if (!data.isNull()) out.push({ id: node.add(0x20).readU32() >>> 0, data: data });
  });
  return out;
}

function sweepOnce(cmd) {
  const re = new RegExp(cmd.match);
  const perBlock = {};
  let hiddenNow = 0;
  // `block` limits it to one block id (the same church kit is placed in neighbouring tiles too),
  // `notBlock` skips one, `enable: true` shows the matches instead of hiding them.
  const want = !!cmd.enable;
  // `mat` additionally requires a material of the drawn FLVER (its matbin name, else its FLVER
  // material name) to match, so a name pattern cannot reach a model it was not meant for: the
  // distant-forest sweep names AEG091 and also requires AET997_600, the matbin carrying the AET802
  // tree billboard atlas.
  const matRe = cmd.mat ? new RegExp(cmd.mat) : null;
  for (const b of allBlocks()) {
    if (cmd.block !== undefined && b.id !== (parseInt(cmd.block) >>> 0)) continue;
    if (cmd.notBlock !== undefined && b.id === (parseInt(cmd.notBlock) >>> 0)) continue;
    let geoms;
    try { geoms = geomsOf(b.data); } catch (e) { continue; }
    for (const g of geoms) {
      let on;
      try { on = ((g.add(0x263).readU8() >> 1) & 1) === 1; } catch (e) { continue; }
      if (on === want) continue;
      const name = partName(g);
      if (!name || !re.test(name)) continue;
      if (matRe) {
        const m = modelOf(g, true);
        if (!(m.materials || []).some((x) => matRe.test(x.matbin || x.name || ''))) continue;
      }
      apply(g, want, true, true);
      hiddenNow++;
      perBlock[hex(b.id)] = (perBlock[hex(b.id)] || 0) + 1;
    }
  }
  return { hiddenNow: hiddenNow, perBlock: perBlock };
}

// A block whose geom vector is unchanged since the last replay has nothing new to hide, so the
// replay only rescans blocks that streamed in or were rebuilt. Rescanning every block every tick
// read every GeomIns in the world under the player's update and dropped a frame each time
// (measured 2026-10-21 with 11 kept sweeps and ~3,400 hidden parts).
const sweptBlocks = new Map();   // block id -> "first:last:sweep count" when last swept
function runSweeps() {
  if (sweeps.length === 0) return;
  const changed = [];
  for (const b of allBlocks()) {
    let key;
    try { key = b.data.add(0x290).readPointer() + ':' + b.data.add(0x298).readPointer() + ':' + sweeps.length; } catch (e) { continue; }
    if (sweptBlocks.get(b.id) === key) continue;
    sweptBlocks.set(b.id, key);
    changed.push(b.id);
  }
  for (const id of changed) {
    for (const cmd of sweeps) {
      if (cmd.block !== undefined && (parseInt(cmd.block) >>> 0) !== id) continue;
      const r = sweepOnce(Object.assign({}, cmd, { block: id }));
      if (r.hiddenNow > 0) emit('sweep', { match: cmd.match, kept: true, hiddenNow: r.hiddenNow, perBlock: r.perBlock });
    }
  }
}

// A reload rebuilds the block's instances, so the anchor part comes back at a new address.
function replayAfterReload(player) {
  // A `keep` place whose anchor block is not loaded yet waits here until it is.
  if (pending.length > 0) {
    const data = blockData(playerState(player).block);
    const names = data === null ? [] : geomsOf(data).map(partName);
    for (const cmd of pending.slice()) {
      if (names.indexOf(cmd.at) < 0) continue;
      pending.splice(pending.indexOf(cmd), 1);
      run(cmd, player);
    }
  }
  if (kept.length === 0) return;
  const me = playerState(player);
  const data = blockData(me.block);
  if (data === null) return;
  // A kept `toggle` has no anchor of its own; the first part its pattern matches stands in, so
  // the replay waits for the block that holds those parts (a toggle kept alone never replayed
  // before this, and the church came back after the user warped away and back, 2026-10-17).
  const geoms = geomsOf(data);
  const k0 = kept[0];
  const anchor = k0.at || k0.from
    ? geoms.find((g) => partName(g) === (k0.at || k0.from))
    : geoms.find((g) => { const n = partName(g); return n && new RegExp(k0.match).test(n); });
  const anchorName = anchor ? partName(anchor) : (k0.at || k0.from || k0.match);
  if (!anchor || anchor.toString() === keptAnchor) return;
  emit('reload', { anchor: anchorName, was: keptAnchor, now: anchor.toString(), replaying: kept.length });
  keptAnchor = anchor.toString();
  for (const cmd of kept.slice()) run(cmd, player);
}

function proxyFor(geom) {
  const proxy = Memory.alloc(16);
  const dynamic = (rttiName(geom) || '').indexOf('Dynamic') >= 0;
  proxy.writePointer(dynamic ? NULL : geom);
  proxy.add(8).writePointer(dynamic ? geom : NULL);
  return proxy;
}

function apply(geom, enable, draw, collision) {
  if (draw) setEnable(geom, enable ? 1 : 0);
  if (collision) setCollision(proxyFor(geom), enable ? 1 : 0);
}

// `sfx`, when a number, is the placement's AssetSfxParamRelativeID (request +0xa4, -1 after init):
// the game then attaches AssetModelSfxParam row modelId*1000 + sfx itself, which is how a placed
// brazier gets its fire and light (bd asset-model-sfx-lights-spawned-geom-2720-2026-10-21).
function spawnAt(data, asset, pos, rot, scale, sfx) {
  const req = Memory.alloc(0x100);
  initSpawnRequest(req, 5);
  for (let i = 0; i < 0x20; i++) req.add(i * 2).writeU16(i < asset.length ? asset.charCodeAt(i) : 0);
  const at = [pos[0], pos[1], pos[2], rot[0], rot[1], rot[2], scale[0], scale[1], scale[2]];
  at.forEach((v, i) => req.add(0x70 + i * 4).writeFloat(v));
  if (typeof sfx === 'number') req.add(0xa4).writeS32(sfx);
  const geom = spawnGeometry(data, req);
  if (!geom.isNull()) spawned.push(geom);
  return geom;
}

function run(cmd, player) {
  const me = playerState(player);
  const data = blockData(me.block);
  if (data === null) { emit('error', { op: cmd.op, error: 'no geom block data for ' + hex(me.block) }); return; }
  const match = cmd.match ? new RegExp(cmd.match) : null;
  const geoms = geomsOf(data);

  if (cmd.op === 'census') {
    // Per loaded block: part count and the most common models, to see what a block holds.
    const out = [];
    for (const b of allBlocks()) {
      let geoms;
      try { geoms = geomsOf(b.data); } catch (e) { out.push({ block: hex(b.id), error: e.message }); continue; }
      const count = {};
      for (const g of geoms) {
        const name = partName(g);
        const model = name ? name.replace(/_\d+$/, '') : '?';
        count[model] = (count[model] || 0) + 1;
      }
      const top = Object.keys(count).sort((a, c) => count[c] - count[a]).slice(0, cmd.top || 8).map((k) => k + ':' + count[k]);
      out.push({ block: hex(b.id), parts: geoms.length, top: top });
    }
    emit('census', { blocks: out });
    return;
  }
  if (cmd.op === 'sweep') {
    // {match, keep, stop}: hide matching parts in every loaded block; `keep` repeats it as blocks
    // load, `stop: true` forgets a kept sweep with this pattern (what it hid stays hidden until a
    // reload of that block).
    if (cmd.stop) {
      sweeps = sweeps.filter((s) => s.match !== cmd.match);
    } else {
      const r = sweepOnce(cmd);
      emit('sweep', { match: cmd.match, kept: !!cmd.keep, hiddenNow: r.hiddenNow, perBlock: r.perBlock, blocks: allBlocks().map((b) => hex(b.id)) });
      if (cmd.keep && !sweeps.some((s) => s.match === cmd.match)) {
        const kept = { match: cmd.match };
        if (cmd.block !== undefined) kept.block = cmd.block;
        if (cmd.mat !== undefined) kept.mat = cmd.mat;
        sweeps.push(kept);
      }
    }
    // Padded to a fixed length: writeAllText here does not truncate a longer old file (measured
    // 2026-10-17, a shorter list left the old tail behind and the file stopped parsing).
    if (!NO_SWEEPS) try { File.writeAllText(SWEEPS_PATH, JSON.stringify(sweeps).padEnd(65536)); } catch (e) { emit('error', { op: 'sweep', error: 'save: ' + e.message }); }
    return;
  }
  if (cmd.op === 'state') {
    // {match}: every matching part in every loaded block with its enable bit (GeomIns+0x263 bit 1)
    // and the update flags setEnable raises at +0x268.
    const rows = [];
    for (const b of allBlocks()) {
      let gs;
      try { gs = geomsOf(b.data); } catch (e) { continue; }
      for (const g of gs) {
        const name = partName(g);
        if (!name || !match.test(name)) continue;
        rows.push({ block: hex(b.id), name: name, ptr: g.toString(), enabled: ((g.add(0x263).readU8() >> 1) & 1) === 1, f268: hex(g.add(0x268).readU32()), flver: modelOf(g, false).flver });
      }
    }
    emit('state', { n: rows.length, enabled: rows.filter((r) => r.enabled).length, rows: rows.slice(0, cmd.top || 80) });
    return;
  }
  if (cmd.op === 'matdump') {
    // Every distinct drawn FLVER in every loaded block with its matbin names and enabled/disabled
    // instance counts per block, written to `file` (a fresh path: writeAllText does not truncate),
    // so the host can join matbins to their textures offline.
    const byFmd = new Map();
    for (const b of allBlocks()) {
      let gs;
      try { gs = geomsOf(b.data); } catch (e) { continue; }
      for (const g of gs) {
        let fmd;
        try { fmd = g.add(0x1d0).readPointer().add(0x10).readPointer().add(0x68).readPointer(); } catch (e) { continue; }
        if (fmd.isNull()) continue;
        const key = fmd.toString();
        let row = byFmd.get(key);
        if (!row) {
          const m = modelOf(g, true);
          row = { flver: m.flver, mats: Array.from(new Set((m.materials || []).map((x) => x.matbin || x.name))), shaders: Array.from(new Set((m.materials || []).map((x) => x.shader))), blocks: {} };
          byFmd.set(key, row);
        }
        const on = ((g.add(0x263).readU8() >> 1) & 1) === 1;
        const k = hex(b.id);
        const c = row.blocks[k] || (row.blocks[k] = [0, 0]);
        c[on ? 0 : 1]++;
      }
    }
    const rows = Array.from(byFmd.values());
    File.writeAllText(cmd.file, JSON.stringify(rows));
    emit('matdump', { file: cmd.file, flvers: rows.length });
    return;
  }
  if (cmd.op === 'treescan') {
    // Every part in every loaded block whose drawn FLVER carries a tree material, by the
    // materials themselves rather than by model name: per model, how many instances are enabled,
    // which blocks hold them, the nearest one's horizontal distance (block grid 256 m, player
    // taken in its own block's frame) and the tree materials it has. `match` narrows by name.
    const classes = new Map();   // fmd ptr -> {tree, world, mats}
    const models = {};
    const pb = me.block;
    const tileXZ = (id) => [(id >>> 16) & 0xff, (id >>> 8) & 0xff];
    const [px, pz] = tileXZ(pb);
    let scanned = 0;
    for (const b of allBlocks()) {
      let gs;
      try { gs = geomsOf(b.data); } catch (e) { continue; }
      const [bx, bz] = tileXZ(b.id);
      const lod = b.id & 0xff;
      for (const g of gs) {
        scanned++;
        const name = partName(g);
        if (!name || (match && !match.test(name))) continue;
        let fmd;
        try { fmd = g.add(0x1d0).readPointer().add(0x10).readPointer().add(0x68).readPointer(); } catch (e) { continue; }
        if (fmd.isNull()) continue;
        const key = fmd.toString();
        if (!classes.has(key)) {
          const m = modelOf(g, true);
          const treeMats = (m.materials || []).filter((x) => /^SpeedTree/i.test(x.name || '') || /^SpeedTree/i.test(x.matbin || '') || /SpeedTree|WorldTree/i.test(x.shader || ''));
          classes.set(key, { flver: m.flver, tree: treeMats.length > 0, mats: treeMats.slice(0, 4).map((x) => (x.matbin || x.name) + ' ' + x.shader) });
        }
        const c = classes.get(key);
        if (!c.tree) continue;
        const model = c.flver || name;
        const row = models[model] || (models[model] = { on: 0, off: 0, blocks: {}, near: null, mats: c.mats });
        const on = ((g.add(0x263).readU8() >> 1) & 1) === 1;
        if (on) row.on++; else row.off++;
        row.blocks[hex(b.id)] = (row.blocks[hex(b.id)] || 0) + 1;
        if (lod === 0 && (pb & 0xff) === 0) {
          const p = vec3(g.add(0x48).readPointer().add(MSB_POS));
          const d = Math.hypot((bx - px) * 256 + p[0] - me.pos[0], (bz - pz) * 256 + p[2] - me.pos[2]);
          if (on && (row.near === null || d < row.near)) row.near = Math.round(d);
        }
      }
    }
    const list = Object.keys(models).map((k) => Object.assign({ model: k }, models[k])).sort((a, c) => c.on - a.on || c.off - a.off);
    emit('treescan', { scanned: scanned, flvers: classes.size, treeModels: list.length, enabledTrees: list.reduce((s, r) => s + r.on, 0), disabledTrees: list.reduce((s, r) => s + r.off, 0), models: list.slice(0, cmd.top || 120) });
    return;
  }
  if (cmd.op === 'batch') {
    // Several commands in one seq, each run as if sent alone (and kept alone, with `keep`).
    for (const sub of cmd.cmds) run(sub, player);
    return;
  }
  if (cmd.op === 'polygon') {
    // An arena wall ring: `verts` [[x, z], ...] in metres around the player, in the block's own
    // frame, are the inner corners; each edge gets one AEG099_002 invisible wall scaled to the
    // edge's length, `thick` metres thick (default 0.6) and pushed outward so its inner face sits
    // on the edge. `diamond: [a, b]` is shorthand for corners at +-a/2 on x and +-b/2 on z.
    // Positions come from PlayerIns+0x6c0 block_position (the engine's own block-local copy, bd
    // er-local-warp-no-reload-1171-2026-10-17), not the physics position, whose origin floats.
    // `centreOf: <regex>` with `block: <id>` centres it on the parts that match instead: x and z
    // at their mean MSB position, y at the lowest one (the footing), in that block's frame.
    let bp;
    let target = data;
    if (cmd.centreOf) {
      target = blockData(parseInt(cmd.block) >>> 0);
      if (target === null) { emit('error', { op: 'polygon', error: 'block ' + cmd.block + ' not loaded' }); return; }
      const re = new RegExp(cmd.centreOf);
      const ps = geomsOf(target).filter((g) => re.test(partName(g) || '')).map((g) => vec3(g.add(0x48).readPointer().add(MSB_POS)));
      if (ps.length === 0) { emit('error', { op: 'polygon', error: 'no part matches ' + cmd.centreOf }); return; }
      bp = [ps.reduce((s, p) => s + p[0], 0) / ps.length, Math.min.apply(null, ps.map((p) => p[1])), ps.reduce((s, p) => s + p[2], 0) / ps.length];
      emit('centre', { parts: ps.length, centre: round(bp), ySpan: round([Math.min.apply(null, ps.map((p) => p[1])), Math.max.apply(null, ps.map((p) => p[1]))]) });
    } else {
      bp = vec3(player.add(0x6c0));
      const bpBlock = player.add(0x6d0).readU32() >>> 0;
      if (bpBlock !== me.block) { emit('error', { op: 'polygon', error: 'block_position block ' + hex(bpBlock) + ' != player block ' + hex(me.block) }); return; }
    }
    const verts = cmd.diamond
      ? [[cmd.diamond[0] / 2, 0], [0, cmd.diamond[1] / 2], [-cmd.diamond[0] / 2, 0], [0, -cmd.diamond[1] / 2]]
      : cmd.verts;
    const W = 14.925;
    const thick = cmd.thick || 0.6;
    const sink = cmd.sink === undefined ? 1 : cmd.sink;
    const walls = [];
    for (let i = 0; i < verts.length; i++) {
      const a = verts[i];
      const b = verts[(i + 1) % verts.length];
      const ex = b[0] - a[0];
      const ez = b[1] - a[1];
      const len = Math.hypot(ex, ez);
      // Outward normal: away from the polygon's centroid (the player).
      let nx = ez / len;
      let nz = -ex / len;
      const mx = (a[0] + b[0]) / 2;
      const mz = (a[1] + b[1]) / 2;
      if (nx * mx + nz * mz < 0) { nx = -nx; nz = -nz; }
      // The wall's model x axis lands on world (cos yaw, -sin yaw), the convention `place` uses.
      const yaw = Math.atan2(-ez, ex);
      const pos = [bp[0] + mx + nx * thick / 2, bp[1] - sink, bp[2] + mz + nz * thick / 2];
      const scale = [(len + thick) / W, cmd.height ? cmd.height / 8.333 : 1, thick / 0.13];
      const geom = cmd.visualOnly ? NULL : spawnAt(target, 'AEG099_002', pos, [0, yaw, 0], scale);
      const wall = { len: Math.round(len * 100) / 100, at: round(pos), yaw: Math.round(yaw * 1000) / 1000, ok: cmd.visualOnly ? null : !geom.isNull(), ptr: geom.toString() };
      // `visual: <model>` with `visualBox: {size: [w, h, t], min: [x, y, z]}` (its FLVER bounds,
      // scripts/er-asset-slab-census.py) draws that model over the collider: scaled so its box is
      // the wall's box, and moved so its box's bottom centre lands where the collider's is.
      // Collision is turned off on it, so the invisible wall stays the only thing you hit.
      if (cmd.visual) {
        const vb = cmd.visualBox;
        const hgt = cmd.height || 8.333;
        const vs = [(len + thick) / vb.size[0], hgt / vb.size[1], thick / vb.size[2]];
        const c = [(vb.min[0] + vb.size[0] / 2) * vs[0], vb.min[1] * vs[1], (vb.min[2] + vb.size[2] / 2) * vs[2]];
        const cy = Math.cos(yaw);
        const sy = Math.sin(yaw);
        const vpos = [pos[0] - (c[0] * cy + c[2] * sy), pos[1] - c[1], pos[2] - (-c[0] * sy + c[2] * cy)];
        const v = spawnAt(target, cmd.visual, vpos, [0, yaw, 0], vs);
        if (!v.isNull()) setCollision(proxyFor(v), 0);
        wall.visual = { ok: !v.isNull(), at: round(vpos), scale: round(vs), ptr: v.toString() };
      }
      walls.push(wall);
    }
    emit('polygon', { block: cmd.centreOf ? cmd.block : hex(me.block), centre: round(bp), physics: round(me.pos), walls: walls });
    return;
  }
  if (cmd.op === 'spawnlist') {
    // {block, items: [{asset, pos, rot, scale, collision?, tag?}]}: spawn each item at a transform
    // already worked out on the host (scripts/er-arena-build.py), in that block's own frame.
    // `collision: false` turns the item's collision off, for a model drawn over an invisible
    // AEG099_002 collider. `sfx` is the item's AssetSfxParamRelativeID (see spawnAt). Every item's
    // pointer and instance name are reported for teardown.
    const target = blockData(parseInt(cmd.block) >>> 0);
    if (target === null) { emit('error', { op: 'spawnlist', error: 'block ' + cmd.block + ' not loaded' }); return; }
    const out = [];
    if (cmd.items.some((it) => typeof it.sfx === 'number')) sfxWatchUntil = Date.now() + 20000;
    for (const it of cmd.items) {
      const g = spawnAt(target, it.asset, it.pos, it.rot, it.scale, it.sfx);
      if (!g.isNull() && it.collision === false) setCollision(proxyFor(g), 0);
      out.push({ tag: it.tag, ok: !g.isNull(), ptr: g.toString(), name: g.isNull() ? null : partName(g) });
    }
    emit('spawnlist', { block: cmd.block, spawned: out.filter((r) => r.ok).length, items: out });
    return;
  }
  if (cmd.op === 'collision') {
    // {ptrs: [...], enable}: collision only, on spawned instances named by pointer (a polygon's
    // `walls[].ptr`), each checked to still be the asset it was before anything is called on it.
    const out = [];
    for (const s of cmd.ptrs) {
      const g = ptr(s);
      let name = null;
      try { name = partName(g); } catch (e) { /* freed */ }
      if (cmd.name && name !== cmd.name) { out.push({ ptr: s, skipped: 'name is ' + name }); continue; }
      setCollision(proxyFor(g), cmd.enable ? 1 : 0);
      out.push({ ptr: s, name: name, collision: !!cmd.enable });
    }
    emit('collision', { changed: out });
    return;
  }
  if (cmd.op === 'boxes') {
    // Collision as boxes: `file` holds [{centre, size}] in `at`'s model frame (from
    // scripts/blender/collision-boxes.py). Each is AEG099_002, the invisible wall, scaled: its
    // Havok box is x -7.466..7.459, y 0.01..8.343, z +-0.065 and scales with the spawn's scale
    // (measured 2026-10-17 by standing on a 0.1-wide copy and falling past its edge).
    const W = [14.925, 8.333, 0.13];
    const OFF = [-0.0034, 4.1764, 0.0];
    const boxes = JSON.parse(File.readAllText(cmd.file));
    for (const b of boxes) {
      const k = [b.size[0] / W[0], b.size[1] / W[1], b.size[2] / W[2]];
      const local = [b.centre[0] - OFF[0] * k[0], b.centre[1] - OFF[1] * k[1], b.centre[2]];
      run({ op: 'place', asset: 'AEG099_002', at: cmd.at, local: local, scale: k, keep: cmd.keep, quiet: true }, player);
    }
    emit('boxes', { file: cmd.file, placed: boxes.length });
    return;
  }
  if (cmd.op === 'survey') {
    const radius = cmd.radius || 60;
    const rows = [];
    for (const g of geoms) {
      const d = describe(g, me.pos);
      if (match && !(d.name && match.test(d.name))) continue;
      if (!match && (d.dist === undefined || d.dist > radius)) continue;
      rows.push(d);
    }
    rows.sort((a, b) => (a.dist || 0) - (b.dist || 0));
    emit('survey', { block: hex(me.block), player: round(me.pos), total: geoms.length, shown: rows.length, rows: rows.slice(0, 80) });
  } else if (cmd.op === 'toggle') {
    const draw = cmd.draw !== false;
    const collision = cmd.collision !== false;
    const changed = [];
    for (const g of geoms) {
      const name = partName(g);
      if (!name || !match || !match.test(name)) continue;
      const before = describe(g).enabled;
      apply(g, !!cmd.enable, draw, collision);
      if (!cmd.enable) hidden.set(g.toString(), { geom: g, draw: draw, collision: collision });
      changed.push({ name: name, before: before, after: describe(g).enabled });
    }
    emit('toggle', { enable: !!cmd.enable, draw: draw, collision: collision, changed: changed });
    // `keep: true` applies again after every reload of the block, like a kept `place`.
    if (cmd.keep && changed.length > 0 && !kept.some((k) => JSON.stringify(k) === JSON.stringify(cmd))) {
      kept.push(cmd);
      if (kept[0] === cmd) keptAnchor = geoms.find((g) => partName(g) === changed[0].name).toString();
    }
  } else if (cmd.op === 'spawn') {
    const pos = [me.pos[0] + (cmd.dx || 0), me.pos[1] + (cmd.dy || 0), me.pos[2] + (cmd.dz || 3)];
    const geom = spawnAt(data, cmd.asset, pos, [0, cmd.rot || 0, 0], [cmd.scale || 1, cmd.scale || 1, cmd.scale || 1]);
    emit('spawn', { asset: cmd.asset, at: round(pos), result: geom.isNull() ? null : describe(geom, me.pos) });
  } else if (cmd.op === 'swap') {
    const from = geoms.find((g) => partName(g) === cmd.from);
    if (!from) { emit('error', { op: 'swap', error: 'no part named ' + cmd.from + ' in ' + hex(me.block) }); return; }
    const part = from.add(0x48).readPointer();
    const pos = vec3(part.add(MSB_POS));
    const rot = vec3(part.add(MSB_POS + 0xc));
    const scale = vec3(part.add(MSB_POS + 0x18));
    apply(from, false, true, true);
    hidden.set(from.toString(), { geom: from, draw: true, collision: true });
    const geom = spawnAt(data, cmd.to, pos, rot, scale);
    emit('swap', { from: describe(from, me.pos), to: geom.isNull() ? null : describe(geom, me.pos) });
    // `keep: true` swaps again after every reload of the block, like a kept `place`.
    if (cmd.keep && !geom.isNull()) {
      if (!kept.some((k) => JSON.stringify(k) === JSON.stringify(cmd))) kept.push(cmd);
      if (kept[0] === cmd) keptAnchor = from.toString();
    }
  } else if (cmd.op === 'place') {
    // Spawn `asset` at part `at`'s MSB transform plus an offset, in block coordinates. The
    // player's physics position is not in the block's frame on every tile (on m60_42_36 it is
    // ~85 m off in y), so placing relative to a known part is the reliable way to aim.
    const anchor = geoms.find((g) => partName(g) === cmd.at);
    if (!anchor) {
      if (cmd.keep && !pending.includes(cmd)) { pending.push(cmd); emit('pending', { asset: cmd.asset, at: cmd.at }); return; }
      emit('error', { op: 'place', error: 'no part named ' + cmd.at }); return;
    }
    const part = anchor.add(0x48).readPointer();
    const p = vec3(part.add(MSB_POS));
    const r = vec3(part.add(MSB_POS + 0xc));
    const pos = [p[0] + (cmd.dx || 0), p[1] + (cmd.dy || 0), p[2] + (cmd.dz || 0)];
    // `local: [x, y, z]` is an offset in the anchor's own model frame, turned by its yaw (the
    // MsbPart keeps rotation in radians in memory, the file in degrees); `dyaw` adds to the yaw.
    if (cmd.local) {
      const c = Math.cos(r[1]);
      const s = Math.sin(r[1]);
      pos[0] += cmd.local[0] * c + cmd.local[2] * s;
      pos[1] += cmd.local[1];
      pos[2] += -cmd.local[0] * s + cmd.local[2] * c;
    }
    const rot = [r[0] + (cmd.drotx || 0), cmd.rot === undefined ? r[1] + (cmd.dyaw || 0) : cmd.rot, r[2]];
    const s = cmd.scale || 1;
    let geom = spawnAt(data, cmd.asset, pos, rot, Array.isArray(s) ? s : [s, s, s]);
    // `drawLike: true` gives the spawned asset's AssetEnvironmentGeometryParam row the anchor's
    // refDrawParamId (+0x8; PartsDrawParam holds isDirLightShadowSrc and shadowDrawDist), so a
    // carrier that was a barrel casts the shadows the building it now draws should. The row is
    // read at spawn, so the first instance is hidden and the asset spawned again.
    if (!geom.isNull() && cmd.drawLike) {
      const row = geom.add(0x20).readPointer();
      const want = anchor.add(0x20).readPointer().add(0x8).readS32();
      const had = row.add(0x8).readS32();
      if (had !== want) {
        Memory.protect(row, 0x10, 'rw-');
        row.add(0x8).writeS32(want);
        apply(geom, false, true, true);
        geom = spawnAt(data, cmd.asset, pos, rot, Array.isArray(s) ? s : [s, s, s]);
      }
      emit('drawLike', { asset: cmd.asset, refDrawParamId: { was: had, now: want } });
    }
    // A carrier asset brings its original model's Havok shape; `collision: false` drops it.
    if (!geom.isNull() && cmd.collision === false) setCollision(proxyFor(geom), 0);
    emit('place', { asset: cmd.asset, at: round(pos), rot: round(rot), result: geom.isNull() ? null : describe(geom) });
    // `keep: true` re-places this after every reload of the anchor's block (rest, death), which
    // otherwise rebuilds every instance and drops what was spawned.
    if (cmd.keep && !geom.isNull()) {
      if (!kept.some((k) => JSON.stringify(k) === JSON.stringify(cmd))) kept.push(cmd);
      keptAnchor = anchor.toString();
    }
  } else if (cmd.op === 'unspawn') {
    // Spawned instances are not in the block's geom vector, so `toggle` cannot reach them; hide
    // them through this agent's own list instead (lost on a reload in place).
    // Given `ptrs`, only those: the list below would otherwise take everything spawned since the
    // last reload with them.
    const gone = [];
    for (const g of cmd.ptrs ? [] : spawned) {
      const name = partName(g);
      if (match && !(name && match.test(name))) continue;
      apply(g, !!cmd.enable, true, true);
      gone.push(name);
    }
    // A reload empties that list, so earlier spawns are also accepted by pointer (from the log),
    // each checked against its expected part name before anything is called on it.
    // `asset` instead of `name` accepts any instance of that model (a spawn's instance number is
    // not in every log line that carries its pointer).
    for (const entry of cmd.ptrs || []) {
      const g = ptr(entry.ptr);
      let name = null;
      try { name = partName(g); } catch (e) { /* freed */ }
      const ok = entry.name ? name === entry.name : !!(entry.asset && name && name.indexOf(entry.asset + '_') === 0);
      if (!ok) { gone.push({ ptr: entry.ptr, skipped: 'name is ' + name }); continue; }
      apply(g, !!cmd.enable, true, true);
      gone.push(name);
    }
    emit('unspawn', { enable: !!cmd.enable, changed: gone });
  } else if (cmd.op === 'ground') {
    // What the player stands on: physics +0x240, a field-ins handle whose top nibble is the kind
    // (0 map collision, 6 an asset, 8 an asset's collision part), +0x92 standing on solid ground
    // (bd lift-detection-ground-handle-1171-2026-10-05). Matching it to an asset's handle is the
    // collision oracle: standing on a spawned asset proves its collision exists.
    const ph = player.add(0x190).readPointer().add(0x68).readPointer();
    const lo = ph.add(0x240).readU32() >>> 0;
    const hi = ph.add(0x244).readU32() >>> 0;
    const groundKind = lo >>> 28;
    const isAsset = groundKind === 6 || groundKind === 8;
    const same = (g) => (g.add(0x8).readU32() & 0xfffff) === (lo & 0xfffff) && (g.add(0xc).readU32() >>> 0) === hi;
    let under = !isAsset ? null : geoms.concat(spawned).find(same);
    // A large asset of this tile lives in a far block (m60_10_09_02 holds m60_42_36_00's rocks), so
    // look through every loaded block when the player's own does not have it.
    if (isAsset && !under) {
      for (const b of allBlocks()) {
        if ((b.id >>> 0) !== hi) continue;
        try { under = geomsOf(b.data).find(same) || null; } catch (e) { /* block mid-reload */ }
        if (under) break;
      }
    }
    emit('ground', { handle: hex(hi) + '_' + hex(lo), groundKind: groundKind, standing: ph.add(0x92).readU8() !== 0, asset: under ? describe(under, me.pos) : null, player: round(me.pos) });
  } else if (cmd.op === 'row') {
    // The AssetEnvironmentGeometryParam row each named part spawned from (GeomIns+0x20):
    // soundBankId, soundBreakSEId, refDrawParamId, as its first three s32.
    const rows = [];
    for (const name of cmd.names) {
      const byPtr = (cmd.ptrs || {})[name];
      const g = byPtr ? ptr(byPtr) : geoms.concat(spawned).find((x) => partName(x) === name);
      if (!g || partName(g) !== name) { rows.push({ name: name, missing: true }); continue; }
      const row = g.add(0x20).readPointer();
      rows.push({ name: name, row: row.toString(), s32: [0, 4, 8].map((o) => row.add(o).readS32()) });
    }
    emit('row', { rows: rows });
  } else if (cmd.op === 'findrot') {
    // Where a part's yaw ends up as a matrix: scan the GeomIns and everything it points at
    // (one level) for cos/sin of its MSB yaw read as degrees and as radians. Run on a map part and
    // on a spawned one with the same yaw; the unit the spawned one matches is the unit the spawn
    // request takes.
    const report = [];
    for (const name of cmd.names) {
      const byPtr = (cmd.ptrs || {})[name];
      const g = byPtr ? ptr(byPtr) : geoms.concat(spawned).find((x) => partName(x) === name);
      if (g && partName(g) !== name) { report.push({ name: name, stale: partName(g) }); continue; }
      if (!g) { report.push({ name: name, missing: true }); continue; }
      const yaw = vec3(g.add(0x48).readPointer().add(MSB_POS + 0xc))[1];
      const want = { degC: Math.cos(yaw * Math.PI / 180), degS: Math.sin(yaw * Math.PI / 180), radC: Math.cos(yaw), radS: Math.sin(yaw) };
      const hits = [];
      const scan = (base, label) => {
        for (let o = 0; o < 0x400; o += 4) {
          let f;
          try { f = base.add(o).readFloat(); } catch (e) { return; }
          for (const k of Object.keys(want)) {
            if (Math.abs(want[k]) > 0.05 && Math.abs(want[k]) < 0.9999 && Math.abs(f - want[k]) < 1e-4) hits.push(label + '+' + hex(o) + ':' + k);
          }
        }
      };
      scan(g, 'self');
      for (let o = 0; o < 0x430; o += 8) {
        let p;
        try { p = g.add(o).readPointer(); } catch (e) { break; }
        if (p.compare(ptr('0x10000')) < 0 || p.compare(ptr('0x7fffffffffff')) > 0) continue;
        scan(p, hex(o));
      }
      report.push({ name: name, yaw: yaw, hits: hits.slice(0, 40) });
    }
    emit('findrot', { report: report });
  } else if (cmd.op === 'model') {
    // Which FLVER a part draws and which materials that FLVER carries: {names: [...], ptrs?:
    // {name: ptr}} for map parts or this agent's spawns, or {match} over both.
    const want = cmd.names || [];
    const targets = [];
    for (const name of want) {
      const byPtr = (cmd.ptrs || {})[name];
      const g = byPtr ? ptr(byPtr) : geoms.concat(spawned).find((x) => partName(x) === name);
      targets.push({ name: name, geom: g && partName(g) === name ? g : null });
    }
    if (match) for (const g of geoms.concat(spawned)) { const n = partName(g); if (n && match.test(n)) targets.push({ name: n, geom: g }); }
    const models = targets.map((t) => (t.geom ? Object.assign({ name: t.name, ptr: t.geom.toString(), spawned: spawned.some((s) => s.equals(t.geom)) }, modelOf(t.geom, !!cmd.all)) : { name: t.name, missing: true }));
    emit('model', { models: models });
  } else if (cmd.op === 'peek') {
    // Discovery: qwords of the object at `path` (offsets dereferenced from the named part's
    // GeomIns), each pointer annotated with its RTTI name and any UTF-16 string it points at.
    const g = geoms.concat(spawned).find((x) => partName(x) === cmd.name);
    if (!g) { emit('error', { op: 'peek', error: 'no part ' + cmd.name }); return; }
    let base = g;
    for (const o of cmd.path || []) base = base.add(o).readPointer();
    const rows = [];
    for (let o = cmd.from || 0; o < (cmd.to || 0x100); o += 8) {
      let q;
      try { q = base.add(o).readPointer(); } catch (e) { rows.push(hex(o) + ' fault'); break; }
      let note = '';
      if (q.compare(ptr('0x10000')) >= 0 && q.compare(ptr('0x7fffffffffff')) <= 0) {
        note = rttiName(q) || '';
        try { const s = q.readUtf16String(80); if (s && s.length >= 3 && /^[\x20-\x7e]+$/.test(s)) note += ' "' + s + '"'; } catch (e) { /* not a string */ }
      }
      rows.push(hex(o) + ' ' + q.toString() + ' ' + note);
    }
    emit('peek', { name: cmd.name, base: base.toString(), rows: rows });
  } else if (cmd.op === 'forget') {
    emit('forget', { dropped: kept.length });
    kept.length = 0;
    keptAnchor = null;
  } else if (cmd.op === 'restore') {
    let n = 0;
    for (const h of hidden.values()) { apply(h.geom, true, h.draw, h.collision); n++; }
    hidden.clear();
    for (const g of spawned) apply(g, false, true, true);
    emit('restore', { reenabled: n, spawnedHidden: spawned.length });
    spawned.length = 0;
  } else {
    emit('error', { error: 'unknown op ' + cmd.op });
  }
}

let frame = 0;
let sfxWatchUntil = 0;
// A reload in place must not replay the command already on disk (a swap would spawn twice).
let lastSeq = null;
try { lastSeq = JSON.parse(File.readAllText(CMD_PATH)).seq; } catch (e) { /* no file yet */ }
const hook = Interceptor.attach(PRE_BEHAVIOR_SAFE, {
  onEnter (args) {
    let player;
    try { player = WORLD_CHR_MAN.readPointer().add(0x1e508).readPointer(); } catch (e) { return; }
    if (player.isNull() || !args[0].equals(player) || ++frame % POLL_FRAMES !== 0) return;
    if (frame % (POLL_FRAMES * 10) === 0) {
      try { replayAfterReload(player); } catch (e) { emit('error', { op: 'replay', error: e.message }); }
      try { runSweeps(); } catch (e) { emit('error', { op: 'sweeps', error: e.message }); }
    }
    let cmd;
    try { cmd = JSON.parse(File.readAllText(CMD_PATH)); } catch (e) { return; }
    if (cmd.seq === lastSeq) return;
    lastSeq = cmd.seq;
    try { run(cmd, player); } catch (e) { emit('error', { op: cmd.op, error: e.message, stack: e.stack }); }
  },
});

// The oracle for `sfx`: CS::GeomModelSfxSlot's fill (2.7.2.0 0x140696f50, 1.16.2 0x140695c50 by
// way of FUN_140692ea0; rcx slot, edx AssetModelSfxParam row) runs for every asset whose placement
// selects a row. Reported for 20 s after a spawnlist that asked for one (sfxWatchUntil), so
// streaming map assets do not flood the log.
const sfxFill = Interceptor.attach(va('0x140696f50'), {
  onEnter (args) {
    if (Date.now() < sfxWatchUntil) emit('sfxslot', { slot: args[0].toString(), row: args[1].toInt32() });
  },
});

emit('ready', { cmd: CMD_PATH });
rpc.exports = { dispose () { hook.detach(); sfxFill.detach(); } };
