// Character photos from the game's own profile renderer: full body, portrait, side profile and
// three-quarter face, each with a depth-keyed background.
//
// The renderer is the `CSMenuProfModelRend` for the loaded slot (`DAT_143d71940[slot]` on 1.17),
// the same object the loading-screen portrait draws. In the world it stays alive and its native
// submit task keeps rasterizing it every frame (`oracle_build_url_portrait_draw_task_calls`), and a
// build_url import rebuilds it from the imported character (`profile-portrait: slot 0 re-rendered`
// in the autoload log). So a shot is: write the orbit camera, rebuild its view matrix and push it
// (the `er-loading-portrait-core` camera lever), wait a few frames, and copy the offscreen colour
// and depth targets out on a queue of our own (`resource_readback.rs`'s bundle chain, ported).
//
// Framing is measured, not assumed. The body shot uses the camera the loading portrait already
// tuned to fit the whole character. One more capture with the target raised gives pixels per
// metre, and the face shots are then placed by iterating on the top of the depth silhouette.
//
// Driven by `scripts/er-portrait-batch.py`, which loads it with a config (`outdir`, `outfit`,
// `settle_s`) and calls `rpc.exports.run`; `scripts/er-portrait-shots-png.py` turns the raw
// targets it writes into PNG and JPEG.
'use strict';

const cfg = globalThis.__ER_FRIDA_CONFIG || {};
const SLOT = cfg.slot === undefined ? 0 : cfg.slot;
const OUTDIR = cfg.outdir;
const SETTLE_S = cfg.settle_s || 0.4;
const STREAM_S = cfg.stream_s === undefined ? 3 : cfg.stream_s;

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
// 2.7.2.0 addresses, carried from 1.17.1 with scripts/map-rvas-1171-to-1172.py and checked against
// eldenring-deobf-1.17.2.bin. The renderer table 0x143d71940 -> 0x143d74960 has the same 8 readers
// in both builds; the vtable 0x142b831d8 -> 0x142b852c8 still carries the RTTI name
// `CSMenuProfModelRend`. The view-matrix builder 0x140bbfad0 -> 0x140bbff90 and the camera push
// 0x140bbbb80 -> 0x140bbc040 are byte-identical apart from relocated operands.
const RENDERER_TABLE = va('0x143d74960');
const RENDERER_VTABLE = va('0x142b852c8');
const BUILD_MATRIX = new NativeFunction(va('0x140bbff90'), 'pointer', ['pointer', 'pointer']);
const PUSH_CAMERA = new NativeFunction(va('0x140bbc040'), 'void', ['pointer', 'pointer']);

// The renderer offsets below read unchanged in 2.7.2.0 game code, each in a function that is
// byte-identical across the two builds (1.17.1 -> 2.7.2.0): +0xa8 in the camera push; +0x9b4..+0x9cc,
// +0x9d0, +0x9e0 and +0xa20 in the orbit-camera setter 0x140bbf7e0 -> 0x140bbfca0; +0x548, +0x33c,
// +0x130, +0x754 and +0x778 in 0x140bb9740 -> 0x140bb9c00 and the step functions; +0x40 in
// `SetNextStep` (vtable +0x20) 0x140bbbcc0 -> 0x140bbc180; step 6 is still `STEP_Wait_Play` in the
// step table filled by 0x1400a75c0 (unmoved); `ChrAsm+0x7c` in the protector accessor 0x1403be960
// (unmoved).

const R_OFFSCREEN = 0xa8;
const R_TARGET = 0x9b4;
const R_DISTANCE = 0x9c4;
const R_YAW = 0x9c8;
const R_PITCH = 0x9cc;
const R_PERSCAM = 0x9d0;
const R_VIEW = 0x9e0;
const R_FOV = 0xa20;

