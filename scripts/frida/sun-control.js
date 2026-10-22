// Overrides the sun's light after the game blends its lighting parameters each frame, and reports
// the blended values, as the prototype for the Sky tab's Sun section (er-effects-rs-pjyh).
//
// Addresses are 2.7.2.0, carried from the 1.17.1 ones the bd notes give with
// scripts/map-rvas-1171-to-1172.py. Each code address is the entry of a function whose bytes match
// the 1.17.1 one once rip-relative and branch operands are masked, every masked target carrying to
// the new operand; each global is the carried target of those same readers.
//
// Where it writes (bd er-gparam-sun-lighting-pipeline-1171-2026-10-15): GXParamDB::Update
// 0x141a4e7a0 -> 0x141a4fe90 writes every gparam field's time-of-day and weather blend into the
// param instances; the per-manager jobs that run after it turn LightSet angles into directions and
// hemisphere colors into ambient. So an instance field written as that function returns is what
// those jobs and the GPU pack read. LightSet instances hang off GXRenderingSystem
// [0x1447f37a8 -> 0x1447f67c8] +0xc500 (the environment manager, inline) +0x28 (LightSet manager)
// +0x320 (list: bank -> list: id -> instance).
//
// GXLightSet instance: DirLight[i] at 0x40 + i*0x60 (0 the sun or moon, 1 the fill from above):
// +0x00 direction (derived), +0x10 DiffColor (alpha = intensity), +0x20 SpecColor, +0x30 Angle
// (pitch, yaw radians), +0x40 Sharpness. +0x1e0 Hemi Color Up, +0x1f0 Hemi Color Down.
//
// Config (edit CFG and save; the watcher reloads in place). null leaves the game's value:
//   pitch, yaw     degrees; the sun's angle (pitch -90 is straight down from overhead)
//   sun            multiplier on DirLight0's intensity (DiffColor.a and SpecColor.a)
//   fill           multiplier on DirLight1's intensity
//   ambient        multiplier on the hemisphere colors' intensity (Hemi Up/Down .a)
//   sharpness      DirLight0 Sharpness, absolute
//   cone           RayTracing DirLightShadow ConeRadius, absolute (the game's is 0.005): the sun's
//                  size as ray-traced shadows see it, so a wider cone gives softer shadow edges
//   rtSamples      RayTracing DirLightShadow Ray SampleCount, absolute (the game's is 1)
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const CFG = Object.assign(
  {
    pitch: null, yaw: null, sun: null, white: false, fill: null, unshadowed: null, ambient: null,
    sharpness: null, autoExposure: null, exposure: null, shadowBlur: null, weather: null,
    brightness: null, cone: 0.05, rtSamples: 4,
  },
  globalThis.__ER_FRIDA_CONFIG || {},
);

const RENDERING_SYSTEM = va('0x1447f67c8'); // GXRenderingSystem* 0x1447f37a8 -> 0x1447f67c8
const PARAM_DB_UPDATE = va('0x141a4fe90'); // GXParamDB::Update 0x141a4e7a0 -> 0x141a4fe90
const DIR = (i) => 0x40 + i * 0x60;
const HEMI_UP = 0x1e0;
const HEMI_DOWN = 0x1f0;
const DEG = Math.PI / 180;

const S = { calls: 0, threads: {}, scaled: new Map(), lastReport: 0 };

function lightSets() {
  const out = [];
  const rs = RENDERING_SYSTEM.readPointer();
  if (rs.isNull()) return out;
  const mgr = rs.add(0xc500 + 0x28).readPointer();
  if (mgr.isNull()) return out;
  const head = mgr.add(0x320).readPointer();
  for (let node = head.readPointer(), n = 0; !node.equals(head) && n < 64; node = node.readPointer(), n++) {
    const bank = node.add(0x10).readS32();
    const inner = node.add(0x18).readPointer();
    const ihead = inner.add(0x10).readPointer();
    for (let inode = ihead.readPointer(), m = 0; !inode.equals(ihead) && m < 64; inode = inode.readPointer(), m++) {
      const inst = inode.add(0x18).readPointer();
      if (inst.add(0x294).readS32() === bank) out.push({ bank, id: inode.add(0x10).readS32(), inst });
    }
  }
  return out;
}

