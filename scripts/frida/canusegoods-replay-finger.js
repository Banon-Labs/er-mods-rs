// Does the SHIPPED `er_invasion_warp.dll` open an invasion finger in a region vanilla refuses?
//
// The gate that has to be exercised is `can_use_goods_gate.rs`'s hook on `CS::CanUseGoods`, and
// the engine only reaches it for a finger when the player is holding one and a menu is asking. A
// launch on a random save gives neither, so waiting for it proves nothing and a silent log is
// indistinguishable from a broken gate.
//
// # Replay rather than synthesise
//
// `CanUseGoods` takes seven arguments, three of them pointers into live game objects. Inventing
// them is how a probe crashes somebody's game. So this captures a REAL argument tuple from an
// engine call -- every pointer is one the engine itself just passed, on the frame it passed it --
// and re-issues that same tuple with only `goodsId` swapped for a finger. Nothing is guessed.
//
// # Why the DLL answers a call Frida makes
//
// Frida suppresses its own interceptors when they are re-entered from inside a callback, which is
// why a probe cannot see a Frida force it armed itself (measured 2026-09-18). The product hook is
// not a Frida interceptor: `er_hook` writes a native detour, and a native detour is on the
// instruction, so it answers any caller including this one. That asymmetry is what makes this a
// test of the shipped DLL rather than of the agent.
//
// # What a pass looks like
//
// The region term must refuse and the gate must allow anyway:
//
//   regionAllows=false   canUseGoods(112)=1   -> the shipped gate opens the finger
//   regionAllows=false   canUseGoods(112)=0   -> the change did not take
//   regionAllows=true                         -> wrong place to be standing; move and re-read
const RVA = {
  canUseGoods: 0x68ee60,
  isBreakInLimitedByEventFlagId: 0xa61980,
  canUseBreakInItem: 0x657d50,
  isInSafePosRange: 0x65f6a0,
};

// `WorldChrMan` and `FieldArea` as the 1.17.1 image's own code loads them, read off the
// rip-relative operands in `CanUseBreakInItem` and `CanStartBreakIn`. Deliberately not the
// constants in `crates/er-game-base/src/rva.rs`: those are 1.16.2 rvas that the Rust translates at
// runtime through `game_data_addr`, so an agent using them raw reads an untranslated address and
// the result looks like a stale constant when it is merely unmapped.
const WORLD_CHR_MAN_GLOBAL_RVA = 0x3d69ff8;
const WORLD_CHR_MAN_PLAYER_INS_OFFSET = 0x1e508;
const PLAYER_INS_PLAY_REGION_ID_OFFSET = 0x6e8;

// The three `EquipParamGoods` rows the gate opens, from the decompile's own branch
// `uVar31 == 0x66 || uVar31 - 0x6f < 2`. The replay asks about the last one.
const RECUSANT_FINGER = 0x70;

function followStub (address) {
  const first = address.readU8();
  if (first !== 0xe9) return address;
  return address.add(5).add(address.add(1).readS32());
}

const game = Process.findModuleByName('eldenring.exe');
const canUseGoodsEntry = game.base.add(RVA.canUseGoods);
const canUseGoodsBody = followStub(canUseGoodsEntry);

const out = {
  entry: canUseGoodsEntry.toString(),
  body: canUseGoodsBody.toString(),
  // The product hook is a native detour, so the first byte here is the engine's or the detour's,
  // never Frida's. Printed because `0xe9` at the entry means Arxan stubbed it, and a detour the
  // Rust installed after following that stub lives at `body`.
  entryFirstByte: '0x' + canUseGoodsEntry.readU8().toString(16),
  bodyFirstByte: '0x' + canUseGoodsBody.readU8().toString(16),
  captured: null,
  replayed: null,
  calls: 0,
};

// One captured tuple, taken from the engine and then left alone. Capturing continuously would
// race the replay against a later frame's pointers.
let tuple = null;

Interceptor.attach(canUseGoodsBody, {
  onEnter (args) {
    out.calls += 1;
    if (tuple !== null) return;
    tuple = {
      goods: args[0],
      player: args[1],
      specialEffect: args[2],
      chrType: args[3],
      rightWeapon: args[4],
      leftWeapon: args[5],
      cannotConsumeForRepair: args[6],
    };
    out.captured = {
      goods: tuple.goods.toUInt32(),
      player: tuple.player.toString(),
      specialEffect: tuple.specialEffect.toString(),
      chrType: tuple.chrType.toUInt32(),
    };
    send({ kind: 'captured', line: `captured a real tuple: goods=${out.captured.goods} `
                                   + `player=${out.captured.player} chrType=${out.captured.chrType}` });
  },
});

