// Chainsaw glitch driver: performs the whole input sequence itself, one semaphore-gated step at a time.
//
// Notes: docs/er-mechanics/chainsaw/driver.md. The gate it races is docs/er-mechanics/chainsaw/equip-gate.md.
//
// One file with two halves:
//   - the core, a pure state machine: `createDriver(config)` and `driver.step(obs) -> out`. No Frida
//     globals, so node runs it offline: `node scripts/frida/chainsaw-driver.js --selftest`.
//   - the binding, which runs only inside Frida. It ticks the core once per game frame (the main
//     player's `PreBehaviorSafe`), feeds it what the hooks saw, stamps the pad the core asks for into
//     `XInputGetState` (replacing the real pad, so the user's pad cannot reach the game), zeroes the
//     DirectInput keyboard and mouse and the USER32 key and pointer queries, and runs the core's
//     native requests (equip, unequip, FP refill) from `CSFeManImp::Update`, outside the character
//     update.
//
// Run with the watcher, never a plain frida.attach (AGENTS.md); `scripts/er-chainsaw-drive.py`
// prints the exact command and reads the result back.
//
// Every wait is counted in game frames from a game event; nothing here runs on a timer. Every step
// that waits names its semaphore, logs the value that released it, and on running out of frames
// fails with a named state instead of carrying on.
'use strict';

// ---------------------------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------------------------

// `XINPUT_GAMEPAD.wButtons` bits. L2 is not a button: it is the analog `bLeftTrigger`.
const PAD = {
  DPAD_UP: 0x0001, DPAD_DOWN: 0x0002, DPAD_LEFT: 0x0004, DPAD_RIGHT: 0x0008,
  START: 0x0010, BACK: 0x0020, LS: 0x0040, RS: 0x0080, LB: 0x0100, RB: 0x0200,
  A: 0x1000, B: 0x2000, X: 0x4000, Y: 0x8000,
};
const TRIGGER_FULL = 255;
const STICK_FULL = 32767;

// CSChrActionRequestModule request bits: bit n is HKS ACTION_ARM n (common_define.hks).
const ACT = { R1: 1 << 0, L1: 1 << 2, L2: 1 << 3, USE_ITEM: 1 << 7, CHANGE_WEAPON_L: 1 << 10, R3: 1 << 12 };
// The pad taps the core asks for, as request bits.
const TAP_ACT = { 0x0004: ACT.CHANGE_WEAPON_L, 0x0080: ACT.R3 };
// A d-pad tap is not a request bit. The pad manipulator (1.17.1 0x1403daa90 [FUN_1403daa80]) hands a
// short press of action 9/10 to the character as a command, `ChrIns` vtable +0x2c0 with 0x21 (right)
// or 0x22 (left), which only stores it at PlayerIns+0x6a8. The decompile's following
// `SetRequestActionState(USE_ITEM)` is not part of the tap: bit 7 is the item-use button, and sending
// it drank a flask on every swap (user report, 2026-10-04).
const CHR_COMMAND = { CHANGE_WEAPON_R: 0x21, CHANGE_WEAPON_L: 0x22 };

// RTTI names of the windows the confirms walk through, read off the window `MenuWindowJob::Run`
// pumps (job+0x130). All three are in the 1.17.1 image (`scripts/er-rtti-map.py --filter Dialog`).
const CLASS_MAIN = '.?AVMainTopDialog@CS@@';
const CLASS_EQUIP = '.?AVEquipDialog@CS@@';
const CLASS_LIST = '.?AVGaitemSelectDialog@CS@@';

// EquipParamWeapon item ids, upgrade level in the ones (the build under test: 98f687a96d43b1).
const ITEM = {
  GHIZAS_WHEEL_10: 23100010, // Spinning Wheel, SwordArtsParam 1039, swordArtsTypeNew 239
  STARSCOURGE_10: 4050010, // Starcaller Cry, SwordArtsParam 1032, swordArtsTypeNew 232
  FRENZIED_FLAME_SEAL_10: 34090010, // SwordArtsParam 10 "No Skill", isRefRightArts 1
  WATCHDOGS_STAFF_10: 23010010, // SwordArtsParam 1192, isRefRightArts 1: L2 still goes to the right hand
  UNARMED: 110000, // Kick, SwordArtsParam 503, isRefRightArts 1
};

const DEFAULTS = {
  mode: 'chainsaw', // 'chainsaw' | 'control'
  controlWeapon: 'source', // control only: 'source' holds the source skill, 'target' the target's own
  sourceWeapon: ITEM.GHIZAS_WHEEL_10,
  targetWeapon: ITEM.STARSCOURGE_10,
  offhandA: ITEM.FRENZIED_FLAME_SEAL_10, // the active left slot
  offhandB: ITEM.WATCHDOGS_STAFF_10, // the next left slot: the d-pad left swap lands on it
  // Bases allowed in the third left slot. Anything else is unequipped by setup: a shield there would
  // take L2 for its own skill (Spiralhorn Shield's Parry has isRefRightArts 0).
  offhandDefers: [34090000, 23010000, 110000],
  sourceArtsType: 239, // swordArtsTypeNew of the source skill; TimeAct category 600 + 239 = 839
  targetArtsType: 232, // the target's own skill, to tell "its own skill played" apart
  setup: true, // equip the loadout natively before the first attempt
  // Where the drive's input enters the game. 'action' writes the player's CSChrActionRequestModule
  // request bits on entry to UpdateFromManipulator (every device lands there; keyboard input never
  // reaches the pad virtual-key array, 2026-10-04) and makes the mid-skill equip through the game's
  // equip commit, which runs the same gate the menu does. 'pad' stamps XInputGetState and walks the
  // pause menu, which needs a pad the game polls.
  input: 'action',
  listCheck: true, // a dry run through the menu before the first attempt, without the skill (pad input only)
  listNav: 'dpad', // 'dpad' | 'stick' | 'none': how to move a cursor that is not on the target
  navFirst: 'up', // planner order puts the target above the source
  navMax: 40,
  fpFraction: 0.9, // an attempt starts only with fp >= ceil(maxFp * fpFraction)
  refillFp: false, // true: write fp = maxFp when short (test setup, not part of the glitch)
  requireHits: 1, // success needs this many hits computed with the target held
  mainMenuCell: null, // pause-menu GridControl cell of Equipment; null learns it in the dry run
  attempts: 3, // refusals retried before giving up
  lockOn: false,
  menuDelayFrames: 6, // frames between "switch complete" and Start: 0.1 s at 60 fps
  sequence: 'hold', // 'hold': skill first, swap mid-loop. 'pivot': R3, soft swap, L2, commit (the video's pivot method)
  pivotL2After: [2], // frames from the soft-swap tap to L2 down, by attempt (the last value repeats)
  pivotCommitAfter: [0], // frames from the source clip's first frame to the earliest commit, by attempt
  pivotWindowFrames: 90, // the source clip plays this long with the gate shut -> refusal, retry
  pivotCommit: 'tick', // 'tick': commit from the main player's tick; 'fe': from the frontend update, on the source selection
  commitWaitFrames: 30, // the commit waits this many frontend updates for the gate to read open
  repressFrames: 20, // re-hold: with no skill clip yet, L2 is released and pressed again this often
  swapRetapFrames: 30, // a d-pad tap the game dropped is repeated this long after the last (longer than a switch takes to land)
  tapHoldFrames: 2,
  tapGapFrames: 2,
  releaseFrames: 3, // L2 released this long after the equip lands
  loopConfirmFrames: 3, // consecutive frames of the loop clip before the skill counts as running
  idleConfirmFrames: 10, // consecutive frames with no stance clip before a retry may start
  successLoopFrames: 20, // frames of the source loop with the target held
  controlHoldFrames: 300,
  baselineFrames: 10,
  windowStaleFrames: 4, // a window not pumped for this many frames is closed
  setupMaxActions: 8,
  budget: {
    precheck: 120, setup: 240, native: 30, fp: 60, skill: 180, lockOn: 30, swap: 300, menuOpen: 60,
    c1: 60, c2: 60, c3: 60, nav: 20, close: 45, dismiss: 60, idle: 240, success: 300, cleanup: 300,
  },
};

function merge (base, over) {
  const out = {};
  Object.keys(base).forEach(function (k) { out[k] = base[k]; });
  if (over) {
    Object.keys(over).forEach(function (k) {
      if (over[k] === undefined) return;
      if (k === 'budget') out.budget = merge(base.budget, over.budget);
      else out[k] = over[k];
    });
  }
  return out;
}

// Weapon param ids carry infusion in the hundreds and level in the ones; the base is the weapon.
function weaponBase (id) {
  if (id === null || id === undefined || id < 0) return null;
  return id - (id % 10000);
}

// TimeAct ids are `category * 1e6 + clip`; a skill plays from category `600 + swordArtsTypeNew`.
// Stance clips (affected-class.md, BEH): start 040050/040055, loop 040051/040056, end
// 040053/040054/040058.
function stanceKind (anim, artsType) {
  if (anim === null || anim === undefined || anim < 0 || artsType === null) return null;
  if (Math.floor(anim / 1000000) !== 600 + artsType) return null;
  const clip = anim % 1000000;
  if (clip === 40050 || clip === 40055) return 'start';
  if (clip === 40051 || clip === 40056) return 'loop';
  if (clip === 40053 || clip === 40054 || clip === 40058) return 'end';
  return 'other';
}

function kindsOf (anims, artsType) {
  const out = {};
  (anims || []).forEach(function (a) {
    const k = stanceKind(a, artsType);
    if (k !== null) out[k] = true;
  });
  return out;
}

function anyCategory (anims, artsType) {
  if (artsType === null || artsType === undefined) return false;
  return (anims || []).some(function (a) { return a >= 0 && Math.floor(a / 1000000) === 600 + artsType; });
}

// Left ChrAsm slots cycle 0 -> 2 -> 4 -> 0 (WeaponLeft1..3).
function nextLeft (slot) { return (slot + 2) % 6; }

// Failures that end the run. Only a refusal by the equipment gate retries.
const FAIL = {
  PRECHECK_NO_PLAYER: 'precheck_no_player',
  PRECHECK_NO_ARM_SLOTS: 'precheck_no_arm_slots',
  PRECHECK_BLOCK_NOT_READY: 'precheck_block_not_ready',
  PRECHECK_NO_PAD_POLLS: 'precheck_no_pad_polls',
  PRECHECK_NO_ACTION_UPDATES: 'precheck_no_action_updates',
  PRECHECK_MENU_ALREADY_OPEN: 'precheck_menu_already_open',
  SETUP_ITEM_NOT_IN_INVENTORY: 'setup_item_not_in_inventory',
  SETUP_NATIVE_FAILED: 'setup_native_failed',
  SETUP_NO_NATIVE_RESULT: 'setup_no_native_result',
  SETUP_DID_NOT_CONVERGE: 'setup_did_not_converge',
  SETUP_HELD_MISMATCH: 'setup_held_mismatch',
  FP_LOW: 'fp_low',
  FP_REFILL_FAILED: 'fp_refill_failed',
  SKILL_NEVER_STARTED: 'skill_never_started',
  LOCK_ON_FAILED: 'lock_on_failed',
  SWAP_NEVER_COMPLETED: 'swap_never_completed',
  MENU_NEVER_OPENED: 'menu_never_opened',
  MAIN_MENU_CURSOR_UNKNOWN: 'main_menu_cursor_unknown',
  MAIN_MENU_CURSOR_MOVED: 'main_menu_cursor_moved',
  WRONG_MAIN_MENU_ENTRY: 'wrong_main_menu_entry',
  EQUIP_DIALOG_NEVER_OPENED: 'equip_dialog_never_opened',
  WRONG_SLOT_FOCUSED: 'wrong_slot_focused',
  SLOT_NAV_NO_PROGRESS: 'slot_nav_no_progress',
  ITEM_LIST_NEVER_OPENED: 'item_list_never_opened',
  DRY_RUN_REFUSED: 'dry_run_refused',
  HIGHLIGHT_UNKNOWN: 'highlight_unknown',
  TARGET_NOT_HIGHLIGHTED: 'target_not_highlighted',
  NAV_NO_EFFECT: 'nav_no_effect',
  NAV_UNNAMED: 'nav_unnamed',
  TARGET_NOT_IN_LIST: 'target_not_in_list',
  NAV_LIMIT: 'nav_limit',
  COMMIT_REFUSED: 'commit_refused',
  EQUIP_DID_NOT_APPLY: 'equip_did_not_apply',
  NO_EQUIP_EVENT: 'no_equip_event',
  MENU_WOULD_NOT_CLOSE: 'menu_would_not_close',
  REFUSAL_DIALOG_WOULD_NOT_CLOSE: 'refusal_dialog_would_not_close',
  NEVER_IDLE: 'never_idle_after_refusal',
  REFUSED_OUT_OF_ATTEMPTS: 'refused_out_of_attempts',
  NO_SOURCE_SKILL_AFTER_EQUIP: 'no_source_skill_after_equip',
  LOOP_WITHOUT_HITS: 'loop_without_hits',
  TARGET_SKILL_PLAYED: 'target_skill_played',
  FOREIGN_SWITCH: 'foreign_switch',
  FOREIGN_EQUIP: 'foreign_equip',
  FOREIGN_MENU: 'foreign_menu',
  NO_FRAME_TICK: 'no_frame_tick',
  ABORTED: 'aborted',
};

