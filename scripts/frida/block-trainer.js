// Perfect-block trainer for the arena's sparring partner (duelists.json: the spot with
// `vs: "player"`, running mods/brain_block.lua). The brain only faces the player two-handed; this
// agent presses and releases its guard on frames it predicts from the player's swing, and learns
// which frames work, per attack, from how each attempt went.
//
// Why here and not in Lua: a perfect block is a guard input timed to the impact frame (bd
// perfect-block-static-findings-1171-2026-10-10: the hit is judged guarded from the guard bit the
// guard-start clip sets, and the block-stun reaction is skipped; the community's description is a
// press or release on the impact frame), and the AI decides on its own schedule.
// CSChrActionRequestModule::UpdateFromManipulator (1.16.2 0x140407c60, 2.7.2.0 0x140408220, same
// bytes) reads the manipulator's raw action bits at +0x10 on entry and turns them into this frame's
// presses and releases, so a bit set or cleared there is a button pressed or let go on exactly this
// frame. Guard is ACTION_ARM_L1 (bit 2): c0000.hks IsGuard reads env(ActionRequest, ACTION_ARM_L1),
// and the stock AI guards with GOAL_COMMON_Guard + NPC_ATK_L1. ComManipulator returns before that
// call while the character's no-attack debug flag is set (1.16.2 FUN_1403d0090), so a held duelist
// takes no input; spawn-npc.js leaves the sparring spot released (`fight: true`).
//
// A swing is one run of one player animation (a new id, or the same id restarted). Per animation:
//   windup   the play time at which its first hit landed on the partner, the earliest seen. An
//            animation never seen to hit is not guarded against: its first hit teaches the windup.
//   action   {lead, release}: press guard on the first frame with `lead` or fewer frames left to the
//            predicted hit, and let go on the first frame with `release` or fewer left ('h': hold
//            until cfg.holdS past the hit). Frames left = (windup - play time) / that swing's own
//            play-time step per frame, so a sped-up animation is counted in its own frames.
//   search   until a held guard has been up at a hit, lead walks up from cfg.lead (unguarded: +1).
//            From the smallest lead that guarded, g, the candidates are leads g-1..g+1, each held
//            or released 1..lead+1 frames after the press, chosen by upper confidence bound on the
//            perfect rate, so every candidate is tried and the best one is pressed most.
//   outcome  read cfg.reactFrames partner frames after the hit:
//              unguarded  action flag +0x40 bit 0x40 (the guard clip's SET_GUARD_TYPE) clear at the hit
//              blocked    guarded, and the partner changed to an animation outside its guard pose
//                         (a block stun, 19500 in the first session) after the hit
//              perfect    guarded, and no such change
//              recovering the hit landed while the partner was still in a reaction before its guard
//                         frame (GUARD_AFTER), so no press could have answered it; nothing is learned
//            The guard pose (idle 0, guard start, loop and end) is read from a calibration guard
//            press when the partner arrives.
//   roll     each guarded hit records what it cost in stamina (guardCost, per player animation).
//            When a swing's learned cost would leave the partner's stamina at or below
//            cfg.breakMargin, it rolls instead of guarding (ACTION_ARM_ROLLING, cfg.rollLeadS before
//            the hit), aimed cfg.behindM behind the attacker. Outcomes: dodged (no hit of that
//            swing landed) or rolled-hit.
// After each outcome the partner's HP is refilled, so a long session never ends in a death the
// timing did not cause. Stamina is refilled only with cfg.refillStamina: the roll decision is
// about the stamina the partner really has. What is learned is written to cfg.stateFile
// (a Wine path; er-ai-lab.py --duelists sets it) after every change and read back on load.
//
// Recipe test (cfg.mode 'recipe'), instead of all of the above. Rust_Bucket's video (bd
// perfect-block-video-rust-bucket-IshrY5kKvAw-2026-10-21) says a perfect block is the guard let go
// as the hit lands while dropping from a sprint to a walk on that same frame, and that the near miss
// (an IPE) takes full damage and no stun. The partner closes in on the player under one condition at
// a time (RECIPES: what it holds, and what it lets go), and each hit becomes a pb-recipe line: the
// guard bit at the hit, the damage, the stamina, the animations after it and a per-frame trace of
// move speed, guard and inputs around it. Nothing is searched. The drop is made in the damage hook,
// the earliest the partner can know of the hit, or `lead` frames before the predicted hit for the
// '-N' conditions, which need the swing's windup learned from an earlier hit.
//
// Events: pb-trainee, pb-calibrated, pb-learned, pb-press, pb-release, pb-hit, pb-attempt, pb-skip,
// pb-recipe-start, pb-recipe, pb-error. rpc: state(), queue(), candidates(), reset(), calibrate(),
// recipe().
'use strict';

const cfg = Object.assign({
    // The sparring partner: team and NpcParam (duelists.json's spot 0 and respawn.npcParam).
    team: 72,
    npcParam: 524320082,
    // First lead the search tries, and the range it stays in.
    lead: 1,
    leadMin: 0,
    leadMax: 15,
    // Seconds a held guard stays up past the predicted hit.
    holdS: 0.4,
    // Weight of the exploration term in the candidate choice.
    explore: 0.6,
    // Partner frames after a hit whose animations decide blocked or perfect.
    reactFrames: 20,
    // Calibration on arrival: guard held this many frames, then this many watched after release.
    calibrateFrames: 40,
    calibrateAfterFrames: 40,
    calibrateTries: 3,
    // Refill the partner's HP after each outcome, and its stamina too when refillStamina is set.
    // Stamina is left to the game by default: the roll decision below reads it.
    refill: true,
    refillStamina: false,
    // Roll instead of guarding when the hit's learned guard cost would empty the stamina: the
    // roll bit goes in this many seconds before the predicted hit (the medium roll is invincible
    // f0-13 at 30 fps, 0.43 s, from its first frame; er-mechanics-ash-whiff.py ROLL_IFRAMES) and is
    // held this many frames.
    roll: true,
    rollLeadS: 0.2,
    rollFrames: 2,
    // Stamina the guard cost must leave over for a guard to be chosen.
    breakMargin: 0,
    // Where the roll aims: this far behind the attacker, along its back.
    behindM: 1.5,
    // Make the partner's body solid to the player (see PASS_THROUGH).
    solidBody: true,
    // Give the partner a human's lock-on dead zone (see LOCK_TURN).
    playerTurn: true,
    // Let the timing search also try pushing forward from the guard press to the hit.
    forward: true,
    // Where what is learned is kept, as the game's Wine side spells the path; null keeps nothing.
    stateFile: null,
    // 'learn' runs the timing search; 'recipe' runs the recipe test (see the header).
    mode: 'learn',
    // The attacker: null for the player, or a team whose character (the other duelist, on
    // cfg.npcParam) stands in for the player everywhere this agent says "player".
    attackerTeam: null,
    // The recipe test's conditions (RECIPES), one attempt each in turn.
    recipeConditions: ['plain', 'both+1', 'both+2', 'both+3', 'both+4', 'guard+1', 'guard+2', 'plain+1',
        'plain+2'],
    // Stick tilt once the sprint is let go: c0000.hks SpeedUpdate walks at a move speed of 0.6 or less.
    walkStick: 0.4,
    // Frames the partner backs off after an attempt before the next one closes in.
    backFrames: 45,
    // Frames an attempt may close in without being hit before it is given up.
    recipeMaxFrames: 900,
    // Poise the partner is held at while it closes in, so every hit breaks it and a missing stun is
    // the inputs' doing, not poise left over from the last hit; null leaves the game's own.
    recipePoise: 0.5,
}, globalThis.__ER_FRIDA_CONFIG || {});

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));

// 2.7.2.0. UpdateFromManipulator: 1.16.2 0x140407c60 -> 1.17.0 0x140408190 (paired on a 42-byte
// signature) -> 1.17.1 unchanged -> 2.7.2.0 0x140408220, first 0x60 bytes identical to
// 1.16.2. CalculateDamage2, WorldChrMan and MAIN_PLAYER as spawn-npc.js uses them.
const UPDATE_FROM_MANIPULATOR = va('0x140408220');
const CALC_DAMAGE2 = va('0x1404489f0');
const WORLD_CHR_MAN = va('0x143d6d018');
const MAIN_PLAYER = 0x1e508;
const CHR_SETS = MAIN_PLAYER - 0x10 - 196 * 8;

