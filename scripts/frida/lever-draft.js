// Arena lever: make a geom-lab-spawned AEG027_002 ("Metal lever") pullable with the game's own
// ObjAct, and flip the AI fight on every pull. Draft, static RE only, not yet run.
//
// Paste below spawn-npc.js's `rpc.exports` block, or load it beside spawn-npc.js. Every name here
// is prefixed (LV_, lv) so a paste does not collide with spawn-npc.js's own `game`, `va`, `emit`.
// toggleFight() at the bottom flips cfg.fight through spawn-npc.js's `fight` export when it is in
// the same script; loaded on its own it emits `lever-toggle` for the host to relay.
//
// How the game makes a lever pullable (1.16.2 dump on :8765, carried to 2.7.2.0, every function
// shape-compared over its whole .pdata body with scripts/compare-fn-shape.py: `SAME`, `offsets SAME`):
//
//   An MSB Event/ObjAct (ObjActEntityID, ObjActPartIndex, ObjActID = ObjActParam row, StateType,
//   EventFlagID) becomes one 0xe0-byte entry in the block's CSWorldBlockObjAct, built at block
//   load (1.16.2 0x1405beb10 -> 2.7.2.0 0x1405bfe10; entry init 0x1405b9060 -> 0x1405ba360).
//   There is no runtime path that adds an entry: EMEVD 2005[05] Initialize ObjAct only re-inits
//   an entry that already matches (handle, param id). But nothing at runtime reads the MSB again:
//   prompt, pull and kick read only these entry fields, and the target is a field-ins handle,
//   which a spawned GeomIns has (SpawnGeometry puts it in the block's geometry tree, so
//   LookupGeomIns resolves it). So a free entry pointed at the spawned lever is a real ObjAct.
//
//   m60_42_36_00 (block 0x3c2a2400, the arena's) has such a free entry: MSB event
//   "ObjAct 回転エレベーター レバー 上側" (rotating elevator lever, upper), ObjActEntityID
//   1042363511, ObjActID 27002 (AEG027_002's own row), StateType 0, and no ObjActPartName. Its
//   init fails on part index -1 after writing the kick id, so the slot exists with no state object
//   and never prompts. No EMEVD in the corpus references 1042363511 (589 files scanned).
//
// Entry fields used (`offsets SAME` 1.16.2 -> 2.7.2.0):
//   +0x00 BlockId (WorldBlockInfo+0x34)   +0x04 index in the block array   +0x08 kick event id
//   +0x48 target field-ins handle (GeomIns+0x8)   +0x50 trigger state: 0 idle, 3 running, 4 done
//   +0x80/+0x81/+0x82 kick / change / send-net bytes   +0x88 ObjActParam id   +0x8c paramdef ver
//   +0x90 ObjActParam row   +0x98 CSObjActState*   +0xa0 initialised   +0xb9 state type
//   +0xbc sub index   +0xc0 first entry of its group (itself for a lever)
// Block (CSWorldBlockObjAct, 0xb8): +0x00 WorldBlockInfo*, +0x10 loaded, +0x78 entries, +0x80 count.
//
// What one pull does, all vanilla once the entry is live:
//   per frame, block update 0x1405c0c50: prompt check per entry (0x1405b98b0: handle resolves,
//   state object and row present, ActionButtonParam = row+0x28 = 8301 "Pull lever", 1.5 m round
//   dummy poly 192) -> nearest wins -> button press -> 0x1405c04a0 request -> 0x1405c1630
//   execute: player aligned to dummy poly 191 (1 m in front, model local -z) and plays 60200, the
//   lever plays its anim 0 (a000_000000, the only one in its anibnd), state 3 -> per-entry update
//   0x1405bb320: when the lever anim stops, eventKickTiming 0 kicks: 0x1405ba990 -> 0x1405bbae0
//   (ecx = kick id) pushes {0, id} onto CSEventMan+0x70 (EMEVD 5[02] reads it), then state 4.
//   A OneState entry stays at 4 (no prompt) until re-enabled; elevator levers get that from EMEVD
//   2005[06] state 1. Here the agent calls the entry-level re-enable 0x1405bade0 (4 -> 0).
//
// Hooks: the ObjAct block update (once per loaded ObjAct block per frame; installs the entry and
// re-arms it on the game thread), the kick (rare; the toggle), and the request (rare; mutes the
// ObjAct network packet for this entry, because a Seamless peer's copy of the slot has no state
// object and the remote execute paths 0x1405c05d0/0x1405c07b0 dereference it unchecked).
//
// Nothing the game keeps points into agent memory: the state object comes from the game's own
// factory and is freed by the entry dtor at block unload, and the row pointer is the game's. On
// unload the agent disables the entry (state 4, handle -1) so it cannot run without the network
// mute.
'use strict';

