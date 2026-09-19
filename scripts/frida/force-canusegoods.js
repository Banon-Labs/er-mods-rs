// Make the vanilla invasion fingers usable under Seamless by answering `CanUseGoods` itself.
//
// # Why the entry address is not the function
//
// `CanUseGoods` is at 1.17 `0x14068ee60` and Arxan STUBS it in the live process: the first five
// bytes read `e9 ee 15 96 ff` -- a rel32 jump -- while bytes 5..15 still match the image, which is
// what proves the address is right rather than wrong. A detour written over those five bytes
// overwrites the jump and never runs.
//
// This is the same shape as the Wine `XInputGetState` thunk that defeated agent-driven movement
// until someone read the first byte. So: read it, and if it is `0xe9`, FOLLOW it.
// `target = addr + 5 + *(i32*)(addr + 1)`, then hook there.
//
// # Why one hook instead of three
//
// The decompile shows four gates, each left failing by Seamless: `IsInOnlineMode()` (line 104,
// copied to a second local at 300, and BOTH appear in the single AND at 865-877),
// `CS::WorldChrManImp::CanStartMultiplay` (330), `CS::PlayerIns::CanUseBreakInItem` (359), and
// `CSSessionManager->lobbyState != Client` (381). Answering the two hookable narrow gates is not
// enough because the online flag alone refuses the item. Answering the function's RETURN VALUE
// settles all four at once, and it is a diagnostic: it proves usability is the only thing in the
// way, without deciding how the product should open the gate.
const CAN_USE_GOODS = ptr('0x14068ee60');
// The three invasion fingers, from the decompile's own branch: `uVar31 == 0x66 || uVar31 - 0x6f < 2`.
const FINGERS = new Set([0x66, 0x6f, 0x70]);

// The engine's own fourth term is `CSSessionManager->lobbyState != Client`, and it is the only
// one of the four that should still be allowed to refuse. `Client` means the join RPC succeeded
// and the P2P session exists -- the player is in somebody else's world, and a second finger there
// is the accidental double use.
//
// It must be `Client` and nothing weaker. During a search the state is `None` or `Joining`, and
// the user's own rule is that the finger stays pressable throughout: pressing it again is how the
// search is cancelled and the mode toggled, in a loop. A re-gate on "a use is queued" would break
// that -- `ChrIns+0x160` holds the finger for the whole search.
//
// Both constants are the ones `er-invasion-warp-core` already pins for this build:
// `er_game_base::rva::CS_SESSION_MANAGER_GLOBAL_RVA` and `warp::SESSION_LOBBY_STATE_OFFSET`.
const SESSION_MANAGER_GLOBAL_RVA = 0x3d7a4d0;
const LOBBY_STATE_OFFSET = 0x0c;
const LOBBY_STATE_CLIENT = 6;
const LOBBY_STATE_NAMES = ['None', 'Creating', 'CreateFailed', 'Host',
                           'Joining', 'JoinFailed', 'Client', 'Closing'];

function lobbyState () {
  try {
    const game = Process.findModuleByName('eldenring.exe');
    if (game === null) return null;
    const manager = game.base.add(SESSION_MANAGER_GLOBAL_RVA).readPointer();
    if (manager.isNull()) return null;
    return manager.add(LOBBY_STATE_OFFSET).readS32();
  } catch (e) {
    return null;
  }
}

function followStub (address) {
  const first = address.readU8();
  if (first !== 0xe9) return { address, followed: false, firstByte: first };
  const rel = address.add(1).readS32();
  return { address: address.add(5).add(rel), followed: true, firstByte: first };
}

const resolved = followStub(CAN_USE_GOODS);
const out = {
  entry: CAN_USE_GOODS.toString(),
  body: resolved.address.toString(),
  followed: resolved.followed,
  firstByte: '0x' + resolved.firstByte.toString(16),
  calls: 0,
  forced: 0,
  lastGoods: null,
  shapes: {},
  regated: 0,
  lastLobbyState: null,
  // On at load. The resident watcher has no rpc channel, so a hot-reload of this file is the
  // only switch it has. Proven live before this was flipped: the hook is reached ~200 times a
  // second and `lastGoods` reported 111 and 112, so it does answer for the fingers.
  enabled: true,
};

