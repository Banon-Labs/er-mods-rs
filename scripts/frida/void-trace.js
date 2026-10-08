// Void tech: does a jump attack that lands during its hit window get a second, fresh attack?
//
// Each TimeAct attack event (type 1) goes through the damage module's create-or-reuse function
// with an attack slot (the event's Args[1]) and a behavior id. While that slot still holds an
// attack with the same behavior id, the attack is reused and keeps its hit list. The module's
// per-frame sweep deletes every attack whose slot no TimeAct event refreshed during the previous
// frame, and the next event then creates a new attack with an empty hit list, which can hit the same
// target again. The in-air and landed jump clips use the same slot and behavior id (straight sword
// R1: slot 2, judge 150, hit frames 11-15 in both), so a handover with no gap is one attack. This
// trace shows, per frame, which happens at the landing.
//
// Per event, all for the main player only:
//   frame    the player's damage-module sweep (one per game frame), numbered from attach
//   attack   create-or-reuse call: slot, behavior id, and whether a new attack was created
//   release  an attack the sweep or a re-create deleted
//   hit      CalculateDamage2 with the player as dealer: attack id and damage
//
// Run with the watcher, never a plain frida.attach (AGENTS.md):
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/void-trace.js
//
// Addresses are 1.17.1, all below the 0xafefe9 shift boundary, mapped from the named 1.16.2 dump
// with scripts/map-rvas-1162-to-1170.py (create-or-reuse read back on the 1.17 dump, :8767):
//   attack create-or-reuse  1.16.2 0x1404428f0 -> 0x140442e50  (CSChrDamageModule*, out, behaviorId,
//                           dmgType, slot, ...)
//   per-frame sweep         1.16.2 0x140445d30 -> 0x140446290  (CSChrDamageModule*)
//   DmgMan create attack    1.16.2 0x140526430 -> 0x140527230  returns the new attack handle
//   DmgMan release attack   1.16.2 0x140527140 -> 0x140527f40  (DmgMan*, handle)
//   CalculateDamage2        1.16.2 0x1404483b0 -> 0x140448910  (as chainsaw-probe.js)
// CSChrModuleBase keeps its owning ChrIns at +0x8 (GetChrOwner 1.16.2 0x14043ccf0).
//
// Uses only Interceptor (no watchpoints), so a detach or reload leaves nothing behind.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const CREATE_OR_REUSE = va('0x140442e50');
const SWEEP = va('0x140446290');
const DMGMAN_CREATE = va('0x140527230');
const DMGMAN_RELEASE = va('0x140527f40');
const CALC_DAMAGE2 = va('0x140448910');
const WORLD_CHR_MAN = game.base.add(0x3d69ff8);
const MODULE_OWNER = 0x8;

function mainPlayer() {
    try {
        const wcm = WORLD_CHR_MAN.readPointer();
        return wcm.isNull() ? null : wcm.add(0x1e508).readPointer();
    } catch (e) {
        return null;
    }
}

function ownedByPlayer(module) {
    const player = mainPlayer();
    if (player === null) return false;
    try {
        return module.add(MODULE_OWNER).readPointer().equals(player);
    } catch (e) {
        return false;
    }
}

function rd(p, off) {
    try {
        return p.add(off).readS32();
    } catch (e) {
        return null;
    }
}

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now(), frame: frame }, fields));
}

let frame = 0;
// Handles of attacks created for the player, so a release can be attributed to them.
const playerHandles = {};
// Set while a player create-or-reuse call is on this thread's stack.
const inCall = {};
const hitCtx = {};
const hooks = [];

