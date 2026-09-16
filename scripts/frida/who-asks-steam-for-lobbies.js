// Who asks Steam for a lobby list during a Seamless invasion search, and from which module.
//
// The question this settles: `er-invasion-warp` hooks `ISteamMatchmaking::RequestLobbyList` at the
// address it reads out of the game's own vtable, and that detour has never once been entered --
// run br-20260916-015211-aa43 logged zero hits in 6043 lines with `hunt_hooked = true`. So either
// nothing asks Steam for lobbies during an invasion search, or the asking goes through an address
// our detour is not on.
//
// The obvious explanation is already falsified statically: `ersc.dll` and `eldenring.exe` both
// carry the string `SteamMatchMaking009`, so they share one interface version, one vtable and one
// set of function addresses.
//
// So this hooks the flat API in `steam_api64.dll` instead, which is a different entry point to the
// same work and is exported by name, and reports the calling module for every hit. A call that
// arrives here from `ersc.dll` while our own detour stays silent localises the problem to our hook
// address; no call at all localises it to Seamless not using the lobby list.
const STEAM = 'steam_api64.dll';
const WATCH = [
  'SteamAPI_ISteamMatchmaking_RequestLobbyList',
  'SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter',
  'SteamAPI_ISteamMatchmaking_RequestLobbyData',
  'SteamAPI_ISteamMatchmaking_JoinLobby',
  'SteamAPI_ISteamMatchmaking_CreateLobby',
];

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  console.log(`who-asks: ${STEAM} is not loaded`);
} else {
  console.log(`who-asks: ${STEAM} @${steam.base}`);
}

function whereFrom(address) {
  const owner = Process.findModuleByAddress(address);
  return owner ? `${owner.name}+0x${address.sub(owner.base).toString(16)}` : `${address}`;
}

const counts = {};
for (const name of WATCH) {
  let address = null;
  try {
    address = steam === null ? null : steam.findExportByName(name);
  } catch (e) {
    address = null;
  }
  if (address === null) {
    console.log(`who-asks: ${name} -- export not found`);
    continue;
  }
  counts[name] = 0;
  console.log(`who-asks: hooked ${name} @${address}`);
  Interceptor.attach(address, {
    onEnter(args) {
      counts[name] += 1;
      const n = counts[name];
      // Every call for the first few, then thinned: a search retries on a timer and the point is
      // made by the first one. A line per call would bury the module name under its own repeats.
      if (n > 6 && n % 25 !== 0) {
        return;
      }
      console.log(`who-asks: ${name} #${n} from ${whereFrom(this.returnAddress)}`);
    },
  });
}

// Integrity control, and it has to be a function in THIS module.
//
// `PeekMessageW` was the control and it never fired once, so the first pass of this agent proved
// nothing at all: a Steam hook that says nothing and a hook that was never installed read
// identically. `SteamAPI_RunCallbacks` is pumped every frame by any Steam application and lives in
// `steam_api64.dll`, so a line from it proves both that Interceptor is live and that hooks on this
// specific module take -- which is the exact claim the silence of the lobby hooks depends on.
let pumped = 0;
/// Set by the arming block below, once the export has been resolved.
let armWhenInWorld = () => {};
const control = steam === null ? null : steam.findExportByName('SteamAPI_RunCallbacks');
if (control === null) {
  console.log('who-asks: control SteamAPI_RunCallbacks not found');
} else {
  console.log(`who-asks: control SteamAPI_RunCallbacks @${control}`);
  Interceptor.attach(control, {
    onEnter() {
      pumped += 1;
      armWhenInWorld();
      if (pumped === 1 || pumped === 600 || pumped === 6000) {
        console.log(
          `who-asks: control -- SteamAPI_RunCallbacks fired ${pumped}x, hooks on ${STEAM} are live`
        );
      }
    },
  });
}

