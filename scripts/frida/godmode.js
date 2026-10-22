// Godmode, one-hit kills, status immunity and grace warps, ported from the Hexinton all-in-one
// table (eldenring_all-in-one_Hexinton-v4.25_ce7.5.ct) to 1.17.1. Each pattern was checked unique
// in eldenring-deobf-1.17.1.bin before use, and again in eldenring-deobf-1.17.2.bin for 2.7.2.0:
// still one hit each (two for the lua event manager), and every resolved target is the 1.17.1
// target carried by scripts/map-rvas-1171-to-1172.py -- chr dbg flags 0x143d6a210 -> 0x143d6d230,
// WorldChrMan 0x143d69ff8 -> 0x143d6d018, lua warp 0x14059aa60 -> 0x14059af10. Nothing here is
// spelled as an address, so the patterns needed no change.
//
// Edit the settings below and save: the watcher reloads this file in place. To warp, set WARP_TO
// to a grace id from the table and save; it warps once per save. Set it back to null afterwards.

const GODMODE = true;          // player cannot die and takes no damage or hits
const ONE_HIT_KILL = true;     // every enemy dies to one hit
const STATUS_IMMUNE = true;    // status buildup is refilled before it can fire
const WARP_TO = null;

// Graces nearest each Tarnished Edition item.
//   1038402950  Liurnia Lake Shore              Idus Sword (swamp to the northwest)
//   1036492950  Bellum Church                   Silver Grooved set (Nomadic Merchant)
//   1045362950  Fort Haight West                Hefty Scimitar (merchant north of Fort Haight)
//   1041322950  Isolated Merchant's Shack       Steel set (Weeping Peninsula)
//   1051362950  Redmane Castle Plaza            Radahn, then Leontiel in his arena
//   1052382950  Starscourge Radahn              the arena itself, once Radahn is dead
//   11102950    Table of Lost Grace             Reverse-Bladed Sword (Twin Maiden Husks)
//   1051392950  Fort Faroth                     Reed Great Katana (white pile to the north)
//   1048402950  Deep Siofra Well                Ritual Thrusting Shield (Caelid colosseum)
//   10002957    Secluded Cell                   Godrick, then the throne room chest
//   1034502950  Ranni's Rise                    Carian Silver regalia
//   1043532950  Hermit Merchant's Shack         Funereal Night regalia
//   1045522950  Capital Rampart                 toward Leyndell for the Golden Order invader

const CHR_DBG_FLAGS_AOB = '80 3D ?? ?? ?? ?? 00 0F 85 ?? ?? ?? ?? 32 C0 48';
const WORLD_CHR_MAN_AOB = '48 8B 05 ?? ?? ?? ?? 48 85 C0 74 0F 48 39 88';
const LUA_WARP_AOB = 'C3 ?? ?? ?? ?? ?? ?? 57 48 83 EC ?? 48 8B FA 44';
const LUA_EVENT_MAN_AOB = '48 8B 05 ?? ?? ?? ?? 48 85 C0 74 ?? 41 BE 01 00 00 00 44 89 75';
// The game's per-frame tick, the call er-duel-rules.js runs its checks in: 1.17.1 0x140773900,
// 2.7.2.0 0x140773db0. These 32 bytes are its prologue, the same in both images and found once in
// each, so the work below runs on the game's own thread, once a frame.
const FRAME_TICK_AOB = '48 8B C4 55 56 57 41 54 41 55 41 56 41 57 48 8D A8 A8 FD FF FF 48 81 EC 20 03 00 00 48 C7 45 A0';

const DBG_PLAYER_NO_DEAD = 0x0;
const DBG_HORSE_NO_DEAD = 0x1;
const DBG_PLAYER_EXTERMINATE = 0x2;   // the table's "One Hit Kill Everything"

const MAIN_PLAYER = 0x1e508;          // er-game-base WORLD_CHR_MAN_PLAYER_INS_OFFSET
const CHR_DEBUG_FLAGS = 0x520;        // ChrDebugFlags; bit 3 is disabled_hit
const CHR_MODULES = 0x190;
const DATA_HP = 0x138;
const DATA_MAX_HP = 0x13c;
const DATA_FLAGS = 0x19b;             // bit 0 no dead, bit 1 no damage
const RESIST_MODULE = 0x20;
const RESIST_CURRENT = 0x10;          // seven i32, poison rot bleed frost sleep madness death
const RESIST_MAX = 0x2c;
const RESIST_COUNT = 7;