// What one frame looks like to the core. The binding fills it; the selftest fakes it.
//   frame          game frame counter (main player's PreBehaviorSafe)
//   player         bool, the main player exists
//   heldR/heldL    EquipParamWeapon ids held now (GetEquipmentEntryParamId -1 / -2)
//   slots          { 0..5: id } ChrAsm weapon slots in game data (EquipGameData::GetParamIdInSlot)
//   armRight/armLeft  ChrAsmSlot of the active right / left weapon (ChrAsm +0x10 / +0x0c)
//   fp/maxFp       CSChrDataModule +0x148 / +0x14c
//   anims          TimeAct ids the player drove last frame
//   lockOn         PlayerIns+0x6b0 handle is not empty
//   windows        RTTI names of menu windows open now (pumped within windowStaleFrames)
//   mainCursor     selected cell of MainTopDialog's GridControl (+0xa38 +0xd4), or null
//   equipCursor    { idx, slot, cells: { slot: idx } } of EquipDialog's grid, or null
//   highlight      EquipParamWeapon id the item list's detail panel last named, or null
//   highlightSeq   increments on every naming, so a cursor move that names a new record is an event
//   panelSeq       increments on every update of the item list's detail panel, which the list runs
//                  on each cursor move whether or not it then names the record; a step that moves
//                  this and not highlightSeq moved the cursor and lost the name
//   listView       the item list's R3 view (DetailStatusViewParts+0x8f8), or null
//   gate           { open, flags, aaf, frame } from the CSPlayerMenuCtrl predicate, or null
//   events         [{ kind: 'switch'|'equip'|'gate'|'msg'|'hit'|'native'|'fpcharge', frame, ... }]
//   block          { ready, pollsXinput, foreign }
function createDriver (userConfig) {
  const cfg = merge(DEFAULTS, userConfig);
  const control = cfg.mode === 'control';
  const action = cfg.input === 'action';
  const rightWeapon = control && cfg.controlWeapon === 'target' ? cfg.targetWeapon : cfg.sourceWeapon;
  const skillArts = control && cfg.controlWeapon === 'target' ? cfg.targetArtsType : cfg.sourceArtsType;
  const d = {
    cfg: cfg,
    rightWeapon: rightWeapon,
    skillArts: skillArts,
    state: 'PRECHECK',
    enteredAt: null,
    attempt: 1,
    frame: 0,
    l2: false,
    tap: null,
    native: [],
    log: [],
    attempts: [],
    verdict: null,
    baseline: {},
    expect: { switch: false, equip: false, menu: false },
    ctx: {},
    dry: false,
    learned: { mainClass: null, mainCell: cfg.mainMenuCell, listOpenHighlight: null, nav: null, cursorSlot: null },
    setupLog: [],
  };

  function emit (kind, fields) {
    const rec = { kind: kind, frame: d.frame, state: d.state, attempt: d.attempt, dry: d.dry };
    Object.keys(fields || {}).forEach(function (k) { rec[k] = fields[k]; });
    d.log.push(rec);
    return rec;
  }

  function cur () {
    if (!d.attempts[d.attempt - 1]) d.attempts[d.attempt - 1] = { attempt: d.attempt, steps: [], refusal: null, outcome: null };
    return d.attempts[d.attempt - 1];
  }

  // A transition: `sem` is the semaphore value that released the step.
  function go (next, why, sem) {
    const s = sem === undefined ? null : sem;
    emit('step', { from: d.state, to: next, why: why, sem: s, waited: d.enteredAt === null ? 0 : d.frame - d.enteredAt });
    cur().steps.push({ from: d.state, to: next, frame: d.frame, why: why, sem: s, dry: d.dry });
    d.state = next;
    d.enteredAt = d.frame;
    d.ctx.entered = true;
  }

  function waited () { return d.frame - d.enteredAt; }

  function startTap (mask, label, ly) {
    d.tap = { mask: mask, ly: ly || 0, label: label, held: 0, gapped: 0, pressedAt: d.frame };
    emit('tap', { label: label, mask: mask, ly: ly || 0 });
  }

  function tapDone () { return d.tap === null; }

  function request (op) {
    d.native.push(op);
    emit('native_request', op);
  }

  function finish (verdict, extra) {
    d.l2 = false;
    d.tap = null;
    d.verdict = Object.assign({ verdict: verdict, attempts: d.attempt, frame: d.frame, learned: d.learned }, extra || {});
    emit('verdict', d.verdict);
    d.state = 'DONE';
  }

  // Non-retry failures release L2, close any menu the driver opened, and then report.
  function fail (name, sem) {
    const s = sem === undefined ? null : sem;
    emit('fail', { failure: name, sem: s });
    cur().outcome = name;
    d.ctx.failure = name;
    d.ctx.failureSem = s;
    d.l2 = false;
    d.tap = null;
    d.expect = { switch: false, equip: false, menu: true };
    go('CLEANUP', name, s);
  }

  function menuWindows (obs) {
    return (obs.windows || []).filter(function (w) { return !d.baseline[w]; });
  }

  function has (obs, cls) { return (obs.windows || []).indexOf(cls) !== -1; }

  function events (obs, kind) { return (obs.events || []).filter(function (e) { return e.kind === kind; }); }

  // Integrity: every switch, equip and menu the game shows must be one this driver caused. Input
  // is blocked, so anything else means the block did not hold or the game did something on its own;
  // either way the attempt is no longer the experiment it claims to be.
  function integrity (obs) {
    if (d.state === 'DONE' || d.state === 'CLEANUP' || d.state === 'CLEANUP_DONE') return false;
    const sw = events(obs, 'switch');
    const eq = events(obs, 'equip');
    // A soft-swap tap owes exactly one switch, whenever it lands (it can land after the re-hold).
    // One tap fires the switch hook more than once on the frame it lands (three times, 2026-10-04).
    if (d.owedSwitchUntil !== undefined && d.frame <= d.owedSwitchUntil) sw.length = 0;
    if (!d.expect.switch && sw.length > 0) { fail(FAIL.FOREIGN_SWITCH, sw[0]); return true; }
    if (!d.expect.equip && eq.length > 0) { fail(FAIL.FOREIGN_EQUIP, eq[0]); return true; }
    if (!d.expect.menu && d.state !== 'PRECHECK' && menuWindows(obs).length > 0) { fail(FAIL.FOREIGN_MENU, menuWindows(obs)); return true; }
    return false;
  }

  const H = {};

  // Baseline the windows that are up with no menu open, and check what the run depends on.
  H.PRECHECK = function (obs) {
    (obs.windows || []).forEach(function (w) { d.baseline[w] = true; });
    const polls = obs.block ? obs.block.pollsXinput : 0;
    if (d.ctx.polls0 === undefined) d.ctx.polls0 = polls;
    if (waited() < cfg.baselineFrames) return;
    const sem = { player: obs.player, heldR: obs.heldR, heldL: obs.heldL, slots: obs.slots, armRight: obs.armRight, armLeft: obs.armLeft, fp: obs.fp, maxFp: obs.maxFp, block: obs.block ? obs.block.ready : false, padPolls: polls - d.ctx.polls0, baseline: Object.keys(d.baseline) };
    if (!obs.player) return waited() >= cfg.budget.precheck ? fail(FAIL.PRECHECK_NO_PLAYER, sem) : undefined;
    if (!obs.block || !obs.block.ready) return fail(FAIL.PRECHECK_BLOCK_NOT_READY, sem);
    // The injection stage is only real if the game is reading it: no XInputGetState(0) polls in the
    // baseline means every press would land nowhere.
    // A wireless pad that has gone to sleep stops the game polling until it wakes, so wait for the
    // first poll up to `padWaitFrames` (default: the precheck budget) instead of failing at once.
    if (action) {
      // The injection stage is only real if the game runs it for this player every frame.
      sem.actionUpdates = obs.block.actionUpdates;
      if (!(obs.block.actionUpdates >= cfg.baselineFrames)) return fail(FAIL.PRECHECK_NO_ACTION_UPDATES, sem);
    } else if (!(polls > d.ctx.polls0)) return waited() >= (cfg.padWaitFrames || cfg.budget.precheck) ? fail(FAIL.PRECHECK_NO_PAD_POLLS, sem) : undefined;
    if (d.baseline[CLASS_MAIN] || d.baseline[CLASS_EQUIP] || d.baseline[CLASS_LIST]) return fail(FAIL.PRECHECK_MENU_ALREADY_OPEN, sem);
    if ([1, 3, 5].indexOf(obs.armRight) === -1 || [0, 2, 4].indexOf(obs.armLeft) === -1) return fail(FAIL.PRECHECK_NO_ARM_SLOTS, sem);
    if (cfg.setup) { d.expect.equip = true; return go('SETUP', 'precheck passed', sem); }
    go('SETUP_VERIFY', 'precheck passed; setup disabled', sem);
  };

  // The next native action that moves the loadout toward the one the run needs, or null when it is
  // already there. Re-planned from a fresh read every time, so an equip that moved an item out of a
  // slot it was needed in is simply planned again.
  function setupPlan (obs) {
    const R = obs.armRight;
    const L = obs.armLeft;
    const s = obs.slots || {};
    if (weaponBase(s[R]) !== weaponBase(d.rightWeapon)) return { op: 'equip', slot: R, item: d.rightWeapon, why: 'right hand' };
    if (weaponBase(s[L]) !== weaponBase(cfg.offhandA)) return { op: 'equip', slot: L, item: cfg.offhandA, why: 'active left' };
    const L2 = nextLeft(L);
    if (weaponBase(s[L2]) !== weaponBase(cfg.offhandB)) return { op: 'equip', slot: L2, item: cfg.offhandB, why: 'next left' };
    const L3 = nextLeft(L2);
    if (cfg.offhandDefers.indexOf(weaponBase(s[L3])) === -1) return { op: 'unequip', slot: L3, was: s[L3], why: 'third left takes L2' };
    return null;
  }

  // Native setup, one action per wait. The first action asks the inventory for the target so a
  // missing target fails here and not three confirms deep.
  H.SETUP = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.pending = null; d.ctx.actions = 0; d.ctx.foundTarget = false; }
    if (d.ctx.pending !== null) {
      const res = events(obs, 'native').filter(function (e) { return e.id === d.ctx.pending.id; })[0];
      if (res === undefined) {
        if (d.frame - d.ctx.pending.at >= cfg.budget.native) return fail(FAIL.SETUP_NO_NATIVE_RESULT, { pending: d.ctx.pending });
        return;
      }
      d.setupLog.push(res);
      emit('sem', { name: 'native result', result: res });
      if (!res.ok) return fail(res.why === 'not_in_inventory' ? FAIL.SETUP_ITEM_NOT_IN_INVENTORY : FAIL.SETUP_NATIVE_FAILED, res);
      if (d.ctx.pending.op === 'find') d.ctx.foundTarget = true;
      d.ctx.pending = null;
      d.ctx.settle = d.frame;
      return; // the next read shows what the action did
    }
    if (!d.ctx.foundTarget && !control) {
      d.ctx.pending = { op: 'find', item: cfg.targetWeapon, id: d.native.length + 1, at: d.frame };
      return request(d.ctx.pending);
    }
    if (d.ctx.settle !== undefined && d.frame - d.ctx.settle < 1) return;
    const next = setupPlan(obs);
    if (next === null) return go('SETUP_VERIFY', 'loadout planned', { slots: obs.slots, actions: d.ctx.actions });
    if (d.ctx.actions >= cfg.setupMaxActions) return fail(FAIL.SETUP_DID_NOT_CONVERGE, { slots: obs.slots, next: next, actions: d.ctx.actions });
    d.ctx.actions += 1;
    d.ctx.pending = Object.assign({ id: d.native.length + 1, at: d.frame }, next);
    request(d.ctx.pending);
    if (waited() >= cfg.budget.setup) fail(FAIL.SETUP_DID_NOT_CONVERGE, { slots: obs.slots });
  };

  // The character copies ChrAsm on its next update, so the held ids lag the slots by a frame.
  H.SETUP_VERIFY = function (obs) {
    const sem = { heldR: obs.heldR, heldL: obs.heldL, slots: obs.slots, armRight: obs.armRight, armLeft: obs.armLeft };
    const plan = setupPlan(obs);
    if (plan === null && weaponBase(obs.heldR) === weaponBase(d.rightWeapon) && weaponBase(obs.heldL) === weaponBase(cfg.offhandA)) {
      d.expect.equip = false;
      d.ctx.sourceWeapon = obs.heldR;
      d.ctx.rightSlot = obs.armRight;
      return go('FP_CHECK', 'held ids verified', sem);
    }
    if (waited() >= cfg.budget.native) fail(FAIL.SETUP_HELD_MISMATCH, Object.assign({ plan: plan }, sem));
  };

  // Spinning Wheel (type 239) leaves its loop when FP runs out, so an attempt starts near full.
  // FP does not regenerate on its own; with refillFp off a short bar ends the run here.
  H.FP_CHECK = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.refillAsked = false; }
    const need = Math.ceil((obs.maxFp || 0) * cfg.fpFraction);
    const sem = { fp: obs.fp, maxFp: obs.maxFp, need: need };
    if (obs.fp !== null && obs.fp !== undefined && obs.maxFp > 0 && obs.fp >= need) {
      // `swapOnly`: a diagnostic that taps the switch with no skill held and stops when it lands.
      if (cfg.swapOnly) return go('SWAP', 'swap-only diagnostic', sem);
      if (action && cfg.sequence === 'pivot') return go('PIVOT_LOCK', 'fp ready; pivot sequence', sem);
      const next = !control && !action && cfg.listCheck && !d.learned.checked ? 'OPEN_MENU' : 'HOLD_L2';
      if (next === 'OPEN_MENU') d.dry = true;
      return go(next, next === 'OPEN_MENU' ? 'fp ready; dry run first' : 'fp ready', sem);
    }
    if (!cfg.refillFp) return fail(FAIL.FP_LOW, sem);
    if (!d.ctx.refillAsked) { d.ctx.refillAsked = true; request({ op: 'refillFp', id: d.native.length + 1 }); }
    if (waited() >= cfg.budget.fp) fail(FAIL.FP_REFILL_FAILED, sem);
  };

  // Hold L2 until the skill is running. Chainsaw waits for the loop clip `loopConfirmFrames` frames
  // in a row; control waits for any clip of the skill's category (Starcaller Cry has no loop).
  H.HOLD_L2 = function (obs) {
    d.l2 = true;
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.loopRun = 0; d.ctx.sawStart = false; d.ctx.fp0 = obs.fp; }
    const k = kindsOf(obs.anims, d.skillArts);
    if (k.start) d.ctx.sawStart = true;
    if (control && anyCategory(obs.anims, d.skillArts)) return go('CONTROL_HOLD', 'skill clip playing', { anims: obs.anims, fp: obs.fp });
    d.ctx.loopRun = k.loop ? d.ctx.loopRun + 1 : 0;
    // `swapAt: 'start'` taps from the skill's first clip instead of waiting for the loop: the game
    // drops a left-weapon switch while the loop plays (ten taps, 2026-10-04).
    if (!control && cfg.swapAt === 'start' && d.ctx.sawStart) {
      return go('SWAP', 'skill start clip playing', { anims: obs.anims, fp: obs.fp, gate: obs.gate });
    }
    if (!control && d.ctx.loopRun >= cfg.loopConfirmFrames) {
      return go(cfg.lockOn ? 'LOCK_ON' : 'SWAP', 'skill loop running', { anims: obs.anims, loopRun: d.ctx.loopRun, sawStart: d.ctx.sawStart, fp: obs.fp, gate: obs.gate });
    }
    if (waited() >= cfg.budget.skill) fail(FAIL.SKILL_NEVER_STARTED, { anims: obs.anims, sawStart: d.ctx.sawStart, fp: obs.fp, heldR: obs.heldR, heldL: obs.heldL });
  };

  // The pivot method from the tutorial the user sent (V21QjSIzGzs, "Works as of patch 1.17.1"):
  // R3, d-pad left, L2 held, then the menu's three confirms, then L2 again. The soft swap comes
  // before the skill: L2 lands in the swap animation, and the equip is made before the skill's state
  // starts closing the gate. With action input the confirms are the commit, so the only free numbers
  // are the two delays, swept across attempts (`pivotL2After`, `pivotCommitAfter`).
  function pick (list) { return list[Math.min(d.attempt - 1, list.length - 1)]; }

  H.PIVOT_LOCK = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; startTap(PAD.RS, 'R3 (pivot)'); }
    if (tapDone()) go('SOFT_SWAP', 'R3 tapped', { lockOn: obs.lockOn });
  };

  H.SOFT_SWAP = function (obs) {
    if (d.ctx.entered) {
      d.ctx.entered = false;
      d.ctx.heldL0 = obs.heldL;
      d.ctx.switchSeen = null;
      d.ctx.swapTap = d.frame;
      d.ctx.l2After = pick(cfg.pivotL2After);
      d.ctx.commitAfter = pick(cfg.pivotCommitAfter);
      d.expect.switch = false; // owedSwitchUntil covers this tap's switch
      d.owedSwitchUntil = d.frame + 90;
      startTap(PAD.DPAD_LEFT, 'd-pad left (soft swap)');
    }
    const sw = events(obs, 'switch');
    if (sw.length > 0 && d.ctx.switchSeen === null) d.ctx.switchSeen = sw[0];
    if (!d.l2 && d.frame - d.ctx.swapTap >= d.ctx.l2After) {
      d.l2 = true; d.ctx.l2Frame = d.frame; d.ctx.sourceFrame = null; d.ctx.window = [];
      emit('sem', { name: 'L2 down', framesAfterSwapTap: d.frame - d.ctx.swapTap, anims: obs.anims, gate: obs.gate });
    }
    if (!d.l2) return;
    // 'fe': the commit is queued now and made by the frontend update itself, on the first update
    // after the source clip is selected with the gate open. The menu's commit runs there too, and
    // the main player's tick (where `obs` is read) never saw the two together (2026-10-05).
    if (cfg.pivotCommit === 'fe') {
      d.ctx.swapFrame = d.ctx.swapTap;
      d.ctx.whenSource = true;
      return go('EQUIP_DIRECT', 'L2 down; commit queued for the source selection', { l2After: d.ctx.l2After, anims: obs.anims, gate: obs.gate });
    }
    // The commit waits for the source skill to be playing: until its clip is chosen the equip only
    // changes which skill L2 starts (two commits on the frame of L2 down, 2026-10-05, both played the
    // target's own skill). Every frame from L2 down is recorded, so a run that never commits still
    // shows whether the clip and an open gate ever coincided.
    const k = kindsOf(obs.anims, cfg.sourceArtsType);
    const source = !!(k.start || k.loop);
    const open = !!(obs.gate && obs.gate.open);
    if (source && d.ctx.sourceFrame === null) d.ctx.sourceFrame = d.frame;
    if (d.ctx.window.length < 120) d.ctx.window.push({ f: d.frame - d.ctx.l2Frame, anims: obs.anims, flags: obs.gate ? obs.gate.flags : null, aaf: obs.gate ? obs.gate.aaf : null, open: open });
    const why = { l2After: d.ctx.l2After, sourceAfterL2: d.ctx.sourceFrame === null ? null : d.ctx.sourceFrame - d.ctx.l2Frame, switchEvent: d.ctx.switchSeen, heldL: obs.heldL, anims: obs.anims, gate: obs.gate, window: d.ctx.window };
    if (source && open && d.frame - d.ctx.sourceFrame >= d.ctx.commitAfter) {
      d.ctx.swapFrame = d.ctx.swapTap;
      return go('EQUIP_DIRECT', 'source clip playing, gate open', why);
    }
    const shut = d.ctx.sourceFrame !== null && d.frame - d.ctx.sourceFrame >= cfg.pivotWindowFrames;
    const never = d.ctx.sourceFrame === null && d.frame - d.ctx.l2Frame >= cfg.budget.skill;
    if (shut || never) {
      cur().refusal = { pivot: shut ? 'gate_shut_while_source_played' : 'source_never_started', l2After: d.ctx.l2After, window: d.ctx.window };
      return go('REFUSED_IDLE', shut ? 'gate never open while the source clip played' : 'source clip never started', why);
    }
  };

  H.LOCK_ON = function (obs) {
    if (d.ctx.entered) {
      d.ctx.entered = false;
      if (obs.lockOn) return go('SWAP', 'already locked on', { lockOn: true });
      startTap(PAD.RS, 'R3 lock-on');
    }
    if (obs.lockOn) return go('SWAP', 'lock-on handle set', { lockOn: true });
    if (waited() >= cfg.budget.lockOn) fail(FAIL.LOCK_ON_FAILED, { lockOn: obs.lockOn });
  };

  // D-pad left with L2 still held. Complete when the switch function has run and the held left
  // weapon is a different one.
  H.SWAP = function (obs) {
    if (d.ctx.entered) {
      d.ctx.entered = false;
      d.ctx.heldL0 = obs.heldL;
      d.ctx.switchSeen = null;
      d.expect.switch = true;
      d.tap0 = d.frame;
      startTap(PAD.DPAD_LEFT, 'd-pad left');
    }
    const sw = events(obs, 'switch');
    if (sw.length > 0 && d.ctx.switchSeen === null) d.ctx.switchSeen = sw[0];
    // The game drops a tap on a frame its current animation does not accept one (measured: the
    // command is taken and the weapon stays), so tap again until the switch runs.
    if (d.ctx.switchSeen === null && tapDone() && d.frame - d.tap0 >= cfg.swapRetapFrames) {
      d.ctx.taps = (d.ctx.taps || 1) + 1;
      d.tap0 = d.frame;
      startTap(PAD.DPAD_LEFT, 'd-pad left (again)');
    }
    if (d.ctx.switchSeen !== null && obs.heldL !== d.ctx.heldL0) {
      d.ctx.swapFrame = d.frame;
      if (cfg.swapOnly) { d.expect.switch = false; return finish('swap_only_switched', { taps: d.ctx.taps || 1, heldL0: d.ctx.heldL0, heldL: obs.heldL, switchEvent: d.ctx.switchSeen }); }
      return go('MENU_DELAY', 'switch ran and left weapon changed', { taps: d.ctx.taps || 1, heldL0: d.ctx.heldL0, heldL: obs.heldL, switchEvent: d.ctx.switchSeen, gate: obs.gate });
    }
    if (waited() >= cfg.budget.swap) fail(FAIL.SWAP_NEVER_COMPLETED, { heldL0: d.ctx.heldL0, heldL: obs.heldL, switchEvent: d.ctx.switchSeen });
  };

  // Counted from the switch-complete frame, not from a clock.
  H.MENU_DELAY = function (obs) {
    if (d.frame - d.ctx.swapFrame >= cfg.menuDelayFrames) {
      d.expect.switch = false;
      return go(action ? 'EQUIP_DIRECT' : 'OPEN_MENU', 'frames since switch complete', { frames: d.frame - d.ctx.swapFrame, gate: obs.gate });
    }
  };

  // Start. Released by the pause menu's window being pumped.
  H.OPEN_MENU = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; d.expect.menu = true; startTap(PAD.START, 'Start'); }
    const open = menuWindows(obs);
    if (has(obs, CLASS_MAIN)) return go('C1', 'MainTopDialog open', { windows: open, mainCursor: obs.mainCursor, gate: obs.gate });
    if (waited() >= cfg.budget.menuOpen) fail(FAIL.MENU_NEVER_OPENED, { windows: open });
  };

  // Confirm 1: Equipment. Pressed only with the pause-menu cursor on the cell the dry run learned
  // is Equipment (or `mainMenuCell`). The dry run learns it from the outcome of its own press.
  H.C1 = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.c1Pressed = false; }
    if (!d.ctx.c1Pressed) {
      if (obs.mainCursor === null || obs.mainCursor === undefined) {
        if (waited() >= cfg.budget.c1) fail(FAIL.MAIN_MENU_CURSOR_UNKNOWN, { windows: menuWindows(obs) });
        return;
      }
      if (d.learned.mainCell !== null && obs.mainCursor !== d.learned.mainCell) {
        return fail(FAIL.MAIN_MENU_CURSOR_MOVED, { mainCursor: obs.mainCursor, equipmentCell: d.learned.mainCell });
      }
      d.ctx.c1Cell = obs.mainCursor;
      d.ctx.c1Pressed = true;
      emit('sem', { name: 'pre-C1', mainCursor: obs.mainCursor, gate: obs.gate });
      startTap(PAD.A, 'confirm 1 (Equipment)');
      return;
    }
    if (tapDone() && has(obs, CLASS_EQUIP)) {
      if (d.dry) d.learned.mainCell = d.ctx.c1Cell;
      return go('C2', 'EquipDialog open', { windows: menuWindows(obs), mainCell: d.ctx.c1Cell });
    }
    const other = menuWindows(obs).filter(function (w) { return w !== CLASS_MAIN && w !== CLASS_EQUIP; });
    if (tapDone() && other.length > 0) return fail(FAIL.WRONG_MAIN_MENU_ENTRY, { opened: other, mainCursor: d.ctx.c1Cell });
    if (waited() >= cfg.budget.c1) fail(FAIL.EQUIP_DIALOG_NEVER_OPENED, { windows: menuWindows(obs) });
  };

  // A d-pad or stick tap that moves a cursor one step, released by the cursor's own value changing.
  function navTap (dir, label) {
    if (cfg.listNav === 'stick' && (dir === 'up' || dir === 'down')) return startTap(0, label + ' (stick)', dir === 'up' ? STICK_FULL : -STICK_FULL);
    const m = { up: PAD.DPAD_UP, down: PAD.DPAD_DOWN, left: PAD.DPAD_LEFT, right: PAD.DPAD_RIGHT }[dir];
    startTap(m, label);
  }

  // Confirm 2: the slot. Pressed only with the cursor on the right-hand slot that holds the source
  // weapon; a cursor on another weapon cell is stepped toward it, each step released by the grid's
  // selected cell changing. Released by the gate's own verdict at the EquipDialog confirm handler.
  H.C2 = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.c2Pressed = false; d.ctx.c2Verdict = null; d.ctx.slotNav = null; }
    if (!d.ctx.c2Pressed) {
      const ec = obs.equipCursor;
      if (ec === null || ec === undefined) {
        if (waited() >= cfg.budget.c2) fail(FAIL.EQUIP_DIALOG_NEVER_OPENED, { equipCursor: ec });
        return;
      }
      if (d.ctx.slotNav !== null) {
        if (!tapDone()) return;
        if (ec.idx === d.ctx.slotNav.from) {
          if (d.frame - d.ctx.slotNav.at >= cfg.budget.nav) return fail(FAIL.SLOT_NAV_NO_PROGRESS, { equipCursor: ec, rightSlot: d.ctx.rightSlot });
          return;
        }
        emit('sem', { name: 'slot cursor moved', from: d.ctx.slotNav.from, to: ec.idx, slot: ec.slot });
        d.ctx.slotNav = null;
      }
      if (ec.slot !== d.ctx.rightSlot) {
        const want = ec.cells ? ec.cells[d.ctx.rightSlot] : undefined;
        // Stepping is only attempted from another right-hand cell: the three sit in one row, so a
        // horizontal step reaches the wanted one. Anywhere else the geometry is not known.
        if (d.ctx.slotNavFrom === undefined) d.ctx.slotNavFrom = ec.slot;
        if (cfg.listNav === 'none' || want === undefined || [1, 3, 5].indexOf(d.ctx.slotNavFrom) === -1) {
          return fail(FAIL.WRONG_SLOT_FOCUSED, { equipCursor: ec, rightSlot: d.ctx.rightSlot });
        }
        d.ctx.slotNav = { from: ec.idx, at: d.frame };
        navTap(want > ec.idx ? 'right' : 'left', 'slot nav');
        return;
      }
      if (d.dry) d.learned.cursorSlot = ec.slot;
      emit('sem', { name: 'pre-C2', equipCursor: ec, gate: obs.gate });
      d.ctx.c2Pressed = true;
      d.ctx.c2PressFrame = d.frame;
      startTap(PAD.A, 'confirm 2 (slot)');
      return;
    }
    events(obs, 'gate').forEach(function (e) { if (e.site === 'slot_confirm' && d.ctx.c2Verdict === null) d.ctx.c2Verdict = e; });
    const refusedMsg = events(obs, 'msg').filter(function (e) { return e.id === 103130; });
    if ((d.ctx.c2Verdict !== null && !d.ctx.c2Verdict.ok) || refusedMsg.length > 0) {
      if (d.dry) return fail(FAIL.DRY_RUN_REFUSED, { verdict: d.ctx.c2Verdict, gate: obs.gate });
      cur().refusal = { verdict: d.ctx.c2Verdict, msg: refusedMsg.length > 0, gate: obs.gate, framesAfterSwap: d.frame - d.ctx.swapFrame };
      return go('REFUSED', 'gate refused the slot', { verdict: d.ctx.c2Verdict, msg103130: refusedMsg.length > 0, gate: obs.gate });
    }
    if (has(obs, CLASS_LIST)) return go('C3', 'GaitemSelectDialog open', { verdict: d.ctx.c2Verdict, highlight: obs.highlight, panelSeq: obs.panelSeq, listView: obs.listView });
    if (d.frame - d.ctx.c2PressFrame >= cfg.budget.c2) fail(FAIL.ITEM_LIST_NEVER_OPENED, { verdict: d.ctx.c2Verdict, windows: menuWindows(obs) });
  };

  // Confirm 3: the target. Pressed only when the detail panel names the target weapon. A cursor on
  // another record is stepped (navFirst, then the other way once), each step released by a new
  // naming; a step with no naming inside budget.nav is the end of the list in that direction.
  H.C3 = function (obs) {
    if (d.ctx.entered) {
      d.ctx.entered = false; d.ctx.c3Pressed = false; d.ctx.commit = null;
      d.ctx.nav = { dir: cfg.navFirst, flipped: false, steps: 0, moved: 0, seq: null, panel: null, at: null, visited: {} };
    }
    const want = weaponBase(cfg.targetWeapon);
    const nav = d.ctx.nav;
    if (!d.ctx.c3Pressed) {
      if (nav.seq !== null) {
        if (!tapDone()) return;
        if (obs.highlightSeq === nav.seq) {
          if (d.frame - nav.at < cfg.budget.nav) return;
          const panelMoved = nav.panel !== null && obs.panelSeq !== undefined && obs.panelSeq !== nav.panel;
          emit('sem', { name: 'nav step named nothing', dir: nav.dir, highlight: obs.highlight, panelMoved: panelMoved, panelSeq: obs.panelSeq, listView: obs.listView });
          // The cursor moved (the panel updated) and no record was named: the highlight semaphore
          // is blind, so whatever it says next is not the cursor. Stop rather than walk blind.
          if (panelMoved) return fail(FAIL.NAV_UNNAMED, { highlight: obs.highlight, nav: nav, panelSeq: obs.panelSeq, listView: obs.listView });
          nav.seq = null;
          if (nav.flipped) return fail(nav.moved === 0 ? FAIL.NAV_NO_EFFECT : FAIL.TARGET_NOT_IN_LIST, { highlight: obs.highlight, nav: nav, panelSeq: obs.panelSeq, listView: obs.listView });
          nav.flipped = true;
          nav.dir = nav.dir === 'up' ? 'down' : 'up';
        } else {
          nav.moved += 1;
          nav.seq = null;
          emit('sem', { name: 'nav step', dir: nav.dir, highlight: obs.highlight, seq: obs.highlightSeq });
          if (nav.visited[obs.highlight] && weaponBase(obs.highlight) !== want) {
            if (nav.flipped) return fail(FAIL.TARGET_NOT_IN_LIST, { highlight: obs.highlight, nav: nav });
            nav.flipped = true;
            nav.dir = nav.dir === 'up' ? 'down' : 'up';
          }
        }
      }
      if (obs.highlight === null || obs.highlight === undefined) {
        if (waited() >= cfg.budget.c3) fail(FAIL.HIGHLIGHT_UNKNOWN, { highlight: null });
        return;
      }
      if (d.learned.listOpenHighlight === null && d.dry) d.learned.listOpenHighlight = obs.highlight;
      if (d.ctx.firstHighlight === undefined) d.ctx.firstHighlight = obs.highlight;
      nav.visited[obs.highlight] = true;
      if (weaponBase(obs.highlight) === want) {
        if (d.dry) {
          d.learned.nav = { steps: nav.steps, moved: nav.moved, dir: nav.dir };
          d.learned.checked = true;
          return go('DRY_CLOSE', 'target highlighted in the dry run', { highlight: obs.highlight, listOpenHighlight: d.learned.listOpenHighlight, nav: d.learned.nav });
        }
        d.ctx.target = obs.highlight;
        emit('sem', { name: 'pre-C3', highlight: obs.highlight, gate: obs.gate, navSteps: nav.steps });
        d.ctx.c3Pressed = true;
        d.ctx.c3PressFrame = d.frame;
        d.expect.equip = true;
        startTap(PAD.A, 'confirm 3 (target)');
        return;
      }
      if (cfg.listNav === 'none') return fail(FAIL.TARGET_NOT_HIGHLIGHTED, { highlight: obs.highlight, target: cfg.targetWeapon });
      if (nav.steps >= cfg.navMax) return fail(FAIL.NAV_LIMIT, { highlight: obs.highlight, nav: nav });
      nav.steps += 1;
      nav.seq = obs.highlightSeq;
      nav.panel = obs.panelSeq === undefined ? null : obs.panelSeq;
      nav.at = d.frame;
      navTap(nav.dir, 'list nav');
      return;
    }
    events(obs, 'gate').forEach(function (e) { if (e.site === 'commit' && d.ctx.commit === null) d.ctx.commit = e; });
    const eq = events(obs, 'equip').filter(function (e) { return e.slot === d.ctx.rightSlot; });
    if (eq.length > 0) {
      d.expect.equip = false;
      if (weaponBase(eq[0].heldR) === weaponBase(d.ctx.target) || weaponBase(obs.heldR) === weaponBase(d.ctx.target)) {
        d.ctx.equipFrame = d.frame;
        cur().equip = { frame: d.frame, framesAfterSwap: d.frame - d.ctx.swapFrame, heldR: obs.heldR };
        return go('RELEASE_L2', 'equip event for the right slot, target held', { equip: eq[0], heldR: obs.heldR, commit: d.ctx.commit });
      }
      if (d.ctx.commit !== null && !d.ctx.commit.ok) return fail(FAIL.COMMIT_REFUSED, { commit: d.ctx.commit, heldR: obs.heldR });
      return fail(FAIL.EQUIP_DID_NOT_APPLY, { equip: eq[0], heldR: obs.heldR, commit: d.ctx.commit });
    }
    if (d.frame - d.ctx.c3PressFrame >= cfg.budget.c3) {
      if (d.ctx.commit !== null && !d.ctx.commit.ok) return fail(FAIL.COMMIT_REFUSED, { commit: d.ctx.commit });
      fail(FAIL.NO_EQUIP_EVENT, { commit: d.ctx.commit, heldR: obs.heldR });
    }
  };

  // Counted from the equip event's frame. L2 is released while the menu is still open: a button
  // held through the menu stays masked after it closes (UpdateFromManipulator 0x140408190).
  H.RELEASE_L2 = function (obs) {
    d.l2 = false;
    if (d.frame - d.ctx.equipFrame >= cfg.releaseFrames) return go(action ? 'REHOLD_L2' : 'CLOSE_MENU', 'L2 released after equip', { frames: d.frame - d.ctx.equipFrame, anims: obs.anims });
  };

  // Action input: the equip the menu's third confirm would commit, made by calling the commit
  // (EquipItemToChrAsmSlot) with L2 still held. The commit asks the gate itself, so a refusal here is
  // the same refusal the menu would show. Released by the native result.
  H.EQUIP_DIRECT = function (obs) {
    if (d.ctx.entered) {
      d.ctx.entered = false;
      d.ctx.commit = null;
      d.expect.equip = true;
      d.ctx.target = cfg.targetWeapon;
      d.ctx.pending = { op: 'equip', slot: d.ctx.rightSlot, item: cfg.targetWeapon, id: 1000 * d.attempt + d.frame, at: d.frame, whenOpen: true, waitFrames: cfg.commitWaitFrames };
      if (d.ctx.whenSource) { delete d.ctx.pending.whenOpen; d.ctx.pending.whenSource = 600 + cfg.sourceArtsType; d.ctx.pending.waitFrames = cfg.pivotWindowFrames; }
      emit('sem', { name: 'pre-commit', gate: obs.gate, framesAfterSwap: d.frame - d.ctx.swapFrame });
      return request(d.ctx.pending);
    }
    events(obs, 'gate').forEach(function (e) { if (e.site === 'commit' && d.ctx.commit === null) d.ctx.commit = e; });
    const res = events(obs, 'native').filter(function (e) { return e.id === d.ctx.pending.id; })[0];
    if (res === undefined) {
      if (d.frame - d.ctx.pending.at >= cfg.budget.native + d.ctx.pending.waitFrames) fail(FAIL.NO_EQUIP_EVENT, { pending: d.ctx.pending, commit: d.ctx.commit });
      return;
    }
    d.expect.equip = false;
    if (res.ok) {
      d.ctx.target = res.after === undefined ? cfg.targetWeapon : res.after;
      d.ctx.equipFrame = d.frame;
      cur().equip = { frame: d.frame, framesAfterSwap: d.frame - d.ctx.swapFrame, heldR: obs.heldR, commit: d.ctx.commit };
      return go('RELEASE_L2', 'commit equipped the target', { native: res, commit: d.ctx.commit, gate: obs.gate });
    }
    if (['slot_unchanged', 'gate_shut_after_selection', 'source_not_selected'].indexOf(res.why) === -1) return fail(FAIL.SETUP_NATIVE_FAILED, res);
    cur().refusal = { verdict: d.ctx.commit, native: res, gate: obs.gate, framesAfterSwap: d.frame - d.ctx.swapFrame };
    go('REFUSED_IDLE', 'commit refused', { native: res, commit: d.ctx.commit, gate: obs.gate });
  };

  // B until no menu window is left. Each press is released by the window count dropping.
  function closeStep (obs, next, failName) {
    const open = menuWindows(obs);
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.closeCount = null; d.ctx.closeAt = d.frame; }
    if (open.length === 0) { d.expect.menu = false; return go(next, 'no menu window open', { windows: obs.windows }); }
    if (d.ctx.closeCount === null || open.length < d.ctx.closeCount) {
      if (d.tap !== null) return;
      d.ctx.closeCount = open.length;
      d.ctx.closeAt = d.frame;
      startTap(PAD.B, 'B close (' + open.join(',') + ')');
      return;
    }
    if (d.tap === null && d.frame - d.ctx.closeAt >= cfg.budget.close) {
      if (failName === null) return go(next, 'cleanup gave up closing', { windows: open, closeFailed: true });
      fail(failName, { windows: open });
    }
  }

  H.CLOSE_MENU = function (obs) { closeStep(obs, 'REHOLD_L2', FAIL.MENU_WOULD_NOT_CLOSE); };

  // The dry run backs out the way it came in, then the first real attempt starts from FP_CHECK.
  H.DRY_CLOSE = function (obs) {
    closeStep(obs, 'FP_CHECK', FAIL.MENU_WOULD_NOT_CLOSE);
    if (d.state === 'FP_CHECK') d.dry = false;
  };

  // Hold L2 again with the target in hand. Success is the source skill's loop clip playing while the
  // target is held, plus `requireHits` hits computed with the target held. The target's own skill
  // playing is the glitch not surviving.
  H.REHOLD_L2 = function (obs) {
    if (d.ctx.entered) { d.ctx.entered = false; d.expect.switch = false; d.ctx.loopOnTarget = 0; d.ctx.hitsOnTarget = 0; d.ctx.sourceFrames = 0; d.ctx.fpAtRehold = obs.fp; d.ctx.charges = []; d.ctx.pressAt = d.frame; d.ctx.presses = 1; d.ctx.anySkill = false; }
    const onTarget = weaponBase(obs.heldR) === weaponBase(d.ctx.target);
    const k = kindsOf(obs.anims, cfg.sourceArtsType);
    if (anyCategory(obs.anims, cfg.sourceArtsType) || anyCategory(obs.anims, cfg.targetArtsType)) d.ctx.anySkill = true;
    // A press the game was not ready for (still in the swap animation) is lost, and holding does
    // not press again (measured: request bit held 60 frames, no clip). Until a skill clip shows,
    // release for 2 frames and press again every `repressFrames`, as a player would.
    const since = d.frame - d.ctx.pressAt;
    d.l2 = d.ctx.anySkill || since < cfg.repressFrames;
    if (!d.ctx.anySkill && since >= cfg.repressFrames + 2) { d.ctx.pressAt = d.frame; d.ctx.presses += 1; d.l2 = true; }
    if (k.loop || k.start) d.ctx.sourceFrames += 1;
    if (onTarget && k.loop) d.ctx.loopOnTarget += 1;
    events(obs, 'hit').forEach(function (h) { if (weaponBase(h.heldR) === weaponBase(d.ctx.target)) d.ctx.hitsOnTarget += 1; });
    events(obs, 'fpcharge').forEach(function (c) { if (d.ctx.charges.length < 40) d.ctx.charges.push(c); });
    const evidence = function () {
      return { presses: d.ctx.presses, loopOnTarget: d.ctx.loopOnTarget, hitsOnTarget: d.ctx.hitsOnTarget, sourceFrames: d.ctx.sourceFrames, fpAtRehold: d.ctx.fpAtRehold, fp: obs.fp, charges: d.ctx.charges, heldR: obs.heldR, anims: obs.anims };
    };
    if (anyCategory(obs.anims, cfg.targetArtsType)) {
      cur().outcome = FAIL.TARGET_SKILL_PLAYED;
      d.ctx.final = Object.assign({ verdict: FAIL.TARGET_SKILL_PLAYED }, evidence());
      return go('FINAL_RELEASE', 'target skill clip seen', d.ctx.final);
    }
    if (d.ctx.loopOnTarget >= cfg.successLoopFrames && d.ctx.hitsOnTarget >= cfg.requireHits) {
      cur().outcome = 'success';
      d.ctx.final = Object.assign({ verdict: 'success' }, evidence());
      return go('FINAL_RELEASE', 'source loop playing on the target and hitting', d.ctx.final);
    }
    if (waited() >= cfg.budget.success) {
      const v = d.ctx.loopOnTarget >= cfg.successLoopFrames ? FAIL.LOOP_WITHOUT_HITS : FAIL.NO_SOURCE_SKILL_AFTER_EQUIP;
      cur().outcome = v;
      d.ctx.final = Object.assign({ verdict: v }, evidence());
      return go('FINAL_RELEASE', 'success budget spent', d.ctx.final);
    }
  };

  H.FINAL_RELEASE = function (obs) {
    d.l2 = false;
    // A pivot attempt whose timing missed (the target's own skill, or no skill) is a sample of the
    // sweep, not the end of it: put the source weapon back and take the next timing.
    const missed = d.ctx.final.verdict === FAIL.TARGET_SKILL_PLAYED || d.ctx.final.verdict === FAIL.NO_SOURCE_SKILL_AFTER_EQUIP;
    if (action && cfg.sequence === 'pivot' && missed && d.attempt < cfg.attempts) {
      if (d.ctx.entered) { d.ctx.entered = false; d.ctx.idleRun = 0; }
      d.ctx.idleRun = anyCategory(obs.anims, cfg.targetArtsType) || anyCategory(obs.anims, cfg.sourceArtsType) ? 0 : d.ctx.idleRun + 1;
      if (d.ctx.idleRun < cfg.idleConfirmFrames) return;
      emit('retry', { after: d.ctx.final.verdict });
      d.attempt += 1;
      d.ctx = {};
      d.expect = { switch: false, equip: true, menu: false };
      return go('SETUP', 'pivot timing missed; next timing', { previous: d.attempts[d.attempt - 2].outcome });
    }
    finish(d.ctx.final.verdict, d.ctx.final);
  };

  // The refusal: dismiss the dialog, close the menus, let the skill end, try again. The dialog is
  // whatever window appears that was not up before the refusal; if none is pumped, the close loop
  // below backs out of whatever is.
  H.REFUSED = function (obs) {
    if (d.ctx.entered) {
      d.ctx.entered = false; d.ctx.dialogClass = null; d.ctx.dismissPressed = false; d.ctx.known = {};
      [CLASS_MAIN, CLASS_EQUIP, CLASS_LIST].forEach(function (c) { d.ctx.known[c] = true; });
    }
    if (d.ctx.dialogClass === null) {
      const fresh = menuWindows(obs).filter(function (w) { return !d.ctx.known[w]; });
      if (fresh.length > 0) { d.ctx.dialogClass = fresh[0]; emit('sem', { name: 'refusal dialog', dialogClass: fresh[0] }); }
      else if (waited() >= cfg.budget.dismiss) {
        cur().refusal.dialogClass = null;
        return go('REFUSED_CLOSE', 'no refusal window pumped; backing out', { windows: menuWindows(obs) });
      }
      return;
    }
    if (!d.ctx.dismissPressed) { d.ctx.dismissPressed = true; d.ctx.dismissAt = d.frame; startTap(PAD.A, 'dismiss refusal'); return; }
    if (tapDone() && !has(obs, d.ctx.dialogClass)) {
      cur().refusal.dialogClass = d.ctx.dialogClass;
      return go('REFUSED_CLOSE', 'refusal dialog closed', { dialogClass: d.ctx.dialogClass });
    }
    if (d.frame - d.ctx.dismissAt >= cfg.budget.dismiss) fail(FAIL.REFUSAL_DIALOG_WOULD_NOT_CLOSE, { dialogClass: d.ctx.dialogClass, windows: menuWindows(obs) });
  };

  H.REFUSED_CLOSE = function (obs) { d.l2 = false; closeStep(obs, 'REFUSED_IDLE', FAIL.MENU_WOULD_NOT_CLOSE); };

  // Released by `idleConfirmFrames` frames in a row with no stance clip of the source skill.
  H.REFUSED_IDLE = function (obs) {
    d.l2 = false;
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.idleRun = 0; }
    const k = kindsOf(obs.anims, cfg.sourceArtsType);
    d.ctx.idleRun = (k.start || k.loop || k.end) ? 0 : d.ctx.idleRun + 1;
    if (d.ctx.idleRun >= cfg.idleConfirmFrames) {
      if (d.attempt >= cfg.attempts) {
        cur().outcome = FAIL.REFUSED_OUT_OF_ATTEMPTS;
        return finish(FAIL.REFUSED_OUT_OF_ATTEMPTS, { refusals: d.attempts.map(function (a) { return a.refusal; }) });
      }
      cur().outcome = 'refused';
      const idleRun = d.ctx.idleRun;
      emit('retry', { idleRun: idleRun });
      d.attempt += 1;
      d.ctx = { sourceWeapon: d.ctx.sourceWeapon, rightSlot: d.ctx.rightSlot };
      d.expect = { switch: false, equip: false, menu: false };
      go('FP_CHECK', 'player idle; retry', { idleRun: idleRun, fp: obs.fp });
      return;
    }
    if (waited() >= cfg.budget.idle) fail(FAIL.NEVER_IDLE, { anims: obs.anims });
  };

  // Control: the plain skill, no swap and no menu. Clip, hit and FP counts are the comparison.
  H.CONTROL_HOLD = function (obs) {
    d.l2 = true;
    if (d.ctx.entered) { d.ctx.entered = false; d.ctx.loopFrames = 0; d.ctx.clipFrames = 0; d.ctx.hits = []; d.ctx.charges = []; }
    if (kindsOf(obs.anims, d.skillArts).loop) d.ctx.loopFrames += 1;
    if (anyCategory(obs.anims, d.skillArts)) d.ctx.clipFrames += 1;
    events(obs, 'hit').forEach(function (h) { d.ctx.hits.push(h); });
    events(obs, 'fpcharge').forEach(function (c) { if (d.ctx.charges.length < 40) d.ctx.charges.push(c); });
    if (waited() >= cfg.controlHoldFrames) {
      d.ctx.final = { verdict: 'control_done', weapon: obs.heldR, artsType: d.skillArts, loopFrames: d.ctx.loopFrames, clipFrames: d.ctx.clipFrames, hits: d.ctx.hits.length, fpAtStart: d.ctx.fp0, fp: obs.fp, charges: d.ctx.charges, hitDetail: d.ctx.hits.slice(0, 50) };
      go('FINAL_RELEASE', 'control hold spent', { loopFrames: d.ctx.loopFrames, clipFrames: d.ctx.clipFrames, hits: d.ctx.hits.length });
    }
  };

  H.CLEANUP = function (obs) {
    d.l2 = false;
    if (d.ctx.entered && menuWindows(obs).length === 0) {
      return finish(d.ctx.failure, { sem: d.ctx.failureSem });
    }
    closeStep(obs, 'CLEANUP_DONE', null);
  };

  H.CLEANUP_DONE = function (obs) { finish(d.ctx.failure, { sem: d.ctx.failureSem, windowsLeft: menuWindows(obs) }); };

  // Advance the tap, then build this frame's pad.
  function pad () {
    let buttons = 0;
    let ly = 0;
    // A d-pad left tap as the manipulator delivers it: request bit 10 while the button is down, then
    // on the release frame the 0x22 command (with bit 7, added below).
    let command = 0;
    if (d.tap !== null && (d.tap.mask & PAD.DPAD_LEFT) && d.tap.held === d.cfg.tapHoldFrames && d.tap.gapped === 0) command = CHR_COMMAND.CHANGE_WEAPON_L;
    if (d.tap !== null) {
      if (d.tap.held < d.cfg.tapHoldFrames) { d.tap.held += 1; buttons |= d.tap.mask; ly = d.tap.ly; }
      else if (d.tap.gapped < d.cfg.tapGapFrames) { d.tap.gapped += 1; }
      if (d.tap.held >= d.cfg.tapHoldFrames && d.tap.gapped >= d.cfg.tapGapFrames) d.tap = null;
    }
    const native = d.native;
    d.native = [];
    let actions = d.l2 ? ACT.L2 : 0;
    Object.keys(TAP_ACT).forEach(function (m) { if (buttons & Number(m)) actions |= TAP_ACT[m]; });
    return { buttons: buttons, lt: d.l2 ? TRIGGER_FULL : 0, ly: ly, actions: actions, command: command, native: native };
  }

  d.step = function (obs) {
    d.frame = obs.frame;
    if (d.enteredAt === null) { d.enteredAt = d.frame; d.ctx.entered = true; }
    if (d.state === 'DONE') return { buttons: 0, lt: 0, ly: 0, native: [], done: true };
    if (!integrity(obs)) {
      const handler = H[d.state];
      if (!handler) throw new Error('no handler for ' + d.state);
      handler(obs);
      // A state entered this frame runs its first frame now, so a press does not lose a frame.
      if (d.ctx.entered && d.state !== 'DONE' && H[d.state]) H[d.state](obs);
    }
    const p = pad();
    if (d.state === 'DONE') return { buttons: 0, lt: 0, ly: 0, native: [], done: true };
    return p;
  };

  d.abort = function (why) {
    if (d.state === 'DONE') return;
    emit('abort', { why: why });
    if (why === FAIL.NO_FRAME_TICK) { finish(FAIL.NO_FRAME_TICK, {}); return; }
    fail(FAIL.ABORTED, { why: why });
  };

  d.summary = function () {
    return { state: d.state, attempt: d.attempt, verdict: d.verdict, attempts: d.attempts, learned: d.learned, setup: d.setupLog, baseline: Object.keys(d.baseline) };
  };

  return d;
}