// The renderer dresses the model from its own `ChrAsm` stages -- +0x548 the inbox, +0x33c the
// staged copy, +0x130 the live one (`er_loading_portrait_core::chr_asm_layout`) -- and the four
// protectors are entries 12..15 (head, body, arms, legs) of the param id array at `ChrAsm+0x7c`.
// Row 10000 + 100 * slot is the bare default (`CS::ChrAsm::GetDefaultProtectorParamId`). Arming
// +0x755 and +0x754 together walks the step machine through a teardown and a rebuild from those
// stages; either byte alone does not (`er_profile_summary_core::portrait_refresh`).
//
// Two uses. `cfg.outfit`, four `EquipParamProtector` rows (-1 for none), dresses the model in an
// NPC's own armour, which the build import cannot grant when the planner has no entry for it --
// the renderer draws any row with a model. And every shot but the full body is of the face, so
// the head comes off for them.
const BARE_HEAD_FACES = cfg.bare_head_faces === undefined ? true : cfg.bare_head_faces;
const OUTFIT = Array.isArray(cfg.outfit) && cfg.outfit.length === 4 ? cfg.outfit : null;
const R_CHR_ASM_STAGES = [0x548, 0x33c, 0x130];
const CHR_ASM_PROTECTORS = 0x7c + 12 * 4;
const BARE_HEAD = 10000;
const bareProtector = (slot) => 10000 + 100 * slot;
const R_STEP = 0x40;
const R_REQ_SETUP = 0x754;
const R_REQ_TEARDOWN = 0x755;
const R_MODEL_INS = 0x778;
const STEP_WAIT_PLAY = 6;

function say(tag, fields) {
    send(Object.assign({ family: 'portrait-shots', tag }, fields || {}));
}
function rp(p) {
    try { return p.readPointer(); } catch (e) { return null; }
}
function plausible(p) {
    return p !== null && p.compare(ptr('0x10000')) > 0 && p.compare(ptr('0x800000000000')) < 0;
}

// --- COM -----------------------------------------------------------------------------------------
function vcall(obj, index, ret, args) {
    const fn = obj.readPointer().add(index * 8).readPointer();
    return new NativeFunction(fn, ret, ['pointer'].concat(args));
}
function guid(text) {
    const h = text.replace(/-/g, '');
    const b = Memory.alloc(16);
    b.writeU32(parseInt(h.slice(0, 8), 16));
    b.add(4).writeU16(parseInt(h.slice(8, 12), 16));
    b.add(6).writeU16(parseInt(h.slice(12, 16), 16));
    for (let i = 0; i < 8; i++) b.add(8 + i).writeU8(parseInt(h.slice(16 + 2 * i, 18 + 2 * i), 16));
    return b;
}
const IID_RESOURCE = guid('696442be-a72e-4059-bc79-5b5c98040fad');
const IID_DEVICE = guid('189819f1-1db6-4b57-be54-1821339b85f7');
const IID_QUEUE = guid('0ec870a6-5d7e-4c22-8cfc-5baae07616ed');
const IID_ALLOCATOR = guid('6102dee4-af59-4b09-b999-b44d73f09b24');
const IID_LIST = guid('5b160d0f-ac1b-4185-8ba8-b3ae42a5a455');
const IID_FENCE = guid('0a753dcf-c4d8-4b91-adf6-be5a60d95a76');
const release = (o) => vcall(o, 2, 'uint32', [])(o);

function outPtr(fn) {
    const slot = Memory.alloc(8);
    const hr = fn(slot);
    if (hr !== 0) throw new Error('HRESULT 0x' + (hr >>> 0).toString(16));
    return slot.readPointer();
}

const D3D_MODULES = ['d3d12core.dll', 'd3d12.dll', 'dxgi.dll']
    .map((n) => Process.findModuleByName(n)).filter((m) => m !== null);
function inModule(p, mods, skipHeader) {
    return mods.some((m) => p.compare(m.base.add(skipHeader ? 0x1000 : 0)) >= 0 && p.compare(m.base.add(m.size)) < 0);
}
function d3dVtable(vt) {
    if (!inModule(vt, D3D_MODULES, true)) return false;
    const qi = rp(vt);
    return qi !== null && inModule(qi, D3D_MODULES, true);
}

const COLOR_FORMATS = [28, 29, 87, 91];
const DEPTH_FORMATS = [19, 20, 21, 44, 45, 39, 40, 53, 55];

