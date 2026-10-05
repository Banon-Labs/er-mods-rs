// Refuse every SpEffect whose SpEffectParam.stateInfo is 457 (0x1c9, "Disallow Hostile Actions")
// before the game adds it to the main player's special-effect list, then self-test the refusal.
//
// # The choke point
//
// 1.16.2 `FUN_1404fd090(SpecialEffect*, int id, FloatVector4*, FieldInsHandle, char,
// float lifeReductionRate, byte, byte)` -> 1.17.1 `0x1404fde60`. It is the only caller of
// `NewSpecialEffectEntry` (1.16.2 `0x1404fb5f0`), and its three callers are every way an entry is
// added: `CS::SpecialEffect::Apply` (1.16.2 `0x1404fa8e0`, reached from apply-with-source
// `0x1403fade0` and three event/menu wrappers) and the two direct wrappers `0x1404f6e30` /
// `0x1404f6e90`. A negative id takes its first branch (`test edx,edx; js`) straight to `return -1`
// with no side effect, so the refusal rewrites `args[1]` to -1 and lets the game refuse it itself.
//
// # Layout used (1.17.1, read from `HasSpecialEffectWithStateInfo` at `0x1404fa370`)
//
//   SpecialEffect: +0x08 entry head, +0x10 owner ChrIns*
//   entry:         +0x00 SP_EFFECT_PARAM_ST*, +0x08 param id, +0x30 next
//   SP_EFFECT_PARAM_ST: +0x156 stateInfo (u16)
//   WorldChrMan global 0x143d69ff8, +0x1e508 main player; ChrIns +0x178 SpecialEffect*
//
// The self-test runs once, on the game thread, from the first return of the player's
// PadManipulator update (`0x1403daa90`): apply 9621 to the player through apply-with-source
// (`0x1403fb010`), confirm the hook refused it and no state-457 entry exists, then apply a short
// unrelated row (392, five seconds) and confirm that one did land.

const game = Process.getModuleByName('eldenring.exe');
const base = game.base;

const ADD_ENTRY = base.add(0x4fde60);
const APPLY_WITH_SOURCE = base.add(0x3fb010);
const GET_SPEFFECT_PARAM = base.add(0xd523a0);
const REMOVE_BY_ID = base.add(0x4f7680);
const PAD_UPDATE = base.add(0x3daa90);
const WORLD_CHR_MAN = base.add(0x3d69ff8);

const MAIN_PLAYER_OFFSET = 0x1e508;
const CHR_SPECIAL_EFFECT_OFFSET = 0x178;
const SE_HEAD = 0x08;
const SE_OWNER = 0x10;
const ENTRY_PARAM_ROW = 0x00;
const ENTRY_PARAM_ID = 0x08;
const ENTRY_NEXT = 0x30;
const ROW_STATE_INFO = 0x156;

const BLOCKED_STATE_INFO = 457;
const TEST_BLOCKED_ID = 9621;
const TEST_HARMLESS_ID = 392;
const HEARTBEAT_EVERY_ADDS = 300;

const EXPECTED = {
  add: '48 8b c4 4c 89 48 20 56',
  applyWithSource: '40 53 55 56 57 41 56 48',
  getParam: '41 56 48 83 ec 40 48 c7',
  removeById: '48 83 ec 28 8b c2 48 8b',
  padUpdate: '40 55 56 57 41 54 41 55',
};

function hex8 (p) {
  return Array.from(new Uint8Array(p.readByteArray(8)))
    .map(b => b.toString(16).padStart(2, '0')).join(' ');
}

const prologues = {
  add: hex8(ADD_ENTRY),
  applyWithSource: hex8(APPLY_WITH_SOURCE),
  getParam: hex8(GET_SPEFFECT_PARAM),
  removeById: hex8(REMOVE_BY_ID),
  padUpdate: hex8(PAD_UPDATE),
};
// Arxan stubs some entries with a five-byte `jmp` and leaves the rest of the prologue intact.
// Calling through the stub is what the game itself does, so a stubbed entry whose tail still
// matches counts as the same function.
const matches = (k) => prologues[k] === EXPECTED[k] ||
  (prologues[k].startsWith('e9 ') && prologues[k].slice(15) === EXPECTED[k].slice(15));
const mismatched = Object.keys(EXPECTED).filter(k => !matches(k));
send({ kind: 'prologues', base: base.toString(), prologues, mismatched });

const getSpEffectParam = new NativeFunction(GET_SPEFFECT_PARAM, 'pointer', ['pointer', 'int']);
const applyWithSource = new NativeFunction(APPLY_WITH_SOURCE, 'bool',
  ['pointer', 'uint', 'pointer', 'pointer', 'pointer', 'uint8', 'bool', 'uint8']);
const removeById = new NativeFunction(REMOVE_BY_ID, 'bool', ['pointer', 'int']);

const lookup = Memory.alloc(0x10);

