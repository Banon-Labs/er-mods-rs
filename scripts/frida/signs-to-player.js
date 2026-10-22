// Move EMEVD NPC summon signs (Great Jar knights by default) under the player's feet.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/signs-to-player.js
//
// Two paths, both from bd placenpcsummonsign-position-1171-2026-10-06 (1.17.1, static):
//  - placed signs: rpc moveSigns() walks SosSignMan's sign map on the next frame, writes the
//    player's position into each SosSignData and re-places its sfx with CreateSignSfx;
//  - new signs: the create call (0x1406fcef0 -> 0x1406fd3a0) gets the player's position in its
//    position argument, so a sign placed after a grace rest lands at the player as well.
// The knight warps to the sign's position when it is touched, so it arrives here too.
//
// Every address below is the 2.7.2.0 one, carried from 1.17.1 with
// scripts/map-rvas-1171-to-1172.py and written "old -> new" beside it. Each function body matches
// between eldenring-deobf-1.17.1.bin and eldenring-deobf-1.17.2.bin with its rip-relative and
// branch operands carried, and each global is what its 2.7.2.0 readers load. Addresses that only
// appear in comments are the 1.17.1 ones the bd notes use, with the 2.7.2.0 one beside them.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const FRAME_TICK = va('0x140773db0');         // frame tick 0x140773900 -> 0x140773db0
const WORLD_CHR_MAN = va('0x143d6d018');      // WorldChrMan 0x143d69ff8 -> 0x143d6d018
const MAIN_PLAYER = 0x1e508;
// CSEventMan 0x143d6c768 -> 0x143d6f788: +0x60 CSEventSosSignCtrl, +0x48 SosSignMan
const CS_EVENT_MAN = va('0x143d6f788');
// 0x1406fcef0 -> 0x1406fd3a0 (man, type, u32* npc, i32* flag, Vec3* pos, Vec3* angle, ...)
const CREATE_NPC_SIGN = va('0x1406fd3a0');
// GetPhysicsPosition 0x1403f0e20, the same address in both builds.
const GET_PHYSICS_POS = new NativeFunction(va('0x1403f0e20'), 'void', ['pointer', 'pointer']);
// CreateSignSfx 0x1406febe0 -> 0x1406ff090
const CREATE_SIGN_SFX = new NativeFunction(va('0x1406ff090'), 'void', ['pointer', 'pointer']);

const SIGN = { POS: 0x14, YAW: 0x20, NPC: 0x234 };
const GREAT_JAR_KNIGHTS = [1047410701, 1047410702, 1047410703];

let only = GREAT_JAR_KNIGHTS;   // npc entity ids to move; [] means every NPC sign
let pending = true;     // er-frida-watch has no rpc channel: loading the agent moves them once
const posBuf = Memory.alloc(16);

function emit(kind, fields) {
    send(Object.assign({ kind: 'signs-' + kind }, fields || {}));
}

function player() {
    const wcm = WORLD_CHR_MAN.readPointer();
    if (wcm.isNull()) return null;
    const p = wcm.add(MAIN_PLAYER).readPointer();
    return p.isNull() ? null : p;
}

// Player position plus a sideways step per sign so three signs do not overlap.
// A fixed sign spot, kept in block-local coordinates because the physics frame's block centre moves
// between loads ([296,32,296] in one Roundtable load, [328,16,304] in another, measured). Physics =
// local + centre (ConvertBlockCoordsToPhysicsCoords). This one is the spot the custom sign had at
// physics (-2.18, 0.21, 1.38) under centre (296, 32, 296). null = at the player.
const FIXED_SPOT = { block: 0x0b0a0000, local: [-298.18, -31.79, -294.62] };
function fixedSpot() {
    const out = Memory.alloc(16);
    const local = Memory.alloc(16);
    const block = Memory.alloc(4);
    block.writeU32(FIXED_SPOT.block);
    FIXED_SPOT.local.forEach((v, k) => local.add(k * 4).writeFloat(v));
    if (!BLOCK_TO_PHYSICS(out, local, block)) return null;
    return { x: out.readFloat(), y: out.add(4).readFloat(), z: out.add(8).readFloat() };
}

function spot(i) {
    if (FIXED_SPOT !== null && only.length === 1) return fixedSpot();
    const p = player();
    if (p === null) return null;
    GET_PHYSICS_POS(p, posBuf);
    const step = (i - 1) * 1.2;
    return { x: posBuf.readFloat() + step, y: posBuf.add(4).readFloat(), z: posBuf.add(8).readFloat() };
}

function wanted(npc) {
    return only.length === 0 || only.indexOf(npc) !== -1;
}

function signMan() {
    const em = CS_EVENT_MAN.readPointer();
    if (em.isNull()) return null;
    const ctrl = em.add(0x60).readPointer();
    if (ctrl.isNull()) return null;
    const man = ctrl.add(0x48).readPointer();
    return man.isNull() ? null : man;
}

// MSVC std::map in-order walk: head at man+0x10, root = head->parent; node +0 left, +8 parent,
// +0x10 right, +0x19 isNil, +0x28 SosSignData*.
function signs(man) {
    const head = man.add(0x10).readPointer();
    const out = [];
    const walk = (n, depth) => {
        if (n.isNull() || n.add(0x19).readU8() !== 0 || depth > 64) return;
        walk(n.readPointer(), depth + 1);
        out.push(n.add(0x28).readPointer());
        walk(n.add(0x10).readPointer(), depth + 1);
    };
    walk(head.add(8).readPointer(), 0);
    return out;
}