// ChrIns: +0x60 npc_param_id, +0x6c team_type, +0x190 module container, +0x638 ChrAsm (arm style
// +0x8: 1 one hand, 2 left two hands, 3 right two hands).
const NPC_PARAM = 0x60;
const TEAM_TYPE = 0x6c;
const MODULES = 0x190;
const CHR_ASM_PTR = 0x638;
const ARM_STYLE = 0x8;
// Module container (fromsoftware-rs ChrInsModuleContainer; the +0x80 getter is still
// `mov rax,[rcx+0x190]; mov rax,[rax+0x80]` at 2.7.2.0 0x1402d3858).
const M_DATA = 0x0;
const M_ACTION_FLAG = 0x8;
const M_TIME_ACT = 0x18;
const M_PHYSICS = 0x68;
const M_ACTION_REQUEST = 0x80;
// CSChrToughnessModule (fromsoftware-rs toughness.rs): toughness +0x10, its maximum +0x18. This is
// the poise that decides a stun: with CSChrSuperArmorModule+0x10 pinned at 0.5 on every hit, hits
// still asked for damage level 0 (no stun) about half the time (2026-10-21), and super armor read
// back unchanged two frames later.
const M_SUPER_ARMOR = 0x48;
const SA_DURABILITY = 0x10;
const SA_MAX = 0x18;
// CSChrActionRequestModule: owner +0x8, raw action requests +0x10 (u64; bit 2 ACTION_ARM_L1, bit 17
// ACTION_ARM_ROLLING). c0000.hks GetEvasionRequest turns env(1106, ACTION_ARM_ROLLING) into a roll,
// and refuses any evasion while env(1001), the stamina, is below STAMINA_MINIMUM.
const AR_OWNER = 0x8;
const AR_REQUESTS = 0x10;
const ACTION_ARM_L1 = 1 << 2;
const ACTION_ARM_ROLLING = 1 << 17;
// Spirit-ash pass-through, 2.7.2.0 (bd buddy-pass-through-chr-collision-2720-2026-10-21): a summon
// carries ChrIns+0x1c9 bit 4, the player's proxy contact adds it to a list at player+0x348 with a
// timer of GameSystemCommonParam buddyPassThroughTriggerTime (0.5 s), and this function, called
// with (main player, other character) from the proxy contact callback, returns true once that
// timer has run out; the contact then takes the proxy's own velocity and the player slides
// through. Returning false for the partner keeps it solid and touches no other summon.
const PASS_THROUGH = va('0x1403f4570');
// Lock-on turn, 2.7.2.0 0x1403c4500 (1.16.2 0x1403c44f0): rcx is the character's joint modifier,
// whose first field is its ChrCtrl (ChrIns+0x58), and nothing turns while the target is within
// +0x18 / +0x1c radians of straight ahead. c0000.hks IdleCommonFunction (act 2015) writes 15
// degrees there for a computer-controlled character and 45 for a human on every idle frame, so the
// partner snaps round at 15 where a player lets the target drift to 45. Both use this function
// and the same 720 degrees a second. Written to 45 degrees on entry, the frame it is read.
const CHR_CTRL = 0x58;
const LOCK_TURN = va('0x1403c4500');
const PLAYER_DEAD_ZONE = 0.7853982;
// NPC manipulator update, 2.7.2.0 0x1403d00a0: it calls 0x1403cf360 (the AI action step, the last
// writer of the move vector), sets the movement request when the vector at +0x10 is non-zero, and
// calls UpdateFromManipulator. The vector is character-local, forward (0,0,-1) and right -x;
// 0x1403cc000 turns it into the move speed and the angle (ChrCtrl+0x3a4) that env 406 hands
// c0000.hks as c_RollingAngle for an NPC. Written on 0x1403cf360's return, so nothing after it
// overwrites it. ChrManipulator +0xa8 is its ChrIns.
const MOVE_STEP = va('0x1403cf360');
// Sprint, read from 1.17.0 (same instructions in 2.7.2.0, only rip-relative data moved). The
// move-speed step 0x1403cc000 (rcx the ChrCtrl, +0x10 its ChrIns) writes move speed 2.0 to
// ChrCtrl+0x3a0 when CSChrActionRequestModule+0xfc bit 2 is set, the move vector is not zero and
// the stamina is above 0, and the vector's length otherwise, so a stick tilt is a speed. The AI's
// own dash sets that bit and raw action bit 5 together (0x1403cfbd0 case 0xf; 0x1403d00a0 sets bit
// 5 while the AI's dash flag is up and it moves), so both are held here; the bit is set on entry to
// the step that reads it, whatever wrote it earlier in the frame. c0000.hks SpeedUpdate turns the
// speed into the move speed index: above 1.1 sprint (2), above 0.6 run (1), otherwise walk (0).
const MOVE_SPEED_STEP = va('0x1403cc000');
const CTRL_SPEED = 0x3a0;
const AR_STATE = 0xfc;
const AR_DASH = 1 << 2;
const ACTION_ARM_SPRINT = 1 << 5;
const MANIP_MOVE = 0x10;
const MANIP_MOVE_COPY = 0x70;
const MANIP_OWNER = 0xa8;
// FD4Time: the frame time, a float at +0x8.
const FD4TIME_DT = 0x8;
// CSChrActionFlagModule +0x40 action modifier flags, bit 6 set by the guard clip's TAE
// ChrActionFlag 3 (SET_GUARD_TYPE); CalculateDamage judges guarded from it.
const AF_MODIFIERS = 0x40;
const GUARD_TYPE_SET = 0x40;
// The reaction a hit asks for, as FUN_140445b20 (1.16.2; instruction-identical in 2.7.2.0) writes
// it and c0000.hks reads it (env 236 damageLevel): damage level, a byte at +0x1c, and the received
// damage type at +0x34 (1.16.2 CSChrActionFlagModule). Poise that holds lowers the level to 0
// before it is written (FUN_140690250), so a level above 0 with no stun clip is a stun dropped
// after it was asked for.
const AF_DAMAGE_LEVEL = 0x1c;
const AF_DAMAGE_TYPE = 0x34;
const reactionOf = (chr) => {
    const af = moduleOf(chr, M_ACTION_FLAG);
    return { dl: af.add(AF_DAMAGE_LEVEL).readU8(), rdt: af.add(AF_DAMAGE_TYPE).readS32() };
};
// CSChrDataModule: hp +0x138, max hp +0x13c, stamina +0x154, max stamina +0x158.
const D_HP = 0x138;
const D_MAX_HP = 0x13c;
const D_SP = 0x154;
const D_MAX_SP = 0x158;
// CSChrTimeActModule: a ring of ten {anim_id, play_time, play_time2, anim_length} at +0x20 with the
// write index at +0xc0. Measured 2026-10-21: the index moves every frame, each entry holding the
// animation playing on that frame (an idle partner fills it with anim 0, play time rising 0.02 a
// step), so only a change of id is an event.
const TA_QUEUE = 0x20;
const TA_WRITE = 0xc0;
// CalculateDamage2's damage info: HP taken at +0x228.
const DAMAGE_HP = 0x228;

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now() }, fields));
}

const errorsSeen = new Set();
function fail(where, e) {
    const key = `${where}:${e.message}`;
    if (errorsSeen.has(key)) return;
    errorsSeen.add(key);
    emit('pb-error', { where, error: e.message });
}

const round = (x, k) => Math.round(x * k) / k;

function allChrs() {
    const wcm = WORLD_CHR_MAN.readPointer();
    const out = [];
    if (wcm.isNull()) return out;
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

const moduleOf = (chr, off) => chr.add(MODULES).readPointer().add(off).readPointer();

// CSChrPhysicsModule (fromsoftware-rs physics.rs): orientation quaternion (x, y, z, w) +0x50,
// position +0x70.
function posOf(chr) {
    const p = moduleOf(chr, M_PHYSICS).add(0x70);
    return [p.readFloat(), p.add(4).readFloat(), p.add(8).readFloat()];
}

function quatOf(chr) {
    const q = moduleOf(chr, M_PHYSICS).add(0x50);
    return [q.readFloat(), q.add(4).readFloat(), q.add(8).readFloat(), q.add(12).readFloat()];
}

const staminaOf = (chr) => moduleOf(chr, M_DATA).add(D_SP).readS32();

function distance(a, b) {
    const p = posOf(a);
    const q = posOf(b);
    return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]);
}