function replay () {
  if (tuple === null) {
    return 'no tuple captured yet -- the engine has not called CanUseGoods on this frame';
  }
  const lines = [];

  // Where the player is, and whether vanilla permits invading here. Read unforced: this agent
  // installs no force, so the region predicate's own answer is the ground truth the verdict is
  // judged against.
  const worldChrMan = game.base.add(WORLD_CHR_MAN_GLOBAL_RVA).readPointer();
  if (worldChrMan.isNull()) return 'no WorldChrMan yet';
  const player = worldChrMan.add(WORLD_CHR_MAN_PLAYER_INS_OFFSET).readPointer();
  if (player.isNull()) return 'no local PlayerIns yet';
  const regionId = player.add(PLAYER_INS_PLAY_REGION_ID_OFFSET);
  lines.push(`playRegionId=${regionId.readU32()}`);

  const ask = (name, rva, argument) => {
    const fn = new NativeFunction(followStub(game.base.add(rva)), 'bool', ['pointer']);
    const answer = fn(argument);
    lines.push(`${name}=${answer}`);
    return answer;
  };
  const regionAllows = ask('regionAllows', RVA.isBreakInLimitedByEventFlagId, regionId);
  ask('isInSafePosRange', RVA.isInSafePosRange, player);
  ask('canUseBreakInItem', RVA.canUseBreakInItem, player);

  // The replay itself: the engine's own seven arguments, with only the goods id changed.
  const canUseGoods = new NativeFunction(canUseGoodsBody, 'uint64',
    ['uint64', 'pointer', 'pointer', 'uint64', 'uint64', 'uint64', 'uint64']);
  const verdict = canUseGoods(
    RECUSANT_FINGER,
    tuple.player,
    tuple.specialEffect,
    tuple.chrType.toUInt32(),
    tuple.rightWeapon.toUInt32(),
    tuple.leftWeapon.toUInt32(),
    tuple.cannotConsumeForRepair.toUInt32(),
  );
  // `bool` comes back in `al` and the tails do not clear the upper bits of `eax`, so an unmasked
  // read gives values like `0x10d200` for a false.
  const masked = verdict.toNumber() & 0xff;
  lines.push(`canUseGoods(0x${RECUSANT_FINGER.toString(16)})=${masked}`);

  let verdictLine;
  if (regionAllows) {
    verdictLine = 'INCONCLUSIVE -- this region already permits invading, so the gate was not tested';
  } else if (masked === 1) {
    verdictLine = 'PASS -- region refuses and the shipped gate opens the finger anyway';
  } else {
    verdictLine = 'FAIL -- region refuses and the finger stayed refused';
  }
  out.replayed = { lines, verdictLine };
  return lines.join(' | ') + ' || ' + verdictLine;
}

// Run the replay on the game's own main thread. A `PeekMessageW` hook is this repo's established
// way onto that thread; the predicates read singletons, and the wrong thread is a race.
//
// Frida 17 removed the module-static `Module.getExportByName(module, name)`.
function exportByName (moduleName, symbol) {
  const owner = Process.findModuleByName(moduleName);
  if (owner !== null) {
    const found = owner.findExportByName(symbol);
    if (found !== null) return found;
  }
  return Module.getGlobalExportByName(symbol);
}

// Sampled by frame count rather than by a clock -- `scripts/check-no-timeouts.py` bans timer APIs,
// and a frame counter follows the game rather than a clock it does not share. Reported only when
// the answer changes, so walking into another region is itself the measurement.
const SAMPLE_EVERY_FRAMES = 120;
let frames = 0;
let lastAnswer = null;

Interceptor.attach(exportByName('user32.dll', 'PeekMessageW'), {
  onEnter () {
    frames += 1;
    if (frames % SAMPLE_EVERY_FRAMES !== 0) return;
    let answer;
    try {
      answer = replay();
    } catch (e) {
      answer = 'replay threw ' + e.message;
    }
    if (answer === lastAnswer) return;
    lastAnswer = answer;
    send({ kind: 'replay', line: answer });
  },
});

rpc.exports = {
  report () { return out; },
};

console.log('canusegoods-replay-finger: ' + JSON.stringify(out));