function moveNow() {
    const man = signMan();
    if (man === null) return emit('moved', { ok: false, why: 'no SosSignMan' });
    const moved = [];
    const seen = [];
    let i = 0;
    for (const s of signs(man)) {
        if (s.isNull()) continue;
        const npc = s.add(SIGN.NPC).readU32();
        seen.push(npc);
        if (!wanted(npc)) continue;
        // One sign lands exactly at the player (spot(1) has no sideways step).
        const at = spot(only.length === 1 ? 1 : i++);
        if (at === null) return emit('moved', { ok: false, why: 'no player' });
        s.add(SIGN.POS).writeFloat(at.x);
        s.add(SIGN.POS + 4).writeFloat(at.y);
        s.add(SIGN.POS + 8).writeFloat(at.z);
        CREATE_SIGN_SFX(man, s);
        moved.push({ npc: npc, at: at });
    }
    emit('moved', { ok: true, count: moved.length, moved: moved, seen: seen });
}

// Place the knights' signs ourselves, without waiting for event 1047412300: the native body of
// EMEVD PlaceNPCSummonSign (2003[25]) 0x1406fa7f0 -> 0x1406faca0 takes (SosSignMan*, u8 type,
// u32* npc, u32* region, i32 summonFlag, BlockId* block, i32 dismissFlag, u8 mpRules), read from
// its caller 0x14060b5a0 -> 0x14060ba50. The region only feeds the position, which the create hook
// below replaces. traps 'all' so this script's own hooks (create position, entity stand-in) fire
// inside the call.
const PLACE_NPC_SIGN = new NativeFunction(va('0x1406faca0'), 'void',
    ['pointer', 'uint8', 'pointer', 'pointer', 'int', 'pointer', 'int', 'uint8'], { traps: 'all' });
const KNIGHT_SIGNS = [0, 1, 2].map((i) => ({
    npc: 1047410701 + i, region: 1047412201 + i, summonFlag: 1047412220 + i, dismissFlag: 1047410230 + i,
}));
const BLOCK_M60_47_41_00 = 0x3c2f2900;
const CHR_SETS = 0x1e508 - 0x10 - 196 * 8;    // as spawn-npc.js allChrs reads WorldChrMan
function allChrs() {
    const wcm = WORLD_CHR_MAN.readPointer();
    const out = [];
    for (let s = 0; s < 196; s++) {
        const set = wcm.add(CHR_SETS + s * 8).readPointer();
        if (set.isNull()) continue;
        const cap = set.add(0x10).readU32();
        const entries = set.add(0x18).readPointer();
        if (entries.isNull() || cap > 4096) continue;
        for (let i = 0; i < cap; i++) {
            const chr = entries.add(i * 16).readPointer();
            if (!chr.isNull()) out.push(chr);
        }
    }
    return out;
}
function listChrs() {
    const p = player();
    return allChrs().filter((c) => !c.equals(p)).map((c) => {
        GET_PHYSICS_POS(c, posBuf);
        const me = spot(1);
        const d = Math.hypot(posBuf.readFloat() - me.x, posBuf.add(4).readFloat() - me.y, posBuf.add(8).readFloat() - me.z);
        return { chr: String(c), npcParam: c.add(0x60).readS32(), team: c.add(0x6c).readU8(), d: Math.round(d) };
    }).sort((a, b) => a.d - b.d).slice(0, 30);
}

// Stand-ins for the knights when the knights are not loaded: equipped NPCs loaded where the
// player is, by NpcParam (Roundtable Hold humans). A sign is keyed to its NPC's own event entity
// id, ChrIns +0x1e8 (measured: the native lookup maps 11100710/725/730 back to these chrs), so the
// stand-in is found natively both when the sign is made and when it is touched.
const STAND_INS = [523200079, 523180079, 523140079];
const CHR_EVENT_ENTITY = 0x1e8;

function whereStandIns() {
    GET_PHYSICS_POS(player(), posBuf);
    const me = [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()];
    return STAND_INS.map((npc) => {
        const c = allChrs().find((chr) => chr.add(0x60).readS32() === npc);
        if (c === undefined) return { npc: npc, chr: null };
        GET_PHYSICS_POS(c, posBuf);
        const p = [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()];
        return { npc: npc, chr: String(c), team: c.add(0x6c).readU8(), block: c.add(0x38).readU32().toString(16),
            pos: p.map((v) => Math.round(v * 10) / 10), d: Math.round(Math.hypot(p[0] - me[0], p[1] - me[1], p[2] - me[2])) };
    }).concat([{ player: me.map((v) => Math.round(v * 10) / 10) }]);
}