function lastAnim(chr) {
    const ta = moduleOf(chr, M_TIME_ACT);
    const w = ta.add(TA_WRITE).readU32();
    const e = ta.add(TA_QUEUE + ((w + 9) % 10) * 16);
    return { w, anim: e.readS32(), t: e.add(4).readFloat() };
}

// The ring entries written since index `from`, one per frame.
function animsSince(chr, from) {
    const ta = moduleOf(chr, M_TIME_ACT);
    const w = ta.add(TA_WRITE).readU32();
    const out = [];
    for (let i = from; i !== w && out.length < 10; i = (i + 1) % 10) {
        out.push(ta.add(TA_QUEUE + i * 16).readS32());
    }
    return { w, anims: out };
}

function guardUp(chr) {
    return (moduleOf(chr, M_ACTION_FLAG).add(AF_MODIFIERS).readU32() & GUARD_TYPE_SET) !== 0;
}

// Reaction clips the partner cannot guard out of before a frame (TimeAct frames, 30 a second):
// [first clip, last clip, guard frame]. Block stuns from er-mechanics-perfect-block.py
// SKIPPED_STUN, hit stuns from `er-mechanics-frame-advantage.py --reactions`, both read from the
// c0000 TAE. A large block stun and a guard break have no guard window, so their whole clip counts.
const GUARD_AFTER = [
    [19200, 19200, 16], [19210, 19210, 32], [19220, 19220, 64], [19500, 19500, 70],
    [5000, 5002, 10], [5100, 5134, 10], [5200, 5234, 25], [5300, 5334, 35], [5350, 5353, 41],
    [5400, 5430, 95], [5450, 5480, 109], [5500, 5530, 40], [5600, 5630, 114], [5700, 5700, 59],
    [5710, 5712, 89],
];

// The reaction the partner is still locked in, or null when it could guard this frame.
function recovering(chr) {
    const a = lastAnim(chr);
    for (const [lo, hi, f] of GUARD_AFTER) {
        if (a.anim < lo || a.anim > hi) continue;
        const at = a.t * 30;
        return at < f ? { anim: a.anim, f: round(at, 10), guardAt: f } : null;
    }
    return null;
}

// 1, 2 or 3 as above; null when the read is not one of those, so a wrong offset never blocks a press.
function armStyle(chr) {
    try {
        const s = chr.add(CHR_ASM_PTR).readPointer().add(ARM_STYLE).readS32();
        return s >= 0 && s <= 3 ? s : null;
    } catch (e) {
        return null;
    }
}

function refill(chr) {
    if (!cfg.refill) return;
    const d = moduleOf(chr, M_DATA);
    d.add(D_HP).writeS32(d.add(D_MAX_HP).readS32());
    if (cfg.refillStamina) d.add(D_SP).writeS32(d.add(D_MAX_SP).readS32());
}

// What is learned, per player animation id (as a string key, the way JSON keeps it):
//   windups[anim]     play time of its first hit
//   searchLead[anim]  the lead the search is at while no held guard has been up at a hit
//   guardLead[anim]   the smallest lead whose held guard was up at a hit
//   cells[anim][key]  tries per action, key `${lead}:${release}`: {n, perfect, blocked, unguarded}
//   guardCost[anim]   the most stamina a guarded hit of it took; a guard break records at least
//                     the stamina it broke
const learned = { windups: {}, searchLead: {}, guardLead: {}, cells: {}, guardCost: {} };

// The guard would break: the hit's learned guard cost leaves no more than cfg.breakMargin.
function breakRisk(anim) {
    const cost = learned.guardCost[String(anim)];
    if (!cfg.roll || cost === undefined) return null;
    const sp = staminaOf(trainee);
    return sp - cost <= cfg.breakMargin ? { sp, cost } : null;
}

function load() {
    if (!cfg.stateFile) return;
    try {
        const saved = JSON.parse(File.readAllText(cfg.stateFile));
        for (const k of Object.keys(learned)) Object.assign(learned[k], saved[k] || {});
        emit('pb-loaded', { file: cfg.stateFile, anims: Object.keys(learned.windups).length });
    } catch (e) {
        emit('pb-loaded', { file: cfg.stateFile, anims: 0, why: e.message });
    }
}

function save() {
    if (!cfg.stateFile) return;
    try {
        const f = new File(cfg.stateFile, 'w');
        f.write(JSON.stringify(learned));
        f.close();
    } catch (e) {
        fail('save', e);
    }
}

function cellOf(anim, key) {
    const per = learned.cells[anim] || (learned.cells[anim] = {});
    return per[key] || (per[key] = { n: 0, perfect: 0, blocked: 0, unguarded: 0 });
}

// `${lead}:${release}`, plus `:f` when the stick is pushed forward from the press to the hit.
const actionKey = (a) => `${a.lead}:${a.release}${a.move === 'f' ? ':f' : ''}`;

function chooseAction(anim) {
    const g = learned.guardLead[anim];
    if (g === undefined) {
        const lead = learned.searchLead[anim] === undefined ? cfg.lead : learned.searchLead[anim];
        return { lead, release: 'h', move: 'n', why: 'search' };
    }
    const options = [];
    const moves = cfg.forward ? ['n', 'f'] : ['n'];
    for (let lead = Math.max(cfg.leadMin, g - 1); lead <= Math.min(cfg.leadMax, g + 1); lead++) {
        for (const move of moves) {
            options.push({ lead, release: 'h', move });
            for (let release = lead - 1; release >= -1; release--) options.push({ lead, release, move });
        }
    }
    const per = learned.cells[anim] || {};
    const total = options.reduce((s, o) => s + ((per[actionKey(o)] || {}).n || 0), 0);
    let best = null;
    let bestScore = -1;
    for (const o of options) {
        const c = per[actionKey(o)] || { n: 0, perfect: 0 };
        const score = (c.perfect + 1) / (c.n + 2) + cfg.explore * Math.sqrt(Math.log(total + 1) / (c.n + 1));
        if (score > bestScore) {
            best = o;
            bestScore = score;
        }
    }
    return { lead: best.lead, release: best.release, move: best.move, why: 'ucb', score: round(bestScore, 1000) };
}

let player = null;
// The attacker standing in for the player (cfg.attackerTeam), as last reported.
let attackerSeen = null;
let trainee = null;
let traineeModule = null;
let traineeCtrl = null;
let playerCtrl = null;
// Turning, for bd er-effects-rs-p4mr: LOCK_TURN calls for the partner and for the player with the
// dead zones (degrees, +0x18/+0x1c) each held before the hook wrote anything, and the last
// TURN_TRACE_FRAMES partner frames of facing against the bearing to the other character.
const TURN_TRACE_FRAMES = 240;
const TURN_ZONE_KEYS = 16;
const turnStats = { partner: { calls: 0, zones: {} }, player: { calls: 0, zones: {} } };
const turnTrace = [];
let turnPrev = null;
let turnWindow = null;
// The stick for the next partner frame, {frame, v: local (x, z)}, written by the MOVE_STEP hook.
let moveOut = null;
// Frames left to the predicted hit as of this frame's pressStep, null when none is predicted.
let curLeft = null;

// Per partner frame.
let frame = 0;
let traineeW = null;
let traineeAnim = null;
let lastHitFrame = -1e9;
let swingIds = 0;
let swing = null;
let press = null;
let lastPress = null;
// The roll in flight until a hit of its swing lands or the swing passes its hit.
let rollWatch = null;
let pending = null;
let calib = null;
let calibrated = false;
let calibTries = 0;
// The guard pose: idle, guard start, loop and end, as four calibrations read it on 2026-10-21,
// plus whatever a calibration adds. Seeded because a calibration that starts with the guard
// already up never sees guard start, and a press answering a hit plays it right after the hit.
// The unhit animations seen otherwise are kept for the record.
const pose = new Set([0, 19020, 120, 19120]);
const quiet = new Set();