// A watched character's FP, sampled once a frame, logged when it changes. A spell charges FP once
// per cast, so two drops inside one jump are two casts. Set with the setWatch RPC (the ChrIns of
// the spawned hostile); FP is CSChrDataModule +0x148 (ChrIns +0x190 -> [0]).
let watched = null;
let watchedFp = null;
let watchedAnim = null;
let watchedReq = null;
let watchedY = null;
// SpEffect apply as action-script.js calls it: (target, id, source, pos, correction, 0, 0, 0).
const APPLY_SPEFFECT_FROM = new NativeFunction(va('0x1403fb010'), 'uint8', ['pointer', 'uint32', 'pointer', 'pointer', 'pointer', 'uint8', 'uint8', 'uint8']);
const SP_POS = Memory.alloc(16);
const SP_CORR = Memory.alloc(0x60);
const BALDACHIN = 503361;
const INJECT_AT = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];
// The bits pressed: R1 (bit 0) for a weapon; R1 + MAGIC_R (bit 19) for a cast, as NPC_ATK_R1 with a
// seal in the right hand requested it (measured 0x80001). Set with the setInjectMask RPC.
let injectMask = uint64(0x80001);
const INJECT_HOLD = 2;
let injectEnabled = true;
let injectN = 0;
let injectFrame = null;
let pendingTakeoff = null;

function fpOf(chr) {
    try { return chr.add(0x190).readPointer().readPointer().add(0x148).readS32(); } catch (e) { return null; }
}

hooks.push(Interceptor.attach(SWEEP, {
    onEnter(args) {
        if (!ownedByPlayer(args[0])) return;
        frame++;
        // Radiant Baldachin's Blessing on the player every 10 s (it lasts 70): poise damage taken
        // x0.55 and enemy physical damage x0.65, so the test hostile's hits neither stagger nor kill.
        if (frame % 600 === 1) {
            const p = mainPlayer();
            if (p !== null) {
                try { APPLY_SPEFFECT_FROM(p, BALDACHIN, p, SP_POS, SP_CORR, 0, 0, 0); } catch (e) { emit('error', { error: 'baldachin: ' + e.message }); }
            }
        }
        if (watched === null) return;
        // Infinite stamina for the watched character, so a jump or cast is never late or refused for
        // want of it: stamina (+0x154) set to max stamina (+0x158) every frame.
        try {
            const data = watched.add(0x190).readPointer().readPointer();
            data.add(0x154).writeS32(data.add(0x158).readS32());
        } catch (e) { /* the character is gone */ }
        // The watched character's playing animation (CSChrTimeActModule, modules +0x18: anim queue at
        // +0x20 of 16-byte entries, read index at +0xc4), logged when it changes.
        try {
            const tam = watched.add(0x190).readPointer().add(0x18).readPointer();
            const anim = tam.add(0x20 + 16 * (tam.add(0xc4).readU32() % 10)).readS32();
            if (anim !== watchedAnim) { watchedAnim = anim; emit('anim', { anim: anim }); }
        } catch (e) { /* the character is gone */ }
        // Its raw action requests (CSChrActionRequestModule, modules +0x80, bits at +0x10: 0 R1,
        // 5 dodge, 6 jump, 17 rolling, 19/20 magic), logged when they change, so every input the AI
        // made is on record.
        try {
            const req = watched.add(0x190).readPointer().add(0x80).readPointer().add(0x10).readU64().toString(16);
            if (req !== watchedReq) { watchedReq = req; emit('req', { bits: req }); }
        } catch (e) { /* the character is gone */ }
        // Frame-exact R1 for the watched character: on the frame its jump attack (behavior 550,
        // created at takeoff) appears, an R1 is scheduled INJECT_AT[n] frames later and held for
        // INJECT_HOLD frames by OR-ing bit 0 into its action requests (+0x10), sweeping n.
        if (injectEnabled && pendingTakeoff !== null) {
            injectFrame = pendingTakeoff + INJECT_AT[injectN % INJECT_AT.length];
            emit('inject-plan', { n: injectN, after: INJECT_AT[injectN % INJECT_AT.length], at: injectFrame });
            injectN += 1;
            pendingTakeoff = null;
        }
        // Its height (physics module, modules +0x68, position +0x70, y at +0x74), logged on every
        // frame it moves vertically, so the jump arc and its apex frame are on record.
        try {
            const y = watched.add(0x190).readPointer().add(0x68).readPointer().add(0x74).readFloat();
            if (watchedY === null || Math.abs(y - watchedY) > 0.002) emit('y', { y: Math.round(y * 1000) / 1000 });
            watchedY = y;
        } catch (e) { /* the character is gone */ }
        const fp = fpOf(watched);
        if (fp !== watchedFp) {
            if (watchedFp !== null && fp !== null) emit('fp', { chr: watched.toString(), from: watchedFp, to: fp, delta: fp - watchedFp });
            watchedFp = fp;
        }
    },
}));