// Each frame: when a stand-in jumps more than 3 m in one frame (the join warp), report where it
// landed against the player's position and every sign's position at that moment.
const lastPos = {};
function trackJumps() {
    const ids = Memory.alloc(4);
    ids.writeU32(CUSTOM.entity);
    const custom = GET_CHR_BY_ENTITY(ids, 0, 0);
    for (const npc of STAND_INS.concat(['custom'])) {
        const c = npc === 'custom' ? (custom.isNull() ? undefined : custom)
            : allChrs().find((chr) => chr.add(0x60).readS32() === npc);
        if (c === undefined) { delete lastPos[npc]; continue; }
        GET_PHYSICS_POS(c, posBuf);
        const p = [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()];
        const was = lastPos[npc];
        lastPos[npc] = p;
        if (was === undefined || Math.hypot(p[0] - was[0], p[1] - was[1], p[2] - was[2]) < 3) continue;
        GET_PHYSICS_POS(player(), posBuf);
        const me = [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()];
        const r = (v) => v.map((x) => Math.round(x * 10) / 10);
        emit('jump', { npc: npc, team: c.add(0x6c).readU8(), from: r(was), to: r(p), player: r(me),
            dPlayer: Math.round(Math.hypot(p[0] - me[0], p[2] - me[2]) * 10) / 10,
            signs: rpc.exports.listSigns().map((s) => ({ npc: s.npc, pos: r(s.pos) })) });
    }
}

// Two readings of each position, to catch a frame mismatch: GetPhysicsPosition, and the physics
// module's own +0x70 (ChrIns +0x190 modules -> +0x68 physics), as spawn-npc.js posOf reads it.
function twoPositions() {
    const r = (v) => v.map((x) => Math.round(x * 10) / 10);
    const read = (c) => {
        GET_PHYSICS_POS(c, posBuf);
        const a = [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()];
        const ph = c.add(0x190).readPointer().add(0x68).readPointer().add(0x70);
        const b = [ph.readFloat(), ph.add(4).readFloat(), ph.add(8).readFloat()];
        return { getPhys: r(a), physics70: r(b), block: c.add(0x38).readU32().toString(16),
            block3c: c.add(0x3c).readU32().toString(16) };
    };
    const yura = allChrs().find((chr) => chr.add(0x60).readS32() === 523180079);
    return { player: read(player()), yura: yura === undefined ? null : read(yura) };
}

// ConvertBlockCoordsToPhysicsCoords (1.16.2 0x14061e120, 1.17.1 0x14061ef70 -> 0x14061f420):
// physics = block-local + the block's origin. Local (0,0,0) gives the origin of the player's block.
const BLOCK_TO_PHYSICS = new NativeFunction(va('0x14061f420'), 'uint8', ['pointer', 'pointer', 'pointer']);
function blockOrigin() {
    const out = Memory.alloc(16);
    const zero = Memory.alloc(16);
    const block = Memory.alloc(4);
    block.writeU32(player().add(0x38).readU32());
    const ok = BLOCK_TO_PHYSICS(out, zero, block);
    return { ok: ok, block: block.readU32().toString(16),
        origin: [out.readFloat(), out.add(4).readFloat(), out.add(8).readFloat()].map((x) => Math.round(x * 10) / 10) };
}

// Give a joined stand-in a combat AI (bd npc-think-swap-live-1171-2026-10-06): EnableLogic sets
// ChrCtrl+0xe8 bit 0x20, ChrIns_SetThinkParam is the body of EMEVD 2004[19]. Think 523590100 is
// logic 10000 + battle goal 29999, the generic equipment-driven human NPC AI.
// EnableLogic 0x1403ee260 and ChrIns_SetThinkParam 0x1402ca470 did not move.
const ENABLE_LOGIC = new NativeFunction(va('0x1403ee260'), 'void', ['pointer', 'uint8']);
const SET_THINK = new NativeFunction(va('0x1402ca470'), 'uint8', ['pointer', 'int']);
const COMBAT_THINK = 523590100;
function thinkOf(chr) {
    const man = new NativeFunction(chr.readPointer().add(0x1d0).readPointer(), 'pointer', ['pointer'])(chr);
    if (man.isNull()) return null;
    const ai = man.add(0xc0).readPointer();
    if (ai.isNull()) return null;
    return { think: ai.add(0x28).readS32(), logic: ai.add(0x38).readS32(), battle: ai.add(0x3c).readS32() };
}
function armStandIns() {
    return STAND_INS.map((npc) => {
        const c = allChrs().find((chr) => chr.add(0x60).readS32() === npc);
        if (c === undefined || c.add(0x6c).readU8() !== 16) return { npc: npc, skipped: true };
        const before = thinkOf(c);
        ENABLE_LOGIC(c, 1);
        const ok = SET_THINK(c, COMBAT_THINK);
        return { npc: npc, before: before, ok: ok, after: thinkOf(c), dbg538: c.add(0x538).readU8() };
    });
}

// A red sign for any loaded character, by entity id, at the player's feet. Summon-path spawns
// (scripts/frida/spawn-npc.js) carry entity id 35000. The two flags are the Great Jar's first
// knight's, borrowed: only that trial's event reads them.
// entity 11100710: a Roundtable resident (NpcParam 523200079) already loaded there.
const CUSTOM = { entity: 11100710, summonFlag: 1047412220, dismissFlag: 1047410230 };
// (int* entity, bool), 0x1405f5c40 -> 0x1405f60f0
const SET_BACKREAD_DISABLED = new NativeFunction(va('0x1405f60f0'), 'void', ['pointer', 'uint8']);
function placeCustom() {
    const man = signMan();
    const ids = Memory.alloc(16);
    ids.writeU32(CUSTOM.entity);
    const chr = GET_CHR_BY_ENTITY(ids, 0, 0);
    if (man === null || chr.isNull()) return { ok: false, man: String(man), chr: String(chr) };
    // Turn the character's backread back on so the game loads its model (loadStatus 0 -> 3 -> 4);
    // a resident whose event script switched it off never loads and is never drawn
    // (bd chrins-10-state8-draw-load-1171-2026-10-06).
    const backreadWas = chr.add(0x20).readU8();
    SET_BACKREAD_DISABLED(ids, 0);
    ids.add(4).writeU32(0);
    const block = Memory.alloc(4);
    block.writeU32(player().add(0x38).readU32());
    const before = signs(man).length;
    PLACE_NPC_SIGN(man, 2, ids, ids.add(4), CUSTOM.summonFlag, block, CUSTOM.dismissFlag, 0);
    only = [CUSTOM.entity];
    moveNow();
    return { ok: true, chr: String(chr), npcParam: chr.add(0x60).readS32(), backreadWas: backreadWas,
        loadStatus: chr.add(0x10).readPointer().add(8).readU8(), before: before, after: signs(man).length };
}