// The recipe test's conditions: what the partner holds while it closes in, and what it lets go
// when a hit lands (or `lead` frames before the predicted one). `tilt` is the stick while not
// sprinting and `after` the stick once the sprint is let go, both cfg.walkStick when absent.
// 'plain' walks in guarding, the ordinary block; 'run' sprints in with nothing held, the ordinary
// hit; 'walk' and 'jog' are the ordinary hit at a walk and at a run, with no sprint to let go.
// Measured 2026-10-21: 'none', 'guard' and 'sprint' were stunned like 'plain'.
const RECIPES = {
    both: { guard: true, sprint: true, drop: ['guard', 'sprint'] },
    guard: { guard: true, sprint: true, drop: ['guard'] },
    sprint: { guard: true, sprint: true, drop: ['sprint'] },
    none: { guard: true, sprint: true, drop: [] },
    ipe: { guard: false, sprint: true, drop: ['sprint'] },
    'ipe-run': { guard: false, sprint: true, drop: ['sprint'], after: 1 },
    run: { guard: false, sprint: true, drop: [] },
    walk: { guard: false, sprint: false, drop: [] },
    jog: { guard: false, sprint: false, tilt: 1, drop: [] },
    plain: { guard: true, sprint: false, drop: [] },
    'both-1': { guard: true, sprint: true, drop: ['guard', 'sprint'], lead: 1 },
    'both-2': { guard: true, sprint: true, drop: ['guard', 'sprint'], lead: 2 },
    'both-4': { guard: true, sprint: true, drop: ['guard', 'sprint'], lead: 4 },
    'both-6': { guard: true, sprint: true, drop: ['guard', 'sprint'], lead: 6 },
    'ipe-1': { guard: false, sprint: true, drop: ['sprint'], lead: 1 },
    'ipe-2': { guard: false, sprint: true, drop: ['sprint'], lead: 2 },
    // Held through the hit and let go `delay` partner frames after it (user, 2026-10-21: the guard
    // is dropped the frame after the hit). The drop at the hit is made after that frame's inputs
    // were read, so the first frame to read it released is f=1 either way: 'both' and 'both+1' differ
    // only in the stick and sprint bit being written mid-frame.
    'both+1': { guard: true, sprint: true, drop: ['guard', 'sprint'], delay: 1 },
    'both+2': { guard: true, sprint: true, drop: ['guard', 'sprint'], delay: 2 },
    'both+3': { guard: true, sprint: true, drop: ['guard', 'sprint'], delay: 3 },
    'both+4': { guard: true, sprint: true, drop: ['guard', 'sprint'], delay: 4 },
    'guard+1': { guard: true, sprint: true, drop: ['guard'], delay: 1 },
    'guard+2': { guard: true, sprint: true, drop: ['guard'], delay: 2 },
    'plain+1': { guard: true, sprint: false, drop: ['guard'], delay: 1 },
    'plain+2': { guard: true, sprint: false, drop: ['guard'], delay: 2 },
};
// The attempt in flight: {name, r, phase 'in' | 'react' | 'back', since, guard, sprint (held now),
// dropAt, dropWhy, backAt, trace}.
let recipe = null;
let recipeNext = 0;
const recipeTally = {};
// Partner frames of trace kept from before the hit.
const RECIPE_TRACE_BEFORE = 6;
// The partner's manipulator (from MOVE_STEP), so a drop can set the walking stick at once.
let traineeManip = null;
// The partner's move speed as the speed step last wrote it.
let traineeSpeed = null;
// Sprint as the speed hook sets it for the partner; null leaves whatever the AI wrote.
let wantDash = null;

function scan() {
    try {
        const wcm = WORLD_CHR_MAN.readPointer();
        player = wcm.isNull() ? null : wcm.add(MAIN_PLAYER).readPointer();
        if (player !== null && player.isNull()) player = null;
        if (player !== null && cfg.attackerTeam !== null) {
            // The attacker on its team nearest the real player; the partner is then found by
            // distance to the real player as before.
            const me = player;
            let near = null;
            let nearD = Number.MAX_VALUE;
            for (const chr of allChrs()) {
                if (chr.equals(me) || chr.add(TEAM_TYPE).readU8() !== cfg.attackerTeam
                    || chr.add(NPC_PARAM).readS32() !== cfg.npcParam) continue;
                const d = distance(chr, me);
                if (d < nearD) {
                    near = chr;
                    nearD = d;
                }
            }
            // No attacker yet (or it is gone): nothing to defend against. Never the real player,
            // which would steer the defender at them.
            if (near === null) {
                player = null;
                playerCtrl = null;
                return;
            }
            if (attackerSeen === null || !attackerSeen.equals(near)) {
                attackerSeen = near;
                emit('pb-attacker', { chr: near.toString(), team: cfg.attackerTeam, d: round(nearD, 100) });
            }
            player = near;
        }
        playerCtrl = player === null ? null : player.add(CHR_CTRL).readPointer();
        if (player === null) return;
        let best = null;
        // Not Infinity: in this agent runtime the global reads NaN (measured 2026-10-21, typeof
        // "number", String "NaN"), and every comparison with it is false.
        let bestD = Number.MAX_VALUE;
        for (const chr of allChrs()) {
            if (chr.equals(player) || chr.add(TEAM_TYPE).readU8() !== cfg.team
                || chr.add(NPC_PARAM).readS32() !== cfg.npcParam) continue;
            const d = distance(chr, player);
            if (d < bestD) {
                best = chr;
                bestD = d;
            }
        }
        if (best === null && trainee !== null) {
            emit('pb-trainee', { chr: null, lost: trainee.toString() });
            trainee = null;
            traineeModule = null;
            traineeCtrl = null;
            traineeManip = null;
            moveOut = null;
            recipe = null;
            wantDash = null;
        }
        if (best !== null && (trainee === null || !best.equals(trainee))) {
            trainee = best;
            traineeModule = moduleOf(best, M_ACTION_REQUEST);
            traineeCtrl = best.add(CHR_CTRL).readPointer();
            turnPrev = null;
            turnWindow = null;
            moveOut = null;
            traineeW = null;
            traineeAnim = null;
            swing = null;
            press = null;
            lastPress = null;
            rollWatch = null;
            pending = null;
            calib = null;
            calibrated = false;
            calibTries = 0;
            traineeManip = null;
            traineeSpeed = null;
            recipe = null;
            wantDash = null;
            emit('pb-trainee', { chr: best.toString(), module: traineeModule.toString(), d: round(bestD, 100),
                armStyle: armStyle(best), team: cfg.team });
        }
    } catch (e) {
        fail('scan', e);
    }
}

function learnWindup(anim, t) {
    const before = learned.windups[anim];
    if (before === undefined || t < before) {
        learned.windups[anim] = t;
        emit('pb-learned', { anim: Number(anim), windup: round(t, 1000), before: before === undefined ? null : round(before, 1000) });
        save();
    }
}

// Guard down, nothing near, two hands on the weapon: hold guard once and record what the partner
// plays, which is its guard pose. The guard bit it sees is the proof that the press reaches the
// partner at all.
function calibrationStep() {
    if (calibrated || calibTries >= cfg.calibrateTries) return false;
    if (calib === null) {
        const style = armStyle(trainee);
        if ((style !== null && style !== 3) || pending !== null || press !== null || rollWatch !== null
            || frame - lastHitFrame <= cfg.reactFrames) return false;
        calib = { start: frame, guardSeen: false, anims: [] };
        calibTries += 1;
    }
    // A hit inside it would put its reaction into the pose: start again later.
    if (lastHitFrame >= calib.start) {
        emit('pb-calibrated', { discarded: 'hit during calibration', try: calibTries });
        calibTries -= 1;
        calib = null;
        return false;
    }
    const at = frame - calib.start;
    if (at < cfg.calibrateFrames && guardUp(trainee)) calib.guardSeen = true;
    if (at >= cfg.calibrateFrames + cfg.calibrateAfterFrames) {
        calibrated = calib.guardSeen;
        if (calibrated) for (const a of calib.anims) pose.add(a);
        emit('pb-calibrated', { guardSeen: calib.guardSeen, try: calibTries, anims: calib.anims, pose: [...pose],
            armStyle: armStyle(trainee) });
        calib = null;
        return false;
    }
    return at < cfg.calibrateFrames;
}

// Frames left to the predicted hit of the current swing, in the swing's own play-time steps.
function framesLeft(dt) {
    const windup = learned.windups[swing.anim];
    if (windup === undefined) return null;
    const step = swing.step > 1e-4 ? swing.step : dt;
    return (windup - swing.t) / step;
}