const LV = Object.assign({
    block: 0x3c2a2400,                      // m60_42_36_00
    name: /^AEG027_002_\d+$/,               // geom-lab spawn names are "%s_%04d"
    near: [-39.05, 92.39, 86.74],           // block-local, to pick one if several match
    objActParam: 27002,                     // AEG027_002's ObjActParam row (ActionButtonParam 8301)
    kickId: 1042363511,                     // the free slot's own ObjActEntityID
    rearmFrames: 45,                        // block updates after the kick before the prompt returns
    scanFrames: 30,                         // block updates between searches while not installed
}, globalThis.__ER_LEVER_CONFIG || {});

// A config from JSON carries the name pattern as a string.
const LV_NAME = LV.name instanceof RegExp ? LV.name : new RegExp(LV.name);

const LV_GAME = Process.findModuleByName('eldenring.exe');
const lvVa = (s) => LV_GAME.base.add(ptr(s).sub(ptr('0x140000000')));

// 2.7.2.0 VAs (1.16.2 in brackets). Functions shape-compared, globals read from the rip operands
// of the carried functions in eldenring-deobf-1.17.2.bin.
const LV_OBJACT_MAN = lvVa('0x143d6f3d8');         // [0x143d68348] GLOBAL_CSWorldObjActMan
const LV_WORLD_GEOM_MAN = lvVa('0x143d70c38');     // geom-lab.js's value
const LV_BLOCK_UPDATE = lvVa('0x1405c0c50');       // [0x1405bf950] (block, ctx)
const LV_KICK = lvVa('0x1405bbae0');               // [0x1405ba7e0] (kickId, DLString* err) -> bool
const LV_REQUEST_EXEC = lvVa('0x1405c04a0');       // [0x1405bf1a0] (block, index, playerHandle)
const lvStateFactory = new NativeFunction(lvVa('0x1405bd0a0'), 'pointer', ['pointer', 'int', 'uint32']); // [0x1405bbda0]
const lvParamLookup = new NativeFunction(lvVa('0x140d45410'), 'void', ['pointer', 'uint32']);          // [0x140d424d0]
const lvReenable = new NativeFunction(lvVa('0x1405bade0'), 'uint64', ['pointer']);                     // [0x1405b9ae0]
const lvLookupGeomIns = new NativeFunction(lvVa('0x1405e6030'), 'pointer', ['pointer', 'pointer']);    // [0x1405e4d30]

const OBJACT_NO_NET = 0x9f48;   // CSWorldObjActMan byte: nonzero skips the execute packet
const ENTRY_SIZE = 0xe0;
const STATE_DONE = 4;

function lvEmit(kind, fields) { send(Object.assign({ kind: kind }, fields || {})); }
function lvHex(n) { return '0x' + (n >>> 0).toString(16); }

function lvWalkMap(head, visit) {
    const stack = [];
    let node = head.add(0x8).readPointer();
    let n = 0;
    while ((stack.length > 0 || node.add(0x19).readU8() === 0) && n < 50000) {
        if (node.add(0x19).readU8() === 0) { stack.push(node); node = node.readPointer(); }
        else { node = stack.pop(); visit(node); n += 1; node = node.add(0x10).readPointer(); }
    }
}

function lvGeomBlock(blockId) {
    let found = null;
    lvWalkMap(LV_WORLD_GEOM_MAN.readPointer().add(0x20).readPointer(), (node) => {
        if ((node.add(0x20).readU32() >>> 0) === blockId) found = node.add(0x28).readPointer();
    });
    return found;
}

function lvPartName(geom) {
    try {
        const part = geom.add(0x48).readPointer();
        return part.isNull() ? null : part.readPointer().readUtf16String(64);
    } catch (e) { return null; }
}

// Spawned instances live in the block data's geometry tree (+0x318, sentinel at +0x320, node key
// +0x20 = field-ins index, value +0x28), read the same way by GetGeomInsByIndex 0x1406a6710.
function lvFindLever() {
    const data = lvGeomBlock(LV.block);
    if (data === null) return null;
    let best = null;
    lvWalkMap(data.add(0x320).readPointer(), (node) => {
        const geom = node.add(0x28).readPointer();
        const name = lvPartName(geom);
        if (name === null || !LV_NAME.test(name)) return;
        let d = 0;
        try {
            const p = geom.add(0x48).readPointer().add(0x20);
            d = Math.hypot(p.readFloat() - LV.near[0], p.add(4).readFloat() - LV.near[1], p.add(8).readFloat() - LV.near[2]);
        } catch (e) { d = 1e9; }
        if (best === null || d < best.d) best = { geom: geom, name: name, d: d };
    });
    return best;
}