// A multiplier on a float the DB rewrites: the value it last left is the base, so a scale never
// compounds whether the DB writes every frame or only when something changed.
function scale(addr, factor) {
  const key = addr.toString();
  const now = addr.readFloat();
  let entry = S.scaled.get(key);
  if (entry === undefined || Math.abs(now - entry.written) > 1e-6) {
    entry = { base: now, written: now };
  }
  entry.written = entry.base * factor;
  addr.writeFloat(entry.written);
  S.scaled.set(key, entry);
}

function apply(sets) {
  for (const { inst } of sets) {
    const sun = inst.add(DIR(0));
    if (CFG.pitch !== null) sun.add(0x30).writeFloat(CFG.pitch * DEG);
    if (CFG.yaw !== null) sun.add(0x34).writeFloat(CFG.yaw * DEG);
    if (CFG.sun !== null) {
      scale(sun.add(0x1c), CFG.sun);
      scale(sun.add(0x2c), CFG.sun);
    }
    if (CFG.sharpness !== null) sun.add(0x40).writeFloat(CFG.sharpness);
    if (CFG.white) {
      for (const color of [0x10, 0x20]) {
        for (const channel of [0, 4, 8]) sun.add(color + channel).writeFloat(1.0);
      }
    }
    if (CFG.fill !== null) {
      scale(inst.add(DIR(1) + 0x1c), CFG.fill);
      scale(inst.add(DIR(1) + 0x2c), CFG.fill);
    }
    // The second directional light casts no shadow (the deferred pass is DirLightShadowed1: one
    // shadowed light), so light moved from the sun into it, from the sun's own direction, reaches
    // the shadows too: the same light, less of it direct.
    if (CFG.unshadowed !== null) {
      const fill = inst.add(DIR(1));
      fill.add(0x30).writeFloat(sun.add(0x30).readFloat());
      fill.add(0x34).writeFloat(sun.add(0x34).readFloat());
      for (const color of [0x10, 0x20]) {
        for (const channel of [0, 4, 8]) fill.add(color + channel).writeFloat(sun.add(color + channel).readFloat());
      }
      fill.add(0x1c).writeFloat(CFG.unshadowed);
      fill.add(0x2c).writeFloat(CFG.unshadowed * 0.5);
    }
    if (CFG.ambient !== null) {
      scale(inst.add(HEMI_UP + 0xc), CFG.ambient);
      scale(inst.add(HEMI_DOWN + 0xc), CFG.ambient);
    }
  }
}

// The other groups keep the same list-map, at manager+0x18 for the tone map, and an inline accessor
// table in each instance whose entries (stride 0x30, +0x08 the field's address) are in registration
// order (scripts/ghidra/gparam-meta-offsets.py). Measured live 2026-10-15: tone map table at
// instance+0xb0, 26 entries; 0 Exposure, 4 Enable AutoExposure, 9/10 AutoExposure Adaption Max/Min.
function instances(envOffset, mapOffset) {
  const out = [];
  const rs = RENDERING_SYSTEM.readPointer();
  if (rs.isNull()) return out;
  const mgr = rs.add(0xc500 + envOffset).readPointer();
  const head = mgr.add(mapOffset).readPointer();
  for (let node = head.readPointer(), n = 0; !node.equals(head) && n < 64; node = node.readPointer(), n++) {
    const ihead = node.add(0x18).readPointer().add(0x10).readPointer();
    for (let inode = ihead.readPointer(), m = 0; !inode.equals(ihead) && m < 64; inode = inode.readPointer(), m++) {
      out.push(inode.add(0x18).readPointer());
    }
  }
  return out;
}

function field(inst, tableOffset, index) {
  return inst.add(tableOffset + 0x28).readPointer().add(index * 0x30 + 8).readPointer();
}

// A tone-map field by its registered offset. The accessor table also lists the ToneMapMode enum
// (registered 0x104) between Exposure and FilmicContrast, so table indices are one past the
// registration list from there on; the registered offset, checked against the table, is not.
function tone(inst, registered) {
  const at = fieldByRegistered(inst, 0xb0, 0x100, registered, 4);
  if (at === null) throw new Error(`tone field 0x${registered.toString(16)} not in the accessor table`);
  return at;
}

function applyTone() {
  if (CFG.autoExposure === null && CFG.exposure === null && CFG.brightness === null) return;
  for (const inst of instances(0x7e0, 0x18)) {
    if (CFG.autoExposure !== null) tone(inst, 0x114).writeU32(CFG.autoExposure);
    if (CFG.exposure !== null) scale(field(inst, 0xb0, 0), CFG.exposure);
    // The grey auto exposure aims the picture's average at (entry 7, AutoExposure Middle Gray):
    // raising it brightens everything the same, sky light and shadows included.
    if (CFG.brightness !== null) scale(tone(inst, 0x120), CFG.brightness);
  }
}