// The yaw-only forward of a physics orientation quaternion, as a world (x, z) direction. Measured
// 2026-10-21 with rpc bodies(): the partner, turned to the player by brain_block.lua, had
// (-sin, -cos) of its yaw 12 degrees off the direction to the player, and the locked-on player had
// it pointing back at the partner; (sin, cos) pointed both of them away.
function forwardOf(q) {
    const yaw = 2 * Math.atan2(q[1], q[3]);
    return [-Math.sin(yaw), -Math.cos(yaw)];
}

// A world (x, z) direction in the partner's own frame, as the manipulator wants it (forward
// (0,0,-1), right -x): the inverse of the yaw rotation forwardOf() measured. Returns (x, z).
function localOf(chr, wx, wz) {
    const q = quatOf(chr);
    const yaw = 2 * Math.atan2(q[1], q[3]);
    const c = Math.cos(yaw);
    const s = Math.sin(yaw);
    const lx = wx * c - wz * s;
    const lz = wx * s + wz * c;
    const n = Math.hypot(lx, lz) || 1;
    return [lx / n, lz / n];
}

// An angle in degrees, wrapped to [-180, 180).
function wrapDeg(d) {
    return ((d % 360) + 540) % 360 - 180;
}

// chr's yaw and how far, in degrees, the bearing to other is off its facing (forwardOf's
// convention: the yaw whose forward points along (dx, dz) is atan2(-dx, -dz)).
function facingOff(chr, other) {
    const q = quatOf(chr);
    const yaw = 2 * Math.atan2(q[1], q[3]);
    const a = posOf(chr);
    const b = posOf(other);
    const to = Math.atan2(-(b[0] - a[0]), -(b[2] - a[2]));
    return { yaw: yaw * 180 / Math.PI, off: wrapDeg((to - yaw) * 180 / Math.PI) };
}

// One trace sample per partner frame, for the partner and the player each: the bearing off its
// facing and its turn rate in degrees a second. Every TURN_EMIT_FRAMES a pb-turn line sums the
// window up: the largest turn rate, the mean and largest bearing off, and LOCK_TURN calls.
const TURN_EMIT_FRAMES = 120;
function traceTurn(dt) {
    const pa = facingOff(trainee, player);
    const me = facingOff(player, trainee);
    const rate = (now, before) => before === null ? 0 : wrapDeg(now - before) / dt;
    const s = { f: frame, anim: traineeAnim, off: round(pa.off, 10), rate: round(rate(pa.yaw, turnPrev && turnPrev.pa), 10),
        meOff: round(me.off, 10), meRate: round(rate(me.yaw, turnPrev && turnPrev.me), 10) };
    turnPrev = { pa: pa.yaw, me: me.yaw };
    turnTrace.push(s);
    if (turnTrace.length > TURN_TRACE_FRAMES) turnTrace.shift();
    if (turnWindow === null) turnWindow = { n: 0, maxRate: 0, sumOff: 0, maxOff: 0, meMaxRate: 0, meSumOff: 0, meMaxOff: 0,
        calls: turnStats.partner.calls, meCalls: turnStats.player.calls, anims: new Set() };
    const w = turnWindow;
    w.n += 1;
    w.maxRate = Math.max(w.maxRate, Math.abs(s.rate));
    w.sumOff += Math.abs(s.off);
    w.maxOff = Math.max(w.maxOff, Math.abs(s.off));
    w.meMaxRate = Math.max(w.meMaxRate, Math.abs(s.meRate));
    w.meSumOff += Math.abs(s.meOff);
    w.meMaxOff = Math.max(w.meMaxOff, Math.abs(s.meOff));
    if (traineeAnim !== null && w.anims.size < 8) w.anims.add(traineeAnim);
    if (w.n < TURN_EMIT_FRAMES) return;
    emit('pb-turn', { partner: { maxRate: round(w.maxRate, 1), meanOff: round(w.sumOff / w.n, 10), maxOff: round(w.maxOff, 10),
        lockTurnCalls: turnStats.partner.calls - w.calls, zones: turnStats.partner.zones, anims: [...w.anims] },
        player: { maxRate: round(w.meMaxRate, 1), meanOff: round(w.meSumOff / w.n, 10), maxOff: round(w.meMaxOff, 10),
        lockTurnCalls: turnStats.player.calls - w.meCalls, zones: turnStats.player.zones }, frames: w.n });
    turnWindow = null;
}

// Where the roll should go: cfg.behindM behind the attacker, along its back. Returned as the world
// direction from the partner and as the signed angle off the partner's own facing in degrees
// (the sign of the forward x target cross product; which side is left is not measured yet).
function rollAim() {
    const me = posOf(trainee);
    const them = posOf(player);
    const f = forwardOf(quatOf(player));
    const tx = them[0] - f[0] * cfg.behindM - me[0];
    const tz = them[2] - f[1] * cfg.behindM - me[2];
    const mine = forwardOf(quatOf(trainee));
    const off = Math.atan2(mine[0] * tz - mine[1] * tx, mine[0] * tx + mine[1] * tz) * 180 / Math.PI;
    return { dir: [round(tx, 100), round(tz, 100)], deg: round(off, 10) };
}

// Roll instead of guarding: the hit would break the guard (breakRisk). From the next frame, the
// roll bit and the stick toward rollAim() go in together for cfg.rollFrames frames. The outcome
// reports where the partner went (moved) against where it aimed, which checks the stick's frame.
function startRoll(left) {
    swing.pressed = true;
    const aim = rollAim();
    press = { roll: true, frame, swingId: swing.id, anim: swing.anim, risk: swing.risk, aim,
        local: localOf(trainee, aim.dir[0], aim.dir[1]), from: posOf(trainee),
        left: round(left, 100), releasedAt: null, anims: [] };
    lastPress = press;
    rollWatch = press;
    emit('pb-roll', { anim: press.anim, left: press.left, sp: press.risk.sp, cost: press.risk.cost, aim: press.aim,
        d: round(distance(trainee, player), 100) });
}

// Where the partner went since the roll began, against where it aimed: the check of localOf().
function rollMoved(r) {
    const p = posOf(trainee);
    const mx = p[0] - r.from[0];
    const mz = p[2] - r.from[2];
    const err = Math.atan2(r.aim.dir[0] * mz - r.aim.dir[1] * mx, r.aim.dir[0] * mx + r.aim.dir[1] * mz);
    return { moved: [round(mx, 100), round(mz, 100)], aimErrDeg: round(err * 180 / Math.PI, 10) };
}

function pressStep(dt) {
    const left = swing === null ? null : framesLeft(dt);
    curLeft = left;
    // A roll that no hit of its swing reached: the swing moved on or passed its hit.
    if (rollWatch !== null && pending === null
        && (swing === null || swing.id !== rollWatch.swingId || (left !== null && left < -cfg.reactFrames))) {
        emit('pb-attempt', Object.assign({ outcome: 'dodged', anim: rollWatch.anim, roll: true, risk: rollWatch.risk,
            aim: rollWatch.aim, local: rollWatch.local.map((v) => round(v, 1000)), sp: staminaOf(trainee),
            rollAnims: rollWatch.anims }, rollMoved(rollWatch)));
        rollWatch = null;
    }
    if (press !== null) {
        let done;
        if (press.roll) {
            // The bit and stick go in on the cfg.rollFrames frames after the deciding one.
            done = frame - press.frame > cfg.rollFrames;
        } else {
            const releaseAt = press.action.release === 'h' ? -Math.round(cfg.holdS / dt) : press.action.release;
            done = swing === null || swing.id !== press.swingId || left === null || left <= releaseAt;
        }
        if (done) {
            press.releasedAt = frame;
            emit('pb-release', { anim: press.anim, roll: press.roll === true, framesHeld: frame - press.frame,
                left: left === null ? null : round(left, 100) });
            press = null;
        }
        return;
    }
    if (swing === null || swing.pressed || pending !== null || left === null) return;
    if (left < -0.5) {
        swing.pressed = true;
        return;
    }
    if (swing.action === undefined) swing.action = chooseAction(String(swing.anim));
    // Roll or guard, decided once, at the earlier of the roll lead and the guard lead, from the
    // stamina the partner has then.
    if (swing.risk === undefined) {
        if (left > Math.max(cfg.rollLeadS / dt, swing.action.lead) + 1e-3) return;
        swing.risk = breakRisk(swing.anim);
    }
    if (swing.risk !== null) {
        startRoll(left);
        return;
    }
    if (left > swing.action.lead + 1e-3) return;
    swing.pressed = true;
    const style = armStyle(trainee);
    if (style !== null && style !== 3) {
        emit('pb-skip', { anim: swing.anim, why: 'not two-handed', armStyle: style });
        return;
    }
    press = { frame, swingId: swing.id, anim: swing.anim, action: swing.action, left: round(left, 100), releasedAt: null };
    lastPress = press;
    emit('pb-press', { anim: press.anim, lead: press.action.lead, release: press.action.release, move: press.action.move,
        why: press.action.why,
        left: press.left, t: round(swing.t, 1000), d: round(distance(trainee, player), 100) });
}