// The first 0x40 bytes and the tail of every sign, to diff the invisible one against the others.
function dumpSigns() {
    const man = signMan();
    return signs(man).filter((s) => !s.isNull()).map((s) => ({
        npc: s.add(SIGN.NPC).readU32(),
        head: Array.from(new Uint8Array(s.readByteArray(0x40))).map((b) => b.toString(16).padStart(2, '0')).join(''),
        tail: Array.from(new Uint8Array(s.add(0x220).readByteArray(0x30))).map((b) => b.toString(16).padStart(2, '0')).join(''),
    }));
}

// The touch check GetSignByActionButtion (0x1406fadb0 -> 0x1406fb260) accepts a sign only when
// f3150(ctx, sign+0x2e, 0) and fc850(&node+0x28) both return nonzero. Log each verdict per sign
// once it changes, to see which one rejects the custom sign.
// f3150: 0x1406f3150 -> 0x1406f3600. Its entry is an Arxan stub (a jmp into Arxan's code) in both
// de-Arxan'd images, so its own bytes cannot be compared; the carry is the call target in
// GetSignByActionButtion, whose body matches: call 0x1406f3150 at 0x1406faee5 becomes call
// 0x1406f3600 at 0x1406fb395.
// fc850: 0x1406fc850 -> 0x1406fcd00, body matches.
const verdicts = {};
const verdictHooks = [];
function note(which, sign, ok) {
    const npc = sign.isNull() ? -1 : sign.add(SIGN.NPC).readU32();
    const key = which + ':' + npc;
    if (verdicts[key] === ok) return;
    verdicts[key] = ok;
    emit('verdict', { which: which, npc: npc, ok: ok });
}
verdictHooks.push(Interceptor.attach(va('0x1406f3600'), {
    onEnter(args) { this.sign = args[1].sub(0x2e); },
    onLeave(ret) { note('f3150', this.sign, ret.toInt32() & 0xff); },
}));
verdictHooks.push(Interceptor.attach(va('0x1406fcd00'), {
    onEnter(args) { this.sign = args[0].readPointer(); },
    onLeave(ret) { note('fc850', this.sign, ret.toInt32() & 0xff); },
}));

// Bytes that differ between the invisible joined NPC and a visible resident, ChrIns 0..0x600.
function diffChrs(a, b) {
    const ba = new Uint8Array(a.readByteArray(0x600));
    const bb = new Uint8Array(b.readByteArray(0x600));
    const out = [];
    for (let i = 0; i < 0x600; i++) if (ba[i] !== bb[i] && (i < 0x180 || i > 0x1f0 || true)) out.push(i.toString(16) + ':' + ba[i].toString(16) + '/' + bb[i].toString(16));
    return out;
}
function diffJoined() {
    const find = (npc) => allChrs().find((chr) => chr.add(0x60).readS32() === npc);
    const joined = find(523200079);
    const visible = find(523140079);
    if (joined === undefined || visible === undefined) return { joined: String(joined), visible: String(visible) };
    return { joined: String(joined), visible: String(visible), diff: diffChrs(joined, visible) };
}

function setChrType(npc, type) {
    const c = allChrs().find((chr) => chr.add(0x60).readS32() === npc);
    if (c === undefined) return { npc: npc, chr: null };
    const before = c.add(0x68).readU8();
    c.add(0x68).writeU8(type);
    GET_PHYSICS_POS(c, posBuf);
    return { npc: npc, chr: String(c), before: before, after: c.add(0x68).readU8(),
        pos: [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()].map((x) => Math.round(x * 10) / 10) };
}

