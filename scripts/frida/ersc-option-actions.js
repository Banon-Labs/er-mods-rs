// Which of Seamless's option actions the Dried Fingers row runs, and what it writes.
//
// # What this answers that a rebuild cannot
//
// The question is "which field does this rule live in", and its shape is *who wrote this*. A value
// polled from a game task can only be sampled after the fact: it says a field differs from the
// last sample, not that the item caused it, and it cannot tell one heap allocation from the next.
// Four build/relaunch cycles on 2026-09-15 produced exactly that -- a diff claiming all eight
// qwords of the session head moved while the player had touched nothing.
//
// An `Interceptor` answers it directly. Every option row Seamless registers runs a plaintext
// function in `ersc+0x25800..0x26e00`; `.pdata` lists nineteen of them, and the registration table
// at `ersc+0x2a1e0` that binds rows to them is a `jmp` into the Themida section and cannot be read.
// So hook all nineteen and let the player's own click say which one is the row.
//
// # What it has ruled out so far
//
// Using the item runs NONE of the nineteen, and the DLL's own diff of `session+0x00..0x80` shows
// nothing moving across a use but the `0x8` bit the engine flaps. So the rule is neither an
// option-row action nor a field in the session head.
//
// # Why there is no guard page here
//
// There was one, and it killed the game: `0xc0000005` at `ersc+0x89e23`, one of Seamless's own
// readers of the watched page, while the player used an item. `MemoryAccessMonitor` revokes access
// to a whole 4 KB of live heap and turns every access by every thread into a fault Frida must
// resume. Debug registers change no protection and trap only the address asked for.
'use strict';

// Reads are the overwhelming majority of the traffic here and none of them can be the rule being
// set. Flip to `true` only to prove a watch is live.
const REPORT_READS = false;

// `ersc!show(OSM, groupId)` -- Seamless building any of its option menus. The one place the
// option-menu object is handed over without a row having been clicked.
const SHOW = 0x241a0;
// `OSM+0x58` is the session; `session+0x00` is the game-rule flags qword.
const NEXT_OBJECT = 0x58;
// `session+0x150` is the state. The codes this build reverses, plus `6`, which the three toggle
// actions gate on (`cmp dword [rax+0x150],6`). Used to prove a pointer is still what it was,
// never to go looking for one: as a search key this shape matched 14,030 places.
const SESSION_STATE = 0x150;
const SESSION_STATES = [0x1, 0x6, 0xe, 0xf, 0x10, 0x12, 0x16, 0x23];
// How much of the session to snapshot around a call. Extended past `session+0x150` on 2026-09-15
// so the state field is inside the diff: using the item ran `ersc+0x259d0`, which this repo calls
// `OPTIONSELECT_LEAVEWORLD` and which is supposed to write `0x23` there. A window that stops at
// `0x80` cannot say whether it did, and "the action ran" is not the same claim as "the action did
// its documented thing".
const HEAD_BYTES = 0x160;
// `CS::ChrIns::confirmedUsedGoods` -- the id of the goods the player actually confirmed using.
//
// This is what removes the agent's own chat from the experiment. Every attribution before it was
// made by correlating a hook hit with an instruction the agent had typed ("use it"), and twice
// that produced a confident, wrong answer: `ersc+0x259d0` was recorded as the Dried Fingers action
// when the player was using the Separation Mist. A hook proves which function ran; it proves
// nothing about which item was picked. This field does.
//
// Read out of `CS::ChrIns::GetToUseItemId` on the 1.16.2 named dump, which is four instructions:
//
//     14042d7c0  MOV EAX,dword ptr [RCX + 0x164]
//     14042d7c6  MOV dword ptr [RDX],EAX
//     14042d7c8  MOV RAX,RDX
//     14042d7cb  RET
//
// The offset is CONFIRMED for the installed 1.17.1 build, by call graph rather than by proximity:
// a caller of that function, `0x14042c2e0`, maps to `0x14042c830` at delta `+0x550`, and the call
// site inside it -- `0x14042c8d6` -- targets `0x14042dd10`, which is `GetToUseItemId` at the same
// delta and still reads `[RCX + 0x164]`. So the field did not move between 1.16.2 and 1.17.1, and
// a wrong read here is a wrong OBJECT, not a wrong offset.
const CHR_INS_CONFIRMED_USED_GOODS = 0x164;

// `WorldChrManImp::mainPlayerIns`, from the 1.16.2 named dump's own structure.
const WORLD_CHR_MAN_MAIN_PLAYER = 0x1e508;

// `GLOBAL_WorldChrMan` on the installed 1.17.1 build.
//
// Carried forward by mapping the FUNCTION and re-reading its operand, not by reusing the data
// address. The 1.16.2 value is `0x143d65f88`, read out of `CanMainPlayerUseGoods` at `14068df5c`,
// and using it here was wrong: on 1.17.1 it holds `0x143d65fa0`, an address inside
// `eldenring.exe` rather than a heap pointer, and the goods behind it read `4259961`.
//
// The route that works: `CanMainPlayerUseGoods` `0x14068dee0` -> `0x14068ed30` (1.17.0, delta
// `+0xe50`, `map-rvas-1162-to-1170.py`), unchanged into 1.17.1 because it sits below the
// `0xafefe9` boundary, and at `0x14068edac` it loads `[0x143d69ff8]` -- the same null-singleton
// guard, one instruction before the `DLPanic` branch.
//
// Still checked at runtime rather than trusted: the pointer must read, the player behind it must
// read, and the goods id must be a plausible param row. A wrong global reads as a refusal here
// rather than as a confident wrong item name.
const GLOBAL_WORLD_CHR_MAN = ptr('0x143d69ff8');
// An `ItemId` carries its category in the top nibble, and `0x40000000` is goods. The raw field
// therefore reads `0x407fde64` for goods row `0x7fde64`, which a bare range check rejects as
// nonsense -- this one did, and the value was written off as "a float repeated at +0x160 and
// +0x164" for several turns. Mask before judging.
//
// `0x7fde64` is 8,379,492, well past any vanilla row, because it is one of Seamless's injected
// goods: `ersc+0x4640b` builds the `MODGOODSNAME_DRIEDFINGERITEM` record beside the id
// `0x7fde6c`, eight rows along. So the high ceiling here is deliberate -- a vanilla-only bound
// would reject exactly the items this probe exists to name.
const ITEM_CATEGORY_MASK = 0xf0000000;
const ITEM_CATEGORY_GOODS = 0x40000000;
const MAX_GOODS_ID = 0x0fffffff;

// Where the debug registers look. Four slots, eight bytes each, past the head the DLL has already
// ruled out.
//
// `0x158` was in this list and is not any more: it holds a ticking counter (`0x6aa99936` ->
// `0x6aa999a3` across one capture), so it traps constantly and every hit it causes is noise.
const WATCH_OFFSETS = [0x160, 0x168, 0x170, 0x178];