// The injected R1 goes in where action-script.js puts the player's: request bits at
// CSChrActionRequestModule +0x10 on entry to UpdateFromManipulator (0x140408190); written from the
// sweep instead, it never reached the game (measured: no attack at any offset).
// Player drive (config { drivePlayer: true, attempts: n }): the watched character is the player,
// whose request bits are replaced outright every frame while the drive runs (the player's own
// buttons do not reach the game), with JUMP pressed for 2 frames every DRIVE_CYCLE frames and the
// R1 injected as above; after `attempts` jumps the bits are handed back.
const cfgDrive = (globalThis.__ER_FRIDA_CONFIG || {});
const DRIVE_CYCLE = 90;
let driveLeft = cfgDrive.drivePlayer ? (cfgDrive.attempts || 28) : 0;
let driveStart = null;

hooks.push(Interceptor.attach(va('0x140408190'), {
    onEnter(args) {
        if (watched === null) return;
        if (!args[0].add(8).readPointer().equals(watched)) return;
        const at = args[0].add(0x10);
        if (driveLeft > 0) {
            if (driveStart === null) { driveStart = frame; emit('drive-start', { attempts: driveLeft }); }
            const phase = (frame - driveStart) % DRIVE_CYCLE;
            let bits = uint64(phase < 2 ? 0x40 : 0);
            if (injectFrame !== null && frame >= injectFrame) {
                bits = bits.or(injectMask);
                if (frame === injectFrame) emit('inject', {});
                if (frame >= injectFrame + INJECT_HOLD - 1) injectFrame = null;
            }
            at.writeU64(bits);
            if (phase === DRIVE_CYCLE - 1) {
                driveLeft -= 1;
                if (driveLeft === 0) emit('drive-done', {});
            }
            return;
        }
        if (injectFrame === null) return;
        if (frame < injectFrame) return;
        at.writeU64(at.readU64().or(injectMask));
        if (frame === injectFrame) emit('inject', {});
        if (frame >= injectFrame + INJECT_HOLD - 1) injectFrame = null;
    },
}));

rpc.exports = {
    setInjectMask(mask) {
        injectMask = uint64(mask);
        injectN = 0;
        return { mask: injectMask.toString(16) };
    },
    // The watched character's PlayerGameData (PlayerIns +0x580) as u32s, offsets 0..len.
    pgdWords(len) {
        const pgd = watched.add(0x580).readPointer();
        const out = [];
        for (let o = 0; o < len; o += 4) out.push([o, pgd.add(o).readU32()]);
        return out;
    },
    pgdWrite(off, value) {
        const at = watched.add(0x580).readPointer().add(off);
        const before = at.readU32();
        at.writeU32(value);
        return { off, before, after: at.readU32() };
    },
    animQueue() {
        const tam = watched.add(0x190).readPointer().add(0x18).readPointer();
        const q = [];
        for (let i = 0; i < 10; i++) q.push([tam.add(0x20 + 16 * i).readS32(), tam.add(0x24 + 16 * i).readFloat()]);
        return { q: q, write: tam.add(0xc0).readU32(), read: tam.add(0xc4).readU32() };
    },
    setWatch(chr) {
        watched = chr ? ptr(chr) : null;
        watchedFp = watched === null ? null : fpOf(watched);
        return { watched: String(watched), fp: watchedFp };
    },
};