// Drive the search from here, rather than asking the player to press anything.
//
// `er_invasion_warp_request_invade` only sets the two atomics the game task drains, so calling it
// from Frida's thread arms exactly what the finger popup arms and leaves the actual call to
// `ersc.dll` on the thread that already owns it. The reach in force is whatever the last popup
// chose, which the run log records as BothNearAndFar.
//
// Called at load rather than on a timer: `setTimeout` does not fire under this watcher.
const ours = Process.findModuleByName('er_invasion_warp.dll');
if (ours === null) {
  console.log('who-asks: er_invasion_warp.dll is not loaded -- cannot arm a search from here');
} else {
  const arm = ours.findExportByName('er_invasion_warp_request_invade');
  if (arm === null) {
    console.log('who-asks: er_invasion_warp_request_invade is not exported');
  } else {
    // Armed from inside the Steam callback pump rather than at load, because an agent attached
    // during boot runs before the world exists: the arm lands while no session can be resolved and
    // the ladder takes its first rung against whatever block is readable at the title screen.
    // `SteamAPI_RunCallbacks` is the only thing here that ticks, since this watcher's `setTimeout`
    // never fires -- so a count on it is the clock.
    const ARM_AFTER_CALLBACKS = 3000;
    let armedOnce = false;
    armWhenInWorld = () => {
      if (armedOnce || pumped < ARM_AFTER_CALLBACKS) {
        return;
      }
      armedOnce = true;
      const armed = new NativeFunction(arm, 'bool', [])();
      console.log(`who-asks: armed a search at callback ${pumped} -> ${armed}`);
    };
  }
}

// The flat wrappers are the wrong instrument, and their own control says so.
//
// Not one of the five fired, and neither did `SteamAPI_RunCallbacks` beside them, while
// `PeekMessageW` hooks in this same attach logged 1200 calls. Hooks are live; these functions are
// simply not entered. That is what using the C++ interface looks like: both `eldenring.exe` and
// `ersc.dll` carry the string `SteamMatchMaking009`, so both resolve the interface through
// `SteamInternal_FindOrCreateUserInterface` and call its virtual methods, never the wrappers.
//
// So resolve the interface the same way they do and hook the vtable slot itself. Slot 4 is
// `RequestLobbyList` and slot 5 `AddRequestLobbyListStringFilter` in the Steamworks header order,
// which is the pair `er-invasion-warp` already detours -- so a hit here while our DLL's log stays
// empty localises the failure to our hook, and silence in both says Seamless never asks.
const SLOTS = { 4: 'RequestLobbyList', 5: 'AddRequestLobbyListStringFilter', 13: 'CreateLobby', 14: 'JoinLobby' };
try {
  const getUser = steam.findExportByName('SteamAPI_GetHSteamUser');
  const find = steam.findExportByName('SteamInternal_FindOrCreateUserInterface');
  if (getUser === null || find === null) {
    console.log('who-asks: cannot resolve the interface -- accessor exports missing');
  } else {
    const user = new NativeFunction(getUser, 'int32', [])();
    const version = Memory.allocUtf8String('SteamMatchMaking009');
    const iface = new NativeFunction(find, 'pointer', ['int32', 'pointer'])(user, version);
    console.log(`who-asks: ISteamMatchmaking(SteamMatchMaking009) user=${user} iface=${iface}`);
    if (!iface.isNull()) {
      const vtable = iface.readPointer();
      const seen = {};
      for (const slot of Object.keys(SLOTS)) {
        const fn = vtable.add(parseInt(slot, 10) * Process.pointerSize).readPointer();
        const name = SLOTS[slot];
        seen[name] = 0;
        console.log(`who-asks: vtable[${slot}] ${name} @${fn} (${whereFrom(fn)})`);
        Interceptor.attach(fn, {
          onEnter() {
            seen[name] += 1;
            const n = seen[name];
            if (n > 6 && n % 25 !== 0) {
              return;
            }
            console.log(`who-asks: VTABLE ${name} #${n} from ${whereFrom(this.returnAddress)}`);
          },
        });
      }
    }
  }
} catch (e) {
  console.log(`who-asks: vtable hook failed: ${e.message}`);
}