// The tick every sampler in this file counts in, in place of the four timers it used to run on.
//
// Three of those timers were samplers and one was a settle delay, and all four were guesses at a
// rate expressed in milliseconds. `scripts/frida/pad-frames.js` measured what this target actually
// offers: `XInputGetState` is called ~82 times a second whether or not a pad is attached, while
// `FD4PadManager`'s builders are never called at all without one. So the input poll is the tick,
// a sampler is "every Nth call" rather than "every N milliseconds", and the settle is a count of
// the game's own frames rather than a wait on a clock the game does not share.
//
// The trade this makes, said plainly: the sampling now happens on a game thread instead of one of
// Frida's. That is affordable only because each sampler reads kilobytes at most -- 0x200 bytes of
// the lead, 0x1000 of the session, three pointers for the goods field. The 11.5 MB snapshot that
// killed a run on 2026-09-15 could never have gone here, and nothing this size may be widened
// into this hook without measuring the cost first.
const XINPUT_MODULE = 'XINPUT1_4.dll';
const XINPUT_READ = 'XInputGetState';
// `FD4PadManager`'s builder A, the fallback `pad-frames.js` keeps for a process with no
// `XINPUT1_4.dll` mapped.
const PAD_BUILDER_RVA = 0x240e70;

// The plaintext option actions, read out of `.pdata` in `vendor-archive/seamless/ersc-2.0.1.dll`
// (`0x25800 <= start < 0x26e00`). Four already have names, from `local_invasion_filter::ersc`.
const ACTIONS = [
  [0x25850, 'invade'],
  [0x258d0, 'cancel'],
  [0x25940, null],
  [0x259d0, 'leave-world'],
  [0x25a50, 'toggle-pvp'],
  [0x25bd0, 'toggle-pvp-teams'],
  [0x25d50, 'toggle-friendly-fire'],
  [0x25f00, null],
  [0x26030, null],
  [0x26180, null],
  [0x26210, null],
  [0x262c0, null],
  [0x26370, null],
  [0x264e0, null],
  [0x26650, null],
  [0x267c0, null],
  [0x26930, null],
  [0x26b80, null],
  [0x26dc0, null],
];

let SESSION = NULL;
// The head as it stood when the watchpoints were armed, so a hit can say WHICH address trapped.
// `details.memory` is absent for a debug-register exception on this target -- the first hit came
// back `offset: null` -- so the address has to be recovered by diffing rather than read off the
// exception.
let BASELINE = null;
// Debug registers are per-thread and Frida programs the thread that calls it, so arming from the
// agent's own thread would watch a thread that never executes Seamless code. Armed from inside a
// hook instead.
//
// Keyed on thread AND session, not thread alone. The session is a heap allocation with a lifetime
// shorter than the process: leaving a world tears it down and hosting again builds another. Keyed
// on the thread only, the first arming wins forever and every later session goes unwatched while
// the log still says `watch-armed` -- which is what happened across a leave-and-rehost here.
const ARMED = new Set();

function say (payload) { send(payload); }

// One line per distinct goods the game asks about, not one per frame: the gate is polled while a
// menu is open and would otherwise be the only thing in the log.
const SEEN_GOODS = new Set();

// How often a baseline may be taken off the goods query. Frida has no clock a script may call --
// `Date.now` is available in the agent, unlike in workflow scripts -- so the gate is a simple
// elapsed check against the last accepted call.
const BASELINE_MIN_GAP_MS = 2000;
let lastBaselineAt = 0;
function baselineDue () {
  const now = Date.now();
  if (now - lastBaselineAt < BASELINE_MIN_GAP_MS) return false;
  lastBaselineAt = now;
  return true;
}

// The goods id the local player last confirmed, or null when the player cannot be read.
//
// Resolved through `er_quickload.dll`'s own `WorldChrMan` if that module is loaded, and otherwise
// not at all: guessing a singleton address is how this file already produced 14,030 candidates for
// one object. Reported beside every action hit so the log names the item rather than the agent
// naming it.
// Each link of the chain, named, so a refusal says WHICH one broke. `confirmedGoods` collapses
// all of them to null on purpose -- that is right for an attribution, and useless for a repair.
function goodsChainSteps () {
  const steps = {};
  try {
    const worldChrMan = GLOBAL_WORLD_CHR_MAN.readPointer();
    steps.worldChrMan = worldChrMan.toString();
    steps.inAModule = Process.findModuleByAddress(GLOBAL_WORLD_CHR_MAN) === null
      ? null
      : Process.findModuleByAddress(GLOBAL_WORLD_CHR_MAN).name;
    if (worldChrMan.isNull()) return steps;
    const player = worldChrMan.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
    steps.mainPlayerIns = player.toString();
    // `sinner_hunter` sits directly after `main_player` in `world_chr_man.rs` and is the Spears of
    // the Church boss, so it is null in every ordinary session. A non-null here means the pair is
    // not `main_player`/`sinner_hunter` and the whole offset is off.
    steps.sinnerHunter = worldChrMan.add(WORLD_CHR_MAN_MAIN_PLAYER + 8).readPointer().toString();
    if (player.isNull()) return steps;
    // Is this a `ChrIns` at all? Its first qword is a vtable, so it must point INTO the game
    // image. `mainPlayerIns` and `confirmedUsedGoods` are both 1.16.2 struct offsets and either
    // could have drifted; without this check a wrong player and a wrong field are the same
    // implausible number.
    const vtable = player.readPointer();
    const owner = Process.findModuleByAddress(vtable);
    steps.vtable = vtable.toString();
    steps.vtableIn = owner === null ? null : owner.name;
    steps.goodsRaw = player.add(CHR_INS_CONFIRMED_USED_GOODS).readS32();
    steps.around = Array.from(new Uint8Array(player.add(0x150).readByteArray(0x30)))
      .map(function (b) { return b.toString(16).padStart(2, '0'); }).join(' ');
  } catch (e) {
    steps.error = e.message;
  }
  return steps;
}

// The goods id, or a REASON it could not be had. Never a bare number that might be something else.
//
// The chain is validated structurally on the installed 1.17.1 build, link by link:
//
//   `GLOBAL_WorldChrMan` `0x143d69ff8`   reads a heap pointer, not an image address
//   `+0x1e508`                            reaches an object whose first qword is a vtable in
//                                         `eldenring.exe`, and whose neighbour at `+0x1e510`
//                                         (`sinner_hunter`) is null, as it is outside a Spears
//                                         of the Church session
//   `ChrIns+0x164`                        call-graph confirmed, see the constant's own comment
//
// What it is NOT is a field that means anything at rest. Its own name upstream is
// `tae_queued_use_item` -- "used by TAE's UseGoods to figure out what item to actually apply" --
// so it carries an id only while a use animation is running, and sampled idle it reads whatever
// the slot last held. That residue is an `ItemId`, not a float, and calling it one cost a wrong
// reading earlier today: `0x407fde64` is the goods category `0x40000000` over row `0x7fde64`, so
// a guard that range-checks the raw word rejects every real id and the mask is what makes it read.
// An implausible value is reported as a refusal carrying its raw number rather than as an item.
function confirmedGoods () {
  try {
    const worldChrMan = GLOBAL_WORLD_CHR_MAN.readPointer();
    if (worldChrMan.isNull()) return { id: null, why: 'WorldChrMan is null' };
    const player = worldChrMan.add(WORLD_CHR_MAN_MAIN_PLAYER).readPointer();
    if (player.isNull()) return { id: null, why: 'main_player is null -- no world' };
    const vtable = player.readPointer();
    if (Process.findModuleByAddress(vtable) === null) {
      return { id: null, why: 'main_player has no game vtable -- wrong offset' };
    }
    const raw = player.add(CHR_INS_CONFIRMED_USED_GOODS).readU32();
    const category = raw & ITEM_CATEGORY_MASK;
    const row = raw & MAX_GOODS_ID;
    if (category !== ITEM_CATEGORY_GOODS) {
      return {
        id: null,
        why: 'ChrIns+0x164 category is ' + category.toString(16) + ', not goods',
        raw: '0x' + raw.toString(16),
      };
    }
    return { id: row, raw: '0x' + raw.toString(16), why: null };
  } catch (e) {
    return { id: null, why: e.message };
  }
}