// LookupGeomIns fills {static*, dynamic*}; an animated asset (behaviorType 0) lands in dynamic,
// which is the slot the lever animation call 0x1405e6ce0 needs.
function lvResolve(handle) {
    const h = Memory.alloc(8);
    h.writeU64(handle);
    const b = Memory.alloc(16);
    lvLookupGeomIns(b, h);
    return { stat: b.readPointer(), dyn: b.add(8).readPointer() };
}

const lv = { block: null, entry: null, index: -1, handle: null, rearmIn: -1, scanIn: 0, checkIn: 0, pulls: 0 };

// WorldBlockInfo +0x08 mapId and +0x34 mapId2; the request built by 0x1405c04a0 carries +0x34,
// so that is what the entry's +0x00 gets. Which of the two equals the geom block key for an m60
// tile is not measured, so either matches.
function lvBlockIds(blk) {
    try {
        const wbi = blk.readPointer();
        return [wbi.add(0x8).readU32() >>> 0, wbi.add(0x34).readU32() >>> 0];
    } catch (e) { return [0, 0]; }
}
function lvIsArena(blk) {
    const ids = lvBlockIds(blk);
    return ids[0] === LV.block || ids[1] === LV.block;
}
function lvBlockId(blk) { return lvBlockIds(blk)[1]; }

function lvFreeSlot(blk) {
    const arr = blk.add(0x78).readPointer();
    const n = blk.add(0x80).readS32();
    let free = null;
    for (let i = 0; i < n; i++) {
        const e = arr.add(i * ENTRY_SIZE);
        if (!e.add(0x98).readPointer().isNull()) continue;
        if ((e.add(0x08).readU32() >>> 0) === LV.kickId) return { entry: e, index: i };
        if (free === null && (e.add(0x48).readU32() >>> 0) === 0xffffffff) free = { entry: e, index: i };
    }
    return free;
}

function lvInstall(blk) {
    const lever = lvFindLever();
    if (lever === null) return false;
    const handle = lever.geom.add(0x8).readU64();
    const res = lvResolve(handle);
    if (res.stat.isNull() && res.dyn.isNull()) {
        lvEmit('lever-error', { why: 'handle does not resolve', name: lever.name });
        return false;
    }
    const slot = lvFreeSlot(blk);
    if (slot === null) {
        lvEmit('lever-error', { why: 'no free ObjAct slot in this block', count: blk.add(0x80).readS32() });
        return false;
    }
    const e = slot.entry;
    e.writeU32(lvBlockId(blk));
    e.add(0x04).writeS32(slot.index);
    e.add(0x08).writeU32(LV.kickId);
    e.add(0x48).writeU64(handle);
    lvParamLookup(e.add(0x88), LV.objActParam);
    const row = e.add(0x90).readPointer();
    if (row.isNull()) {
        e.add(0x48).writeU64(uint64('0xffffffffffffffff'));
        lvEmit('lever-error', { why: 'ObjActParam row missing', id: LV.objActParam });
        return false;
    }
    e.add(0x50).writeU32(0);
    e.add(0xb9).writeU8(0);
    e.add(0xbc).writeU32(0);
    e.add(0xc0).writePointer(e);
    e.add(0xa0).writeU8(1);
    // Last: a non-null state object is what makes the prompt check and the per-entry update treat
    // the slot as live. OneState (type 0) takes no event flag.
    e.add(0x98).writePointer(lvStateFactory(e, 0, 0));
    lv.block = blk; lv.entry = e; lv.index = slot.index; lv.handle = handle;
    lvEmit('lever-installed', {
        name: lever.name, geom: lever.geom.toString(), handle: handle.toString(16), dynamic: !res.dyn.isNull(),
        entry: e.toString(), index: slot.index, row: row.toString(), actionButton: row.add(0x28).readS32(),
        playerAnim: row.add(0xc).readU32(), objAnim: row.add(0x1c).readU32(), dummy: row.add(0x1a).readU8(),
    });
    return true;
}

// Still ours: same array, our state object, our handle, and the handle still resolves (geom-lab
// `restore` or a block reload removes the spawned instance).
function lvStillInstalled(blk) {
    if (lv.entry === null || !blk.equals(lv.block)) return false;
    const arr = blk.add(0x78).readPointer();
    if (!lv.entry.equals(arr.add(lv.index * ENTRY_SIZE))) return false;
    if (lv.entry.add(0x98).readPointer().isNull()) return false;
    if (!lv.entry.add(0x48).readU64().equals(lv.handle)) return false;
    return true;
}