// Jump-attack behavior judges (behavior id mod 1000): 150 jump R1, 160 jump R2. Attacks with these
// are traced for every character, so a spawned NPC doing the tech on the player shows up too.
const JUMP_JUDGES = { 150: true, 160: true };

function ownerOf(module) {
    try { return module.add(MODULE_OWNER).readPointer(); } catch (e) { return null; }
}

hooks.push(Interceptor.attach(CREATE_OR_REUSE, {
    onEnter(args) {
        const owner = ownerOf(args[0]);
        const player = mainPlayer();
        const isPlayer = owner !== null && player !== null && owner.equals(player);
        const isWatched = owner !== null && watched !== null && owner.equals(watched);
        if (!isPlayer && !isWatched && !JUMP_JUDGES[((args[2].toInt32() % 1000) + 1000) % 1000]) return;
        this.owner = owner === null ? null : owner.toString();
        this.isPlayer = isPlayer;
        this.isWatched = isWatched;
        let slot = null;
        try { slot = this.context.rsp.add(0x28).readS32(); } catch (e) { slot = null; }
        this.ctx = { behavior: args[2].toInt32(), dmgType: args[3].toInt32(), slot: slot, created: null };
        inCall[this.threadId] = this.ctx;
    },
    onLeave() {
        if (!this.ctx) return;
        delete inCall[this.threadId];
        if (this.isWatched && this.ctx.behavior === 550 && this.ctx.created !== null) pendingTakeoff = frame;
        emit('attack', {
            owner: this.owner,
            is_player: this.isPlayer,
            slot: this.ctx.slot,
            behavior: this.ctx.behavior,
            dmg_type: this.ctx.dmgType,
            new_attack: this.ctx.created !== null,
            handle: this.ctx.created,
        });
    },
}));

hooks.push(Interceptor.attach(DMGMAN_CREATE, {
    onLeave(ret) {
        const ctx = inCall[this.threadId];
        if (!ctx) return;
        const handle = ret.toUInt32();
        ctx.created = handle;
        playerHandles[handle] = true;
    },
}));

hooks.push(Interceptor.attach(DMGMAN_RELEASE, {
    onEnter(args) {
        const handle = args[1].toUInt32();
        if (!playerHandles[handle]) return;
        delete playerHandles[handle];
        emit('release', { handle: handle });
    },
}));

// CalculateDamage2(victimDamageModule, damageDealer, AttackDamageInfo*, ...): every hit the player
// deals or takes.
hooks.push(Interceptor.attach(CALC_DAMAGE2, {
    onEnter(args) {
        const player = mainPlayer();
        if (player === null) return;
        const victim = ownerOf(args[0]);
        const dealt = args[1].equals(player);
        const taken = victim !== null && victim.equals(player);
        if (!dealt && !taken) return;
        hitCtx[this.threadId] = { adi: args[2], dealer: args[1].toString(), taken: taken };
    },
    onLeave() {
        const ctx = hitCtx[this.threadId];
        delete hitCtx[this.threadId];
        if (!ctx) return;
        emit('hit', { atk: rd(ctx.adi, 0x40), damage: rd(ctx.adi, 0x228), dealer: ctx.dealer, taken: ctx.taken });
    },
}));