// An address as `module+rva` where a module owns it, so a hit names a function rather than a
// number to look up by hand afterwards.
function describe (address) {
  const module = Process.findModuleByAddress(address);
  if (module === null) return address.toString();
  return module.name + '+0x' + address.sub(module.base).toString(16);
}

// An export sitting behind a `jmp rel32` thunk is followed to the real entry, because attaching to
// the five bytes of the thunk itself can fail for want of room.
function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

// The per-frame call this agent samples on, named so the transcript says which one it got.
function gameTick () {
  const xinput = Process.findModuleByName(XINPUT_MODULE);
  if (xinput !== null) {
    return {
      at: follow(xinput.getExportByName(XINPUT_READ)),
      what: XINPUT_MODULE + '!' + XINPUT_READ,
    };
  }
  const game = Process.findModuleByName('eldenring.exe');
  if (game === null) return null;
  return {
    at: follow(game.base.add(PAD_BUILDER_RVA)),
    what: 'eldenring.exe+0x' + PAD_BUILDER_RVA.toString(16),
  };
}

function readHead (session) {
  if (session.isNull()) return null;
  try {
    return Array.from(new Uint8Array(session.readByteArray(HEAD_BYTES)));
  } catch (e) {
    return null;
  }
}

function qword (bytes, offset) {
  let text = '0x';
  for (let byte = 7; byte >= 0; byte--) {
    text += bytes[offset + byte].toString(16).padStart(2, '0');
  }
  return text;
}

function diff (before, after) {
  const moved = [];
  for (let offset = 0; offset < HEAD_BYTES; offset += 8) {
    let same = true;
    for (let byte = 0; byte < 8; byte++) {
      if (before[offset + byte] !== after[offset + byte]) { same = false; break; }
    }
    if (same) continue;
    moved.push({
      offset: '0x' + offset.toString(16),
      before: qword(before, offset),
      after: qword(after, offset),
    });
  }
  return moved;
}

function stillASession (session) {
  try {
    return SESSION_STATES.indexOf(session.add(SESSION_STATE).readU32()) !== -1;
  } catch (e) {
    return false;
  }
}

// Deliberately never called since 2026-09-15. Kept because the Frida 17 API note below is worth
// more than the function is: arming debug registers on the player's own threads is a live-thread
// hazard for no measured return, and it ran on the same menu-open path as the snapshot that
// stalled and then killed a run. Call it only for a bounded experiment you are watching.
function armWatchpointsHere (session) {
  if (session.isNull()) return;
  const thread = Process.getCurrentThreadId();
  const key = thread + '@' + session.toString();
  if (ARMED.has(key)) return;
  ARMED.add(key);
  // On Frida 17 the watchpoint lives on the thread OBJECT, not the `Thread` namespace: measured
  // here, `Object.getOwnPropertyNames(Thread)` is `[length, name, prototype, _backtrace, sleep,
  // backtrace]` and nothing else, so `Thread.setHardwareWatchpoint` threw "not a function" and
  // four slots armed nothing while the log said `watch-armed`.
  let handle;
  try {
    handle = Process.getThreadById(thread);
  } catch (e) {
    say({ kind: 'watch-failed', thread: thread, error: 'getThreadById: ' + e.message });
    return;
  }
  const armed = [];
  WATCH_OFFSETS.forEach(function (offset, slot) {
    try {
      handle.setHardwareWatchpoint(slot, session.add(offset), 8, 'w');
      armed.push('0x' + offset.toString(16));
    } catch (e) {
      say({
        kind: 'watch-failed',
        thread: thread,
        offset: '0x' + offset.toString(16),
        error: e.message,
      });
    }
  });
  BASELINE = readHead(session);
  say({ kind: 'watch-armed', thread: thread, session: session.toString(), offsets: armed });
}