Interceptor.attach(resolved.address, {
  onEnter (args) {
    // First argument is the goods ROW id, per the decompile's `uVar31`.
    this.goods = args[0].toUInt32();
    // The signature is
    // `CanUseGoods(goodsId, PlayerIns*, SpecialEffect*, CharacterType chrType, rightWeaponId,
    //              leftWeaponId, cannotConsumeForRepair)`
    // so arg3 is the engine's own answer to "what is this character right now" -- host, invader,
    // co-op phantom. It is the cheapest candidate for the re-gate: the gate is already handed the
    // discriminator, so nothing new has to be found in memory to read it.
    this.chrType = args[3].toUInt32();
    // arg1 is the `PlayerIns`, which is a `ChrIns`. `+0x160` holds the item id of a use the engine
    // has taken and `0xffffffff` at rest -- measured in this repo as the oracle for "the use
    // landed" (bd the-near-far-chain-is-complete-and-gated-on-seamless-session). A use already in
    // flight is exactly the double-use this gate has to refuse, and reading it here costs nothing
    // because the gate is handed the pointer.
    this.player = args[1];
    this.inFlight = null;
    if (!this.player.isNull()) {
      try {
        this.inFlight = this.player.add(0x160).readU32();
      } catch (e) {
        this.inFlight = null;
      }
    }
  },
  onLeave (retval) {
    out.calls += 1;
    reportIfDue();
    if (!FINGERS.has(this.goods)) return;
    out.lastGoods = this.goods;
    // Every distinct `(goods, chrType, original verdict)` the engine has produced, recorded before
    // the override touches anything. This is what says which `chrType` values mean "already in
    // someone else's world", and it has to be read off a real invasion rather than guessed.
    const flight = this.inFlight === null ? 'unreadable'
      : (this.inFlight === 0xffffffff ? 'idle' : '0x' + this.inFlight.toString(16));
    const shape = `${this.goods}/${this.chrType}/${retval.toInt32()}/${flight}`;
    if (!out.shapes[shape]) {
      out.shapes[shape] = 0;
      send({ kind: 'shape', line: `first seen goods=${this.goods} chrType=${this.chrType} ` +
                                  `original=${retval.toInt32()} inFlight=${flight}` });
    }
    out.shapes[shape] += 1;
    if (!out.enabled) return;
    // Connected to another player: hand the engine's refusal back untouched.
    const state = lobbyState();
    if (state === LOBBY_STATE_CLIENT) {
      if (out.regated % 500 === 0) {
        send({ kind: 'regate', line: `lobbyState=Client -- leaving goods ${this.goods} refused ` +
                                     `(#${out.regated + 1})` });
      }
      out.regated += 1;
      return;
    }
    out.lastLobbyState = state;
    if (retval.toInt32() === 0) {
      retval.replace(ptr(1));
      out.forced += 1;
      // The menu asks about these rows a few hundred times a second, so one line per flip buries
      // everything else in the log. The heartbeat already carries the running total.
      if (out.forced % 500 === 1) {
        send({ kind: 'forced', line: `CanUseGoods(${this.goods}) 0 -> 1 (#${out.forced})` });
      }
    }
  },
});

// The resident watcher (`scripts/er-frida-watch.py`) has no rpc channel -- it only records what
// the agent `send()`s. Without a heartbeat the interesting number, "was this function reached at
// all", never leaves the process, and a silent log is indistinguishable from a silent hook. A
// 2026-09-15 attempt at this address logged zero calls and the zero was never explained; this is
// what tells the two apart.
//
// It counts CALLS rather than seconds on purpose. A timer would report on a clock the game does
// not share, so an idle process would emit identical lines forever and a busy one would under-
// sample; and `scripts/check-no-timeouts.py` bans timer APIs here for exactly that reason. Every
// line below is therefore caused by the gate actually running.
const REPORT_EVERY_CALLS = 400;

function reportIfDue () {
  if (out.calls % REPORT_EVERY_CALLS !== 0) return;
  send({
    kind: 'report',
    line: `CanUseGoods calls=${out.calls} forced=${out.forced} regated=${out.regated} ` +
          `lastFinger=${out.lastGoods} lobbyState=${out.lastLobbyState} ` +
          `(${LOBBY_STATE_NAMES[out.lastLobbyState] || '?'}) enabled=${out.enabled}`,
  });
}

rpc.exports = {
  // Off until asked, so the hook can be proven live before it changes an answer.
  enable (on) { out.enabled = !!on; return out.enabled; },
  report () { return out; },
};
console.log(`force-canusegoods: body ${out.body} (followed=${out.followed}, first=${out.firstByte})`);