// A field named by its registered offset (scripts/ghidra/gparam-meta-offsets.py): the instance
// holds it at registered - delta, delta taken from entry 0 (whose registered offset is `first`),
// and it is used only if some accessor entry points at exactly that address with that size, so a
// wrong delta writes nothing.
function fieldByRegistered(inst, tableOffset, first, registered, size) {
  const entries = inst.add(tableOffset + 0x28).readPointer();
  const count = inst.add(tableOffset + 0x24).readS32();
  const delta = first - entries.add(8).readPointer().sub(inst).toInt32();
  const want = inst.add(registered - delta);
  for (let i = 0; i < count; i++) {
    const entry = entries.add(i * 0x30);
    if (entry.add(8).readPointer().equals(want) && entry.add(0x28).readU32() === size) return want;
  }
  return null;
}

// ShadowParam: list-map at manager+0x20, table at instance+0xd0, entry 0 is Angle (registered 0x60).
// KernelScale 0xec and Blur Radius 0xf8 widen the shadow filter: softer shadow edges.
function applyShadow() {
  if (CFG.shadowBlur === null) return;
  for (const inst of instances(0x830, 0x20)) {
    for (const registered of [0xec, 0xf8]) {
      const at = fieldByRegistered(inst, 0xd0, 0x60, registered, 4);
      if (at !== null) scale(at, CFG.shadowBlur);
    }
  }
}

function describeShadow() {
  return instances(0x830, 0x20).slice(0, 2).map((inst) => {
    const kernel = fieldByRegistered(inst, 0xd0, 0x60, 0xec, 4);
    const radius = fieldByRegistered(inst, 0xd0, 0x60, 0xf8, 4);
    return {
      kernel: kernel === null ? null : +kernel.readFloat().toFixed(3),
      radius: radius === null ? null : +radius.readFloat().toFixed(3),
    };
  });
}

// RayTracing: list-map at manager+0x18, table at instance+0x70, entry 0 is Enable DirLightShadow
// (registered 0x58). ConeRadius 0x6c, DirLightShadow SampleCount 0x5c. The first value seen at each
// address is kept so unload can put it back.
const RT_TABLE = 0x70;
const RT_FIRST = 0x58;
const RT_CONE = 0x6c;
const RT_SAMPLES = 0x5c;
const rtOriginal = new Map();
function rtField(inst, registered) {
  return fieldByRegistered(inst, RT_TABLE, RT_FIRST, registered, 4);
}
function rtWrite(at, kind, value) {
  const key = at.toString();
  if (!rtOriginal.has(key)) {
    rtOriginal.set(key, { at, kind, value: kind === 'u32' ? at.readU32() : at.readFloat() });
  }
  if (kind === 'u32') at.writeU32(value);
  else at.writeFloat(value);
}

function applyRayTracing() {
  if (CFG.cone === null && CFG.rtSamples === null) return;
  for (const inst of instances(0x880, 0x18)) {
    const cone = rtField(inst, RT_CONE);
    if (CFG.cone !== null && cone !== null) rtWrite(cone, 'f32', CFG.cone);
    const samples = rtField(inst, RT_SAMPLES);
    if (CFG.rtSamples !== null && samples !== null) rtWrite(samples, 'u32', CFG.rtSamples);
  }
}

function describeRayTracing() {
  return instances(0x880, 0x18).slice(0, 3).map((inst) => {
    const enable = fieldByRegistered(inst, RT_TABLE, RT_FIRST, RT_FIRST, 1);
    const cone = rtField(inst, RT_CONE);
    const samples = rtField(inst, RT_SAMPLES);
    return {
      shadow: enable === null ? null : enable.readU8(),
      cone: cone === null ? null : +cone.readFloat().toFixed(4),
      samples: samples === null ? null : samples.readU32(),
    };
  });
}

function describeTone() {
  return instances(0x7e0, 0x18).slice(0, 3).map((inst) => ({
    exposure: +field(inst, 0xb0, 0).readFloat().toFixed(3),
    auto: tone(inst, 0x114).readU32(),
    grey: +tone(inst, 0x120).readFloat().toFixed(3),
    adaptMax: +tone(inst, 0x128).readFloat().toFixed(3),
    adaptMin: +tone(inst, 0x12c).readFloat().toFixed(3),
  }));
}