// A joined NPC phantom is sent home and disabled by PartyMemberInfo::Update_PartyMemberState
// (0x1409fcfa0 -> 0x1409fd460) at state 3 when the host's play region forbids multiplay
// (Roundtable does). Clear the entry's +0x1d flag, which arms that check, for the character our
// sign summons, every frame (bd npc-sign-join-visibility-1171-2026-10-06): PartyMemberInfo
// [[GameMan]+0xd90], 6 entries at +0x28, 0x30 each, +0 handle == ChrIns+0x8, +0xc state, +0x1d
// restriction flag.
// GameMan 0x143d6d988 -> 0x143d709a8 (also fromsoftware-rs rva_ww.rs game_man 0x3d709a8).
const GAME_MAN = va('0x143d709a8');
const PARTY_ROOT = GAME_MAN;
let partyLog = '';
function keepSignNpcInParty() {
    const root = PARTY_ROOT.readPointer();
    if (root.isNull()) return;
    const party = root.add(0xd90).readPointer();
    if (party.isNull()) return;
    const ids = Memory.alloc(4);
    ids.writeU32(OTHER_MAP.entity);   // the spawned character our sign summons
    const chr = GET_CHR_BY_ENTITY(ids, 0, 0);
    if (chr.isNull()) return;
    const handle = chr.add(8).readU64();
    for (let i = 0; i < 6; i++) {
        const e = party.add(0x28 + i * 0x30);
        if (!e.readU64().equals(handle)) continue;
        const state = e.add(0xc).readS32();
        const flag = e.add(0x1d).readU8();
        if (flag !== 0) e.add(0x1d).writeU8(0);
        const line = 'state ' + state + ' flag ' + flag;
        if (line !== partyLog) { partyLog = line; emit('party', { entry: i, state: state, flagWas: flag }); }
    }
}

// Visibility state of the sign's NPC (bd npc-sign-join-visibility-1171-2026-10-06).
// IsDrawn 0x1403f3930, the same address in both builds; its term fe9c0 is 0x1403fe9c0 -> 0x1403fea50.
const IS_DRAWN = new NativeFunction(va('0x1403f3930'), 'uint8', ['pointer']);
const IS_DRAWN_TERM_FE9C0 = new NativeFunction(va('0x1403fea50'), 'uint8', ['pointer']);
function signNpcState() {
    const ids = Memory.alloc(4);
    ids.writeU32(OTHER_MAP.entity);
    const c = GET_CHR_BY_ENTITY(ids, 0, 0);
    if (c.isNull()) return { chr: null };
    GET_PHYSICS_POS(c, posBuf);
    // IsDrawn 0x1403f3930 terms, in order. Its [chr+0x10]+0x8 == 4 term was the only one failing
    // (0) on the joined NPC. Read only: forcing it to 4 drew the NPC and crashed the game seconds later.
    const h = c.add(0x10).readPointer();
    const ctrl = c.add(0x58).readPointer();
    const terms = { b1c5_8: c.add(0x1c5).readU8() & 8, hA: h.add(0xa).readU8().toString(16), h8: h.add(8).readU8(),
        ctrlC8_24: ctrl.isNull() || ctrl.add(0xc8).readPointer().isNull() ? null : ctrl.add(0xc8).readPointer().add(0x24).readU8() & 1,
        b1ca_1: c.add(0x1ca).readU8() & 1,
        fe9c0: IS_DRAWN_TERM_FE9C0(c) };
    return { terms: terms, chr: String(c), team: c.add(0x6c).readU8(), chrType: c.add(0x68).readU8(), drawn: IS_DRAWN(c),
        disabled: c.add(0x10).readPointer().add(0xa).readU8() & 1, b1c5: c.add(0x1c5).readU8().toString(16),
        alpha: [0x238, 0x240, 0x248].map((o) => Math.round(c.add(o).readFloat() * 100) / 100),
        pos: [posBuf.readFloat(), posBuf.add(4).readFloat(), posBuf.add(8).readFloat()].map((x) => Math.round(x * 10) / 10) };
}

let mode = 'list';   // what the next frame does: 'place' new signs, or 'move' placed ones

// 0x1405eefd0 -> 0x1405ef480
const GET_CHR_BY_ENTITY = new NativeFunction(va('0x1405ef480'), 'pointer', ['pointer', 'int', 'int']);
let createIndex = 0;
const hooks = [];

// Calls made from inside an Interceptor callback do not fire this script's own hooks, so the
// create-hook positioning does not apply here: place, then move what was placed.
function placeNow() {
    const man = signMan();
    if (man === null) return emit('placed-all', { ok: false, why: 'no SosSignMan' });
    const ids = Memory.alloc(16);
    const block = Memory.alloc(4);
    block.writeU32(player().add(0x38).readU32());
    const before = signs(man).length;
    const used = [];
    KNIGHT_SIGNS.forEach((k, i) => {
        ids.writeU32(k.npc);
        ids.writeU32(k.npc);
        let npc = k.npc;
        if (GET_CHR_BY_ENTITY(ids, 0, 0).isNull()) {
            const c = allChrs().find((chr) => chr.add(0x60).readS32() === STAND_INS[i]);
            if (c === undefined) return;
            npc = c.add(CHR_EVENT_ENTITY).readU32();
        }
        ids.writeU32(npc);
        ids.add(4).writeU32(k.region);
        PLACE_NPC_SIGN(man, 2, ids, ids.add(4), k.summonFlag, block, k.dismissFlag, 0);
        used.push(npc);
    });
    only = used;
    emit('placed-all', { ok: true, before: before, after: signs(man).length, npcs: used });
    moveNow();
}