const MODULE = Process.getModuleByName('eldenring.exe');

function scanOne (pattern) {
  const hits = [];
  for (const range of MODULE.enumerateRanges('r-x')) {
    for (const hit of Memory.scanSync(range.base, range.size, pattern)) hits.push(hit.address);
  }
  return hits;
}

function ripTarget (hit, dispAt, length) {
  return hit.add(length).add(hit.add(dispAt).readS32());
}

function rttiName (object) {
  try {
    const locator = object.readPointer().sub(8).readPointer();
    const typeRva = locator.add(0xc).readU32();
    return MODULE.base.add(typeRva).add(0x10).readCString();
  } catch (e) {
    return null;
  }
}

const dbgHits = scanOne(CHR_DBG_FLAGS_AOB);
const wcmHits = scanOne(WORLD_CHR_MAN_AOB);
const warpHits = scanOne(LUA_WARP_AOB);
const tickHits = scanOne(FRAME_TICK_AOB);
if (dbgHits.length !== 1 || wcmHits.length !== 1 || warpHits.length !== 1 || tickHits.length !== 1) {
  send({ kind: 'refused', line: 'pattern hits dbg=' + dbgHits.length + ' wcm=' + wcmHits.length + ' warp=' + warpHits.length + ' tick=' + tickHits.length });
  throw new Error('godmode: a pattern is not unique on this build');
}
const chrDbgFlags = ripTarget(dbgHits[0], 2, 7);
const worldChrManGlobal = ripTarget(wcmHits[0], 3, 7);
const luaWarp = new NativeFunction(warpHits[0].add(2), 'void', ['pointer', 'pointer', 'int']);

let luaEventManGlobal = null;
const luaCandidates = [];
for (const hit of scanOne(LUA_EVENT_MAN_AOB)) {
  const global = ripTarget(hit, 3, 7);
  const object = global.readPointer();
  const name = object.isNull() ? null : rttiName(object);
  luaCandidates.push(global + '=' + object + ':' + name);
  if (name !== null && name.indexOf('LuaEvent') !== -1) luaEventManGlobal = global;
}
// Measured on 1.17.1: the pattern's two hits are 0x143d6beb8, an object with no RTTI, and
// 0x143d5f040, CS::FaceGenManImp; in the 2.7.2.0 image they resolve to 0x143d6eed8 and
// 0x143d62060, the same two globals carried. The table takes the first hit, as Cheat Engine does, and the
// warp reads two pointers out of it, so take the first hit whose +0x8 and +0x18 both point at memory.
if (luaEventManGlobal === null) {
  for (const hit of scanOne(LUA_EVENT_MAN_AOB)) {
    const global = ripTarget(hit, 3, 7);
    const object = global.readPointer();
    if (object.isNull() || (rttiName(object) || '').indexOf('FaceGen') !== -1) continue;
    try {
      object.add(0x8).readPointer().readU8();
      object.add(0x18).readPointer().readU8();
      luaEventManGlobal = global;
      break;
    } catch (e) {}
  }
}
send({ kind: 'lua-candidates', line: luaCandidates.join(' | ') });

const originalDbg = [DBG_PLAYER_NO_DEAD, DBG_HORSE_NO_DEAD, DBG_PLAYER_EXTERMINATE].map(function (offset) {
  return [offset, chrDbgFlags.add(offset).readU8()];
});

function player () {
  const wcm = worldChrManGlobal.readPointer();
  if (wcm.isNull()) return null;
  const ins = wcm.add(MAIN_PLAYER).readPointer();
  return ins.isNull() ? null : ins;
}