function getDesc(res) {
    const d = Memory.alloc(64);
    vcall(res, 10, 'pointer', ['pointer'])(res, d);
    return {
        buf: d,
        dimension: d.readU32(),
        width: d.add(16).readU64().toNumber(),
        height: d.add(24).readU32(),
        format: d.add(32).readU32(),
    };
}
function tryTexture(p, wantDepth) {
    let res;
    try {
        res = outPtr((slot) => vcall(p, 0, 'int32', ['pointer', 'pointer'])(p, IID_RESOURCE, slot));
    } catch (e) { return null; }
    const desc = getDesc(res);
    const formats = wantDepth ? DEPTH_FORMATS : COLOR_FORMATS;
    if (desc.dimension === 3 && formats.includes(desc.format) && desc.width >= 8 && desc.height >= 8
        && desc.width <= 16384 && desc.height <= 16384) {
        return { res, desc, area: desc.width * desc.height };
    }
    release(res);
    return null;
}
// Bounded walk of the game's wrapper nest from a view object to the largest texture of the asked
// kind, the same shape `find_d3d12_resource_ex` walks.
function findTexture(start, wantDepth) {
    const visited = new Set();
    const queue = [[start, 0]];
    let budget = 0;
    let best = null;
    while (queue.length && budget < 256) {
        const [obj, depth] = queue.pop();
        budget++;
        if (visited.has(obj.toString())) continue;
        visited.add(obj.toString());
        for (let off = 0; off < 0x60; off += 8) {
            const v = rp(obj.add(off));
            if (!plausible(v)) continue;
            const vt = rp(v);
            if (vt === null) continue;
            if (d3dVtable(vt)) {
                const t = tryTexture(v, wantDepth);
                if (t === null) continue;
                if (best === null || t.area > best.area) {
                    if (best !== null) release(best.res);
                    best = t;
                } else {
                    release(t.res);
                }
            } else if (depth < 6 && inModule(vt, [game], false)) {
                queue.push([v, depth + 1]);
            }
        }
    }
    return best;
}

// --- renderer --------------------------------------------------------------------------------------
function renderer() {
    const r = rp(RENDERER_TABLE.add(SLOT * 8));
    if (!plausible(r) || !rp(r).equals(RENDERER_VTABLE)) throw new Error('no profile renderer in slot ' + SLOT);
    return r;
}
// Offscreen +0x48 and context +0x248 appear unchanged in the offscreen draw 0x140bb8a70 ->
// 0x140bb8f30 (byte-identical). Facade +0x38 and bundle +0x30, +0x40 and +0x548 have no 2.7.2.0
// witness yet. The chain is only read, and every texture it yields is checked for a D3D12 vtable
// and a known format, so a moved field shows up as one of the errors below or in `capture`.
function targetBundle(r) {
    const off = rp(r.add(R_OFFSCREEN));
    const facade = off && rp(off.add(0x48));
    const ctx = facade && rp(facade.add(0x38));
    if (!plausible(ctx)) throw new Error('offscreen scene chain is not live');
    const bundle = ctx.add(0x248);
    if (bundle.add(0x548).readU8() !== 0) throw new Error('target bundle redirects to the global target');
    return bundle;
}
function readOrbit(r) {
    return {
        target: [0, 4, 8].map((o) => r.add(R_TARGET + o).readFloat()),
        distance: r.add(R_DISTANCE).readFloat(),
        yaw: r.add(R_YAW).readFloat(),
        pitch: r.add(R_PITCH).readFloat(),
        fov: r.add(R_FOV).readFloat(),
    };
}
function applyOrbit(r, o) {
    [0, 4, 8].forEach((off, i) => r.add(R_TARGET + off).writeFloat(o.target[i]));
    r.add(R_DISTANCE).writeFloat(o.distance);
    r.add(R_YAW).writeFloat(o.yaw);
    r.add(R_PITCH).writeFloat(o.pitch);
    r.add(R_FOV).writeFloat(o.fov);
    BUILD_MATRIX(r, r.add(R_VIEW));
    PUSH_CAMERA(r, r.add(R_PERSCAM));
}