// Is Seamless's session pointer there at all?
//
// Every drive in this run died upstream of the lobby question: the DLL logs `SessionNotIdentified`
// and never reaches `drive_pending_reinvade`, so no invade call is made and nothing can reach
// Steam. A working run resolved it as "found at 0x1b76c1130 via a pointer in ersc's own writable
// data at 0x180c64c88" -- ersc+0xc64c88 with the module at its preferred base. Read that slot and
// the session head it points at, which says whether the pointer is absent or the resolver is
// rejecting something real.
const SESSION_SLOT_RVA = 0xc64c88;
const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  console.log('who-asks: ersc.dll not loaded');
} else {
  const slot = ersc.base.add(SESSION_SLOT_RVA);
  try {
    const session = slot.readPointer();
    console.log(`who-asks: ersc+0x${SESSION_SLOT_RVA.toString(16)} @${slot} -> session ${session}`);
    if (!session.isNull()) {
      // The slot holds the OWNER, not the session -- both actions read `rcx+0x58` exactly once,
      // and the working run reported `owner 0x72cffd20` beside `session 0x1b76c1130`. So the hop
      // is `+0x58` first; reading `+0x150` on the owner faults, which is what the first pass did.
      let owner = 'unreadable';
      try {
        owner = `${session.add(0x58).readPointer()}`;
      } catch (e) {
        owner = `+0x58 unreadable (${e.message})`;
      }
      console.log(`who-asks: owner ${session} -> +0x58 session ${owner}`);
      try {
        const real = session.add(0x58).readPointer();
        if (!real.isNull()) {
          console.log(
            `who-asks: session ${real} +0x150 state=0x${real.add(0x150).readU32().toString(16)} ` +
              `+0x14c guard=${real.add(0x14c).readS32()}`
          );
        }
      } catch (e) {
        console.log(`who-asks: session head unreadable: ${e.message}`);
      }
    }
  } catch (e) {
    console.log(`who-asks: session slot unreadable: ${e.message}`);
  }
}

// A second control, because the first one went quiet.
//
// `SteamAPI_RunCallbacks` fired 600 times in one attach and not once in the next, while the lobby
// hooks stayed silent in both. That makes the silence unreadable: a hook that says nothing and a
// hook that was never reached look identical without something in the same attach that is known to
// fire. `PeekMessageW` logged 1200 calls earlier in this session and belongs to the game's own
// message loop rather than to Steam, so it survives whatever quietens the Steam pump.
let framePumped = 0;
try {
  const peek = Module.getGlobalExportByName('PeekMessageW');
  Interceptor.attach(peek, {
    onEnter() {
      framePumped += 1;
      if (framePumped === 1 || framePumped === 1200) {
        console.log(
          `who-asks: control2 -- PeekMessageW fired ${framePumped}x; steam callbacks so far ${pumped}`
        );
      }
    },
  });
  console.log(`who-asks: control2 PeekMessageW @${peek}`);
} catch (e) {
  console.log(`who-asks: control2 failed: ${e.message}`);
}

// Reload marker, so a silent attach is distinguishable from a stale one.
console.log('who-asks: reloaded -- controls re-armed on a single attach');

// Arm now, not on a callback count.
//
// The count was chosen so the arm landed after the world was up, but it depends on
// `SteamAPI_RunCallbacks` being pumped, and on an unfocused game it is not -- the controls sit at
// zero and the arm never fires. The heartbeat's `ersc_session=` field already says a session is
// resolvable, which is the condition the count was standing in for, so arm directly.
try {
  const mine = Process.findModuleByName('er_invasion_warp.dll');
  const armNow = mine === null ? null : mine.findExportByName('er_invasion_warp_request_invade');
  if (armNow === null) {
    console.log('who-asks: cannot arm -- export missing');
  } else {
    console.log(`who-asks: arming immediately -> ${new NativeFunction(armNow, 'bool', [])()}`);
  }
} catch (e) {
  console.log(`who-asks: immediate arm failed: ${e.message}`);
}