// Which jump-attack clip is playing: the air clip (a<cat>_0310x0 / 0312x0 / 0345x0 ...) or the
// landed one (031070 ...). The CustomManualSelectorGenerator writer stores the chosen TimeAct at
// node+0xec as category * 1000000 + anim (skill-clip-trace.js; 1.17.1 0x1419bb530). It runs for
// every character, so only jump-attack clips (anim 031000-031999 and 034500-034599) are kept, and
// a repeat of the same clip on the next frame is folded.
const CMSG_SET_TAE = va('0x1419bb530');
let lastClip = null;
let lastClipFrame = -10;
hooks.push(Interceptor.attach(CMSG_SET_TAE, {
    onEnter(args) {
        this.node = args[0];
    },
    onLeave() {
        let tae;
        try { tae = this.node.add(0xec).readS32(); } catch (e) { return; }
        const anim = tae % 1000000;
        // Jump attacks (031000-031999, powerstance 034500-034599) and jump casts (045070 in the air,
        // 045074 landed: c0000.behbnd JumpMagic_* selectors).
        if (!((anim >= 31000 && anim < 32000) || (anim >= 33000 && anim < 34000) || (anim >= 34500 && anim < 34600) || (anim >= 45070 && anim <= 45074))) return;
        const fold = tae === lastClip && frame - lastClipFrame <= 1;
        lastClip = tae;
        lastClipFrame = frame;
        if (fold) return;
        emit('clip', { clip: 'a' + Math.floor(tae / 1000000) + '_' + String(anim).padStart(6, '0') });
    },
}));

// The selector's TimeAct clock, per update, for jump clips: CustomManualSelectorGenerator::update
// (1.16.2 0x1419b82e0 -> 1.17.1 0x1419ba150, unique prologue match, the same +0x1e70 as the clip
// setter above) stores preLocalTime at node +0xe0 and localTime at +0xe4 and fires the TAE
// callback with (pre, local) for the clip at +0xec. A clip switch zeroes both (setter, 1.16.2
// 0x1419b96c0), so the new clip's first update fires from -1. This shows the time the landed
// clip starts from and which window each update fires.
const CMSG_UPDATE = va('0x1419ba150');
hooks.push(Interceptor.attach(CMSG_UPDATE, {
    onEnter(args) { this.node = args[0]; },
    onLeave() {
        let tae, pre, cur;
        try {
            tae = this.node.add(0xec).readS32();
            pre = this.node.add(0xe0).readFloat();
            cur = this.node.add(0xe4).readFloat();
        } catch (e) { return; }
        const anim = tae % 1000000;
        if (!((anim >= 31000 && anim < 32000) || (anim >= 33000 && anim < 34000) || (anim >= 45070 && anim <= 45074))) return;
        emit('tl', { tae: tae, node: this.node.toString(), pre: Math.round(pre * 10000) / 10000, cur: Math.round(cur * 10000) / 10000 });
    },
}));

// Every bullet the watched character fires: CSBulletManager::SpawnBullet(manager, outHandle,
// BulletSpawnData*, ...), 1.16.2 0x1403a2ca0 -> 1.17.1 0x1403a2cb0 (prologue bytes 0x00-0x13 and
// 0x20-0x5f identical; only the cookie displacement differs). BulletSpawnData: owner FieldInsHandle
// +0x0, behaviorId +0x8, magicId +0xc, bulletId +0x14. FP is no cast counter on this NPC (it refills
// a frame after every charge, and drops without a cast), so a cast is counted by its bullets: a
// double cast fires the spell's bullets twice.
const SPAWN_BULLET = va('0x1403a2cb0');
hooks.push(Interceptor.attach(SPAWN_BULLET, {
    onEnter(args) {
        if (watched === null) return;
        try {
            const data = args[2];
            if (!data.readU64().equals(watched.add(0x8).readU64())) return;
            // +0x44 bit 8: SpawnBullet packs a network sync entry for this spawn (1.16.2 decompile).
            emit('bullet', { behavior: data.add(0x8).readS32(), magic: data.add(0xc).readS32(), bullet: data.add(0x14).readS32(), flags: data.add(0x44).readU32() });
        } catch (e) { /* unreadable spawn data */ }
    },
}));

if (cfgDrive.drivePlayer) {
    injectMask = uint64(cfgDrive.mask || 1);
    const p = mainPlayer();
    if (p !== null) rpc.exports.setWatch(p.toString());
}

emit('armed',{ hooks: hooks.length, player: String(mainPlayer()) });
