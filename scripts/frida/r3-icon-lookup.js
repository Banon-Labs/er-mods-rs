// Resolve Misericorde's item icon (`MENU_ItemIcon_10003`) the way the menu does, and walk from it
// toward the GPU texture, so er-r3-view's board can copy the icon into the overlay.
//
// Once, on the first `MenuWindowJob::Run` (1.17.1 rva 0x7ae040) of the item list's window -- the
// menu thread, the thread the repository lookup inserts on -- this calls the Scaleform texture
// repository's lookup (1.17.1 rva 0xd65c00, 1.16.2 0xd63e50) on the repository global (1.17.1 rva
// 0x3d86580, null-checked first) and reports the resource's sub-rect (+0x50), image (+0x18) and
// symbol (+0x70), then every pointer within 0x100 bytes of the image and of its `+0x10` that lands
// on an object whose vtable sits in a d3d12 or vkd3d module. Offsets are from 1.16.2 and this run
// is what confirms them on 1.17.1.
'use strict';

const RUN_RVA = 0x7ae040;
const LOOKUP_RVA = 0xd65c00;
const REPO_GLOBAL_RVA = 0x3d86580;
const JOB_WINDOW = 0x130;
const TARGET = '.?AVGaitemSelectDialog@CS@@';
const ICON = 'MENU_ItemIcon_10003';

const mod = Process.findModuleByName('eldenring.exe');
const lo = mod.base;
const hi = mod.base.add(mod.size);
const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;
const follow = (a) => a.readU8() === 0xe9 ? a.add(5).add(a.add(1).readS32()) : a;
const gpuModules = Process.enumerateModules().filter((m) => /d3d12|vkd3d/i.test(m.name));
const inGpu = (p) => gpuModules.some((m) => p.compare(m.base) >= 0 && p.compare(m.base.add(m.size)) < 0);

const names = new Map();
function rtti (obj) {
  let vt;
  try { vt = obj.readPointer(); } catch (e) { return null; }
  if (!inImage(vt)) return null;
  const k = vt.toString();
  if (names.has(k)) return names.get(k);
  let n = null;
  try {
    const col = vt.sub(8).readPointer();
    if (inImage(col) && col.readU32() === 1) {
      const base = col.sub(col.add(0x14).readU32());
      n = base.add(col.add(0x0c).readU32()).add(0x10).readCString();
    }
  } catch (e) {}
  names.set(k, n);
  return n;
}

function gpuPointers (obj, depth) {
  const out = [];
  for (let o = 0; o < 0x100; o += 8) {
    let p;
    try { p = obj.add(o).readPointer(); } catch (e) { break; }
    if (p.isNull() || p.compare(ptr('0x10000')) < 0) continue;
    let vt;
    try { vt = p.readPointer(); } catch (e) { continue; }
    if (inGpu(vt)) out.push({ at: `+0x${o.toString(16)}`, ptr: p.toString(), module: gpuModules.find((m) => vt.compare(m.base) >= 0 && vt.compare(m.base.add(m.size)) < 0).name, depth });
  }
  return out;
}

let done = false;
const lookup = new NativeFunction(lo.add(LOOKUP_RVA), 'pointer', ['pointer', 'pointer', 'pointer']);
Interceptor.attach(follow(lo.add(RUN_RVA)), {
  onEnter (args) {
    if (done) return;
    let w;
    try { w = args[0].add(JOB_WINDOW).readPointer(); } catch (e) { return; }
    if (w.isNull() || rtti(w) !== TARGET) return;
    done = true;
    try {
      const repo = lo.add(REPO_GLOBAL_RVA).readPointer();
      if (repo.isNull()) { send({ tag: 'icon', error: 'repository global is null' }); return; }
      const out = Memory.alloc(16);
      out.writePointer(NULL);
      const name = Memory.allocUtf16String(ICON);
      const ret = lookup(repo, out, name);
      const res = out.readPointer();
      if (res.isNull()) { send({ tag: 'icon', error: 'lookup returned no resource', ret: ret.toString() }); return; }
      const image = res.add(0x18).readPointer();
      let symbol = null;
      try { symbol = res.add(0x70).readPointer().readUtf16String(); } catch (e) {}
      const rectI = [0, 4, 8, 12].map((o) => res.add(0x50 + o).readS32());
      const rectF = [0, 4, 8, 12].map((o) => res.add(0x50 + o).readFloat());
      const hal = image.isNull() ? NULL : image.add(0x10).readPointer();
      // The 1.16.2 rect offset reads zero on 1.17.1: report every dword in the resource and in the
      // image that holds a plausible icon coordinate, as an integer or as a UV.
      const hits = [];
      for (const [label, base] of [['res', res], ['image', image]]) {
        if (base.isNull()) continue;
        for (let o = 0; o < 0x100; o += 4) {
          let i, f;
          try { i = base.add(o).readS32(); f = base.add(o).readFloat(); } catch (e) { break; }
          if ([160, 3608, 1804, 3768, 1964, 4096, 2048].includes(i)) hits.push(`${label}+0x${o.toString(16)} i32 ${i}`);
          if (f > 0.05 && f <= 1.0 && Math.abs(f * 4096 - Math.round(f * 4096)) < 0.01) hits.push(`${label}+0x${o.toString(16)} f32 ${f} (x4096=${Math.round(f * 4096)}, x2048=${Math.round(f * 2048)})`);
        }
      }
      // ID3D12Resource::GetDesc, vtable slot 10, returns its 40-byte desc through a hidden pointer.
      const descs = [];
      const gpu = hal.isNull() ? [] : gpuPointers(hal, 2);
      for (const g of gpu) {
        try {
          const obj = ptr(g.ptr);
          const getDesc = new NativeFunction(obj.readPointer().add(10 * 8).readPointer(), 'pointer', ['pointer', 'pointer']);
          const d = Memory.alloc(64);
          getDesc(obj, d);
          descs.push({ at: g.at, dimension: d.readU32(), width: d.add(16).readU64().toNumber(), height: d.add(24).readU32(), mips: d.add(30).readU16(), format: d.add(32).readU32() });
        } catch (e) { descs.push({ at: g.at, error: String(e) }); }
      }
      send({
        tag: 'icon', repo: repo.toString(), res: res.toString(), resClass: rtti(res), symbol,
        rectHits: hits, descs,
        rect_i32: rectI, rect_f32: rectF, image: image.toString(), imageClass: rtti(image),
        hal: hal.toString(), halClass: hal.isNull() ? null : rtti(hal),
        gpuFromImage: image.isNull() ? [] : gpuPointers(image, 1),
        gpuFromHal: hal.isNull() ? [] : gpuPointers(hal, 2),
        gpuModules: gpuModules.map((m) => m.name),
      });
    } catch (e) { send({ tag: 'icon', error: String(e) }); }
  },
});

send({ tag: 'armed', gpuModules: gpuModules.map((m) => m.name) });