function describe(sets) {
  return sets.map(({ bank, id, inst }) => {
    const sun = inst.add(DIR(0));
    const f = (a, n) => Array.from({ length: n }, (_, i) => +a.add(i * 4).readFloat().toFixed(3));
    return {
      bank, id,
      pitch: +(sun.add(0x30).readFloat() / DEG).toFixed(1),
      yaw: +(sun.add(0x34).readFloat() / DEG).toFixed(1),
      dir: f(sun, 3), diff: f(sun.add(0x10), 4), sharp: +sun.add(0x40).readFloat().toFixed(3),
      fill: f(inst.add(DIR(1) + 0x10), 4), hemiUp: f(inst.add(HEMI_UP), 4),
    };
  });
}

Interceptor.attach(PARAM_DB_UPDATE, {
  onLeave() {
    S.calls++;
    const tid = Process.getCurrentThreadId();
    S.threads[tid] = (S.threads[tid] || 0) + 1;
    let sets;
    try {
      sets = lightSets();
      apply(sets);
      applyTone();
      applyShadow();
      applyRayTracing();
    } catch (e) {
      if (S.calls % 600 === 1) send({ kind: 'error', message: String(e) });
      return;
    }
    if (S.calls - S.lastReport >= 120) {
      S.lastReport = S.calls;
      send({ kind: 'sun', calls: S.calls, threads: S.threads, rt: describeRayTracing(), tone: describeTone(), shadow: describeShadow(), sets: describe(sets) });
    }
  },
});

// The game's own overcast light is the realistic diffuse light: asking for a cloudy weather blends
// in its weatheroutdoor gparam (softer sun, more sky light). Asked once, from the weather's own
// update (ChangeWeather 0x140647940 -> 0x140647df0, lifespan -1, immediate), and ended again on
// unload.
const WEATHER_UPDATE = va('0x140648e80'); // WorldAreaWeather::Update 0x1406489d0 -> 0x140648e80
const WEATHER_GLOBAL = va('0x143d70410'); // WorldAreaWeather* 0x143d6d3f0 -> 0x143d70410
const CHANGE_WEATHER = new NativeFunction(va('0x140647df0'), 'void', ['pointer', 'int16', 'float', 'uint8']);
let weatherAsked = false;
Interceptor.attach(WEATHER_UPDATE, {
  onEnter(args) {
    if (CFG.weather === null || weatherAsked) return;
    weatherAsked = true;
    CHANGE_WEATHER(args[0], CFG.weather, -1.0, 1);
    send({ kind: 'weather', type: CFG.weather });
  },
});

send({ kind: 'loaded', cfg: CFG, sets: describe(lightSets()) });

// Put back what the next DB pass may not rewrite (it may only write fields that changed): every
// scaled float to its base, and the angles and sharpness to the values seen at load.
const LOADED = lightSets().map(({ inst }) => ({
  inst,
  angle: [inst.add(DIR(0) + 0x30).readFloat(), inst.add(DIR(0) + 0x34).readFloat()],
  sharp: inst.add(DIR(0) + 0x40).readFloat(),
}));
const TONE_LOADED = instances(0x7e0, 0x18).map((inst) => ({ inst, auto: tone(inst, 0x114).readU32() }));
rpc.exports.dispose = function () {
  for (const [key, entry] of S.scaled) ptr(key).writeFloat(entry.base);
  for (const { at, value, kind } of rtOriginal.values()) {
    if (kind === 'u32') at.writeU32(value);
    else at.writeFloat(value);
  }
  if (CFG.autoExposure !== null) {
    for (const { inst, auto } of TONE_LOADED) tone(inst, 0x114).writeU32(auto);
  }
  const weather = WEATHER_GLOBAL.readPointer();
  if (weatherAsked && !weather.isNull()) CHANGE_WEATHER(weather, -1, 0.0, 1);
  for (const { inst, angle, sharp } of LOADED) {
    if (CFG.pitch !== null) inst.add(DIR(0) + 0x30).writeFloat(angle[0]);
    if (CFG.yaw !== null) inst.add(DIR(0) + 0x34).writeFloat(angle[1]);
    if (CFG.sharpness !== null) inst.add(DIR(0) + 0x40).writeFloat(sharp);
  }
};