function resolve() {
    const r = pending;
    pending = null;
    if (cfg.mode === 'recipe') {
        resolveRecipe(r);
        return;
    }
    // Only what followed the first hit: a multi-hit swing's later hits make reactions of their own.
    const reaction = r.anims.filter((x) => !pose.has(x.anim) && (r.nextHitAt === null || x.f <= r.nextHitAt));
    const rolled = r.press !== null && r.press.roll === true;
    let outcome;
    if (r.recovering !== null) outcome = 'recovering';
    else if (rolled) outcome = 'rolled-hit';
    else if (r.press === null) outcome = 'no-attempt';
    else if (!r.guard) outcome = 'unguarded';
    else if (reaction.length > 0) outcome = 'blocked';
    else outcome = 'perfect';
    const anim = String(r.anim);
    // The guard's stamina cost: what the guarded hits in the window took. A guard break empties
    // the stamina, so all that is known is that it cost more than there was.
    let cost = null;
    if (r.guard) {
        const broke = reaction.some((x) => x.anim === 19500);
        cost = broke ? Math.max(r.spBefore - r.spMin, r.spBefore + 1) : r.spBefore - r.spMin;
        if (cost > 0 && !(learned.guardCost[anim] >= cost)) {
            learned.guardCost[anim] = cost;
            save();
        }
    }
    const a = r.press !== null && !rolled ? r.press.action : null;
    if (a !== null && outcome !== 'recovering') {
        const c = cellOf(anim, actionKey(a));
        c.n += 1;
        c[outcome] += 1;
        if (a.release === 'h') {
            if (r.guard) {
                const g = learned.guardLead[anim];
                if (g === undefined || a.lead < g) learned.guardLead[anim] = a.lead;
            } else if (learned.guardLead[anim] === undefined) {
                learned.searchLead[anim] = Math.min(cfg.leadMax, a.lead + 1);
            }
        }
        save();
    }
    emit('pb-attempt', { outcome, anim: r.anim, lead: a ? a.lead : null,
        release: a ? a.release : null, why: a ? a.why : null, roll: rolled,
        aim: rolled ? r.press.aim : undefined, risk: rolled ? r.press.risk : undefined,
        rollAnims: rolled ? r.press.anims : undefined, rollMoved: rolled ? rollMoved(r.press) : undefined,
        move: a ? a.move : null,
        pressedFramesBefore: r.press ? r.framesSincePress : null, releasedFramesBefore: r.releasedFramesBefore,
        predictedLeft: r.press ? r.press.left : null, guard: r.guard, recovering: r.recovering,
        sp: r.spBefore, spAfter: r.spMin, cost, guardCost: learned.guardCost[anim],
        damage: r.damage, hits: r.hits, t: r.t,
        windup: learned.windups[anim], reaction, anims: r.anims, d: r.d,
        cell: a ? cellOf(anim, actionKey(a)) : null, guardLead: learned.guardLead[anim] });
    try {
        refill(trainee);
    } catch (e) {
        fail('refill', e);
    }
}

function writeStick(m, v) {
    for (const off of [MANIP_MOVE, MANIP_MOVE_COPY]) {
        const p = m.add(off);
        p.writeFloat(v[0]);
        p.add(4).writeFloat(0);
        p.add(8).writeFloat(v[1]);
        p.add(12).writeFloat(0);
    }
}

// The stick toward the player (sign 1) or away from it (-1) in the partner's own frame, at a tilt.
function stickTo(sign, tilt) {
    const a = posOf(trainee);
    const b = posOf(player);
    const l = localOf(trainee, sign * (b[0] - a[0]), sign * (b[2] - a[2]));
    return [l[0] * tilt, l[1] * tilt];
}

function recipeStick() {
    if (recipe.phase === 'back') return stickTo(-1, 1);
    if (recipe.sprint) return stickTo(1, 1);
    const tilt = recipe.dropAt !== null && recipe.r.sprint ? recipe.r.after : recipe.r.tilt;
    return stickTo(1, tilt === undefined ? cfg.walkStick : tilt);
}

// The raw action bits with the attempt's guard and sprint, and never a roll.
function recipeBits(lo) {
    let want = recipe !== null && recipe.guard ? lo | ACTION_ARM_L1 : lo & ~ACTION_ARM_L1;
    want = recipe !== null && recipe.sprint ? want | ACTION_ARM_SPRINT : want & ~ACTION_ARM_SPRINT;
    return (want & ~ACTION_ARM_ROLLING) >>> 0;
}

// The next condition, with full stamina so the sprint has some to spend.
function recipeStart() {
    const names = cfg.recipeConditions;
    const name = names[recipeNext % names.length];
    recipeNext += 1;
    const r = RECIPES[name];
    if (r === undefined) throw new Error(`no recipe ${name}`);
    const d = moduleOf(trainee, M_DATA);
    d.add(D_SP).writeS32(d.add(D_MAX_SP).readS32());
    recipe = { name, r, phase: 'in', since: frame, guard: r.guard, sprint: r.sprint, dropAt: null, dropWhy: null,
        dropDue: null, backAt: null, trace: [] };
    emit('pb-recipe-start', { name, d: round(distance(trainee, player), 100) });
}

function recipeBack() {
    recipe.phase = 'back';
    recipe.backAt = frame;
    recipe.guard = false;
    recipe.sprint = false;
}

// Let go of what the attempt drops, now: the raw bits, the sprint bit and the stick are written
// here rather than on the partner's next frame.
function recipeDrop(why) {
    if (recipe.dropAt !== null) return;
    recipe.dropAt = frame;
    recipe.dropWhy = why;
    if (recipe.r.drop.includes('guard')) recipe.guard = false;
    if (recipe.r.drop.includes('sprint')) recipe.sprint = false;
    wantDash = recipe.sprint;
    const req = traineeModule.add(AR_REQUESTS);
    const lo = req.readU32();
    const want = recipeBits(lo);
    if (want !== lo) req.writeU32(want);
    if (!recipe.sprint) {
        const s = traineeModule.add(AR_STATE);
        s.writeU32((s.readU32() & ~AR_DASH) >>> 0);
    }
    const v = recipeStick();
    if (traineeManip !== null) writeStick(traineeManip, v);
    moveOut = { frame: frame + 1, v };
}

// One partner frame of the recipe test: start, give up or end the backing off, then hold what the
// attempt holds and trace it.
function recipeStep() {
    if (recipe === null) {
        if (pending !== null) return;
        recipeStart();
    }
    if (recipe.phase === 'in') {
        if (recipe.r.lead !== undefined && recipe.dropAt === null && curLeft !== null && curLeft > -0.5
            && curLeft <= recipe.r.lead + 1e-3) recipeDrop('lead');
        // A drop on a prediction no hit followed: the swing missed, or its windup was off.
        const lapsed = recipe.dropAt !== null && frame - recipe.dropAt > cfg.reactFrames;
        if (lapsed || frame - recipe.since > cfg.recipeMaxFrames) {
            emit('pb-recipe', { name: recipe.name, outcome: lapsed ? 'no hit after the drop' : 'never hit',
                frames: frame - recipe.since });
            recipeBack();
        }
    } else if (recipe.phase === 'react') {
        // On the partner's own frame, before UpdateFromManipulator reads its inputs.
        if (recipe.dropDue !== null && recipe.dropAt === null && frame >= recipe.dropDue) recipeDrop('after');
    } else if (recipe.phase === 'back' && frame - recipe.backAt >= cfg.backFrames) {
        recipe = null;
        if (pending !== null) return;
        recipeStart();
    }
    wantDash = recipe.sprint;
    moveOut = { frame: frame + 1, v: recipeStick() };
    if (recipe.phase === 'back') return;
    const asked = reactionOf(trainee);
    recipe.trace.push({ f: frame, anim: traineeAnim, speed: traineeSpeed === null ? null : round(traineeSpeed, 100),
        guardFlag: guardUp(trainee), guard: recipe.guard, sprint: recipe.sprint,
        dash: (traineeModule.add(AR_STATE).readU32() & AR_DASH) !== 0, dl: asked.dl, rdt: asked.rdt });
    if (recipe.phase === 'in' && recipe.trace.length > RECIPE_TRACE_BEFORE) recipe.trace.shift();
}