// Warp the player to a block-local spot: TriggerAreaReload's (0x1405f36e0 -> 0x1405f3b90) own
// sequence with our destination substituted (bd
// invasion-warp-local-warp-primitive-triggerareareload-2026-08-03, addresses read out of 1.17.1 at
// 0x1405f3785..0x1405f3838, 2.7.2.0 0x1405f3c35..0x1405f3ce8). Coordinates go in raw: MoveMapStep
// converts them itself. GREAT_JAR is beside the three knights' MSB spots in m60_47_41_00.
const GREAT_JAR = { block: 0x3c2f2900, local: [-2.7, 142.3, 91.4], yaw: 0 };
const SESSION_MAN = va('0x143d81560');                 // session global 0x143d7e540 -> 0x143d81560
// 0x140cb1370 -> 0x140cb2a60
const SETUP_MAP_REENTRY = new NativeFunction(va('0x140cb2a60'), 'void', ['pointer', 'uint8']);
// 0x14067b6a0 -> 0x14067bb50
const SET_NO_ENTER_ANIM = new NativeFunction(va('0x14067bb50'), 'void', ['uint8']);
// 0x14067ba20 -> 0x14067bed0
const SET_MOVE_BLOCK = new NativeFunction(va('0x14067bed0'), 'void', ['pointer', 'pointer']);
// 0x14067b970 -> 0x14067be20; writes GameMan +0xc90, +0xca0 and sets +0xcb0 in both builds.
const SET_EXPLICIT_SPAWN = new NativeFunction(va('0x14067be20'), 'void', ['pointer', 'pointer']);
// 0x1405f89c0 -> 0x1405f8e70
const WARP_KICK = new NativeFunction(va('0x1405f8e70'), 'void', []);
const warpBuf = Memory.alloc(0x80);
function warpTo(dest) {
    const mem = warpBuf.add(0xf).and(ptr('0xfffffffffffffff0'));   // movaps needs 16-byte alignment
    const blockOut = mem;
    const blockIn = mem.add(4);
    const pos = mem.add(0x10);
    const euler = mem.add(0x20);
    blockIn.writeU32(dest.block);
    dest.local.forEach((v, i) => pos.add(i * 4).writeFloat(v));
    pos.add(12).writeFloat(1);
    [0, dest.yaw, 0, 0].forEach((v, i) => euler.add(i * 4).writeFloat(v));
    const session = SESSION_MAN.readPointer();
    if (!session.isNull() && session.add(0x10).readU32() === 6) SETUP_MAP_REENTRY(session, 1);
    SET_NO_ENTER_ANIM(1);
    SET_MOVE_BLOCK(blockOut, blockIn);
    SET_EXPLICIT_SPAWN(pos, euler);
    const gameMan = GAME_MAN.readPointer();
    const armed = gameMan.add(0xcb0).readU8();
    if (armed !== 1) return { ok: false, why: 'explicit-spawn flag not set', armed: armed };
    WARP_KICK();
    return { ok: true, requested: dest.block.toString(16), effective: blockOut.readU32().toString(16), local: dest.local };
}

// A red sign for a character from another map. The AI lab (scripts/er-ai-lab.py, summon path)
// spawns it with entity id 35000 and its model loaded; on the first frame it exists this parks it
// behind the arena wall in the knights' own holding spot, then places a red sign keyed to it at
// the arena's first NPC appearance point. Touching the sign warps it out as a red phantom (join
// tick 0x1406ff260 -> 0x1406ff710 calling WarpChr 0x1403fa5d0 -> 0x1403fa660), the way the knights
// come out. Flags are knight 1's, borrowed.
const OTHER_MAP = { entity: 35000, summonFlag: 1047412220, dismissFlag: 1047410230, block: 0x3c2f2900,
    pen: [17.0, 148.4, 153.5], sign: [-1.26, 141.24, 90.6] };
// ChangeCharacterDisableState(ChrIns*, bool), 0x1403f6500 -> 0x1403f6590
const SET_DISABLED = new NativeFunction(va('0x1403f6590'), 'void', ['pointer', 'uint8']);
// (ChrIns*, pos*, rot*), 0x1403fa5d0 -> 0x1403fa660
const WARP_CHR = new NativeFunction(va('0x1403fa660'), 'void', ['pointer', 'pointer', 'pointer']);
let otherMapDone = null;
const OTHER_MAP_ARMED = true;
function toPhysics(local) {
    const out = Memory.alloc(16);
    const inp = Memory.alloc(16);
    const block = Memory.alloc(4);
    block.writeU32(OTHER_MAP.block);
    local.forEach((v, i) => inp.add(i * 4).writeFloat(v));
    BLOCK_TO_PHYSICS(out, inp, block);
    return out;
}
function otherMapSign() {
    const ids = Memory.alloc(8);
    ids.writeU32(OTHER_MAP.entity);
    const chr = GET_CHR_BY_ENTITY(ids, 0, 0);
    if (!OTHER_MAP_ARMED || chr.isNull() || (otherMapDone !== null && otherMapDone.equals(chr))) return;
    const man = signMan();
    const p = player();
    if (man === null || p === null) return;
    otherMapDone = chr;
    // Already joined (red team) or already signed: a hot reload must not hide or re-sign him.
    const signed = signs(man).some((s) => !s.isNull() && s.add(SIGN.NPC).readU32() === OTHER_MAP.entity);
    const party = GAME_MAN.readPointer().add(0xd90).readPointer();
    let joined = false;
    for (let i = 0; !party.isNull() && i < 6; i++) if (party.add(0x28 + i * 0x30).readU64().equals(chr.add(8).readU64())) joined = true;
    if (joined) return emit('other-map', { skipped: true, joined: joined, signed: signed });
    // Hide him the way the Great Jar knights wait, with ChangeCharacterDisableState(chr, true); the
    // sign join enables him again itself (0x14050c6d0 calls it with 0 at 0x14050c74f; 2.7.2.0
    // 0x14050c950 and 0x14050c9cf), bd
    // chr-enable-setter-and-join-enable-1171-2026-10-06. No holding pen needed.
    SET_DISABLED(chr, 1);
    // The sign goes 1.5 m from where the player stands, in the player's own block.
    const at = Memory.alloc(16);
    GET_PHYSICS_POS(p, at);
    at.writeFloat(at.readFloat() + 1.5);
    ids.add(4).writeU32(0);
    const block = Memory.alloc(4);
    block.writeU32(p.add(0x38).readU32());
    const before = signs(man).length;
    if (!signed) PLACE_NPC_SIGN(man, 2, ids, ids.add(4), OTHER_MAP.summonFlag, block, OTHER_MAP.dismissFlag, 0);
    let placed = null;
    for (const s of signs(man)) {
        if (s.isNull() || s.add(SIGN.NPC).readU32() !== OTHER_MAP.entity) continue;
        for (let k = 0; k < 3; k++) s.add(SIGN.POS + k * 4).writeFloat(at.add(k * 4).readFloat());
        CREATE_SIGN_SFX(man, s);
        placed = String(s);
    }
    emit('other-map', { chr: String(chr), npcParam: chr.add(0x60).readS32(), disabled: chr.add(0x10).readPointer().add(0xa).readU8() & 1,
        loadStatus: chr.add(0x10).readPointer().add(8).readU8(), signsBefore: before, signsAfter: signs(man).length, sign: placed,
        at: [0, 4, 8].map((o) => at.add(o).readFloat()) });
}