// Every pointer and field is checked before anything is written, so a layout that moved shows up
// as a refusal in the log rather than as a write into the wrong place.
function playerParts (ins) {
  const modules = ins.add(CHR_MODULES).readPointer();
  const data = modules.readPointer();
  const hp = data.add(DATA_HP).readS32();
  const maxHp = data.add(DATA_MAX_HP).readS32();
  if (!(maxHp > 0 && maxHp < 100000 && hp >= 0 && hp <= maxHp)) return { refused: 'hp ' + hp + '/' + maxHp };
  const debugFlags = ins.add(CHR_DEBUG_FLAGS).readU32();
  if (debugFlags > 0xfffff) return { refused: 'debug flags 0x' + debugFlags.toString(16) };
  const resist = modules.add(RESIST_MODULE).readPointer();
  return { data: data, resist: resist, hp: hp, maxHp: maxHp, debugFlags: debugFlags };
}

function resistValues (resist, at) {
  const values = [];
  for (let i = 0; i < RESIST_COUNT; i++) values.push(resist.add(at + i * 4).readS32());
  return values;
}

let ticks = 0;
let lastReport = '';
let lastError = '';
function tick () {
  try {
    step();
  } catch (e) {
    // A read that faults inside the game's frame is reported once, and the next frame tries again.
    if (lastError !== String(e)) send({ kind: 'tick-error', line: String(e) });
    lastError = String(e);
  }
}

function step () {
  ticks += 1;
  chrDbgFlags.add(DBG_PLAYER_NO_DEAD).writeU8(GODMODE ? 1 : 0);
  chrDbgFlags.add(DBG_HORSE_NO_DEAD).writeU8(GODMODE ? 1 : 0);
  chrDbgFlags.add(DBG_PLAYER_EXTERMINATE).writeU8(ONE_HIT_KILL ? 1 : 0);
  const ins = player();
  if (ins === null) return;
  const parts = playerParts(ins);
  if (parts.refused) {
    if (lastReport !== parts.refused) send({ kind: 'player-refused', line: parts.refused });
    lastReport = parts.refused;
    return;
  }
  if (GODMODE) {
    // ChrIns+0x520 is not ChrDebugFlags on 1.17.1: the game rewrote the word every frame when
    // bit 3 was set there, so only the data module's no-dead and no-damage bits are used.
    const flags = parts.data.add(DATA_FLAGS);
    flags.writeU8(flags.readU8() | 0x3);
  }
  // Measured live: at rest each current value equals its maximum (92 of 92), so buildup counts
  // the current value down and the status fires at zero. Immunity refills it to the maximum.
  const current = resistValues(parts.resist, RESIST_CURRENT);
  const max = resistValues(parts.resist, RESIST_MAX);
  if (STATUS_IMMUNE) {
    for (let i = 0; i < RESIST_COUNT; i++) {
      if (current[i] < max[i] && max[i] > 0 && max[i] < 100000) parts.resist.add(RESIST_CURRENT + i * 4).writeS32(max[i]);
    }
  }
  const report = 'hp ' + parts.hp + '/' + parts.maxHp + ' resist ' + current.join(',') + ' of ' + max.join(',');
  if (ticks % 120 === 1 || report !== lastReport) {
    send({ kind: 'player', line: report, debugFlags: '0x' + parts.debugFlags.toString(16) });
    lastReport = report;
  }
}

if (WARP_TO !== null) {
  if (luaEventManGlobal === null) {
    send({ kind: 'warp-refused', line: 'no CSLuaEventManager among the pattern hits' });
  } else {
    const man = luaEventManGlobal.readPointer();
    luaWarp(man.add(0x18).readPointer(), man.add(0x8).readPointer(), WARP_TO - 1000);
    send({ kind: 'warp', line: 'warping to grace ' + WARP_TO });
  }
}

const ticking = Interceptor.attach(tickHits[0], { onEnter () { tick(); } });

rpc.exports = {
  dispose: function () {
    ticking.detach();
    for (const pair of originalDbg) chrDbgFlags.add(pair[0]).writeU8(pair[1]);
  }
};

send({
  kind: 'armed',
  line: 'chr dbg flags ' + chrDbgFlags + ', WorldChrMan global ' + worldChrManGlobal + ', lua warp ' +
    warpHits[0].add(2) + ', lua event manager ' + luaEventManGlobal +
    ' | godmode ' + GODMODE + ' one-hit ' + ONE_HIT_KILL + ' status-immune ' + STATUS_IMMUNE
});