// --- readback ----------------------------------------------------------------------------------------
let gpu = null;
function gpuObjects(device) {
    if (gpu !== null) return gpu;
    const qdesc = Memory.alloc(16);   // DIRECT, priority 0, no flags, node 0
    const queue = outPtr((s) => vcall(device, 8, 'int32', ['pointer', 'pointer', 'pointer'])(device, qdesc, IID_QUEUE, s));
    const alloc = outPtr((s) => vcall(device, 9, 'int32', ['uint32', 'pointer', 'pointer'])(device, 0, IID_ALLOCATOR, s));
    const list = outPtr((s) => vcall(device, 12, 'int32', ['uint32', 'uint32', 'pointer', 'pointer', 'pointer', 'pointer'])(
        device, 0, 0, alloc, NULL, IID_LIST, s));
    vcall(list, 9, 'int32', [])(list);
    const fence = outPtr((s) => vcall(device, 36, 'int32', ['uint64', 'uint32', 'pointer', 'pointer'])(device, uint64(0), 0, IID_FENCE, s));
    gpu = { device, queue, alloc, list, fence, value: 0 };
    return gpu;
}
function readbackBuffer(device, bytes) {
    const heap = Memory.alloc(20);
    heap.writeU32(3);              // READBACK
    heap.add(12).writeU32(1);
    heap.add(16).writeU32(1);
    const d = Memory.alloc(56);
    d.writeU32(1);                 // BUFFER
    d.add(16).writeU64(uint64(bytes));
    d.add(24).writeU32(1);
    d.add(28).writeU16(1);
    d.add(30).writeU16(1);
    d.add(36).writeU32(1);         // SampleDesc.Count
    d.add(44).writeU32(1);         // ROW_MAJOR
    return outPtr((s) => vcall(device, 27, 'int32', ['pointer', 'uint32', 'pointer', 'uint32', 'pointer', 'pointer', 'pointer'])(
        device, heap, 0, d, 0x400, NULL, IID_RESOURCE, s));
}
function barrier(list, res, before, after) {
    const b = Memory.alloc(32);
    b.add(8).writePointer(res);
    b.add(16).writeU32(0xffffffff);
    b.add(20).writeU32(before);
    b.add(24).writeU32(after);
    vcall(list, 26, 'void', ['uint32', 'pointer'])(list, 1, b);
}
// Copy each texture's subresource 0 into a fresh readback buffer on one list and one fence, so
// colour and depth are the same frame. Returns the bytes and the row pitch of each.
function readTextures(textures) {
    const device = outPtr((s) => vcall(textures[0].res, 7, 'int32', ['pointer', 'pointer'])(textures[0].res, IID_DEVICE, s));
    const g = gpuObjects(device);
    if (vcall(g.alloc, 8, 'int32', [])(g.alloc) !== 0) throw new Error('allocator reset failed');
    if (vcall(g.list, 10, 'int32', ['pointer', 'pointer'])(g.list, g.alloc, NULL) !== 0) throw new Error('list reset failed');
    const jobs = textures.map((t) => {
        const foot = Memory.alloc(32);
        const total = Memory.alloc(8);
        vcall(device, 38, 'void', ['pointer', 'uint32', 'uint32', 'uint64', 'pointer', 'pointer', 'pointer', 'pointer'])(
            device, t.desc.buf, 0, 1, uint64(0), foot, NULL, NULL, total);
        const bytes = total.readU64().toNumber();
        const buf = readbackBuffer(device, bytes);
        barrier(g.list, t.res, 0, 0x800);
        const src = Memory.alloc(48);
        src.writePointer(t.res);
        src.add(8).writeU32(0);    // SUBRESOURCE_INDEX, index 0
        const dst = Memory.alloc(48);
        dst.writePointer(buf);
        dst.add(8).writeU32(1);    // PLACED_FOOTPRINT
        Memory.copy(dst.add(16), foot, 32);
        vcall(g.list, 16, 'void', ['pointer', 'uint32', 'uint32', 'uint32', 'pointer', 'pointer'])(g.list, dst, 0, 0, 0, src, NULL);
        barrier(g.list, t.res, 0x800, 0);
        return { buf, bytes, rowPitch: foot.add(24).readU32() };
    });
    vcall(g.list, 9, 'int32', [])(g.list);
    const lists = Memory.alloc(8);
    lists.writePointer(g.list);
    vcall(g.queue, 10, 'void', ['uint32', 'pointer'])(g.queue, 1, lists);
    g.value += 1;
    vcall(g.queue, 14, 'int32', ['pointer', 'uint64'])(g.queue, g.fence, uint64(g.value));
    const completed = vcall(g.fence, 8, 'uint64', []);
    for (let i = 0; i < 200 && completed(g.fence).toNumber() < g.value; i++) Thread.sleep(0.01);
    if (completed(g.fence).toNumber() < g.value) throw new Error('readback fence never completed');
    return jobs.map((j) => {
        const data = outPtr((s) => vcall(j.buf, 8, 'int32', ['uint32', 'pointer', 'pointer'])(j.buf, 0, NULL, s));
        const bytes = data.readByteArray(j.bytes);
        vcall(j.buf, 9, 'void', ['uint32', 'pointer'])(j.buf, 0, NULL);
        release(j.buf);
        return { bytes, rowPitch: j.rowPitch };
    });
}