// ---------------------------------------------------------------------------------------------
// Selftest: a fake game that reacts to the driver's pad, frame by frame.
// ---------------------------------------------------------------------------------------------

const EQUIP_CELLS = [1, 3, 5, 0, 2, 4];

function fakeGame (opt) {
  const T = opt.target === undefined ? ITEM.STARSCOURGE_10 : opt.target;
  const g = {
    frame: 0,
    // The build as imported: Bloodfiend's Arm R1, Misericorde R3, Spiralhorn Shield L1.
    slots: { 0: 30190000, 1: 12530000, 2: 110000, 3: 110000, 4: 110000, 5: 1030000 },
    armRight: 1, armLeft: 0,
    inventory: opt.inventory || [ITEM.GHIZAS_WHEEL_10, T, ITEM.FRENZIED_FLAME_SEAL_10, ITEM.WATCHDOGS_STAFF_10],
    target: T, fp: opt.fp === undefined ? 75 : opt.fp, maxFp: 75,
    stance: null, stanceFrames: 0, windows: ['HUD'], pending: [], events: [], prevButtons: 0, prevLy: 0,
    lockOn: false, gateOpen: false, refusals: opt.refusals || 0,
    mainCursor: opt.mainCursor === undefined ? 0 : opt.mainCursor,
    // The item list in game order; the cursor opens on `listStart`.
    list: opt.list || [T, ITEM.GHIZAS_WHEEL_10, 1030000],
    listIdx: 0, highlightSeq: 0, panelSeq: 0,
  };
  g.listIdx = opt.listStart === undefined ? 0 : opt.listStart;
  function later (frames, fn) { g.pending.push({ at: g.frame + frames, fn: fn }); }
  function addWin (w) { if (g.windows.indexOf(w) === -1) g.windows.push(w); }
  function delWin (w) { g.windows = g.windows.filter(function (x) { return x !== w; }); }
  function top () { return g.windows[g.windows.length - 1]; }
  function held (slot) { return g.slots[slot]; }
  // A cursor move updates the detail panel; `unnamedMoves` is the game as it was before the driver
  // forced the panel's naming on: the panel updates and names nothing.
  function name () { g.panelSeq += 1; if (!opt.unnamedMoves || g.listNamedOnce !== true) g.highlightSeq += 1; g.listNamedOnce = true; }
  g.obs = function () {
    let anims = [];
    const arts = weaponBase(held(g.armRight)) === weaponBase(T) && !g.chainsaw ? 232 : 239;
    if (g.stance !== null) anims = [(600 + arts) * 1000000 + g.stance];
    if (opt.noSkill) anims = [];
    if (g.ownSkill) anims = [(600 + 232) * 1000000 + 40000];
    const inList = g.windows.indexOf(CLASS_LIST) !== -1;
    const o = {
      frame: g.frame, player: true, heldR: held(g.armRight), heldL: held(g.armLeft),
      slots: Object.assign({}, g.slots), armRight: g.armRight, armLeft: g.armLeft,
      fp: g.fp, maxFp: g.maxFp, anims: anims, lockOn: g.lockOn,
      windows: g.windows.slice(),
      mainCursor: g.windows.indexOf(CLASS_MAIN) !== -1 ? g.mainCursor : null,
      // Right hand first in one row, then the left: cell i edits EQUIP_CELLS[i].
      equipCursor: g.windows.indexOf(CLASS_EQUIP) !== -1 ? { idx: g.equipIdx, slot: EQUIP_CELLS[g.equipIdx], cells: { 1: 0, 3: 1, 5: 2, 0: 3, 2: 4, 4: 5 } } : null,
      highlight: inList && !opt.noHighlight ? g.list[g.listIdx] : null,
      highlightSeq: g.highlightSeq, panelSeq: g.panelSeq, listView: inList ? 0 : null,
      gate: { open: g.gateOpen && !(opt.gateShutInStance && g.stance !== null), flags: 0, frame: g.frame }, events: g.events,
      block: { ready: true, foreign: {}, pollsXinput: opt.noPolls ? 0 : g.frame * 2, actionUpdates: opt.noActionUpdates ? 0 : g.frame },
    };
    g.events = [];
    return o;
  };
  g.native = function (op) {
    const ev = { kind: 'native', id: op.id, op: op.op, slot: op.slot, item: op.item, ok: true, why: null };
    if (op.op === 'find' || op.op === 'equip') {
      if (g.inventory.indexOf(op.item) === -1) { ev.ok = false; ev.why = 'not_in_inventory'; }
    }
    if (ev.ok && op.op === 'equip') {
      if (opt.equipNoop) { g.events.push(ev); return; }
      // The commit's own gate: a refusal leaves the slot as it was.
      if (g.stance !== null && g.refusals > 0) { g.refusals -= 1; ev.ok = false; ev.why = 'slot_unchanged'; g.events.push(ev); return; }
      if (g.stance !== null) { g.afterEquip = true; g.chainsaw = true; }
      Object.keys(g.slots).forEach(function (s) { if (g.slots[s] === op.item) g.slots[s] = 110000; });
      g.slots[op.slot] = op.item;
      g.events.push({ kind: 'equip', slot: op.slot, heldR: held(g.armRight) });
    }
    if (ev.ok && op.op === 'unequip') g.slots[op.slot] = 110000;
    if (op.op === 'refillFp') g.fp = g.maxFp;
    g.events.push(ev);
  };
  g.apply = function (p0) {
    // Action input reaches the fake as the pad it stands for.
    const p = opt.action ? { buttons: (p0.command === CHR_COMMAND.CHANGE_WEAPON_L ? PAD.DPAD_LEFT : 0) | (p0.actions & ACT.R3 ? PAD.RS : 0), lt: p0.actions & ACT.L2 ? TRIGGER_FULL : 0, ly: 0, native: p0.native } : p0;
    const pressed = p.buttons & ~g.prevButtons;
    const stickEdge = p.ly !== 0 && g.prevLy === 0 ? (p.ly > 0 ? 'up' : 'down') : null;
    g.prevButtons = p.buttons;
    g.prevLy = p.ly;
    (p.native || []).forEach(function (op) { later(1, function () { g.native(op); }); });
    g.pending.filter(function (x) { return x.at <= g.frame; }).forEach(function (x) { x.fn(); });
    g.pending = g.pending.filter(function (x) { return x.at > g.frame; });
    // L2 drives the stance: start for 30 frames, then loop; release ends it.
    if (p.lt > 0 && !opt.noSkill) {
      if (g.stance === null && !g.inMenu) { g.stance = 40050; g.stanceFrames = 0; }
      if (g.stance !== null) { g.stanceFrames += 1; if (g.stanceFrames > 30) g.stance = 40051; }
      if (g.afterEquip && opt.ownSkillAfterEquip) g.ownSkill = true;
      if (g.afterEquip && g.stance === 40051 && !opt.noHits && g.frame % 10 === 0) g.events.push({ kind: 'hit', heldR: held(g.armRight), frame: g.frame });
    } else if (g.stance !== null) {
      g.stance = null;
    }
    if (pressed & PAD.RS) later(3, function () { g.lockOn = true; });
    // `dropTaps`: the game takes that many switch taps and does nothing with them.
    const dropped = (pressed & PAD.DPAD_LEFT) && (g.dropped || 0) < (opt.dropTaps || 0);
    if (dropped) g.dropped = (g.dropped || 0) + 1;
    if ((pressed & PAD.DPAD_LEFT) && !dropped && !g.inMenu && !opt.noSwap) {
      later(8, function () {
        g.events.push({ kind: 'switch', frame: g.frame });
        let n = nextLeft(g.armLeft);
        while (g.slots[n] === 110000 && n !== g.armLeft) n = nextLeft(n);
        g.armLeft = n; g.gateOpen = true;
      });
    }
    if (opt.foreignSwitchAt !== undefined && g.frame === opt.foreignSwitchAt) g.events.push({ kind: 'switch', frame: g.frame });
    if (pressed & PAD.START) later(5, function () { addWin(CLASS_MAIN); });
    const t = top();
    if (t === CLASS_LIST && !opt.navDead) {
      let dir = stickEdge;
      if (pressed & PAD.DPAD_UP) dir = 'up';
      if (pressed & PAD.DPAD_DOWN) dir = 'down';
      if (dir === 'up' && g.listIdx > 0) later(2, function () { g.listIdx -= 1; name(); });
      if (dir === 'down' && g.listIdx < g.list.length - 1) later(2, function () { g.listIdx += 1; name(); });
    }
    if (t === CLASS_EQUIP && !opt.navDead) {
      if (pressed & PAD.DPAD_RIGHT) later(2, function () { g.equipIdx += 1; });
      if (pressed & PAD.DPAD_LEFT) later(2, function () { g.equipIdx -= 1; });
    }
    if (pressed & PAD.A) {
      if (t === CLASS_MAIN) later(5, function () { addWin(g.mainCursor === 0 ? CLASS_EQUIP : 'InventoryDialog'); g.equipIdx = EQUIP_CELLS.indexOf(opt.cursorSlot === undefined ? 1 : opt.cursorSlot); });
      else if (t === CLASS_EQUIP) {
        const ok = g.refusals === 0 || !g.stance;
        later(1, function () {
          g.events.push({ kind: 'gate', site: 'slot_confirm', slot: g.equipIdx, ok: ok, frame: g.frame });
          if (ok) later(4, function () { addWin(CLASS_LIST); g.listIdx = opt.listStart === undefined ? 0 : opt.listStart; name(); });
          else { g.refusals -= 1; g.events.push({ kind: 'msg', id: 103130, frame: g.frame }); later(3, function () { addWin('MsgDialog'); }); }
        });
      } else if (t === CLASS_LIST) {
        later(2, function () {
          const commitOk = !opt.commitRefused;
          g.events.push({ kind: 'gate', site: 'commit', slot: g.armRight, ok: commitOk, frame: g.frame });
          if (commitOk) { g.slots[g.armRight] = g.list[g.listIdx]; g.afterEquip = true; g.chainsaw = true; }
          g.events.push({ kind: 'equip', slot: g.armRight, heldR: held(g.armRight), frame: g.frame });
        });
      } else if (t === 'MsgDialog') later(3, function () { delWin('MsgDialog'); });
    }
    if (pressed & PAD.B) {
      if (t !== 'HUD') later(4, function () { delWin(t); });
    }
    g.inMenu = g.windows.length > 1;
    if (g.afterEquip && g.stance !== null && opt.ownSkillAfterEquip) g.stance = null;
    g.frame += 1;
  };
  return g;
}