// A hit's result under its condition. stun is what followed the first hit in the c0000 hit-stun
// (5000-5999) and guard-hit and guard-break (19200-19599) ranges, GUARD_AFTER's clips among them;
// unseen is every animation after it not seen while the partner was unhit, which also catches a
// stop or guard-end clip, so it is a hint and stun is the verdict.
function resolveRecipe(r) {
    const rec = r.recipe === undefined ? null : r.recipe;
    const after = r.anims.filter((x) => r.nextHitAt === null || x.f <= r.nextHitAt);
    const stun = after.filter((x) => (x.anim >= 5000 && x.anim < 6000) || (x.anim >= 19200 && x.anim < 19600));
    const unseen = after.filter((x) => !quiet.has(x.anim) && !pose.has(x.anim));
    const name = rec === null ? 'stray' : rec.name;
    const t = recipeTally[name] || (recipeTally[name] = { n: 0, guarded: 0, stunned: 0, damage: [], staminaLost: [] });
    t.n += 1;
    if (r.guard) t.guarded += 1;
    if (stun.length > 0) t.stunned += 1;
    t.damage.push(r.damage);
    t.staminaLost.push(r.spBefore - r.spMin);
    emit('pb-recipe', { name, outcome: stun.length > 0 ? 'stunned' : 'no stun', guard: r.guard, damage: r.damage,
        hits: r.hits, sp: r.spBefore, staminaLost: r.spBefore - r.spMin, poiseFound: r.poiseFound, poise: r.poise,
        poiseAfter: r.poiseAfter, poiseLater: r.poiseLater, asked: r.asked,
        stun, unseen, anims: r.anims,
        drop: rec === null ? null : rec.dropWhy,
        dropFramesBeforeHit: rec === null || rec.dropAt === null ? null : r.frame - rec.dropAt,
        attack: r.anim, t: r.t, d: r.d,
        trace: rec === null ? [] : rec.trace.map((s) => Object.assign({}, s, { f: s.f - r.frame })) });
    if (rec !== null && recipe === rec) recipeBack();
    try {
        refill(trainee);
    } catch (e) {
        fail('refill', e);
    }
}

function traineeFrame(module, dt) {
    frame += 1;
    traceTurn(dt);
    // The partner's animation changes: a reaction while a hit is resolving, quiet otherwise. One
    // still running from a hit is not a change, so a long damage or block-stun animation never
    // becomes quiet.
    const seen = animsSince(trainee, traineeW === null ? lastAnim(trainee).w : traineeW);
    traineeW = seen.w;
    for (const anim of seen.anims) {
        if (anim === traineeAnim) continue;
        traineeAnim = anim;
        if (pending !== null) pending.anims.push({ anim, f: frame - pending.frame });
        else if (frame - lastHitFrame > cfg.reactFrames) quiet.add(anim);
        if (calib !== null) calib.anims.push(anim);
        if (rollWatch !== null && rollWatch.anims.length < 12) rollWatch.anims.push({ anim, f: frame - rollWatch.frame });
    }
    if (pending !== null) pending.spMin = Math.min(pending.spMin, staminaOf(trainee));
    if (pending !== null && cfg.mode === 'recipe' && frame - pending.frame === 2) {
        pending.poiseLater = round(moduleOf(trainee, M_SUPER_ARMOR).add(SA_DURABILITY).readFloat(), 100);
    }
    if (pending !== null && frame - pending.frame >= cfg.reactFrames) resolve();

    // The player's swing: a new animation id, or the same one restarted.
    const p = lastAnim(player);
    if (swing === null || p.anim !== swing.anim || p.t < swing.t - 0.05) {
        swingIds += 1;
        swing = { id: swingIds, anim: p.anim, t: p.t, step: 0, pressed: false };
    } else {
        if (p.t > swing.t) swing.step = p.t - swing.t;
        swing.t = p.t;
    }
    if (cfg.mode === 'recipe') {
        curLeft = framesLeft(dt);
        recipeStep();
        if (recipe === null) wantDash = false;
        const req = module.add(AR_REQUESTS);
        const lo = req.readU32();
        const want = recipeBits(lo);
        if (want !== lo) req.writeU32(want);
        return;
    }
    pressStep(dt);

    const rolling = press !== null && press.roll === true && frame > press.frame;
    const hold = (press !== null && press.roll !== true) || calibrationStep();
    // The stick for the next frame, written before that frame's UpdateFromManipulator: the roll's
    // direction on its bit frames, or forward from a 'f' guard press until the hit.
    moveOut = null;
    if (press !== null && press.roll === true) {
        const k = frame + 1 - press.frame;
        if (k >= 1 && k <= cfg.rollFrames) moveOut = { frame: frame + 1, v: press.local };
    } else if (press !== null && press.action.move === 'f' && curLeft !== null && curLeft > -1) {
        moveOut = { frame: frame + 1, v: [0, -1] };
    }
    const req = module.add(AR_REQUESTS);
    const lo = req.readU32();
    // The brain never presses L1 or rolls, so a clear bit here undoes only this agent's own press.
    let want = hold ? lo | ACTION_ARM_L1 : lo & ~ACTION_ARM_L1;
    want = (rolling ? want | ACTION_ARM_ROLLING : want & ~ACTION_ARM_ROLLING) >>> 0;
    if (want !== lo) req.writeU32(want);
}

function onHit() {
    const p = lastAnim(player);
    learnWindup(String(p.anim), p.t);
    lastHitFrame = frame;
    if (pending !== null) {
        if (pending.nextHitAt === null) pending.nextHitAt = frame - pending.frame;
        pending.hits += 1;
        return null;
    }
    const pr = lastPress !== null && swing !== null && lastPress.swingId === swing.id ? lastPress : null;
    lastPress = null;
    if (pr !== null && pr.roll) rollWatch = null;
    const sp = staminaOf(trainee);
    pending = { frame, anim: p.anim, t: round(p.t, 1000), guard: guardUp(trainee), recovering: recovering(trainee), press: pr,
        spBefore: sp, spMin: sp,
        framesSincePress: pr ? frame - pr.frame : null,
        releasedFramesBefore: pr && pr.releasedAt !== null ? frame - pr.releasedAt : null,
        anims: [], hits: 1, nextHitAt: null, damage: 0, d: round(distance(trainee, player), 100) };
    emit('pb-hit', { anim: p.anim, t: pending.t, guard: pending.guard, recovering: pending.recovering, pressed: pr !== null,
        framesSincePress: pending.framesSincePress, releasedFramesBefore: pending.releasedFramesBefore });
    // The recipe test lets go here, inside the damage calculation, for an attempt still closing in.
    // Poise (toughness) as the hit found it, then pinned to cfg.recipePoise for the damage
    // calculation about to run, which is the moment that reads it.
    if (cfg.mode === 'recipe') {
        const sa = moduleOf(trainee, M_SUPER_ARMOR).add(SA_DURABILITY);
        pending.poiseFound = round(sa.readFloat(), 100);
        if (cfg.recipePoise !== null && recipe !== null && recipe.phase === 'in') {
            sa.writeFloat(Math.min(cfg.recipePoise, moduleOf(trainee, M_SUPER_ARMOR).add(SA_MAX).readFloat()));
        }
        pending.poise = round(sa.readFloat(), 100);
    }
    if (cfg.mode === 'recipe' && recipe !== null && recipe.phase === 'in') {
        pending.recipe = recipe;
        recipe.phase = 'react';
        if (recipe.r.delay === undefined) recipeDrop('hit');
        else recipe.dropDue = frame + recipe.r.delay;
    }
    return pending;
}