// The character's silhouette from depth: every texel that differs from the cleared value in the
// corner. Compared as raw 32-bit words, so the depth format does not matter.
function silhouette(depth, width, height) {
    const words = new Uint32Array(depth.bytes);
    const pitch = depth.rowPitch / 4;
    const bg = words[0];
    let top = -1, bottom = -1, left = width, right = -1;
    for (let y = 0; y < height; y += 2) {
        const row = y * pitch;
        for (let x = 0; x < width; x += 2) {
            if (words[row + x] !== bg) {
                if (top < 0) top = y;
                bottom = y;
                if (x < left) left = x;
                if (x > right) right = x;
            }
        }
    }
    return top < 0 ? null : { top, bottom, left, right };
}

// Widest silhouette row between 0.22 m and 0.40 m below the top of the head, where the shoulders
// are on a standing human, read straight from the depth words.
function shoulderWidth(shot, pxPerM) {
    const words = new Uint32Array(shot.depth.bytes);
    const pitch = shot.depth.rowPitch / 4;
    const bg = words[0];
    let widest = 0;
    const from = Math.round(shot.box.top + 0.22 * pxPerM);
    const to = Math.min(Math.round(shot.box.top + 0.40 * pxPerM), shot.height - 1);
    for (let y = from; y <= to; y += 4) {
        let left = -1, right = -1;
        for (let x = 0; x < shot.width; x += 2) {
            if (words[y * pitch + x] !== bg) {
                if (left < 0) left = x;
                right = x;
            }
        }
        if (left >= 0 && right - left > widest) widest = right - left;
    }
    return widest;
}

function capture(r) {
    const bundle = targetBundle(r);
    const color = findTexture(bundle.add(0x30).readPointer(), false);
    const depth = findTexture(bundle.add(0x40).readPointer(), true);
    if (color === null || depth === null) throw new Error('render targets not found: color=' + !!color + ' depth=' + !!depth);
    try {
        const [c, d] = readTextures([color, depth]);
        const w = color.desc.width, h = color.desc.height;
        return { color: c, depth: d, width: w, height: h, format: color.desc.format, depthFormat: depth.desc.format, box: silhouette(d, w, h) };
    } finally {
        release(color.res);
        release(depth.res);
    }
}
function shoot(r, orbit) {
    applyOrbit(r, orbit);
    Thread.sleep(SETTLE_S);
    return capture(r);
}
function save(name, shot, orbit, pxPerM) {
    const stem = OUTDIR + '\\' + name;
    const write = (path, bytes) => { const f = new File(path, 'wb'); f.write(bytes); f.close(); };
    write(stem + '.color.bin', shot.color.bytes);
    write(stem + '.depth.bin', shot.depth.bytes);
    write(stem + '.json', JSON.stringify({
        width: shot.width, height: shot.height, format: shot.format, depth_format: shot.depthFormat,
        color_row_pitch: shot.color.rowPitch, depth_row_pitch: shot.depth.rowPitch, box: shot.box, orbit, px_per_m: pxPerM,
    }));
    say('saved', { name, box: shot.box, orbit });
}