// Put the joined OTHER_MAP character back 2 m from the player (physics space, same warp the join uses).
function otherMapToPlayer() {
    const ids = Memory.alloc(4);
    ids.writeU32(OTHER_MAP.entity);
    const chr = GET_CHR_BY_ENTITY(ids, 0, 0);
    const p = player();
    if (chr.isNull() || p === null) return { ok: false };
    const pos = Memory.alloc(16);
    GET_PHYSICS_POS(p, pos);
    pos.writeFloat(pos.readFloat() + 2);
    WARP_CHR(chr, pos, Memory.alloc(16));
    return { ok: true, physics: [0, 4, 8].map((o) => pos.add(o).readFloat()), team: chr.add(0x6c).readU8() };
}

// The six PartyMemberInfo entries as the game holds them (+0 handle, +0xc state, +0x1d restrict).
function partyTable() {
    const party = GAME_MAN.readPointer().add(0xd90).readPointer();
    if (party.isNull()) return { party: null };
    const out = [];
    for (let i = 0; i < 6; i++) {
        const e = party.add(0x28 + i * 0x30);
        out.push({ i: i, handle: e.readU64().toString(16), state: e.add(0xc).readS32(), restrict: e.add(0x1d).readU8(),
            raw: Array.from(new Uint8Array(e.readByteArray(0x30))).map((b) => b.toString(16).padStart(2, '0')).join('') });
    }
    const ids = Memory.alloc(4);
    ids.writeU32(OTHER_MAP.entity);
    const y = GET_CHR_BY_ENTITY(ids, 0, 0);
    return { entries: out, yura: y.isNull() ? null : { handle: y.add(8).readU64().toString(16), team: y.add(0x6c).readU8(), chrType: y.add(0x68).readU8() } };
}

// Native control run: leave the game's own signs where the game puts them.
const RELOCATE = false;

// What makes a natively summoned knight drawn. Each frame, read the knights' IsDrawn terms and party
// state and report every change. A write watchpoint on each knight's [chr+0x10]+0x8 byte (the term
// that stayed 0 on our custom-sign NPC) is armed on the frame-tick thread only (AGENTS.md: one
// thread, never all), and names the instruction that writes it. Read only: nothing here writes game
// memory.
const knightLast = {};
let watch = null;        // { thread, slots: [{ slot, entity, address }] }
let watchHits = 0;
const WATCH_MAX_HITS = 40;
const WATCH_ENABLED = false;   // answered: knights load at 4; a map reload frees the watched entries

function knightRow(entity) {
    const ids = Memory.alloc(4);
    ids.writeU32(entity);
    const c = GET_CHR_BY_ENTITY(ids, 0, 0);
    if (c.isNull()) return null;
    const h = c.add(0x10).readPointer();
    const handle = c.add(8).readU32();
    const party = GAME_MAN.readPointer().add(0xd90).readPointer();
    let entry = null;
    if (!party.isNull()) {
        for (let i = 0; i < 6; i++) {
            const e = party.add(0x28 + i * 0x30);
            if (e.readU32() === handle) entry = { i: i, state: e.add(0xc).readU32(), restrict: e.add(0x1d).readU8() };
        }
    }
    return { chr: c, h8addr: h.add(8), row: { h8: h.add(8).readU8(), hA: h.add(0xa).readU8(),
        b1c5: c.add(0x1c5).readU8(), b1ca: c.add(0x1ca).readU8(), team: c.add(0x6c).readU8(),
        chrType: c.add(0x68).readU8(), drawn: IS_DRAWN(c), party: entry } };
}