function simulate (config, opt, maxFrames) {
  const g = fakeGame(opt || {});
  // The cases below exercise the menu walk; the fake game reacts to pad buttons.
  const d = createDriver(Object.assign({ input: (opt || {}).action ? 'action' : 'pad' }, config));
  const pads = [];
  for (let i = 0; i < (maxFrames || 6000); i++) {
    const p = d.step(g.obs());
    pads.push(p);
    if (p.done) break;
    g.apply(p);
  }
  return { d: d, g: g, pads: pads };
}

function runSelftest () {
  let failed = 0;
  let passed = 0;
  function check (name, cond, detail) {
    if (cond) { passed += 1; console.log('ok   ' + name); }
    else { failed += 1; console.log('FAIL ' + name + (detail === undefined ? '' : ' :: ' + JSON.stringify(detail))); }
  }
  const v = function (r) { return r.d.verdict ? r.d.verdict.verdict : null; };
  const steps = function (r) { return r.d.log.filter(function (e) { return e.kind === 'step'; }); };

  // Action input: the request bits drive the skill and the switch, the commit makes the equip.
  let r = simulate({}, { action: true });
  check('action input ends in success', v(r) === 'success', r.d.verdict);
  check('action input visits no menu step', JSON.stringify(steps(r).map(function (e) { return e.to; })) ===
    JSON.stringify(['SETUP', 'SETUP_VERIFY', 'FP_CHECK', 'HOLD_L2', 'SWAP', 'MENU_DELAY', 'EQUIP_DIRECT', 'RELEASE_L2', 'REHOLD_L2', 'FINAL_RELEASE']), steps(r).map(function (e) { return e.to; }));
  check('action input holds L2 as request bit 3', r.pads.some(function (p) { return p.actions === ACT.L2; }));
  check('action input switches with the left-weapon command once, L2 held', r.pads.filter(function (p) { return p.command === CHR_COMMAND.CHANGE_WEAPON_L; }).length === 1 &&
    r.pads.some(function (p) { return p.command === CHR_COMMAND.CHANGE_WEAPON_L && p.actions === ACT.L2; }) &&
    r.pads.every(function (p) { return (p.actions & ACT.USE_ITEM) === 0; }));
  r = simulate({}, { action: true, refusals: 1 });
  check('action input: a refused commit retries and then succeeds', v(r) === 'success' && r.d.attempt === 2 && r.d.attempts[0].refusal !== null, r.d.verdict);
  r = simulate({ sequence: 'pivot' }, { action: true });
  check('pivot: R3, soft swap, L2, commit, re-hold, success', v(r) === 'success' &&
    JSON.stringify(steps(r).map(function (e) { return e.to; })) === JSON.stringify(['SETUP', 'SETUP_VERIFY', 'FP_CHECK', 'PIVOT_LOCK', 'SOFT_SWAP', 'EQUIP_DIRECT', 'RELEASE_L2', 'REHOLD_L2', 'FINAL_RELEASE']),
  { verdict: r.d.verdict, steps: steps(r).map(function (e) { return e.to; }) });
  check('pivot: L2 goes down pivotL2After frames after the swap tap; the commit waits for the source clip', (function () {
    const l2 = r.d.log.filter(function (e) { return e.kind === 'sem' && e.name === 'L2 down'; })[0];
    const c = steps(r).filter(function (e) { return e.to === 'EQUIP_DIRECT'; })[0];
    return l2 && l2.framesAfterSwapTap === 2 && c && c.sem.sourceAfterL2 !== null && kindsOf(c.sem.anims, 239).start === true;
  })(), steps(r).filter(function (e) { return e.to === 'EQUIP_DIRECT'; })[0]);
  r = simulate({ sequence: 'pivot', attempts: 3, pivotL2After: [2, 4, 6] }, { action: true, refusals: 2 });
  check('pivot: refused commits retry with the next L2 delay', v(r) === 'success' && r.d.attempt === 3 &&
    steps(r).filter(function (e) { return e.to === 'EQUIP_DIRECT'; }).map(function (e) { return e.sem.l2After; }).join(',') === '2,4,6', r.d.verdict);
  r = simulate({ sequence: 'pivot', attempts: 2 }, { action: true, gateShutInStance: true });
  check('pivot: a gate shut for the whole source clip is a refusal with the frame window recorded, never a commit',
    v(r) === FAIL.REFUSED_OUT_OF_ATTEMPTS && steps(r).every(function (e) { return e.to !== 'EQUIP_DIRECT'; }) &&
    r.d.attempts[0].refusal.pivot === 'gate_shut_while_source_played' && r.d.attempts[0].refusal.window.length > 0, r.d.verdict);
  r = simulate({ sequence: 'pivot', pivotCommit: 'fe' }, { action: true });
  check('pivot fe: the commit is queued at L2 down for the source category, with no tick gate wait', v(r) === 'success' &&
    r.d.log.some(function (e) { return e.kind === 'native_request' && e.op === 'equip' && e.whenSource === 839 && e.whenOpen === undefined; }), r.d.verdict);
  r = simulate({ sequence: 'pivot', attempts: 1 }, { action: true, noSkill: true });
  check('pivot: no source clip after L2 -> source_never_started refusal', v(r) === FAIL.REFUSED_OUT_OF_ATTEMPTS &&
    r.d.attempts[0].refusal.pivot === 'source_never_started', r.d.verdict);
  r = simulate({}, { action: true, dropTaps: 2 });
  check('action input: dropped switch taps are repeated until one lands, and only once more', v(r) === 'success' &&
    r.pads.filter(function (p) { return p.command === CHR_COMMAND.CHANGE_WEAPON_L; }).length === 3, r.d.verdict);
  r = simulate({}, { action: true, noActionUpdates: true });
  check('action input: no action updates -> precheck refuses', v(r) === FAIL.PRECHECK_NO_ACTION_UPDATES, r.d.verdict);

  r = simulate({}, {});
  check('happy path ends in success', v(r) === 'success', r.d.verdict);
  check('happy path took one attempt', r.d.attempt === 1, r.d.attempt);
  const order = steps(r).map(function (e) { return e.to; });
  const want = ['SETUP', 'SETUP_VERIFY', 'FP_CHECK', 'OPEN_MENU', 'C1', 'C2', 'C3', 'DRY_CLOSE', 'FP_CHECK',
    'HOLD_L2', 'SWAP', 'MENU_DELAY', 'OPEN_MENU', 'C1', 'C2', 'C3', 'RELEASE_L2', 'CLOSE_MENU', 'REHOLD_L2', 'FINAL_RELEASE'];
  check('happy path visits the steps in order', JSON.stringify(order) === JSON.stringify(want), order);
  check('every transition carries its semaphore', steps(r).every(function (e) { return e.sem !== null; }));
  const setupDone = steps(r).filter(function (e) { return e.from === 'SETUP_VERIFY'; })[0];
  check('setup put the wheel in the right hand and both off-hand items in the left cycle', setupDone &&
    setupDone.sem.slots[1] === ITEM.GHIZAS_WHEEL_10 && setupDone.sem.slots[0] === ITEM.FRENZIED_FLAME_SEAL_10 && setupDone.sem.slots[2] === ITEM.WATCHDOGS_STAFF_10, setupDone);
  check('setup looked the target up in the inventory first', r.d.setupLog[0] && r.d.setupLog[0].op === 'find' && r.d.setupLog[0].ok, r.d.setupLog);
  check('the dry run learned the Equipment cell and the list-open highlight',
    r.d.learned.mainCell === 0 && r.d.learned.listOpenHighlight === ITEM.STARSCOURGE_10 && r.d.learned.checked === true, r.d.learned);
  check('the dry run pressed no third confirm', !r.d.log.some(function (e) { return e.kind === 'tap' && e.dry && e.label.indexOf('confirm 3') === 0; }));
  const delay = steps(r).filter(function (e) { return e.to === 'OPEN_MENU' && !e.dry; })[0];
  check('Start waits menuDelayFrames after the switch completes', delay && delay.sem.frames === 6, delay);
  check('L2 is held through the confirms', (function () {
    const c3 = r.d.log.filter(function (e) { return e.kind === 'tap' && e.label.indexOf('confirm 3') === 0; })[0];
    return c3 && r.pads[c3.frame].lt === TRIGGER_FULL;
  })());
  check('L2 is released before the menu closes', (function () {
    const close = steps(r).filter(function (e) { return e.to === 'CLOSE_MENU'; })[0];
    const b = r.d.log.filter(function (e) { return e.kind === 'tap' && e.label.indexOf('B close') === 0 && !e.dry; })[0];
    return close && b && r.pads[close.frame].lt === 0 && b.frame >= close.frame;
  })());
  check('a tap is tapHoldFrames down then released', (function () {
    const t = r.d.log.filter(function (e) { return e.kind === 'tap' && e.label === 'd-pad left'; })[0];
    return r.pads[t.frame].buttons === PAD.DPAD_LEFT && r.pads[t.frame + 1].buttons === PAD.DPAD_LEFT && r.pads[t.frame + 2].buttons === 0;
  })());
  check('success carries hit and loop evidence', r.d.verdict.hitsOnTarget >= 1 && r.d.verdict.loopOnTarget >= 20, r.d.verdict);
  check('pad is all zero after the verdict', r.pads[r.pads.length - 1].buttons === 0 && r.pads[r.pads.length - 1].lt === 0);

  r = simulate({}, { list: [ITEM.GHIZAS_WHEEL_10, 1030000, ITEM.STARSCOURGE_10], listStart: 0 });
  check('target below the cursor: up finds the top, then down walks to it', v(r) === 'success' && r.d.learned.nav.moved === 2, r.d.learned);

  r = simulate({ listNav: 'stick' }, { list: [ITEM.STARSCOURGE_10, ITEM.GHIZAS_WHEEL_10], listStart: 1 });
  check('stick navigation is gated on the naming too', v(r) === 'success' && r.d.log.some(function (e) { return e.kind === 'tap' && e.ly === STICK_FULL; }), r.d.verdict);

  r = simulate({}, { list: [ITEM.GHIZAS_WHEEL_10, ITEM.STARSCOURGE_10], listStart: 0, navDead: true });
  check('a list that ignores the pad -> nav_no_effect', v(r) === FAIL.NAV_NO_EFFECT, r.d.verdict);
  check('a failure closes the menus it opened', r.g.windows.length === 1, r.g.windows);
  check('nav_no_effect says the panel never updated', (function () {
    const f = r.d.log.filter(function (e) { return e.kind === 'sem' && e.name === 'nav step named nothing'; });
    return f.length === 2 && f.every(function (e) { return e.panelMoved === false; });
  })());

  r = simulate({}, { list: [1030000, ITEM.GHIZAS_WHEEL_10, ITEM.STARSCOURGE_10], listStart: 1, unnamedMoves: true });
  check('a cursor that moves without a naming -> nav_unnamed, not nav_no_effect', v(r) === FAIL.NAV_UNNAMED, r.d.verdict);
  check('nav_unnamed stops after the first silent step', r.d.log.filter(function (e) { return e.kind === 'tap' && e.label.indexOf('list nav') === 0; }).length === 1);

  r = simulate({}, { list: [ITEM.GHIZAS_WHEEL_10, 1030000], listStart: 0 });
  check('target missing from the list -> target_not_in_list', v(r) === FAIL.TARGET_NOT_IN_LIST, r.d.verdict);

  r = simulate({ listNav: 'none' }, { list: [ITEM.GHIZAS_WHEEL_10, ITEM.STARSCOURGE_10], listStart: 0 });
  check('nav off and another item highlighted -> target_not_highlighted', v(r) === FAIL.TARGET_NOT_HIGHLIGHTED, r.d.verdict);

  r = simulate({}, { inventory: [ITEM.GHIZAS_WHEEL_10, ITEM.FRENZIED_FLAME_SEAL_10, ITEM.WATCHDOGS_STAFF_10] });
  check('target not in the inventory -> setup_item_not_in_inventory', v(r) === FAIL.SETUP_ITEM_NOT_IN_INVENTORY, r.d.verdict);

  r = simulate({}, { equipNoop: true });
  check('equips that change nothing -> setup_did_not_converge', v(r) === FAIL.SETUP_DID_NOT_CONVERGE, r.d.verdict);

  r = simulate({}, { fp: 40 });
  check('FP short of 90 percent -> fp_low', v(r) === FAIL.FP_LOW, r.d.verdict);
  r = simulate({ refillFp: true }, { fp: 40 });
  check('refillFp tops FP up and the run proceeds', v(r) === 'success', r.d.verdict);

  r = simulate({}, { mainCursor: 1 });
  check('the dry run press opened something else -> wrong_main_menu_entry', v(r) === FAIL.WRONG_MAIN_MENU_ENTRY, r.d.verdict);
  r = simulate({ mainMenuCell: 0, listCheck: false }, { mainCursor: 2 });
  check('cursor off the Equipment cell -> main_menu_cursor_moved, nothing pressed', v(r) === FAIL.MAIN_MENU_CURSOR_MOVED &&
    !r.d.log.some(function (e) { return e.kind === 'tap' && e.label.indexOf('confirm 1') === 0; }), r.d.verdict);

  r = simulate({}, { cursorSlot: 3 });
  check('equip cursor on R2 is stepped to R1 before confirm 2', v(r) === 'success' && r.d.log.some(function (e) { return e.kind === 'tap' && e.label === 'slot nav'; }), r.d.verdict);
  r = simulate({}, { cursorSlot: 0 });
  check('equip cursor on a left slot -> wrong_slot_focused', v(r) === FAIL.WRONG_SLOT_FOCUSED, r.d.verdict);

  r = simulate({ attempts: 3 }, { refusals: 1 });
  check('one refusal then success', v(r) === 'success' && r.d.attempt === 2, r.d.verdict);
  check('the refusal is recorded on attempt 1', r.d.attempts[0] && r.d.attempts[0].refusal !== null && r.d.attempts[0].outcome === 'refused', r.d.attempts[0]);
  check('the refusal dialog was dismissed by its own window closing', r.d.log.some(function (e) { return e.kind === 'step' && e.to === 'REFUSED_CLOSE' && e.sem.dialogClass === 'MsgDialog'; }));
  check('a retry re-checks FP first', r.d.log.some(function (e) { return e.kind === 'step' && e.from === 'REFUSED_IDLE' && e.to === 'FP_CHECK'; }));

  r = simulate({ attempts: 2 }, { refusals: 5 });
  check('refusals past the attempt budget end the run', v(r) === FAIL.REFUSED_OUT_OF_ATTEMPTS && r.d.attempt === 2, r.d.verdict);

  r = simulate({}, { noSkill: true });
  check('no skill clip -> skill_never_started', v(r) === FAIL.SKILL_NEVER_STARTED, r.d.verdict);
  const fl = r.d.log.filter(function (e) { return e.kind === 'fail'; })[0];
  const held = steps(r).filter(function (e) { return e.to === 'HOLD_L2'; })[0];
  check('skill_never_started fires at exactly budget.skill frames', fl && held && fl.frame - held.frame === DEFAULTS.budget.skill, [fl, held]);

  r = simulate({}, { noSwap: true });
  check('no switch event -> swap_never_completed', v(r) === FAIL.SWAP_NEVER_COMPLETED, r.d.verdict);

  r = simulate({}, { noHighlight: true });
  check('no naming in the list -> highlight_unknown', v(r) === FAIL.HIGHLIGHT_UNKNOWN, r.d.verdict);

  r = simulate({}, { commitRefused: true });
  check('commit refused by the gate -> commit_refused', v(r) === FAIL.COMMIT_REFUSED, r.d.verdict);

  r = simulate({}, { foreignSwitchAt: 20 });
  check('a switch the driver did not press -> foreign_switch', v(r) === FAIL.FOREIGN_SWITCH, r.d.verdict);

  r = simulate({}, { ownSkillAfterEquip: true });
  check('the target own skill after the equip -> target_skill_played', v(r) === FAIL.TARGET_SKILL_PLAYED, r.d.verdict);

  r = simulate({}, { noHits: true });
  check('loop on the target with nothing to hit -> loop_without_hits', v(r) === FAIL.LOOP_WITHOUT_HITS, r.d.verdict);

  r = simulate({ mode: 'control', controlHoldFrames: 60 }, {});
  check('control (Spinning Wheel) holds the skill and reports', v(r) === 'control_done' && r.d.verdict.loopFrames > 0 && r.d.verdict.artsType === 239, r.d.verdict);
  check('control never opens a menu', r.d.log.every(function (e) { return e.kind !== 'tap' || e.label === 'R3 lock-on'; }));
  r = simulate({ mode: 'control', controlWeapon: 'target', controlHoldFrames: 60 }, {});
  check('control (Starcaller Cry) puts the target in the right hand', v(r) === 'control_done' && r.d.verdict.artsType === 232 &&
    weaponBase(r.d.verdict.weapon) === weaponBase(ITEM.STARSCOURGE_10) && r.d.verdict.clipFrames > 0, r.d.verdict);

  r = simulate({ lockOn: true }, {});
  check('lock-on step is gated on the lock-on handle', r.d.log.some(function (e) { return e.kind === 'step' && e.to === 'SWAP' && e.sem.lockOn === true; }) && v(r) === 'success', r.d.verdict);

  r = simulate({}, { noPolls: true });
  check('no XInput polls in the baseline -> precheck refuses', v(r) === FAIL.PRECHECK_NO_PAD_POLLS, r.d.verdict);

  const dd = createDriver({});
  dd.step({ frame: 0, windows: [], events: [] });
  dd.abort(FAIL.NO_FRAME_TICK);
  check('abort(no_frame_tick) ends the run at once', dd.verdict && dd.verdict.verdict === FAIL.NO_FRAME_TICK, dd.verdict);

  check('stanceKind reads the Spinning Wheel clips', stanceKind(839040051, 239) === 'loop' && stanceKind(839040050, 239) === 'start' && stanceKind(832040051, 239) === null && stanceKind(839040053, 239) === 'end');
  check('weaponBase drops infusion and level', weaponBase(23100010) === 23100000 && weaponBase(4050010) === 4050000);

  console.log((failed === 0 ? 'selftest passed: ' : 'selftest FAILED: ') + passed + ' ok, ' + failed + ' failed');
  return failed === 0;
}