function stateInfoOf (id) {
  lookup.writeByteArray(new Array(16).fill(0));
  lookup.add(8).writeU32(0xffffffff);
  getSpEffectParam(lookup, id);
  const row = lookup.readPointer();
  if (row.isNull()) return null;
  return row.add(ROW_STATE_INFO).readU16();
}

function mainPlayer () {
  const wcm = WORLD_CHR_MAN.readPointer();
  if (wcm.isNull()) return null;
  const p = wcm.add(MAIN_PLAYER_OFFSET).readPointer();
  return p.isNull() ? null : p;
}

function entries (se) {
  const out = [];
  let e = se.add(SE_HEAD).readPointer();
  for (let guard = 0; !e.isNull() && guard < 512; guard++) {
    const row = e.add(ENTRY_PARAM_ROW).readPointer();
    out.push({
      id: e.add(ENTRY_PARAM_ID).readS32(),
      stateInfo: row.isNull() ? 0 : row.add(ROW_STATE_INFO).readU16(),
    });
    e = e.add(ENTRY_NEXT).readPointer();
  }
  return out;
}

const stats = { addCalls: 0, refused: [], refusedCount: 0 };

if (mismatched.length === 0) {
  Interceptor.attach(ADD_ENTRY, {
    onEnter (args) {
      stats.addCalls++;
      // Progress rides the game's own add traffic (about twenty a second in the world) instead
      // of a timer.
      if (stats.addCalls % HEARTBEAT_EVERY_ADDS === 0) {
        send({ kind: 'heartbeat', addCalls: stats.addCalls, refusedCount: stats.refusedCount, refused: stats.refused });
      }
      const id = args[1].toInt32();
      if (id < 0) return;
      const player = mainPlayer();
      if (player === null) return;
      const se = args[0];
      if (!se.add(SE_OWNER).readPointer().equals(player)) return;
      if (stateInfoOf(id) !== BLOCKED_STATE_INFO) return;
      args[1] = ptr(-1);
      stats.refusedCount++;
      if (stats.refused.length < 32) stats.refused.push(id);
      send({ kind: 'refused', id, refusedCount: stats.refusedCount });
    },
  });

  // A replacement, not a listener: Frida bypasses every hook for calls made from inside a listener
  // callback (measured: the first run's apply from an `onLeave` reached the add with
  // `addCalls` still 0 and the row landed). A replacement runs as ordinary code on the game thread,
  // so the nested apply goes through the add hook exactly as the game's own calls do.
  let tested = false;
  const padOriginal = new NativeFunction(PAD_UPDATE, 'void', ['pointer', 'pointer']);
  Interceptor.replace(PAD_UPDATE, new NativeCallback(function (manipulator, time) {
    padOriginal(manipulator, time);
    if (tested) return;
    const player = mainPlayer();
    if (player === null) return;
    tested = true;
    // The replacement stays installed as a pass-through after the one test; Frida reverts it when
    // the script unloads.
    runSelfTest(player);
  }, 'void', ['pointer', 'pointer']));

  function runSelfTest (player) {
    {
      const se = player.add(CHR_SPECIAL_EFFECT_OFFSET).readPointer();
      const result = {
        kind: 'selftest',
        player: player.toString(),
        ownerMatches: se.add(SE_OWNER).readPointer().equals(player),
        stateInfo9621: stateInfoOf(TEST_BLOCKED_ID),
        stateInfo392: stateInfoOf(TEST_HARMLESS_ID),
      };
      const before = entries(se);
      result.before457 = before.filter(x => x.stateInfo === BLOCKED_STATE_INFO).map(x => x.id);
      const refusedBefore = stats.refusedCount;
      const pos = Memory.alloc(0x10);
      const corr = Memory.alloc(0x60);
      result.apply9621Returned = applyWithSource(player, TEST_BLOCKED_ID, player, pos, corr, 0, 0, 0);
      result.hookRefused9621 = stats.refusedCount - refusedBefore;
      const after = entries(se);
      result.after457 = after.filter(x => x.stateInfo === BLOCKED_STATE_INFO).map(x => x.id);
      if (result.after457.length > 0 && result.before457.length === 0) {
        result.cleanupRemoved9621 = removeById(se, TEST_BLOCKED_ID);
      }
      const had392 = after.some(x => x.id === TEST_HARMLESS_ID);
      result.apply392Returned = applyWithSource(player, TEST_HARMLESS_ID, player, pos, corr, 0, 0, 0);
      result.has392After = entries(se).some(x => x.id === TEST_HARMLESS_ID);
      result.had392Before = had392;
      result.entryCount = entries(se).length;
      result.addCallsSoFar = stats.addCalls;
      send(result);
    }
  }
  send({ kind: 'armed', add: ADD_ENTRY.toString() });
} else {
  send({ kind: 'not-armed', reason: 'prologue mismatch', mismatched });
}