function threadById(id) {
    const all = Process.enumerateThreads();
    for (let i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
}

function disarmWatch() {
    if (watch === null) return;
    const t = threadById(watch.thread);
    if (t !== null) watch.slots.forEach((s) => { try { t.unsetHardwareWatchpoint(s.slot); } catch (e) {} });
    emit('watch-disarmed', { hits: watchHits });
    watch = { thread: -1, slots: [] };   // never re-arm in this agent load
}

function traceKnights(tid) {
    const rows = [];
    GREAT_JAR_KNIGHTS.forEach((entity) => {
        const k = knightRow(entity);
        if (k === null) return;
        rows.push({ entity: entity, k: k });
        const key = JSON.stringify(k.row);
        if (knightLast[entity] !== key) {
            knightLast[entity] = key;
            emit('knight', Object.assign({ entity: entity, chr: String(k.chr) }, k.row));
        }
    });
    if (watch !== null && watch.thread === tid && watchHits >= WATCH_MAX_HITS) disarmWatch();
    if (!WATCH_ENABLED || watch !== null || rows.length === 0) return;
    const t = threadById(tid);
    if (t === null) return;
    const slots = [];
    rows.forEach((r, i) => {
        try {
            t.setHardwareWatchpoint(i, r.k.h8addr, 1, 'w');
            slots.push({ slot: i, entity: r.entity, address: r.k.h8addr });
        } catch (e) { emit('watch-failed', { entity: r.entity, error: String(e) }); }
    });
    watch = { thread: tid, slots: slots };
    emit('watch-armed', { thread: tid, slots: slots.map((s) => ({ slot: s.slot, entity: s.entity, address: String(s.address) })) });
}

Process.setExceptionHandler(function (details) {
    if (watch === null || watch.thread !== Process.getCurrentThreadId() || watch.slots.length === 0) return false;
    if (details.type !== 'single-step' && details.type !== 'breakpoint') return false;
    watchHits += 1;
    const pc = details.context.pc;
    const rel = (a) => (a.compare(game.base) >= 0 && a.compare(game.base.add(game.size)) < 0) ? '0x' + a.sub(game.base).add(BASE).toString(16) : String(a);
    emit('writer', { hit: watchHits, type: details.type, pc: rel(pc),
        values: watch.slots.map((s) => ({ entity: s.entity, h8: s.address.readU8() })),
        backtrace: Thread.backtrace(details.context, Backtracer.ACCURATE).slice(0, 10).map(rel) });
    return true;
});

hooks.push(Interceptor.attach(FRAME_TICK, {
    onEnter() {
        try { traceKnights(this.threadId); } catch (e) { emit('error', { where: 'knights', error: String(e) }); }
        try { otherMapSign(); } catch (e) { emit('error', { where: 'other-map', error: String(e) }); }
        try { trackJumps(); } catch (e) { emit('error', { where: 'track', error: String(e) }); }
        try { keepSignNpcInParty(); } catch (e) { emit('error', { where: 'party', error: String(e) }); }
        if (!pending) return;
        pending = false;
        try { if (mode === 'yurahere') emit('yura-here', otherMapToPlayer()); else if (mode === 'party') emit('party-table', partyTable()); else if (mode === 'jar') emit('warp', warpTo(GREAT_JAR)); else if (mode === 'state') emit('state', signNpcState()); else if (mode === 'chrtype') emit('chrtype', setChrType(523200079, 2)); else if (mode === 'diff') emit('diff', diffJoined()); else if (mode === 'dump') emit('dump', { signs: dumpSigns() }); else if (mode === 'custom') emit('custom', placeCustom()); else if (mode === 'arm') emit('armed-ai', { chrs: armStandIns() }); else if (mode === 'origin') emit('origin', Object.assign(blockOrigin(), twoPositions())); else if (mode === 'two') emit('two', twoPositions()); else if (mode === 'where') emit('where', { chrs: whereStandIns(), signs: rpc.exports.listSigns() }); else if (mode === 'list') emit('chrs', { chrs: listChrs() }); else if (mode === 'place') placeNow(); else moveNow(); } catch (e) { emit('error', { where: mode, error: String(e) }); }
    },
}));

hooks.push(Interceptor.attach(CREATE_NPC_SIGN, {
    onEnter(args) {
        try {
            const npc = args[2].readU32();
            emit('sign-created', { npc: npc, relocated: RELOCATE && wanted(npc) });
            if (!RELOCATE || !wanted(npc)) return;
            const at = spot(createIndex++ % 3);
            if (at === null) return;
            args[4].writeFloat(at.x);
            args[4].add(4).writeFloat(at.y);
            args[4].add(8).writeFloat(at.z);
            emit('placed', { npc: npc, at: at });
        } catch (e) { emit('error', { where: 'create', error: String(e) }); }
    },
}));

rpc.exports = {
    // Move the placed signs on the next frame; ids overrides which npc signs (empty = all).
    moveSigns(ids) {
        if (Array.isArray(ids)) only = ids;
        pending = true;
        return { only: only };
    },
    listSigns() {
        const man = signMan();
        if (man === null) return [];
        return signs(man).filter((s) => !s.isNull()).map((s) => ({
            sign: String(s), npc: s.add(SIGN.NPC).readU32(),
            pos: [s.add(SIGN.POS).readFloat(), s.add(SIGN.POS + 4).readFloat(), s.add(SIGN.POS + 8).readFloat()],
        }));
    },
    dispose() {
        disarmWatch();
        hooks.concat(verdictHooks).forEach((h) => h.detach());
    },
};

emit('armed', { signMan: String(signMan()) });