// The four protector rows each stage holds, as [stage][slot].
function protectorIds(r) {
    return R_CHR_ASM_STAGES.map((o) => [0, 1, 2, 3].map((s) => r.add(o + CHR_ASM_PROTECTORS + 4 * s).readS32()));
}
// Dress every stage from `perStage` ([stage][slot]) and wait for the model to be torn down and
// rebuilt: the model instance pointer goes absent or changes, and the machine is back in
// `STEP_Wait_Play`.
function redress(r, perStage) {
    const worn = protectorIds(r);
    if (worn.every((stage, i) => stage.every((id, s) => id === perStage[i][s]))) {
        say('redressed', { protectors: worn[2], unchanged: true });
        return;
    }
    R_CHR_ASM_STAGES.forEach((o, i) => perStage[i].forEach((id, s) => r.add(o + CHR_ASM_PROTECTORS + 4 * s).writeS32(id)));
    const before = rp(r.add(R_MODEL_INS));
    const ask = () => {
        r.add(R_REQ_TEARDOWN).writeU8(1);
        r.add(R_REQ_SETUP).writeU8(1);
    };
    ask();
    // Any one of three witnesses that the rebuild ran: the model went absent, came back at another
    // address, or the step machine left `STEP_Wait_Play`. The allocator can hand the rebuilt model
    // the address it had before, so the pointer alone missed one (Knight Bernahl, 2026-10-13).
    let torn = false;
    let walked = false;
    for (let i = 0; i < 500; i++) {
        Thread.sleep(0.02);
        // A request that nothing answered within 3 s is asked again: twice on 2026-10-13 the
        // first one went unanswered and the same capture retried a moment later succeeded.
        if (i % 150 === 149 && !torn && !walked) ask();
        const step = r.add(R_STEP).readS32();
        if (step !== STEP_WAIT_PLAY) walked = true;
        const model = rp(r.add(R_MODEL_INS));
        if (model === null || model.isNull()) { torn = true; continue; }
        if ((torn || walked || !model.equals(before)) && step === STEP_WAIT_PLAY) {
            // Parts stream in after the instance exists.
            Thread.sleep(0.8);
            const live = protectorIds(r)[2];
            if (live.some((id, s) => id !== perStage[2][s])) {
                throw new Error('rebuilt wearing ' + live + ', asked for ' + perStage[2]);
            }
            say('redressed', { protectors: live, model: rp(r.add(R_MODEL_INS)) });
            return;
        }
    }
    throw new Error('the portrait model was not rebuilt within 10 s (step ' + r.add(R_STEP).readS32() + ')');
}

// Place a face shot: raise or lower the target until the top of the head sits at `topFrac` of
// the frame height.
function framed(r, base, pxPerM, topFrac) {
    let orbit = base;
    let shot = null;
    for (let i = 0; i < 4; i++) {
        shot = shoot(r, orbit);
        // Right after a boot a frame can come back empty even once the camera answers (measured
        // 2026-10-13, Blackguard); an empty frame is observable, so it is shot again.
        for (let again = 0; shot.box === null && again < 10; again++) {
            Thread.sleep(1);
            shot = shoot(r, orbit);
        }
        if (shot.box === null) throw new Error('character left the frame while framing');
        const miss = topFrac * shot.height - shot.box.top;
        say('framing', { pass: i, top: shot.box.top, miss });
        if (Math.abs(miss) < 0.01 * shot.height) break;
        orbit = Object.assign({}, orbit, { target: [orbit.target[0], orbit.target[1] + miss / pxPerM, orbit.target[2]] });
    }
    return { shot, orbit };
}