// A watchpoint raises an exception rather than calling back, so the handler is where a hit is
// reported. Everything not ours returns `false` and is taken by the process untouched -- one wrong
// resume is what killed the game the last time this file watched memory.
Process.setExceptionHandler(function (details) {
  if (details.type !== 'single-step' && details.type !== 'breakpoint') return false;
  // Deliberately NOT gated on a known session. Reloading this file resets `SESSION` but leaves the
  // debug registers programmed on the game thread, so gating here would drop exactly the hits the
  // previous arming is still catching -- the trap would fire, the handler would decline it, and
  // the log would look idle.
  const address = details.memory ? details.memory.address : null;
  const offset = (address === null || SESSION.isNull()) ? null : address.sub(SESSION).toInt32();
  const head = SESSION.isNull() ? null : readHead(SESSION);
  const moved = (BASELINE === null || head === null) ? null : diff(BASELINE, head);
  if (head !== null) BASELINE = head;
  say({
    kind: 'watchpoint-hit',
    exception: details.type,
    from: describe(details.context.pc),
    offset: offset === null ? null : '0x' + offset.toString(16),
    session: SESSION.isNull() ? null : SESSION.toString(),
    moved: moved,
  });
  return false;
});

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  say({ kind: 'error', text: 'ersc.dll is not loaded in this process' });
} else {
  say({ kind: 'ready', base: ersc.base.toString(), actions: ACTIONS.length, reads: REPORT_READS });
  // `Thread.setHardwareWatchpoint` answered "not a function" on this build, so the four slots
  // armed nothing and the empty `offsets` list in `watch-armed` was the only sign. Report what
  // the runtime actually exposes rather than picking the next name out of the documentation.
  say({
    kind: 'api',
    frida: Frida.version,
    thread: Object.getOwnPropertyNames(Thread),
    process: Object.getOwnPropertyNames(Process).filter(function (n) {
      return /watch|hardware|thread|exception/i.test(n);
    }),
  });

  // The instruction the watchpoint caught, read out of live memory.
  //
  // Read once and kept for the record, but it does NOT name the field and no later version of
  // this file should expect it to. `ersc+0x2646e6` decodes as
  //
  //     movzx ecx,[rcx] / add rcx,rbp / mov rcx,[rcx] / mov rbx,rbp / add rbx,0x123 /
  //     movzx rbx,[rbx] / add rbx,rbp / mov rbx,[rbx] / mov [rcx],rbx / ...
  //
  // which is a Themida virtual-machine handler: every operand comes out of a bytecode stream at
  // `rbp`, so one address serves every write the VM performs. Instruction-level attribution is
  // therefore dead on this target, and the field has to be identified by which value changes
  // across a clean window instead.
  const WRITER = 0x2646e6;
  try {
    const at = ersc.base.add(WRITER);
    say({
      kind: 'writer-bytes',
      at: describe(at),
      bytes: Array.from(new Uint8Array(at.sub(0x20).readByteArray(0x60)))
        .map(function (b) { return b.toString(16).padStart(2, '0'); })
        .join(' '),
    });
  } catch (e) {
    say({ kind: 'writer-bytes-failed', error: e.message });
  }

  // `CanMainPlayerUseGoods(int goodsId, bool cannotConsumeForRepair)` on the installed 1.17.1
  // build. The game asks this about the goods under the cursor, so it names Seamless's own items
  // by their param row -- which is the thing that has been missing all along. Nothing in this
  // repo knows the Dried Fingers' id; the locale file gives it a NAME and no number.
  //
  // `0x14068dee0` on the 1.16.2 named dump -> `0x14068ed30` (delta `+0xe50`,
  // `map-rvas-1162-to-1170.py`), unchanged into 1.17.1 as it sits below the `0xafefe9` boundary.
  const CAN_USE_GOODS = ptr('0x14068ed30');
  try {
    Interceptor.attach(CAN_USE_GOODS, {
      onEnter: function (args) {
        const goods = args[0].toInt32();
        // `CanMainPlayerUseGoods` is the game asking about the item under the cursor, so it fires
        // in the seconds before a use and nowhere else. Baselining here instead of at menu-open
        // closes the window: the last diff spanned 80 seconds because the menu was cancelled and
        // the item used a minute later, and 80 seconds of ordinary churn is not a rule.
        // Throttled hard: the game may ask this every frame for every row on screen, and an
        // unthrottled snapshot here would be far worse than the three-second timer that killed a
        // run. One baseline every two seconds while an item menu is open is enough to make the
        // window short, and nothing happens at all when no menu is open.
        if (SEEN_GOODS.has(goods)) return;
        SEEN_GOODS.add(goods);
        say({ kind: 'goods-asked', id: goods });
      },
    });
    say({ kind: 'goods-hook', at: CAN_USE_GOODS.toString() });
  } catch (e) {
    say({ kind: 'goods-hook-failed', error: e.message });
  }

  // Every key on the lobby Seamless is advertising on, dumped whenever the set changes.
  //
  // This is the only candidate left for a Dried Fingers state that DEACTIVATES with the game. A
  // latch on the item's use would not: if Seamless clears the state, the latch keeps advertising
  // it, and the key would then be a lie an invader filters on. Lobby data cannot drift that way,
  // because it IS what Seamless is telling the world.
  //
  // `GetLobbyDataCount` and `GetLobbyDataByIndex` are slots 21 and 22, immediately after
  // `GetLobbyData` (19) and `SetLobbyData` (20), which were measured live. Following header order
  // is a guess and is treated as one: the dump is only believed if one of the VALUES it returns is
  // `yknx3_seamless_master_lobby`. Match the value, never the key -- 2.0.0 hashes its key names,
  // so the `lobby_type` this comment used to name is not published at all and matching on it would
  // reject a correct dump.
  const GET_LOBBY_DATA_COUNT_SLOT = 21;
  const GET_LOBBY_DATA_BY_INDEX_SLOT = 22;
  const SET_LOBBY_DATA_SLOT = 20;
  const ADVERT_MARKER = 'yknx3_seamless_master_lobby';

  function dumpLobby (iface, lobbyId) {
    const vtable = iface.readPointer();
    const count = new NativeFunction(
      vtable.add(GET_LOBBY_DATA_COUNT_SLOT * 8).readPointer(),
      // The lobby id is a `CSteamID`, and it arrives as a `NativePointer` from the interceptor.
      // Declaring it `uint64` makes the call throw `UInt64 object expected` rather than convert,
      // so it is declared `pointer` and passed straight through.
      'int', ['pointer', 'pointer'], 'win64',
    )(iface, lobbyId);
    if (count < 0 || count > 64) return { count: count, pairs: null, why: 'implausible count' };
    const byIndex = new NativeFunction(
      vtable.add(GET_LOBBY_DATA_BY_INDEX_SLOT * 8).readPointer(),
      'bool', ['pointer', 'pointer', 'int', 'pointer', 'int', 'pointer', 'int'], 'win64',
    );
    const key = Memory.alloc(256);
    const value = Memory.alloc(8192);
    const pairs = {};
    for (let i = 0; i < count; i++) {
      if (!byIndex(iface, lobbyId, i, key, 256, value, 8192)) continue;
      pairs[key.readUtf8String()] = value.readUtf8String();
    }
    return { count: count, pairs: pairs, why: null };
  }
  globalThis.dumpLobby = dumpLobby;

  // Who inside `ersc` builds the rulebook it advertises.
  //
  // The key hashed to `ae78af44...` carries `00000000000000000000000000000101`, and bit 2 of that
  // string is Seamless's `allow_invaders`. Measured across the ten runs of 2026-09-15: the single
  // run whose `ersc_settings.ini` set `allow_invaders = 0` published `...0001`, and the nine whose
  // session flags carried `0x100000` published `...0101`. So the string is the rulebook, compacted,
  // and whatever function assembles it reads the rulebook's fields one after another.
  //
  // That caller is what this hook is for. A Dried Fingers field, if Seamless has one, is read
  // there or beside it -- which is an address to disassemble instead of a bit to guess, and the
  // guessing is what produced two retracted findings today.
  function describeCaller (ret) {
    const owner = Process.findModuleByAddress(ret);
    if (owner === null) return { address: ret.toString(), module: null };
    return {
      address: ret.toString(),
      module: owner.name,
      rva: '0x' + ret.sub(owner.base).toString(16),
    };
  }

  try {
    // `Module.getExportByName(module, name)` is gone in Frida 17 -- it throws `not a function`
    // rather than returning null, so the failure looks like a missing export instead of a missing
    // API. Exports hang off the module object now.
    const steamApi = Process.findModuleByName('steam_api64.dll');
    if (steamApi === null) throw new Error('steam_api64.dll is not loaded');
    const matchmaking = new NativeFunction(
      steamApi.getExportByName('SteamAPI_SteamMatchmaking_v009'),
      'pointer', [], 'win64',
    )();
    globalThis.steamMatchmaking = matchmaking;
    const dumped = {};
    Interceptor.attach(matchmaking.readPointer().add(SET_LOBBY_DATA_SLOT * 8).readPointer(), {
      onEnter: function (args) {
        this.lobby = args[1];
        say({
          kind: 'set-lobby-data',
          lobby: this.lobby.toString(),
          key: args[2].readUtf8String(),
          value: args[3].readUtf8String(),
          caller: describeCaller(this.returnAddress),
        });
      },
      onLeave: function () {
        const id = this.lobby.toString();
        if (dumped[id]) return;
        dumped[id] = true;
        const dump = dumpLobby(matchmaking, this.lobby);
        const values = dump.pairs === null ? [] : Object.keys(dump.pairs).map(function (k) {
          return dump.pairs[k];
        });
        say({
          kind: 'lobby-dump',
          lobby: id,
          count: dump.count,
          why: dump.why,
          advertisement: values.indexOf(ADVERT_MARKER) !== -1,
          pairs: dump.pairs,
        });
      },
    });
    say({ kind: 'set-lobby-hook', iface: matchmaking.toString() });

    // The publish routine, read out of live memory rather than off the disk.
    //
    // Every caller captured so far sits inside the Themida-protected `ERSC` section
    // (rva `0x240000..0xd30000`), where the bytes on disk are encrypted and the bytes in the
    // process are not. So the only readable copy of the code that assembles what Seamless tells
    // the world is this one, and it exists only while the game is up.
    // The search half, as Seamless actually issues it on 2.0.1.
    //
    // This module's own notes describe the filter set from an older build, including
    // `AddRequestLobbyListStringFilter("lobby_type", "yknx3_seamless_master_lobby")` -- and
    // `lobby_type` is a key 2.0.x does not publish at all, because it hashes its key names. So the
    // documented search may describe a build that is no longer installed, and a prefilter that
    // appends a key to a search is worth checking against what the search really carries.
    //
    // Slots 4, 5 and 6 are `RequestLobbyList`, `AddRequestLobbyListStringFilter` and
    // `AddRequestLobbyListNumericalFilter`.
    try {
      const vt = matchmaking.readPointer();
      Interceptor.attach(vt.add(5 * 8).readPointer(), {
        onEnter: function (args) {
          say({
            kind: 'search-string-filter',
            key: args[1].readUtf8String(),
            value: args[2].readUtf8String(),
            comparison: args[3].toInt32(),
            caller: describeCaller(this.returnAddress),
          });
        },
      });
      Interceptor.attach(vt.add(6 * 8).readPointer(), {
        onEnter: function (args) {
          say({
            kind: 'search-numerical-filter',
            key: args[1].readUtf8String(),
            value: args[2].toInt32(),
            comparison: args[3].toInt32(),
            caller: describeCaller(this.returnAddress),
          });
        },
      });
      Interceptor.attach(vt.add(4 * 8).readPointer(), {
        onEnter: function () {
          say({ kind: 'search-request', caller: describeCaller(this.returnAddress) });
        },
      });
      say({ kind: 'search-hooks' });
    } catch (e) {
      say({ kind: 'search-hooks-failed', error: e.message });
    }

    const PUBLISH_SITES = [0x612d8a, 0x623448, 0x624289];
    const SITE_WINDOW = 0x180;
    PUBLISH_SITES.forEach(function (rva) {
      const at = ersc.base.add(rva - SITE_WINDOW);
      try {
        say({
          kind: 'publish-site-bytes',
          rva: '0x' + rva.toString(16),
          from: '0x' + (rva - SITE_WINDOW).toString(16),
          bytes: Array.prototype.map.call(
            new Uint8Array(at.readByteArray(SITE_WINDOW * 2)),
            function (b) { return ('0' + b.toString(16)).slice(-2); },
          ).join(''),
        });
      } catch (e) {
        say({ kind: 'publish-site-bytes-failed', rva: '0x' + rva.toString(16), error: e.message });
      }
    });

    // `SetLobbyData` only fires when a world opens, so on a host that is already up it would say
    // nothing until a rehost. `GetLobbyData` (slot 19) is read traffic and arrives continuously --
    // this module's own `is_advertisement_lobby` calls it -- so it hands over a live lobby id
    // within seconds, and the dump above runs against the lobby the host is advertising right now.
    // `GetNumLobbyMembers` (17) is polled by a live host, so it arrives even when nothing is being
    // read out of the lobby's data. Both it and `GetLobbyData` take the lobby id in the same
    // argument, so one handler serves both.
    const LOBBY_ID_READERS = [17, 19];
    LOBBY_ID_READERS.forEach(function (slot) {
    Interceptor.attach(matchmaking.readPointer().add(slot * 8).readPointer(), {
      onEnter: function (args) {
        const id = args[1].toString();
        if (dumped[id]) return;
        dumped[id] = true;
        const dump = dumpLobby(matchmaking, args[1]);
        const values = dump.pairs === null ? [] : Object.keys(dump.pairs).map(function (k) {
          return dump.pairs[k];
        });
        say({
          kind: 'lobby-dump',
          via: 'slot' + slot,
          lobby: id,
          count: dump.count,
          why: dump.why,
          advertisement: values.indexOf(ADVERT_MARKER) !== -1,
          pairs: dump.pairs,
        });
      },
    });
    });
  } catch (e) {
    say({ kind: 'set-lobby-hook-failed', error: e.message });
  }

  Interceptor.attach(ersc.base.add(SHOW), {
    onEnter: function (args) {
      const osm = args[0];
      if (osm.isNull()) return;
      let session;
      try {
        session = osm.add(NEXT_OBJECT).readPointer();
      } catch (e) {
        return;
      }
      if (session.isNull() || !stillASession(session)) return;
      if (!SESSION.equals(session)) {
        SESSION = session;
        say({ kind: 'session-seen', osm: osm.toString(), session: session.toString() });
      }
      // Baseline now and again as the session settles. The first Dried Fingers diff was taken
      // against a baseline captured the instant the menu opened, while the session was still
      // filling itself in -- its rules qword went from zero to `0x100011` inside the window, which
      // is the field being WRITTEN FOR THE FIRST TIME rather than the item editing it. Re-taking
      // the baseline a few seconds later means the last one before an item use is of a settled
      // object, and setup stops being mistaken for the rule.
      // No snapshot here. An `Interceptor` callback runs on the GAME's thread, so a multi-megabyte
      // copy taken from one blocks the game for as long as the copy takes -- which is what the
      // player felt as sluggishness, and at 11.5 MB it is more than the two seconds the throttle
      // was spacing the copies by. Whatever this instrument reads, it reads small and off the
      // game's threads.
    },
  });

  ACTIONS.forEach(function (entry) {
    const rva = entry[0];
    const name = entry[1];
    Interceptor.attach(ersc.base.add(rva), {
      onEnter: function (args) {
        this.osm = args[0];
        try {
          this.session = this.osm.isNull() ? NULL : this.osm.add(NEXT_OBJECT).readPointer();
        } catch (e) {
          this.session = NULL;
        }
        this.before = readHead(this.session);
        if (!this.session.isNull() && !SESSION.equals(this.session)) SESSION = this.session;
        say({
          kind: 'action-enter',
          rva: '0x' + rva.toString(16),
          name: name,
          session: this.session.toString(),
          goods: confirmedGoods(),
        });
      },
      onLeave: function () {
        const after = readHead(this.session);
        say({
          kind: 'action-leave',
          rva: '0x' + rva.toString(16),
          name: name,
          session: this.session.toString(),
          moved: (this.before === null || after === null) ? null : diff(this.before, after),
        });
      },
    });
  });

  // Prove the chain before anything relies on it. A null here means the 1.16.2 global did not
  // survive the move to 1.17 and the `goods` field on every hit below will read null -- which is
  // a refusal, not a wrong answer, and is exactly the distinction the chat-correlation failure
  // did not have.
  say({ kind: 'goods-chain', global: GLOBAL_WORLD_CHR_MAN.toString(), steps: goodsChainSteps() });
  // What the Dried Fingers rule changes, since neither of the two paths that could have named it
  // outright survives contact with this build:
  //
  //   - Seamless's advertisement cannot carry it. Lobby data is written once per world opening and
  //     never again -- seven keys at each of two openings across a whole run, nothing between -- so
  //     a rule that turns on mid-session is never advertised at all.
  //   - Its publish routine cannot be read. Every captured call site inside the protected `ERSC`
  //     section starts `pushfq` and continues into mutated junk, so there is no builder to follow.
  //
  // So the rule gets found by what it moves. Snapshot the module's writable pages on a timer, and
  // when `confirmedUsedGoods` names the row, diff a snapshot taken after the use against the last
  // one from before it. Reads only: no page protection is touched and no watchpoint is armed,
  // because a guard page on this target killed the game with `0xc0000005` earlier today.
  const DRIED_FINGERS_ROW = 0x7fde6c;
  // Raised from 1500 once the heap joined the set: the snapshot went from 11.5 MB to 46.9 MB, and
  // at 1500 ms that is 31 MB/s of copying and the same again in agent garbage, inside a session the
  // player is using. A baseline three seconds old is still from before a use.
  // The goods field is three pointer reads, so sampling it often costs nothing. Nothing here runs
  // on a timer at all any more: every period below is a count of the game's own input polls, at
  // the ~82 a second `pad-frames.js` measured, and the millisecond each one replaces is named
  // beside it so the rates can be compared with the runs that used them.
  const GOODS_SAMPLE_TICKS = 12; // was a 150 ms poll
  const SETTLE_TICKS = 64; // was an 800 ms wait after a use
  const LEAD_SAMPLE_TICKS = 20; // was a 250 ms poll
  const RULE_BYTE_SAMPLE_TICKS = 40; // was a 500 ms poll
  const MAX_REPORTED_RUNS = 96;

  // The module's own writable pages are not enough on their own. Seamless keeps the session on the
  // heap, outside the module entirely, and a rule stored there would not appear in a module-only
  // diff at all -- the diff would come back empty and read as "the item changes nothing", which is
  // the most expensive wrong answer available here. So the heap range holding the session is
  // snapshotted alongside, whole, and it joins the set as soon as a hook has seen a session.
  // What gets copied, and why it is now kilobytes rather than tens of megabytes.
  //
  // This swept every writable page of the module plus the heap around the session, on a timer.
  // Measured cost on 2026-09-15: 51 MB per snapshot every three seconds, and opening the Seamless
  // menu widened it mid-session because that is when the session pointer arrives. The player felt
  // it as a stall every few seconds and the game hard-locked and died when they opened the menu to
  // use the item -- the watcher recorded `session detached: process-terminated`. The instrument
  // killed the run it was measuring, which is the one thing it may never do.
  //
  // So it copies two small things instead: the module's real `.data` section, and the session
  // object. Seamless's own per-session state is in one of those or it is somewhere this has to be
  // widened to DELIBERATELY, with the cost measured first.
  const ERSC_DATA_RVA = 0x21a000;
  const ERSC_DATA_SIZE = 0x809c;
  const SESSION_BYTES = 0x1000;

  // Cached, because this was the sluggishness the player felt. `Process.enumerateRanges` walks
  // every mapping in the process, and it was being called twice inside every snapshot, which then
  // ran four times around a single menu-open. The set only changes when the session does.
  let cachedRanges = null;
  let cachedFor = null;

  function snapshotRanges () {
    const key = SESSION.toString();
    if (cachedRanges !== null && cachedFor === key) return cachedRanges;
    const built = buildRanges();
    cachedRanges = built;
    cachedFor = key;
    return built;
  }

  // The two regions worth watching, and nothing else.
  //
  // `ersc+0x24dea0..0x24dfd0` is where the only diff that ever produced module hits landed -- eight
  // runs inside one 0x130-byte block, including a byte going 0 -> 1. That diff spanned 80 seconds
  // and so proves nothing on its own, but it is a lead, and a lead 0x200 bytes wide can be read
  // continuously for free. The session gets the same treatment at its first 0x1000.
  //
  // Everything wider has now been measured and is not worth its cost: `.data` and the session
  // answered zero, all 11.5 MB of the module's writable pages answered zero, and copying that much
  // is what made the game sluggish and, on a timer, killed it outright.
  const LEAD_RVA = 0x24de00;
  const LEAD_SIZE = 0x200;

  function buildRanges () {
    const ranges = [{ base: ersc.base.add(LEAD_RVA), size: LEAD_SIZE }];
    if (!SESSION.isNull()) ranges.push({ base: SESSION, size: SESSION_BYTES });
    return ranges;
  }

  function snapshot () {
    return snapshotRanges().map(function (r) {
      let bytes = null;
      try {
        bytes = new Uint8Array(r.base.readByteArray(r.size));
      } catch (e) {
        bytes = null;
      }
      return { base: r.base.toString(), size: r.size, bytes: bytes };
    });
  }

  // Which bytes move on their own. Eleven megabytes of a live game contain allocator links,
  // counters and timers, so a raw before/after diff of it answers with thousands of runs and names
  // nothing -- which is the same failure as the eight-qword session diff, at a larger scale.
  //
  // The mask is built from the run itself rather than reasoned about: every pair of consecutive
  // idle snapshots marks the bytes that differed between them, and a byte that has ever moved
  // while nobody used anything is not evidence when it moves again. It only grows, so the longer
  // the session runs before the item is used, the quieter the answer.
  const noiseMask = {};

  function markNoise (before, after) {
    const index = {};
    before.forEach(function (r) { index[r.base] = r; });
    after.forEach(function (r) {
      const was = index[r.base];
      if (!was || was.bytes === null || r.bytes === null || was.size !== r.size) return;
      let mask = noiseMask[r.base];
      if (mask === undefined || mask.length !== r.size) {
        mask = new Uint8Array(r.size);
        noiseMask[r.base] = mask;
      }
      for (let i = 0; i < r.size; i++) {
        if (was.bytes[i] !== r.bytes[i]) mask[i] = 1;
      }
    });
  }

  function maskedBytes () {
    let n = 0;
    Object.keys(noiseMask).forEach(function (base) {
      const mask = noiseMask[base];
      for (let i = 0; i < mask.length; i++) n += mask[i];
    });
    return n;
  }

  // Changed byte runs, as module-relative offsets where the range is the module. A run rather than
  // a byte because a rule that moves is a field, and a field reported as eight separate findings is
  // what made the last diff unreadable.
  function diffSnapshots (before, after) {
    const index = {};
    before.forEach(function (r) { index[r.base] = r; });
    const runs = [];
    let suppressed = 0;
    after.forEach(function (r) {
      const was = index[r.base];
      if (!was || was.bytes === null || r.bytes === null || was.size !== r.size) return;
      const mask = noiseMask[r.base];
      let start = -1;
      for (let i = 0; i <= r.size; i++) {
        let differs = i < r.size && was.bytes[i] !== r.bytes[i];
        if (differs && mask !== undefined && mask[i] === 1) {
          differs = false;
          suppressed += 1;
        }
        if (differs && start < 0) start = i;
        if (!differs && start >= 0) {
          if (runs.length < MAX_REPORTED_RUNS) {
            const at = ptr(r.base).add(start);
            const inModule = at.compare(ersc.base) >= 0
              && at.compare(ersc.base.add(ersc.size)) < 0;
            runs.push({
              where: inModule
                ? 'ersc+0x' + at.sub(ersc.base).toString(16)
                : at.toString(),
              len: i - start,
              before: Array.prototype.slice.call(was.bytes.subarray(start, Math.min(i, start + 16))),
              after: Array.prototype.slice.call(r.bytes.subarray(start, Math.min(i, start + 16))),
            });
          }
          start = -1;
        }
      }
    });
    return { runs: runs, suppressed: suppressed };
  }

  // The baseline, taken when a Seamless menu opens. That is the only way to the item, so it is
  // both recent enough to be from before a use and rare enough to cost nothing -- and it replaces
  // a three-second timer that stalled the player's game and then killed it.
  // A continuous watch over the 0x200-byte lead, one sample every `LEAD_SAMPLE_TICKS` input polls.
  // At this size a copy is microseconds, which is what makes it affordable on the game's own
  // thread -- the whole difference between this and the 11.5 MB snapshot it replaces. Every change
  // is reported with the goods field beside it, so a write that coincides with an item use names
  // itself.
  let lastLead = null;
  function sampleLead () {
    let bytes;
    try {
      bytes = Array.prototype.slice.call(new Uint8Array(
        ersc.base.add(LEAD_RVA).readByteArray(LEAD_SIZE),
      ));
    } catch (e) {
      return;
    }
    if (lastLead === null) {
      lastLead = bytes;
      return;
    }
    const moved = [];
    for (let i = 0; i < bytes.length; i++) {
      if (bytes[i] !== lastLead[i]) {
        moved.push({ at: 'ersc+0x' + (LEAD_RVA + i).toString(16), from: lastLead[i], to: bytes[i] });
      }
    }
    lastLead = bytes;
    if (moved.length === 0) return;
    say({ kind: 'lead-moved', count: moved.length, moved: moved.slice(0, 24), goods: confirmedGoods().id });
  }

  let rolling = null;
  function takeBaseline () {
    const next = snapshot();
    if (rolling !== null) markNoise(rolling, next);
    rolling = next;
  }
  globalThis.takeBaseline = takeBaseline;
  let lastGoods = null;
  let pending = false;

  // The use that is still settling, and the tick its diff comes due on.
  //
  // This was an 800 ms `setTimeout`, which is a guess at how long the game takes to finish
  // applying an item expressed in a unit the game does not use. The wait is the same length in
  // wall clock at the measured poll rate, and it is now counted in the frames the game itself
  // ran -- so a loaded or stuttering game gets the frames it needs rather than the milliseconds a
  // healthy one needed. There is nothing to hook for "the item finished": naming that writer is
  // what this whole instrument exists to find out, so the settle cannot be an event yet.
  let settle = null;

  function sampleGoods (tick) {
    // `confirmedGoods` returns a record, so comparing it to a row number is always unequal and the
    // trigger never fires -- which looks exactly like an item that changes nothing. Compare ids.
    const goods = confirmedGoods().id;
    if (goods === lastGoods) return;
    const previous = lastGoods;
    lastGoods = goods;
    if (pending || goods === null) return;
    // The first ordinary item the player uses runs the whole diff as a control. An experiment that
    // gets one moment has to be known working before that moment arrives, and a control also says
    // what an item that is NOT the rule looks like here -- an empty control means the instrument
    // is blind and a loud one means the mask is not finished yet. Either verdict is worth more
    // before the Dried Fingers than after it.
    const dried = goods === DRIED_FINGERS_ROW;
    // This test was inverted on its first outing and the control silently never ran: `control`
    // starts true and the guard returned while it was true, so every ordinary item was skipped and
    // the one Dried Fingers use had nothing to be compared against. A control that does not fire
    // looks exactly like an item that changes nothing.
    // Every ordinary item runs a control, not just the first. The once-only latch spent itself on
    // whichever item happened to come first and then silently ignored the rest, so an item used to
    // answer a question produced nothing at all. A control now costs one 37 KB diff, which is
    // cheap enough that there is no reason to ration it.
    if (!dried && previous === null) return;
    pending = true;
    settle = {
      dueAt: tick + SETTLE_TICKS,
      before: rolling,
      dried: dried,
      row: '0x' + goods.toString(16),
    };
    say({
      kind: dried ? 'dried-fingers-used' : 'control-item-used',
      row: settle.row,
    });
  }

  // The diff, taken once the game has run `SETTLE_TICKS` more frames since the use.
  function finishSettle (tick) {
    if (settle === null || tick < settle.dueAt) return;
    const done = settle;
    settle = null;
    const after = snapshot();
    const diff = done.before === null ? null : diffSnapshots(done.before, after);
    say({
      kind: done.dried ? 'dried-fingers-diff' : 'control-diff',
      row: done.row,
      had_baseline: done.before !== null,
      settle_ticks: SETTLE_TICKS,
      masked_bytes: maskedBytes(),
      suppressed: diff === null ? null : diff.suppressed,
      runs: diff === null ? null : diff.runs,
    });
    rolling = after;
    pending = false;
  }

  // Find the session without waiting for a menu, and take the first baseline at load.
  //
  // A hot-reload resets this agent's state, so after every edit the session was unknown and the
  // snapshot covered only the module -- which is not where the Dried Fingers changes were, and a
  // control taken in that state would have measured the wrong region and looked like a clean
  // result. This module's own DLL already solved it without hooking anything: it reports finding
  // the session "via a pointer in ersc's own writable data at 0x1805e51c0". Read that pointer and
  // let `stillASession` judge it, so a wrong or stale value is a refusal rather than a bad answer.
  // Scan ersc's own `.data` for a pointer the validator accepts as a session.
  //
  // Two guesses at a single address failed before this: `ersc+0x5e51c0` holds `0x71c20140`, which
  // `stillASession` refuses, and so does its `+0x58`. Rather than guess a third offset, walk the
  // whole section -- 32 KB, so 4,112 candidate pointers, once per load -- and let the validator
  // decide. Every read is wrapped, so a pointer into nothing is skipped rather than faulting the
  // game, and the report says how many candidates matched: more than one means the validator is
  // too loose to be trusted here, and the answer then is not to take the first.
  // `.data` holds none: the scan came back with zero matches, because Seamless keeps the pointer in
  // the protected `ERSC` section's writable pages -- which is exactly where this module's own DLL
  // found it, at rva 0x5e51c0, well past `.data`'s 32 KB. So the search runs over those pages
  // instead, read once in bulk rather than a pointer at a time, and only qwords that look like a
  // heap address in the low 4 GB are put to the validator. The observed session this run was
  // `0x469ac930`; module bases here are `0x140000000`, `0x180000000` and `0x6ffff9…`, all above
  // the window, so the filter separates heap from module without naming any address.
  const HEAP_LOW = 0x10000000;
  const HEAP_HIGH = 0x100000000;

  function erscWritablePages () {
    return Process.enumerateRanges('rw-').filter(function (r) {
      return r.base.compare(ersc.base) >= 0
        && r.base.compare(ersc.base.add(ersc.size)) < 0;
    });
  }

  if (SESSION.isNull()) {
    const found = [];
    const pages = erscWritablePages();
    let examined = 0;
    pages.forEach(function (page) {
      if (found.length >= 8) return;
      let words;
      try {
        words = new BigUint64Array(page.base.readByteArray(page.size - (page.size % 8)));
      } catch (e) {
        return;
      }
      for (let index = 0; index < words.length && found.length < 8; index++) {
        const value = words[index];
        if (value < BigInt(HEAP_LOW) || value >= BigInt(HEAP_HIGH)) continue;
        examined += 1;
        const candidate = ptr('0x' + value.toString(16));
        // `stillASession` alone is far too loose to search with: it only asks whether `+0x150`
        // holds one of eight small states, and on its own it accepted eight candidates here,
        // including `0x1000898b` -- an odd address no allocator ever returned. Two more conditions
        // separate an object from a coincidence, and both come from the session that was actually
        // observed this run: it was 16-byte aligned, it lay inside a committed rw- range, and its
        // rules qword at `+0x00` read `0x100011` -- a flags word, so non-zero and far too small to
        // be a pointer.
        if ((value & 0xfn) !== 0n) continue;
        if (Process.findRangeByAddress(candidate) === null) continue;
        let rules;
        try {
          rules = candidate.readU64();
        } catch (e) {
          continue;
        }
        if (rules.equals(0) || rules.compare(uint64('0x100000000')) >= 0) continue;
        // The rules qword has to carry `allow_invaders`. Without this the scan chose an object
        // whose rules read `0x65` -- no bit 20 at all -- while the `show` hook, which is
        // authoritative, was handing over one reading `0x100011`. Two diffs were then measured
        // against different memory and neither could be subtracted from the other. This host has
        // the rule on, so an object that does not say so is not its session.
        if (rules.and(uint64('0x100000')).equals(0)) continue;
        try {
          if (!stillASession(candidate)) continue;
        } catch (e) {
          continue;
        }
        found.push({
          at: page.base.add(index * 8).sub(ersc.base).toString(),
          session: candidate.toString(),
          rules: rules.toString(16),
        });
      }
    });
    if (found.length === 1) SESSION = ptr(found[0].session);
    say({
      kind: 'session-scan',
      pages: pages.length,
      examined: examined,
      matches: found.length,
      found: found,
      taken: SESSION.toString(),
    });
  }

  if (false) {
    const found = [];
    for (let offset = 0; offset + 8 <= ERSC_DATA_SIZE && found.length < 8; offset += 8) {
      let candidate;
      try {
        candidate = ersc.base.add(ERSC_DATA_RVA + offset).readPointer();
      } catch (e) {
        continue;
      }
      if (candidate.isNull()) continue;
      try {
        if (!stillASession(candidate)) continue;
      } catch (e) {
        continue;
      }
      found.push({
        rva: '0x' + (ERSC_DATA_RVA + offset).toString(16),
        session: candidate.toString(),
      });
    }
    if (found.length === 1) SESSION = ptr(found[0].session);
    say({ kind: 'session-scan', matches: found.length, found: found, taken: SESSION.toString() });
  }

  if (typeof globalThis.takeBaseline === 'function') globalThis.takeBaseline();

  const armed = snapshotRanges();
  say({
    kind: 'dried-fingers-watch',
    poll_ticks: GOODS_SAMPLE_TICKS,
    ranges: armed.length,
    bytes: armed.reduce(function (n, r) { return n + r.size; }, 0),
    session: SESSION.toString(),
  });

  // The one byte the Dried Fingers use moved outside the session.
  //
  // Measured 2026-09-15 on run br-20260915-202555-3500: using the item took `ersc+0x21ad70` from
  // `0x62` to `0x63`, i.e. bit 0 up, while the other sixteen changed runs were all inside the
  // session object and most of them look like the session filling itself in -- `session+0x00` went
  // from zero to `0x100011`, which is the rules qword being POPULATED rather than edited.
  //
  // This byte decides the whole question. It lives in the module's static data, so it does NOT die
  // with the session the way a heap field does: if Seamless never clears it, a key built on it
  // would keep advertising after the game had stopped honouring the rule, which is the exact
  // failure the key must not have. So watch it, and watch the rules qword beside it, and say every
  // change with its neighbours -- what matters is not that it went up but whether it comes down.
  const RULE_BYTE_RVA = 0x21ad70;
  const RULE_BYTE_CONTEXT = 8;
  let lastRuleBytes = null;
  let lastRules = null;
  function sampleRuleByte () {
    let bytes;
    try {
      bytes = Array.prototype.slice.call(new Uint8Array(
        ersc.base.add(RULE_BYTE_RVA - RULE_BYTE_CONTEXT).readByteArray(RULE_BYTE_CONTEXT * 2 + 1),
      ));
    } catch (e) {
      return;
    }
    const text = bytes.join(',');
    let rules = null;
    if (!SESSION.isNull()) {
      try {
        rules = SESSION.readU64().toString(16);
      } catch (e) {
        rules = null;
      }
    }
    if (text === lastRuleBytes && rules === lastRules) return;
    lastRuleBytes = text;
    lastRules = rules;
    // Two different objects have been taken for the session in one run: the `show` hook handed over
    // `0x469ac930` and the data scan chose `0x5a030140`, and the Dried Fingers diff and the control
    // diff were therefore measured against different memory. Report both shapes every time either
    // moves, so the disagreement is visible rather than silently averaged.
    const shapes = [SESSION].map(function (at) {
      if (at.isNull()) return null;
      try {
        return {
          at: at.toString(),
          rules: at.readU64().toString(16),
          state: at.add(SESSION_STATE).readU32(),
        };
      } catch (e) {
        return { at: at.toString(), unreadable: true };
      }
    });
    say({
      kind: 'rule-byte',
      session_shape: shapes[0],
      at: 'ersc+0x' + RULE_BYTE_RVA.toString(16),
      value: bytes[RULE_BYTE_CONTEXT],
      around: bytes,
      session_rules: rules,
      goods: confirmedGoods().id,
    });
  }

  // One hook, driving all three samplers and the settle.
  //
  // Installed last on purpose: every piece of state the callbacks close over -- the rolling
  // baseline, the session the scan above chose, the lead and rule-byte histories -- is initialised
  // by the time the game next polls its pad, so no tick can run against a half-built agent.
  //
  // The three periods share a common multiple every 120 ticks -- once every second and a half at
  // the measured rate -- and that is the only frame carrying all of them: 0x200 bytes of the lead,
  // 0x11 around the rule byte, and the three pointers the goods field sits behind. Every other
  // frame carries one sampler or none, and a sampler that finds nothing moved sends nothing.
  const tick = gameTick();
  if (tick === null) {
    say({
      kind: 'tick-missing',
      text: 'neither XInputGetState nor the pad builder is reachable, so nothing is sampled',
    });
  } else {
    let ticks = 0;
    Interceptor.attach(tick.at, {
      onEnter: function () {
        ticks += 1;
        if (ticks % GOODS_SAMPLE_TICKS === 0) sampleGoods(ticks);
        finishSettle(ticks);
        if (ticks % LEAD_SAMPLE_TICKS === 0) sampleLead();
        if (ticks % RULE_BYTE_SAMPLE_TICKS === 0) sampleRuleByte();
      },
    });
    say({
      kind: 'tick',
      at: tick.what,
      goods_every: GOODS_SAMPLE_TICKS,
      lead_every: LEAD_SAMPLE_TICKS,
      rule_byte_every: RULE_BYTE_SAMPLE_TICKS,
      settle: SETTLE_TICKS,
    });
  }

  say({ kind: 'waiting', text: 'open any Seamless menu and show hands the session over' });
}