// The partner is looked for again every SCAN_EVERY UpdateFromManipulator calls, which every
// character with an action request module makes once a frame: about a second with a few dozen
// characters, and paced by the game's own frames, not a clock.
const SCAN_EVERY = 2000;
let scanTick = 0;

const updateHook = Interceptor.attach(UPDATE_FROM_MANIPULATOR, {
    onEnter(args) {
        if (++scanTick >= SCAN_EVERY) {
            scanTick = 0;
            scan();
        }
        if (trainee === null || player === null) return;
        try {
            if (!args[0].equals(traineeModule) || !args[0].add(AR_OWNER).readPointer().equals(trainee)) return;
            let dt = args[1].add(FD4TIME_DT).readFloat();
            if (!(dt > 0.001 && dt < 0.1)) dt = 1 / 60;
            traineeFrame(args[0], dt);
        } catch (e) {
            fail('frame', e);
        }
    },
});

const damageHook = Interceptor.attach(CALC_DAMAGE2, {
    onEnter(args) {
        if (trainee === null || player === null) return;
        try {
            if (!args[0].add(8).readPointer().equals(trainee) || !args[1].equals(player)) return;
            this.pb = onHit();
            this.info = args[2];
        } catch (e) {
            fail('hit', e);
        }
    },
    onLeave() {
        if (!this.pb) return;
        try {
            this.pb.damage += this.info.add(DAMAGE_HP).readS32();
            if (cfg.mode === 'recipe' && this.pb.poiseAfter === undefined) {
                this.pb.poiseAfter = round(moduleOf(trainee, M_SUPER_ARMOR).add(SA_DURABILITY).readFloat(), 100);
                this.pb.asked = reactionOf(trainee);
            }
        } catch (e) {
            fail('hit-damage', e);
        }
    },
});

const passHook = Interceptor.attach(PASS_THROUGH, {
    onEnter(args) {
        this.ours = cfg.solidBody && trainee !== null && args[1].equals(trainee);
    },
    onLeave(ret) {
        if (this.ours) ret.replace(ptr(0));
    },
});

const moveHook = Interceptor.attach(MOVE_STEP, {
    onEnter(args) {
        this.m = trainee !== null ? args[0] : null;
    },
    onLeave() {
        if (this.m === null) return;
        try {
            if (!this.m.add(MANIP_OWNER).readPointer().equals(trainee)) return;
            traineeManip = this.m;
            if (moveOut === null || moveOut.frame !== frame + 1) return;
            writeStick(this.m, moveOut.v);
        } catch (e) {
            fail('move', e);
        }
    },
});

// The sprint bit on entry to the step that reads it (recipe mode), and the speed it wrote.
const speedHook = Interceptor.attach(MOVE_SPEED_STEP, {
    onEnter(args) {
        this.ours = traineeCtrl !== null && args[0].equals(traineeCtrl);
        if (!this.ours || wantDash === null || cfg.mode !== 'recipe') return;
        try {
            const s = traineeModule.add(AR_STATE);
            const v = s.readU32();
            const want = (wantDash ? v | AR_DASH : v & ~AR_DASH) >>> 0;
            if (want !== v) s.writeU32(want);
        } catch (e) {
            fail('dash', e);
        }
    },
    onLeave() {
        if (!this.ours) return;
        try {
            traineeSpeed = traineeCtrl.add(CTRL_SPEED).readFloat();
        } catch (e) {
            fail('speed', e);
        }
    },
});

const turnHook = Interceptor.attach(LOCK_TURN, {
    onEnter(args) {
        if (traineeCtrl === null && playerCtrl === null) return;
        try {
            const ctrl = args[0].readPointer();
            const who = traineeCtrl !== null && ctrl.equals(traineeCtrl) ? 'partner'
                : playerCtrl !== null && ctrl.equals(playerCtrl) ? 'player' : null;
            if (who === null) return;
            const s = turnStats[who];
            s.calls += 1;
            const zone = `${round(args[0].add(0x18).readFloat() * 180 / Math.PI, 10)}/${round(args[0].add(0x1c).readFloat() * 180 / Math.PI, 10)}`;
            if (zone in s.zones || Object.keys(s.zones).length < TURN_ZONE_KEYS) s.zones[zone] = (s.zones[zone] || 0) + 1;
            if (who !== 'partner' || !cfg.playerTurn) return;
            args[0].add(0x18).writeFloat(PLAYER_DEAD_ZONE);
            args[0].add(0x1c).writeFloat(PLAYER_DEAD_ZONE);
        } catch (e) {
            fail('turn', e);
        }
    },
});

load();
scan();

rpc.exports = {
    state() {
        return { trainee: trainee && trainee.toString(), calibrated, frame, pose: [...pose], quiet: [...quiet],
            learned };
    },
    // The partner's TimeAct ring as it stands: write index, read index, ten {anim, play time, length}.
    queue() {
        if (trainee === null) return null;
        const ta = moduleOf(trainee, M_TIME_ACT);
        return { w: ta.add(TA_WRITE).readU32(), r: ta.add(TA_WRITE + 4).readU32(), armStyle: armStyle(trainee),
            guard: guardUp(trainee), entries: Array.from({ length: 10 }, (_, i) => {
                const e = ta.add(TA_QUEUE + i * 16);
                return [e.readS32(), round(e.add(4).readFloat(), 1000), round(e.add(12).readFloat(), 1000)];
            }) };
    },
    // Every character on the partner's team, as the scan sees it.
    candidates() {
        const wcm = WORLD_CHR_MAN.readPointer();
        const me = wcm.isNull() ? null : wcm.add(MAIN_PLAYER).readPointer();
        return allChrs().filter((c) => c.add(TEAM_TYPE).readU8() === cfg.team)
            .map((c) => ({ chr: c.toString(), npcParam: c.add(NPC_PARAM).readS32(), isPlayer: me !== null && c.equals(me),
                armStyle: armStyle(c), d: me === null ? null : round(distance(c, me), 100) }));
    },
    // Positions, orientation quaternions and forwardOf() of the partner and the player, with the
    // direction from the partner to the player: the check of forwardOf's convention.
    bodies() {
        if (trainee === null || player === null) return null;
        const body = (c) => ({ pos: posOf(c).map((v) => round(v, 100)), quat: quatOf(c).map((v) => round(v, 1000)),
            fwd: forwardOf(quatOf(c)).map((v) => round(v, 1000)) });
        const a = posOf(trainee);
        const b = posOf(player);
        const len = Math.hypot(b[0] - a[0], b[2] - a[2]);
        return { trainee: body(trainee), player: body(player), toPlayer: [round((b[0] - a[0]) / len, 1000),
            round((b[2] - a[2]) / len, 1000)], aim: rollAim(), sp: staminaOf(trainee) };
    },
    // LOCK_TURN calls and dead zones per character, and the last TURN_TRACE_FRAMES trace samples.
    turn() {
        return { stats: turnStats, trace: turnTrace };
    },
    // Change one cfg value live (a reload restores the configured one): set('breakMargin', 1e9)
    // makes every known swing a roll.
    set(key, value) {
        if (!(key in cfg)) throw new Error(`no cfg.${key}`);
        cfg[key] = value;
        return cfg[key];
    },
    reset() {
        for (const k of Object.keys(learned)) learned[k] = {};
        save();
        return true;
    },
    calibrate() {
        calibrated = false;
        calibTries = 0;
        calib = null;
        return true;
    },
    // The recipe test: the attempt in flight and the tally per condition.
    recipe() {
        return { mode: cfg.mode, recipe: recipe && { name: recipe.name, phase: recipe.phase, guard: recipe.guard,
            sprint: recipe.sprint, speed: traineeSpeed }, tally: recipeTally };
    },
    dispose() {
        updateHook.detach();
        damageHook.detach();
        passHook.detach();
        moveHook.detach();
        turnHook.detach();
        speedHook.detach();
        try {
            if (traineeModule !== null) {
                const req = traineeModule.add(AR_REQUESTS);
                req.writeU32((req.readU32() & ~(ACTION_ARM_L1 | ACTION_ARM_ROLLING | ACTION_ARM_SPRINT)) >>> 0);
                if (cfg.mode === 'recipe') {
                    const s = traineeModule.add(AR_STATE);
                    s.writeU32((s.readU32() & ~AR_DASH) >>> 0);
                }
            }
        } catch (e) {
            // The partner is gone; nothing of ours is left in it.
        }
    },
};