function main() {
    if (!OUTDIR) throw new Error('config.outdir is required');
    const r = renderer();
    const home = readOrbit(r);
    // What the model wore before this agent dressed it, to put back; null while untouched.
    let original = null;
    let headBare = false;
    say('renderer', { renderer: r, orbit: home, d3d_modules: D3D_MODULES.map((m) => m.name) });
    try {
        // The outfit first, before anything is measured: armour changes the silhouette.
        if (OUTFIT !== null) {
            original = protectorIds(r);
            const wanted = OUTFIT.map((id, s) => (id > 0 ? id : bareProtector(s)));
            redress(r, original.map(() => wanted.slice()));
        }
        // Textures for the gear just put on stream in after the model exists, and the game
        // signals nothing when one becomes resident, so this one wait is a duration.
        Thread.sleep(STREAM_S);
        // Pixels per metre at the loading portrait's own distance: raise the target and see how
        // far the top of the head moves.
        // For a while after a boot the renderer draws but ignores the camera (measured
        // 2026-10-13: the head did not move with the target 6 s after the boot import, and did
        // 17 s after). That is observable, so it is waited out here: calibrate until the head
        // moves, up to 30 s.
        const RAISE = 0.25;
        let first = null;
        let pxPerMHome = 0;
        for (let attempt = 0; attempt < 15 && !(pxPerMHome > 1); attempt++) {
            if (attempt > 0) {
                say('calibration_retry', { attempt });
                Thread.sleep(2);
            }
            first = shoot(r, home);
            if (first.box === null) throw new Error('nothing drawn at the loading-portrait camera');
            const raised = shoot(r, Object.assign({}, home, { target: [home.target[0], home.target[1] + RAISE, home.target[2]] }));
            pxPerMHome = raised.box === null ? 0 : (raised.box.top - first.box.top) / RAISE;
        }
        if (!(pxPerMHome > 1)) throw new Error('calibration failed: the head did not move with the target in 30 s');
        const H = first.height;
        // The camera looks at its target, so the target projects to mid-frame and the head top's
        // height follows from its row. The model stands on y = 0, so that is also its height.
        const headTopY = home.target[1] - (first.box.top - H / 2) / pxPerMHome;
        say('calibrated', { px_per_m: pxPerMHome, head_top_y: headTopY });

        // The front, measured: the loading portrait's yaw carries the game's own stylistic turn,
        // so sweep yaw at head-and-shoulders scale and take the widest shoulders. The width peaks
        // where the shoulder line faces the camera, and a parabola through the best sample and its
        // neighbours places the peak between samples.
        const sweepPx = 0.80 * H / 0.65;
        const sweepBase = Object.assign({}, home, {
            distance: home.distance * pxPerMHome / sweepPx,
            target: [home.target[0], headTopY + (0.10 * H - H / 2) / sweepPx, home.target[2]],
        });
        const STEP = 0.15;
        const widths = [];
        for (let k = -4; k <= 4; k++) {
            const s = shoot(r, Object.assign({}, sweepBase, { yaw: home.yaw + k * STEP }));
            widths.push(s.box === null ? 0 : shoulderWidth(s, sweepPx));
        }
        const best = widths.indexOf(Math.max(...widths));
        let peak = best - 4;
        if (best > 0 && best < widths.length - 1) {
            const [a, b, c] = [widths[best - 1], widths[best], widths[best + 1]];
            const denom = a - 2 * b + c;
            if (denom !== 0) peak += 0.5 * (a - c) / denom;
        }
        const frontYaw = home.yaw + peak * STEP;
        say('front', { widths, front_yaw: frontYaw, offset_from_home: frontYaw - home.yaw });

        // Body: the whole figure fills 88% of the frame. Head-and-shoulders: 0.65 m fills 80%.
        // Face shots: a 0.27 m head fills 45%.
        const plans = [
            { name: 'body', span: headTopY, fill: 0.88, top: 0.06, yaw: 0, whole: true },
            { name: 'portrait', span: 0.65, fill: 0.80, top: 0.10, yaw: 0 },
            // The renderer's light falls on the side this turn shows (user's call on 2026-10-13).
            { name: 'face_three_quarter', span: 0.27, fill: 0.45, top: 0.27, yaw: -Math.PI / 4 },
            { name: 'face_profile', span: 0.27, fill: 0.45, top: 0.27, yaw: Math.PI / 2 },
        ];
        for (const p of plans) {
            // Only the full-body shot shows the character as equipped; every closer shot is of
            // the face (user's call on 2026-10-13), so the helmet comes off for all of them.
            if (!p.whole && BARE_HEAD_FACES && !headBare) {
                headBare = true;
                const worn = protectorIds(r);
                if (worn[2][0] !== BARE_HEAD) {
                    if (original === null) original = worn;
                    say('helmet_off', { head: worn[2][0] });
                    redress(r, worn.map((stage) => [BARE_HEAD].concat(stage.slice(1))));
                }
            }
            let pxPerM = p.fill * H / p.span;
            for (let widen = 0; ; widen++) {
                const distance = home.distance * pxPerMHome / pxPerM;
                const guessY = headTopY + (p.top * H - H / 2) / pxPerM;
                const base = Object.assign({}, home, { distance, yaw: frontYaw + p.yaw, target: [home.target[0], guessY, home.target[2]] });
                const { shot, orbit } = framed(r, base, pxPerM, p.top);
                // A whole-body shot whose silhouette reaches the bottom edge has lost the feet.
                if (p.whole && shot.box.bottom >= H - 3 && widen < 4) {
                    say('widening', { name: p.name, bottom: shot.box.bottom });
                    pxPerM /= 1.12;
                    continue;
                }
                save(p.name, shot, orbit, pxPerM);
                break;
            }
        }
    } finally {
        if (original !== null) redress(r, original);
        applyOrbit(r, home);
        say('restored', { orbit: readOrbit(r) });
    }
    say('done', {});
}

// Started by the caller once the script has loaded (`script.exports_sync.run()`), not from the
// load itself, so the load returns at once and the caller reads the messages as they arrive.
rpc.exports = {
    run() {
        try { main(); } catch (e) { say('error', { message: e.message, stack: e.stack }); }
    },
};