// ---------------------------------------------------------------------------------------------
// Binding (Frida only)
// ---------------------------------------------------------------------------------------------

function bind () {
  const game = Process.findModuleByName('eldenring.exe');
  if (game === null) throw new Error('eldenring.exe is not present');
  const IMAGE = ptr('0x140000000');
  const va = function (s) { return game.base.add(ptr(s).sub(IMAGE)); };
  const lo = game.base;
  const hi = game.base.add(game.size);
  const inImage = function (p) { return !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0; };
  // About 28 percent of entries on this build open with an Arxan healing stub; a hook on the stub
  // never fires. Wine's XInput exports open with the same 5-byte jump.
  // 1.16.2 0x144588af1 -> 1.17.0 0x14458cb71 (docs/recon/rva-map-1162-to-1170.data.tsv); .data did
  // not move in 1.17.1.
  const FOCUS_BYTE = va('0x14458cb71');
  const follow = function (a) { return a.readU8() === 0xe9 ? a.add(5).add(a.add(1).readS32()) : a; };

  // 1.17.1 addresses, each with its 1.16.2 source. Everything below rva 0xafefe9 is identical in
  // 1.17.0 and 1.17.1; mapped with scripts/map-rvas-1162-to-1170.py (unique signatures unless noted).
  const A = {
    PRE_BEHAVIOR_SAFE: va('0x140401f30'), // CS::ChrIns::PreBehaviorSafe [0x140401bd0]
    UPDATE_FROM_MANIPULATOR: va('0x140408190'), // CSChrActionRequestModule::UpdateFromManipulator [0x140407c60]
    FE_UPDATE: va('0x140772a50'), // CSFeManImp::Update [0x140771bd0]; hooked live 2026-09-19
    MENU_WINDOW_JOB_RUN: va('0x1407ae040'), // MenuWindowJob::Run, job+0x130 = window [0x1407ad1c0]
    GATE: va('0x140789910'), // CanChangeEquipmentInSlot(ChrAsmSlot) [0x140788a90], equip-gate.md
    GATE_RET_SLOT_CONFIRM: va('0x1408de3aa'), // its call in EquipDialog vtable+0x90 (0x1408de350)
    GATE_RET_COMMIT: va('0x140788b00'), // its call in EquipItemToChrAsmSlot
    GR_DIALOGUES: va('0x140760a20'), // GetGR_Dialogues(out, id); 103130 "Cannot change equipment"
    RECORD_NAME: va('0x14099a5a0'), // item list detail panel names the highlighted record (er-r3-view)
    // The detail panel's update, run on every cursor move [0x140998790] (er-r3-view imp.rs,
    // r3-view3-cursor.js). It names the record only while one of its two status holders is shown:
    // holder A (parts+0x180) or holder B (parts+0x198, shown only in view 2).
    PANEL_UPDATE: va('0x140999930'),
    GET_WEAPON_NAME: va('0x140d12ab0'), // MsgRepositoryImp::GetWeaponName(msg, id) [0x140d11370]
    EQUIP_TO_SLOT: va('0x140788ab0'), // EquipItemToChrAsmSlot(ChrAsmSlot, MenuGaitem*) [0x140787c30]
    UNEQUIP: va('0x14078ace0'), // UnequipItem(ChrAsmSlot, bool removeItem) [0x140789e60]
    GET_EQUIP_INVENTORY: va('0x140247b30'), // EquipGameData::GetEquipInventoryData = egd+0x158 [0x140247b30]
    GET_ITEM_INVENTORY_IDX: va('0x14024c560'), // EquipInventoryData::GetItemInventoryIdx(inv, int*) [0x14024c560]
    GET_SLOT_BY_ITEM_IDX: va('0x140248440'), // EquipGameData::GetSlotIndexByItemIndex(egd, idx) [0x140248440]
    // EquipGameData::GetParamIdInSlot(egd, slot) [0x1402470e0]: the game-data view, which an equip
    // changes at once; the PlayerIns view (GET_EQUIP) follows on the character's next update.
    GET_PARAM_ID_IN_SLOT: va('0x1402470e0'),
    WEAPON_SWITCH: va('0x14042d4d0'), // d-pad weapon switch [0x14042cf80]
    CMSG_SET_TAE: va('0x1419bb530'), // CustomManualSelectorGenerator: argTaeId (+0xec) from the chosen clip [0x1419b96c0]
    CALC_DAMAGE2: va('0x140448910'), // CalculateDamage2 [0x1404483b0]
    CONSUME_FP: va('0x14047fba0'), // CSChrSwordArtsModule::ConsumeFp [0x14047f640]
    GET_EQUIP: va('0x1406577b0'), // PlayerIns::GetEquipmentEntryParamId(PlayerIns*, -6..11) [0x140656960]
    PAD_DEVICE_POLL: va('0x141f6d940'), // DLUID::PadDevice::Poll; wButtons at device+0x890 (1.17.1, measured 2026-09-19)
    WORLD_CHR_MAN: va('0x143d69ff8'),
    GAME_DATA_MAN: va('0x143d61f98'), // [0x143d5df38], data map 642/642
  };
  const OFF = {
    MAIN_PLAYER: 0x1e508, // WorldChrMan -> main PlayerIns
    MODULES: 0x190, // ChrIns.modules
    MOD_DATA: 0x00, // CSChrDataModule
    MOD_ACTION_FLAG: 0x08, // CSChrActionFlagModule
    MOD_TIME_ACT: 0x18, // CSChrTimeActModule
    MOD_SWORD_ARTS: 0x110, // CSChrSwordArtsModule
    FP: 0x148, MAX_FP: 0x14c, // CSChrDataModule (fromsoftware-rs data.rs)
    ACTION_ANIM_FLAGS: 0x10, // actionAnimationFlags
    ANIM_QUEUE: 0x20, ANIM_STRIDE: 0x10, ANIM_LEN: 10, WRITE_IDX: 0xc0, READ_IDX: 0xc4,
    MENU_CTRL: 0x6a0, // PlayerIns.player_menu_ctrl (CSPlayerMenuCtrl*)
    MENU_FLAGS: 0x20, // CSPlayerMenuCtrl+0x18 CSChrMenuFlags, its word at +0x8
    LOCK_ON: 0x6b0, // PlayerIns.locked_on_enemy; selector -1 when empty
    JOB_WINDOW: 0x130,
    GRID: 0xa38, GRID_CURSOR: 0xd4, // every dialog here keeps its GridControl at +0xa38
    EQUIP_ROWS: 0x2550, EQUIP_ROW: 0x58, EQUIP_ROW_SLOT: 4,
    PGD: 0x08, // GameDataMan -> main PlayerGameData
    EGD: 0x2b0, // PlayerGameData.equipment, inline
    CHR_ASM: 0x6c, ARM_LEFT: 0x0c, ARM_RIGHT: 0x10, // EquipGameData.chr_asm; selected left/right slot 0..2
    MENU_GAITEM_SIZE: 0x80, MENU_GAITEM_IDX: 0x48, MENU_GAITEM_ID: 0x4c,
    PARTS_WINDOW: 0x10, // DetailStatusViewParts -> the GaitemSelectDialog that owns it
    PARTS_VIEW: 0x8f8, // R3 view index (the step at 0x140998260)
    // Holder A's CompositeItemStatusDialog (parts+0x180+8); its first byte is the shown flag the
    // holder's vtable+8 (0x140997c40) answers with.
    PARTS_HOLDER_A_COMPOSITE: 0x188,
    PAD_BUTTONS: 0x890,
  };
  const GR_CANNOT_CHANGE_EQUIPMENT = 0x192da;

  const GET_EQUIP = new NativeFunction(A.GET_EQUIP, 'int', ['pointer', 'int']);
  const EQUIP = new NativeFunction(A.EQUIP_TO_SLOT, 'void', ['int', 'pointer']);
  const UNEQUIP = new NativeFunction(A.UNEQUIP, 'void', ['int', 'uint8']);
  const GET_INV = new NativeFunction(A.GET_EQUIP_INVENTORY, 'pointer', ['pointer']);
  const GET_IDX = new NativeFunction(A.GET_ITEM_INVENTORY_IDX, 'int', ['pointer', 'pointer']);
  const GET_SLOT_OF_IDX = new NativeFunction(A.GET_SLOT_BY_ITEM_IDX, 'int', ['pointer', 'int']);
  const GET_PARAM_IN_SLOT = new NativeFunction(A.GET_PARAM_ID_IN_SLOT, 'int', ['pointer', 'int']);
  function slotParam (egd, slot) { try { return GET_PARAM_IN_SLOT(egd, slot); } catch (e) { return null; } }
  const cfgIn = (globalThis.__ER_FRIDA_CONFIG || {});

  function mainPlayer () {
    try {
      const wcm = A.WORLD_CHR_MAN.readPointer();
      if (wcm.isNull()) return null;
      const p = wcm.add(OFF.MAIN_PLAYER).readPointer();
      return p.isNull() ? null : p;
    } catch (e) { return null; }
  }
  function equipGameData () {
    try {
      const gdm = A.GAME_DATA_MAN.readPointer();
      if (gdm.isNull()) return null;
      const pgd = gdm.add(OFF.PGD).readPointer();
      return pgd.isNull() ? null : pgd.add(OFF.EGD);
    } catch (e) { return null; }
  }
  function equipOf (player, i) { try { return GET_EQUIP(player, i); } catch (e) { return null; } }

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
      if (inImage(col) && col.readU32() === 1) n = col.sub(col.add(0x14).readU32()).add(col.add(0x0c).readU32()).add(0x10).readCString();
    } catch (e) { n = null; }
    names.set(k, n);
    return n;
  }

  // ------------------------------------------------------------------ shared state between hooks
  const S = {
    frame: 0,
    driver: null,
    blocking: false,
    pad: { buttons: 0, lt: 0, ly: 0, actions: 0 },
    actionUpdates: 0,
    lastStamp: 0,
    pollsSinceTick: 0,
    events: [],
    windows: new Map(), // name -> { frame, ptr }
    highlight: null,
    highlightSeq: 0,
    panelSeq: 0,
    listView: null,
    panelForced: 0,
    gate: null,
    tae: null, // the last skill-clip selection: { tae, frame, fe, gate } (CMSG argTaeId writer)
    feTrace: 0,
    nativeQueue: [],
    foreign: { pad: 0, padOther: 0, kb: 0, mouse: 0, cursor: 0, keys: 0, dinputOther: 0, actions: 0 },
    listWasOpen: false,
    polls: { xinput: 0, xinputStamped: 0, xinputForced: 0, xinputEx: 0, xinputExStamped: 0, kb: 0, mouse: 0, feUpdates: 0 },
    padDevices: new Map(), // device -> { polls, pressedPolls, matches, mismatches }
    cursorLatch: null,
    block: { xinput: false, xinputEx: false, dinput: false, user32: false, padDevice: false, errors: [] },
    trace: !!cfgIn.trace,
  };
  function push (e) { e.frame = S.frame; S.events.push(e); }
  function send2 (kind, fields) { send(Object.assign({ kind: kind, t: Date.now(), frame: S.frame }, fields || {})); }

  const hooks = [];
  function attach (addr, cb) { hooks.push(Interceptor.attach(addr, cb)); }

  // ------------------------------------------------------------------ input stage: XInput
  // The stage this machine's game acts on (driver.md, "Injection stage"): ER polls XInputGetState(0)
  // about 98 times a second, and a state stamped there moved the character (br-20260916-082140-882f)
  // and answered a menu with A (br-20260916-160436-f3e8). This hook replaces the gamepad the real
  // pad reported, which is the block and the injection at once, and is the only way to hold L2: the
  // trigger is the analog byte bLeftTrigger.
  function stampState (st, ret, user, isEx) {
    if (!S.blocking || st.isNull()) return;
    // Every other pad slot reads as unplugged for the drive, so a second controller cannot reach
    // the game either.
    if (user !== 0) {
      if (ret.toInt32() === 0) { S.foreign.padOther += 1; ret.replace(ptr(0x48f)); }
      return;
    }
    if (isEx) S.polls.xinputEx += 1; else S.polls.xinput += 1;
    S.pollsSinceTick += 1;
    if (ret.toInt32() === 0) {
      const b = st.add(4).readU16();
      const lt = st.add(6).readU8();
      const rt = st.add(7).readU8();
      let stick = false;
      for (let i = 0; i < 4; i++) { const v = st.add(8 + i * 2).readS16(); if (v > 8000 || v < -8000) stick = true; }
      if (b !== 0 || lt > 30 || rt > 30 || stick) S.foreign.pad += 1;
    } else {
      ret.replace(ptr(0));
      S.polls.xinputForced += 1;
    }
    st.writeU32(0x40000000 + S.padSeq);
    st.add(4).writeU16(S.pad.buttons);
    st.add(6).writeU8(S.pad.lt);
    st.add(7).writeU8(0);
    st.add(8).writeS16(0); // left stick X
    st.add(10).writeS16(S.pad.ly); // left stick Y, list navigation only
    st.add(12).writeS16(0);
    st.add(14).writeS16(0);
    S.lastStamp = S.pad.buttons;
    if (isEx) S.polls.xinputExStamped += 1; else S.polls.xinputStamped += 1;
    if (S.pollsSinceTick > 300 && S.driver !== null && S.driver.state !== 'DONE') {
      S.driver.abort('no_frame_tick');
      finishRun();
    }
  }
  S.padSeq = 0;
  const XI = ['xinput1_4.dll', 'xinput1_3.dll', 'xinput9_1_0.dll'].map(function (n) { return Process.findModuleByName(n); }).filter(function (m) { return m !== null; })[0] || null;
  if (XI !== null) {
    attach(follow(XI.getExportByName('XInputGetState')), {
      onEnter (args) { this.user = args[0].toUInt32(); this.state = args[1]; },
      onLeave (ret) { stampState(this.state, ret, this.user, false); },
    });
    S.block.xinput = true;
    // Ordinal 100, XInputGetStateEx: same signature, a different export. A poll through it that
    // went unstamped would be a hole in the block, so it gets the same treatment and its own count.
    try {
      const gpa = new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('GetProcAddress'), 'pointer', ['pointer', 'pointer']);
      const ex = gpa(XI.base, ptr(100));
      if (!ex.isNull()) {
        attach(follow(ex), {
          onEnter (args) { this.user = args[0].toUInt32(); this.state = args[1]; },
          onLeave (ret) { stampState(this.state, ret, this.user, true); },
        });
        S.block.xinputEx = true;
      }
    } catch (e) { S.block.errors.push('xinput ex: ' + e.message); }
    // The physical pad is not a dependency: this machine's controller often reaches the game only
    // as mouse emulation (sticks turn the camera, d-pad does nothing, zero XInputGetState calls,
    // user report 2026-10-04). The game finds pads through XInputGetCapabilities, so while the
    // drive runs slot 0 answers as a connected wired gamepad and the GetState hook above supplies
    // its state.
    attach(follow(XI.getExportByName('XInputGetCapabilities')), {
      onEnter (args) { this.user = args[0].toUInt32(); this.caps = args[2]; },
      onLeave (ret) {
        S.polls.caps = (S.polls.caps || 0) + 1;
        if (!S.blocking || this.user !== 0 || this.caps.isNull()) return;
        if (ret.toInt32() !== 0) S.polls.capsForced = (S.polls.capsForced || 0) + 1;
        this.caps.writeU8(1); // XINPUT_DEVTYPE_GAMEPAD
        this.caps.add(1).writeU8(1); // XINPUT_DEVSUBTYPE_GAMEPAD
        this.caps.add(2).writeU16(0);
        this.caps.add(4).writeU16(0xf3ff);
        this.caps.add(6).writeU8(0xff);
        this.caps.add(7).writeU8(0xff);
        for (let i = 0; i < 4; i++) this.caps.add(8 + i * 2).writeS16(-64);
        this.caps.add(16).writeU16(0xffff);
        this.caps.add(18).writeU16(0xffff);
        ret.replace(ptr(0));
      },
    });
  } else {
    S.block.errors.push('no XInput module loaded');
  }

  // ------------------------------------------------------------------ input stage: action requests
  // CSChrActionRequestModule::UpdateFromManipulator 1.17.1 0x140408190 [0x140407c60]. On entry +0x10
  // holds the request bits the manipulator wrote this frame from whatever device; the function then
  // masks, edges and times them. Replacing them here for the player's module (owner at +0x8) is the
  // block and the injection at once, at the layer every device reaches.
  attach(A.UPDATE_FROM_MANIPULATOR, {
    onEnter (args) {
      if (!S.blocking) return;
      const p = mainPlayer();
      if (p === null || !args[0].add(8).readPointer().equals(p)) return;
      S.actionUpdates += 1;
      const raw = args[0].add(0x10).readU64();
      const mine = uint64(S.pad.actions || 0);
      if (!raw.and(mine.not()).equals(0)) S.foreign.actions += 1;
      args[0].add(0x10).writeU64(mine);
      // The pad manipulator's tap command, issued at the point it would have been: during this
      // player's manipulator update, before the request bits are consumed.
      if (S.pendingCommand) {
        const cmd = S.pendingCommand;
        S.pendingCommand = 0;
        try {
          new NativeFunction(p.readPointer().add(0x2c0).readPointer(), 'void', ['pointer', 'uint32'])(p, cmd);
          S.events.push({ kind: 'command', id: cmd, frame: S.frame });
          S.cmdWatch = 12;
          send2('command', { id: cmd, fn: p.readPointer().add(0x2c0).readPointer().sub(game.base).add(ptr('0x140000000')).toString() });
        } catch (e) {
          S.events.push({ kind: 'command', id: cmd, frame: S.frame, error: e.message });
          send2('command', { id: cmd, error: e.message });
        }
      }
      this.mod = args[0];
      this.mine = mine;
    },
    // What the game made of the bits, for the steps around the equip: requests after the disable
    // mask (+0x10), new presses (+0x20) and the disable mask (+0x40), at most `actionTrace` records.
    onLeave () {
      if (this.mod === undefined || S.driver === null) return;
      const st = S.driver.state;
      if (['SOFT_SWAP', 'EQUIP_DIRECT', 'RELEASE_L2', 'REHOLD_L2'].indexOf(st) === -1) return;
      if ((S.actionTraced = (S.actionTraced || 0) + 1) > 160) return;
      send2('action_trace', { st: st, mine: this.mine.toNumber(), req: this.mod.add(0x10).readU64().toNumber(), fresh: this.mod.add(0x20).readU64().toNumber(), disabled: '0x' + this.mod.add(0x40).readU64().toString(16) });
    },
  });
  S.block.action = true;

  // The verification at the stage the game reads: after DLUID::PadDevice::Poll, the device's own
  // wButtons (+0x890) must equal what was stamped. A device that disagrees while a press is stamped
  // is holding input that did not come from the driver.
  try {
    attach(follow(A.PAD_DEVICE_POLL), {
      onEnter (args) { this.dev = args[0]; },
      onLeave () {
        if (!S.blocking) return;
        let v;
        try { v = this.dev.add(OFF.PAD_BUTTONS).readU16(); } catch (e) { return; }
        const k = this.dev.toString();
        let rec = S.padDevices.get(k);
        if (!rec) { rec = { polls: 0, pressedPolls: 0, matches: 0, mismatches: 0, lastMismatch: null }; S.padDevices.set(k, rec); }
        rec.polls += 1;
        if (S.lastStamp !== 0) rec.pressedPolls += 1;
        if (v === S.lastStamp) rec.matches += 1;
        else { rec.mismatches += 1; rec.lastMismatch = { frame: S.frame, device: v, stamped: S.lastStamp }; }
      },
    });
    S.block.padDevice = true;
  } catch (e) { S.block.errors.push('pad device: ' + e.message); }

  // ------------------------------------------------------------------ block: DirectInput keyboard and mouse
  // The game's keyboard and mouse stage (er-quickload input_blocker.rs: eldenring.exe 1.17 imports
  // DirectInput8Create and no RawInput API). The GetDeviceState entries are resolved the way the
  // product resolves them: a throwaway device's vtable slot 9.
  function guid (s) {
    const h = s.replace(/-/g, '');
    const m = Memory.alloc(16);
    m.writeU32(parseInt(h.slice(0, 8), 16));
    m.add(4).writeU16(parseInt(h.slice(8, 12), 16));
    m.add(6).writeU16(parseInt(h.slice(12, 16), 16));
    for (let i = 0; i < 8; i++) m.add(8 + i).writeU8(parseInt(h.slice(16 + i * 2, 18 + i * 2), 16));
    return m;
  }
  try {
    const di8 = Process.findModuleByName('dinput8.dll');
    if (di8 === null) throw new Error('dinput8.dll not loaded');
    const create = new NativeFunction(di8.getExportByName('DirectInput8Create'), 'int', ['pointer', 'uint32', 'pointer', 'pointer', 'pointer']);
    const out = Memory.alloc(8);
    const hr = create(game.base, 0x800, guid('bf798031-483a-4da2-aa99-5d64ed369700'), out, NULL);
    if (hr !== 0) throw new Error('DirectInput8Create ' + hr);
    const di = out.readPointer();
    const vcall = function (obj, slot, ret, argt) { return new NativeFunction(obj.readPointer().add(slot * 8).readPointer(), ret, argt); };
    const targets = new Set();
    ['6f1d2b61-d5a0-11cf-bfc7-444553540000', '6f1d2b60-d5a0-11cf-bfc7-444553540000'].forEach(function (g) {
      const devOut = Memory.alloc(8);
      const h = vcall(di, 3, 'int', ['pointer', 'pointer', 'pointer', 'pointer'])(di, guid(g), devOut, NULL);
      if (h !== 0) throw new Error('CreateDevice ' + h);
      const dev = devOut.readPointer();
      targets.add(dev.readPointer().add(9 * 8).readPointer().toString());
      vcall(dev, 2, 'uint32', ['pointer'])(dev);
    });
    vcall(di, 2, 'uint32', ['pointer'])(di);
    targets.forEach(function (t) {
      attach(ptr(t), {
        onEnter (args) { this.size = args[1].toUInt32(); this.data = args[2]; },
        onLeave (ret) {
          if (!S.blocking || ret.toInt32() !== 0 || this.data.isNull()) return;
          // 256 is the keyboard; 16 and 20 are DIMOUSESTATE and DIMOUSESTATE2. Anything else (a
          // DirectInput joystick) is counted and left alone: zeroing a joystick state is a full
          // stick deflection, not a neutral pad.
          const size = this.size;
          if (size !== 256 && size !== 16 && size !== 20) { S.foreign.dinputOther += 1; return; }
          const bytes = new Uint8Array(this.data.readByteArray(size));
          if (bytes.some(function (b) { return b !== 0; })) { if (size === 256) S.foreign.kb += 1; else S.foreign.mouse += 1; }
          if (size === 256) S.polls.kb += 1; else S.polls.mouse += 1;
          this.data.writeByteArray(new Uint8Array(size));
        },
      });
    });
    S.block.dinput = true;
  } catch (e) {
    S.block.errors.push('dinput: ' + e.message);
  }

  // ------------------------------------------------------------------ block: USER32 pointer and key queries
  // The pause menu hit-tests the pointer (er-quickload mh.rs, br-20260905-175511-72a6), so a moved
  // mouse can change the highlighted entry. The pointer is pinned where it was when the block began.
  try {
    const u32 = Process.findModuleByName('user32.dll');
    attach(u32.getExportByName('GetCursorPos'), {
      onEnter (args) { this.pt = args[0]; },
      onLeave (ret) {
        if (!S.blocking || ret.toInt32() === 0 || this.pt.isNull()) return;
        const x = this.pt.readS32();
        const y = this.pt.add(4).readS32();
        if (S.cursorLatch === null) S.cursorLatch = { x: x, y: y };
        else if (x !== S.cursorLatch.x || y !== S.cursorLatch.y) S.foreign.cursor += 1;
        this.pt.writeS32(S.cursorLatch.x);
        this.pt.add(4).writeS32(S.cursorLatch.y);
      },
    });
    ['GetKeyState', 'GetAsyncKeyState'].forEach(function (n) {
      attach(u32.getExportByName(n), {
        onLeave (ret) {
          if (!S.blocking) return;
          if ((ret.toInt32() & 0x8000) !== 0) S.foreign.keys += 1;
          ret.replace(ptr(0));
        },
      });
    });
    attach(u32.getExportByName('GetKeyboardState'), {
      onEnter (args) { this.buf = args[0]; },
      onLeave (ret) {
        if (!S.blocking || ret.toInt32() === 0 || this.buf.isNull()) return;
        const bytes = new Uint8Array(this.buf.readByteArray(256));
        if (bytes.some(function (b) { return (b & 0x80) !== 0; })) S.foreign.keys += 1;
        this.buf.writeByteArray(new Uint8Array(256));
      },
    });
    S.block.user32 = true;
  } catch (e) {
    S.block.errors.push('user32: ' + e.message);
  }

  // ------------------------------------------------------------------ semaphores
  // Open menu windows, by RTTI, from the pump (measured working 2026-10-02, r3-menu-view-toggle.js).
  // A window is open while it was pumped in the last `windowStaleFrames` frames.
  attach(follow(A.MENU_WINDOW_JOB_RUN), {
    onEnter (args) {
      let w;
      try { w = args[0].add(OFF.JOB_WINDOW).readPointer(); } catch (e) { return; }
      if (w.isNull()) return;
      const n = rtti(w);
      if (n === null) return;
      S.windows.set(n, { frame: S.frame, ptr: w });
      if (S.gate === null || S.gate.frame !== S.frame) S.gate = readGate();
    },
  });

  // `CSPlayerMenuCtrl` predicate, equip-gate.md section 2: allowed = (f & 1) && !(f & 0x10) &&
  // ((actionAnimationFlags & 1) || (f & 6)).
  function readGate () {
    const p = mainPlayer();
    if (p === null) return null;
    try {
      const f = p.add(OFF.MENU_CTRL).readPointer().add(OFF.MENU_FLAGS).readU32();
      const aaf = p.add(OFF.MODULES).readPointer().add(OFF.MOD_ACTION_FLAG).readPointer().add(OFF.ACTION_ANIM_FLAGS).readU32();
      const open = (f & 1) !== 0 && (f & 0x10) === 0 && ((aaf & 1) !== 0 || (f & 6) !== 0);
      return { open: open, flags: f, aaf: aaf & 1, frame: S.frame };
    } catch (e) { return null; }
  }

  // A skill clip chosen: the behavior graph's selector stores the clip's TimeAct id at node+0xec
  // (skill-survives-equip.md section 2). Only skill categories (6xx/8xx) are kept. The gate is read
  // at the same instant, so the order of selection and the skill state's `act(163)` shows.
  attach(follow(A.CMSG_SET_TAE), {
    onEnter (args) { this.node = args[0]; },
    onLeave () {
      let tae = -1;
      try { tae = this.node.add(0xec).readS32(); } catch (e) { return; }
      const cat = Math.floor(tae / 1000000);
      if (cat < 600 || cat >= 1000) return;
      S.tae = { tae: tae, frame: S.frame, fe: S.polls.feUpdates, gate: readGate() };
      if (S.blocking) send({ kind: 'tae', frame: S.frame, tae: tae, fe: S.polls.feUpdates, gate: S.tae.gate });
    },
  });

  // The gate's verdict, told apart by its caller: the slot confirm or the commit.
  attach(follow(A.GATE), {
    onEnter (args) { this.slot = args[0].toInt32(); this.ra = this.returnAddress; },
    onLeave (ret) {
      const ok = (ret.toUInt32() & 0xff) !== 0;
      let site = 'other';
      if (this.ra.equals(A.GATE_RET_SLOT_CONFIRM)) site = 'slot_confirm';
      else if (this.ra.equals(A.GATE_RET_COMMIT)) site = 'commit';
      if (site === 'other' && !S.blocking) return;
      push({ kind: 'gate', site: site, slot: this.slot, ok: ok, pred: readGate() });
    },
  });

  attach(follow(A.GR_DIALOGUES), {
    onEnter (args) { if (args[1].toUInt32() === GR_CANNOT_CHANGE_EQUIPMENT) push({ kind: 'msg', id: 103130 }); },
  });

  // The item list's highlighted weapon: the id the weapon-name getter is asked for from inside
  // the record naming (er-r3-view imp.rs, r3-selected-weapon-name.js). Every naming is an event.
  const naming = {};
  attach(follow(A.RECORD_NAME), {
    onEnter () { naming[this.threadId] = true; },
    onLeave () { delete naming[this.threadId]; },
  });
  attach(follow(A.GET_WEAPON_NAME), {
    onEnter (args) {
      if (!naming[this.threadId]) return;
      S.highlight = args[1].toUInt32();
      S.highlightSeq += 1;
    },
  });

  // Why the naming alone went stale (run of 2026-10-05, nav_no_effect in C3): the panel update
  // names the record only while holder A or holder B is shown. Opening the list in view 0 runs it
  // once while holder A's freshly built composite still says shown (its constructor 0x140999ec0
  // sets byte 0 to 1), which is the one naming the driver saw; view 0's pane callback
  // (0x140999130) then applies sub-list +0xb50 index 0 and -1, which hides holder A through
  // 0x140998f60, and view 0 is level 0 so holder B (level == 2) is hidden too. Every later cursor
  // move reaches this function and names nothing. er-r3-view hit the same gate in its view 3 and
  // answers it the same way: holder A says shown for the length of this one call, so the record
  // under the cursor is named on every move whatever view the list is in. The panel itself stays
  // hidden by its view's layout. Only while the drive holds the block, and only for the item list.
  attach(follow(A.PANEL_UPDATE), {
    onEnter (args) {
      this.flag = null;
      let win;
      try { win = args[0].add(OFF.PARTS_WINDOW).readPointer(); } catch (e) { return; }
      if (rtti(win) !== CLASS_LIST) return;
      S.panelSeq += 1;
      try { S.listView = args[0].add(OFF.PARTS_VIEW).readS32(); } catch (e) { S.listView = null; }
      if (!S.blocking) return;
      try {
        const flag = args[0].add(OFF.PARTS_HOLDER_A_COMPOSITE).readPointer();
        if (!flag.isNull() && flag.readU8() === 0) {
          flag.writeU8(1);
          this.flag = flag;
          S.panelForced += 1;
        }
      } catch (e) { this.flag = null; }
    },
    onLeave () {
      if (this.flag !== null) { try { this.flag.writeU8(0); } catch (e) { /* the composite went away */ } }
    },
  });

  attach(follow(A.EQUIP_TO_SLOT), {
    onEnter (args) { this.slot = args[0].toInt32(); },
    onLeave () {
      const p = mainPlayer();
      push({ kind: 'equip', slot: this.slot, heldR: p === null ? null : equipOf(p, -1) });
    },
  });

  attach(follow(A.WEAPON_SWITCH), {
    onEnter () { const p = mainPlayer(); this.before = p === null ? null : equipOf(p, -2); },
    onLeave () { const p = mainPlayer(); push({ kind: 'switch', beforeL: this.before, heldL: p === null ? null : equipOf(p, -2) }); },
  });

  const dmg = {};
  attach(follow(A.CALC_DAMAGE2), {
    onEnter (args) {
      const p = mainPlayer();
      if (p === null || !args[1].equals(p)) return;
      dmg[this.threadId] = args[2];
    },
    onLeave () {
      const adi = dmg[this.threadId];
      delete dmg[this.threadId];
      if (!adi) return;
      const p = mainPlayer();
      const rd = function (o) { try { return adi.add(o).readS32(); } catch (e) { return null; } };
      push({ kind: 'hit', atk: rd(0x40), damage: rd(0x228), launchWeapon: rd(0x144), heldR: p === null ? null : equipOf(p, -1) });
    },
  });

  // An FP charge: the reserved amount ConsumeFp spends (damage-and-fp.md section 3a).
  attach(follow(A.CONSUME_FP), {
    onEnter (args) {
      const p = mainPlayer();
      if (p === null) return;
      try {
        const sa = p.add(OFF.MODULES).readPointer().add(OFF.MOD_SWORD_ARTS).readPointer();
        if (!args[0].equals(sa)) return;
        this.reserved = sa.add(0x10).readS32();
        this.mine = true;
      } catch (e) { this.mine = false; }
    },
    onLeave () { if (this.mine) push({ kind: 'fpcharge', reserved: this.reserved, heldR: equipOf(mainPlayer(), -1) }); },
  });

  // ------------------------------------------------------------------ native requests
  // Run from CSFeManImp::Update, on the game thread but outside the character update, one request
  // per frame. Each answers with a 'native' event the core waits on.
  function inventoryIndex (egd, item) {
    const inv = GET_INV(egd);
    const id = Memory.alloc(4);
    id.writeS32(item);
    return GET_IDX(inv, id);
  }
  function runNative (op) {
    const ev = { kind: 'native', id: op.id, op: op.op, slot: op.slot, item: op.item, ok: false, why: null, waited: op.waited };
    try {
      const egd = equipGameData();
      const p = mainPlayer();
      if (egd === null || p === null) { ev.why = 'no_player'; return push(ev); }
      if (op.op === 'find' || op.op === 'equip') {
        const idx = inventoryIndex(egd, op.item);
        ev.idx = idx;
        if (idx < 0) { ev.why = 'not_in_inventory'; return push(ev); }
        if (op.op === 'find') { ev.ok = true; ev.inSlot = GET_SLOT_OF_IDX(egd, idx); return push(ev); }
        // Equipping an item into the slot it already occupies takes it off (equip_native.rs).
        const at = GET_SLOT_OF_IDX(egd, idx);
        ev.wasInSlot = at;
        if (at === op.slot) { ev.ok = true; ev.why = 'already'; return push(ev); }
        const mg = Memory.alloc(OFF.MENU_GAITEM_SIZE);
        mg.add(OFF.MENU_GAITEM_IDX).writeS32(idx);
        mg.add(OFF.MENU_GAITEM_ID).writeS32(op.item);
        // The gate's inputs at the moment of the commit (the commit asks the gate first and returns
        // on a refusal), since the hooked gate does not report calls from here.
        ev.gate = readGate();
        EQUIP(op.slot, mg);
        ev.after = slotParam(egd, op.slot);
        ev.ok = weaponBase(ev.after) === weaponBase(op.item);
        if (!ev.ok) ev.why = 'slot_unchanged';
        return push(ev);
      }
      if (op.op === 'unequip') {
        UNEQUIP(op.slot, 0);
        ev.after = slotParam(egd, op.slot);
        ev.ok = weaponBase(ev.after) === ITEM.UNARMED;
        if (!ev.ok) ev.why = 'slot_unchanged';
        return push(ev);
      }
      if (op.op === 'refillFp') {
        const dm = p.add(OFF.MODULES).readPointer().add(OFF.MOD_DATA).readPointer();
        dm.add(OFF.FP).writeS32(dm.add(OFF.MAX_FP).readS32());
        ev.ok = true;
        return push(ev);
      }
      ev.why = 'unknown_op';
      push(ev);
    } catch (e) {
      ev.why = 'threw: ' + e.message;
      push(ev);
    }
  }
  attach(follow(A.FE_UPDATE), {
    onEnter () {
      S.polls.feUpdates += 1;
      // An op marked `whenOpen` waits for the gate's predicate to read open (at most `waitFrames`
      // frontend updates) and then commits on that same update: the windows this sequence races
      // are one or two frames long.
      const head = S.nativeQueue[0];
      // `whenSource` (a TimeAct category) also waits for that skill's clip to have been selected
      // since the op was queued, so the commit lands after the selection and before anything else.
      if (head && head.whenSource) {
        if (head.fe0 === undefined) head.fe0 = S.polls.feUpdates;
        head.waited = (head.waited || 0) + 1;
        const g = readGate();
        const t = S.tae;
        const selected = t !== null && Math.floor(t.tae / 1000000) === head.whenSource && t.fe >= head.fe0;
        if (S.feTrace < 400) { S.feTrace += 1; send({ kind: 'fe_wait', frame: S.frame, fe: S.polls.feUpdates, id: head.id, n: head.waited, selected: selected, tae: t === null ? null : t.tae, gate: g }); }
        if (selected && g !== null && g.open) return runNative(S.nativeQueue.shift());
        if (head.waited >= (head.waitFrames || 30)) { S.nativeQueue.shift(); push({ kind: 'native', id: head.id, op: head.op, ok: false, why: selected ? 'gate_shut_after_selection' : 'source_not_selected', waited: head.waited, gate: g, tae: t }); }
        return;
      }
      if (head && head.whenOpen) {
        head.waited = (head.waited || 0) + 1;
        const g = readGate();
        if ((g === null || !g.open) && head.waited < (head.waitFrames || 30)) return;
      }
      if (S.nativeQueue.length > 0) runNative(S.nativeQueue.shift());
    },
  });

  // ------------------------------------------------------------------ per-frame observation
  function anims (p) {
    try {
      const ta = p.add(OFF.MODULES).readPointer().add(OFF.MOD_TIME_ACT).readPointer();
      const w = ta.add(OFF.WRITE_IDX).readU32() % OFF.ANIM_LEN;
      const r = ta.add(OFF.READ_IDX).readU32() % OFF.ANIM_LEN;
      // Entries [read, write) are the animations driven last frame; equal means nothing was
      // pushed, and then the newest entry is the one still playing.
      const out = [];
      if (r === w) out.push(ta.add(OFF.ANIM_QUEUE + ((w + 9) % 10) * OFF.ANIM_STRIDE).readS32());
      else for (let i = r; i !== w; i = (i + 1) % 10) out.push(ta.add(OFF.ANIM_QUEUE + i * OFF.ANIM_STRIDE).readS32());
      return out;
    } catch (e) { return []; }
  }

  function liveWindow (cls) {
    const live = S.windows.get(cls);
    if (!live || S.frame - live.frame > S.driver.cfg.windowStaleFrames) return null;
    return live.ptr;
  }

  function mainCursor () {
    const w = liveWindow(CLASS_MAIN);
    if (w === null) return null;
    try { return w.add(OFF.GRID + OFF.GRID_CURSOR).readS32(); } catch (e) { return null; }
  }

  // The cell under EquipDialog's cursor and the ChrAsmSlot it edits, read the way its confirm
  // handler (0x1408de350) reads them: rows at +0x2550, 0x58 apart, 8-aligned, slot at row+4.
  function equipCursor () {
    const dlg = liveWindow(CLASS_EQUIP);
    if (dlg === null) return null;
    try {
      const idx = dlg.add(OFF.GRID + OFF.GRID_CURSOR).readS32();
      if (idx < 0 || idx > 63) return null;
      const rows = dlg.add(OFF.EQUIP_ROWS);
      const base = rows.add((8 - (rows.and(7).toInt32())) & 7); // the handler's `neg; and 7`
      const slotAt = function (i) { return base.add(i * OFF.EQUIP_ROW + OFF.EQUIP_ROW_SLOT).readS32(); };
      const cells = {};
      for (let i = 0; i < 24; i++) { const s = slotAt(i); if (s >= 0 && s <= 5 && cells[s] === undefined) cells[s] = i; }
      return { idx: idx, slot: slotAt(idx), cells: cells };
    } catch (e) { return null; }
  }

  function observe (p) {
    const stale = S.driver.cfg.windowStaleFrames;
    const windows = [];
    S.windows.forEach(function (v, k) { if (S.frame - v.frame <= stale) windows.push(k); });
    const listOpen = windows.indexOf(CLASS_LIST) !== -1;
    // Cleared when the list closes, not whenever it is absent: the first naming can land a frame
    // before the pump first reports the new window.
    if (S.listWasOpen && !listOpen) S.highlight = null;
    S.listWasOpen = listOpen;
    const slots = {};
    let armRight = null;
    let armLeft = null;
    const egd = equipGameData();
    for (let s = 0; s < 6; s++) slots[s] = egd === null ? null : slotParam(egd, s);
    if (egd !== null) {
      try {
        const asm = egd.add(OFF.CHR_ASM);
        const r = asm.add(OFF.ARM_RIGHT).readU32();
        const l = asm.add(OFF.ARM_LEFT).readU32();
        if (r <= 2) armRight = r * 2 + 1;
        if (l <= 2) armLeft = l * 2;
      } catch (e) { armRight = null; }
    }
    let fp = null;
    let maxFp = null;
    try {
      const dm = p.add(OFF.MODULES).readPointer().add(OFF.MOD_DATA).readPointer();
      fp = dm.add(OFF.FP).readS32();
      maxFp = dm.add(OFF.MAX_FP).readS32();
    } catch (e) { fp = null; }
    let lockOn = false;
    try { lockOn = p.add(OFF.LOCK_ON).readU32() !== 0xffffffff; } catch (e) { lockOn = false; }
    const ev = S.events;
    S.events = [];
    return {
      frame: S.frame, player: true, heldR: equipOf(p, -1), heldL: equipOf(p, -2), slots: slots,
      armRight: armRight, armLeft: armLeft, fp: fp, maxFp: maxFp,
      anims: anims(p), lockOn: lockOn, windows: windows, mainCursor: mainCursor(), equipCursor: equipCursor(),
      highlight: listOpen ? S.highlight : null, highlightSeq: S.highlightSeq,
      panelSeq: S.panelSeq, listView: listOpen ? S.listView : null, gate: S.gate, events: ev,
      block: { ready: S.block.xinput && S.block.dinput && S.block.user32 && S.block.action === true, pollsXinput: S.polls.xinput + S.polls.xinputEx, actionUpdates: S.actionUpdates, foreign: Object.assign({}, S.foreign) },
    };
  }

  // The frame clock: the main player's PreBehaviorSafe, once per game frame on the game thread.
  attach(follow(A.PRE_BEHAVIOR_SAFE), {
    onEnter (args) {
      S.pbsCalls = (S.pbsCalls || 0) + 1;
      if (S.pbsCalls % 600 === 0 && S.hbSend) S.hbSend();
      const p = mainPlayer();
      if (p === null || !args[0].equals(p)) return;
      S.frame += 1;
      S.pollsSinceTick = 0;
      // PlayerIns+0x6a8 holds the last character command (vtable +0x2c0 only stores it); follow it
      // for a while after one is issued, to see who takes it.
      if (S.cmdWatch > 0) { S.cmdWatch -= 1; try { send2('cmd_field', { v: p.add(0x6a8).readS32() }); } catch (e) { /* the player went away */ } }
      // CSPadStep::STEP_Update skips the whole input update (XInput included) on a frame the window
      // is not foreground unless Game.Debug.IsEnableControlOnDisactiveWindow is set; the same store
      // er-focus-input makes (crates/er-focus-input/src/predicate.rs), held while the drive runs.
      if (S.driver !== null && S.driver.state !== 'DONE' && !S.focusErr) {
        try { FOCUS_BYTE.writeU8(1); } catch (e) { S.focusErr = e.message; send2('focus_byte_error', { error: e.message, addr: FOCUS_BYTE.toString() }); }
      }
      if (S.driver === null || S.driver.state === 'DONE') return;
      const obs = observe(p);
      const logLen = S.driver.log.length;
      const out = S.driver.step(obs);
      if (out.buttons !== S.pad.buttons || out.lt !== S.pad.lt || out.ly !== S.pad.ly) S.padSeq += 1;
      S.pad = { buttons: out.buttons, lt: out.lt, ly: out.ly || 0, actions: out.actions || 0 };
      if (out.command) S.pendingCommand = out.command;
      (out.native || []).forEach(function (op) { S.nativeQueue.push(op); });
      for (let i = logLen; i < S.driver.log.length; i++) send2('drive', { rec: S.driver.log[i], foreign: obs.block.foreign });
      if (S.trace) send2('obs', { obs: { heldR: obs.heldR, heldL: obs.heldL, slots: obs.slots, fp: obs.fp, anims: obs.anims, windows: obs.windows, mainCursor: obs.mainCursor, equipCursor: obs.equipCursor, highlight: obs.highlight, panelSeq: obs.panelSeq, listView: obs.listView, gate: obs.gate, lockOn: obs.lockOn, events: obs.events }, pad: S.pad });
      if (out.done) finishRun();
    },
  });

  // The block held when every slot-0 poll during the drive carried the driver's state and no pad
  // device the game polled disagreed with it while a press was stamped.
  function blockVerdict () {
    const devices = [];
    S.padDevices.forEach(function (v, k) { devices.push(Object.assign({ device: k }, v)); });
    const tracking = devices.filter(function (d) { return d.pressedPolls > 0 && d.mismatches === 0; });
    const disagreeing = devices.filter(function (d) { return d.mismatches > 0 && d.matches > 0; });
    const pollsAll = S.polls.xinput + S.polls.xinputEx;
    const stampedAll = S.polls.xinputStamped + S.polls.xinputExStamped;
    return {
      held: pollsAll > 0 && stampedAll === pollsAll && disagreeing.length === 0,
      padDeviceTracksStamp: tracking.length > 0,
      polls: pollsAll, stamped: stampedAll, devices: devices,
      foreignInputSeen: S.foreign,
    };
  }

  function finishRun () {
    S.pad = { buttons: 0, lt: 0, ly: 0, actions: 0 };
    S.padSeq += 1;
    S.blocking = false;
    try { FOCUS_BYTE.writeU8(0); } catch (e) { /* vanilla focus behaviour back */ }
    S.cursorLatch = null;
    S.nativeQueue = [];
    send2('result', { summary: S.driver.summary(), block: blockVerdict(), polls: S.polls, hooks: S.block, panel: { updates: S.panelSeq, forced: S.panelForced, namings: S.highlightSeq, lastView: S.listView } });
  }

  function start (config) {
    if (S.driver !== null && S.driver.state !== 'DONE') return { ok: false, why: 'a drive is running' };
    S.driver = createDriver(config);
    S.foreign = { pad: 0, padOther: 0, kb: 0, mouse: 0, cursor: 0, keys: 0, dinputOther: 0, actions: 0 };
    S.polls = { xinput: 0, xinputStamped: 0, xinputForced: 0, xinputEx: 0, xinputExStamped: 0, kb: 0, mouse: 0, feUpdates: 0 };
    S.actionUpdates = 0;
    S.actionTraced = 0;
    S.padDevices = new Map();
    S.events = [];
    S.nativeQueue = [];
    S.pad = { buttons: 0, lt: 0, ly: 0, actions: 0 };
    S.panelForced = 0;
    S.blocking = true;
    send2('start', { config: S.driver.cfg, block: S.block });
    return { ok: true };
  }

  send2('armed', { hooks: hooks.length, block: S.block, player: mainPlayer() !== null, xinput: XI === null ? null : XI.name });
  // The heartbeat rides PreBehaviorSafe below: raw calls (every character) against counted player frames.
  S.hbSend = function () { send2('hb', { pbsCalls: S.pbsCalls || 0, state: S.driver === null ? null : S.driver.state, polls: S.polls.xinput + S.polls.xinputEx, caps: S.polls.caps || 0, capsForced: S.polls.capsForced || 0, focus: (function () { try { return FOCUS_BYTE.readU8(); } catch (e) { return e.message; } })() }); };

  rpc.exports = {
    start: start,
    abort: function () { if (S.driver !== null && S.driver.state !== 'DONE') { S.driver.abort('rpc'); } return true; },
    report: function () { return { frame: S.frame, state: S.driver === null ? null : S.driver.summary(), block: blockVerdict(), polls: S.polls, hooks: S.block }; },
    // Frida calls this on unload, a reload in place and the watcher's SIGTERM: input goes back to
    // the player whatever state the drive was in.
    dispose: function () {
      S.blocking = false;
      try { FOCUS_BYTE.writeU8(0); } catch (e) { /* detaching anyway */ }
      S.pad = { buttons: 0, lt: 0, ly: 0, actions: 0 };
      hooks.forEach(function (h) { h.detach(); });
    },
  };

  if (cfgIn.mode === 'chainsaw' || cfgIn.mode === 'control') start(cfgIn);
}

if (typeof Process !== 'undefined' && typeof Interceptor !== 'undefined') {
  bind();
} else if (typeof module !== 'undefined') {
  module.exports = { createDriver: createDriver, stanceKind: stanceKind, weaponBase: weaponBase, FAIL: FAIL, PAD: PAD, ITEM: ITEM, DEFAULTS: DEFAULTS, runSelftest: runSelftest };
  if (typeof process !== 'undefined' && process.argv.indexOf('--selftest') !== -1) process.exit(runSelftest() ? 0 : 1);
}