// geom-lab `restore`, an unspawn or a respawn leaves the entry's handle naming nothing (the
// prompt check then fails quietly). Point it at the lever that stands now, unless a pull is running.
function lvRetarget() {
    const r = lvResolve(lv.handle);
    if (!r.stat.isNull() || !r.dyn.isNull()) return;
    if (lv.entry.add(0x50).readU32() === 3) return;
    const lever = lvFindLever();
    if (lever === null) return;
    const handle = lever.geom.add(0x8).readU64();
    lv.entry.add(0x48).writeU64(handle);
    lv.handle = handle;
    lvEmit('lever-retarget', { name: lever.name, handle: handle.toString(16) });
}

Interceptor.attach(LV_BLOCK_UPDATE, {
    onEnter(args) {
        const blk = args[0];
        this.ours = false;
        if (blk.add(0x10).readU8() === 0 || !lvIsArena(blk)) return;
        this.ours = true;
        try {
            if (lvStillInstalled(blk)) {
                if (lv.checkIn-- > 0) return;
                lv.checkIn = LV.scanFrames;
                lvRetarget();
                return;
            }
            if (lv.entry !== null) { lvEmit('lever-lost', {}); lv.entry = null; }
            if (lv.scanIn-- > 0) return;
            lv.scanIn = LV.scanFrames;
            lvInstall(blk);
        } catch (e) { lvEmit('lever-error', { where: 'install', error: e.message }); }
    },
    onLeave() {
        if (!this.ours || lv.entry === null) return;
        try {
            if (lv.entry.add(0x50).readU32() !== STATE_DONE) return;
            if (lv.rearmIn > 0) { lv.rearmIn -= 1; return; }
            lvReenable(lv.entry);
            lv.rearmIn = -1;
            lvEmit('lever-rearmed', { state: lv.entry.add(0x50).readU32() });
        } catch (e) { lvEmit('lever-error', { where: 'rearm', error: e.message }); }
    },
});

Interceptor.attach(LV_REQUEST_EXEC, {
    onEnter(args) {
        this.muted = false;
        if (lv.entry === null || !args[0].equals(lv.block) || args[1].toInt32() !== lv.index) return;
        const man = LV_OBJACT_MAN.readPointer();
        this.flag = man.add(OBJACT_NO_NET);
        this.was = this.flag.readU8();
        this.flag.writeU8(1);
        this.muted = true;
        lvEmit('lever-press', { entry: lv.entry.toString() });
    },
    onLeave() {
        if (this.muted) this.flag.writeU8(this.was);
    },
});

Interceptor.attach(LV_KICK, {
    onEnter(args) {
        if (lv.entry === null || (args[0].toUInt32() >>> 0) !== LV.kickId) return;
        lv.pulls += 1;
        lv.rearmIn = LV.rearmFrames;
        let dyn = null;
        try {
            const d = lvResolve(lv.handle).dyn;
            if (!d.isNull()) dyn = { anim: d.add(0x444).readS32(), playing: (d.add(0x433).readU8() >> 5) & 1 };
        } catch (e) { dyn = null; }
        const on = toggleFight();
        lvEmit('lever-pull', { pulls: lv.pulls, fight: on, leverAnim: dyn });
    },
});

// In spawn-npc.js this flips cfg.fight through its own export, which also applies it to every
// live summon at once. Anywhere else it asks the host to do that.
function toggleFight() {
    if (typeof cfg !== 'undefined' && typeof rpc.exports.fight === 'function') {
        rpc.exports.fight(cfg.fight !== true);
        return cfg.fight;
    }
    lvEmit('lever-toggle', {});
    return null;
}

// Frida calls this on unload and on a reload in place. Without the request hook a pull would send
// the ObjAct packet, so leave the slot unable to run: done state, no target.
function lvDispose() {
    if (lv.entry === null) return;
    try {
        lv.entry.add(0x50).writeU32(STATE_DONE);
        lv.entry.add(0x48).writeU64(uint64('0xffffffffffffffff'));
    } catch (e) { /* the block may already be gone */ }
}
if (typeof rpc.exports.dispose === 'function') {
    const lvPrevDispose = rpc.exports.dispose;
    rpc.exports.dispose = function () { lvDispose(); lvPrevDispose(); };
} else {
    rpc.exports.dispose = lvDispose;
}
