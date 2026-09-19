// Does the lobby-list result ever get delivered back to whoever asked for it?
//
// # The hole this fills
//
// `invasion-connect-probe.js` hooks the four `ISteamMatchmaking` reader slots and, on run
// `br-20260918-002055-bcc4`, caught nothing at all across 28 full search cycles:
//
// ```text
// GetLobbyByIndex     0
// JoinLobby           0
// GetNumLobbyMembers  0
// GetLobbyData        0
// ```
//
// Those addresses are not dead. `scripts/er-lobby-search-proof.py` called the same slots minutes
// earlier and read every key of a real lobby back through them, and the same run's own log shows
// Seamless reaching our `RequestLobbyList` detour on every cycle. So the request goes out and
// nobody ever looks at a single entry of what comes back.
//
// A request whose answer is never read is the signature of a result that never arrives. Steam
// returns `RequestLobbyList` asynchronously as a `SteamAPICall_t`, and the `LobbyMatchList_t` that
// carries the answer reaches the caller only when something pumps the callback queue. If nothing
// pumps it, the caller waits, learns nothing, and gives up -- which is the fifteen seconds this
// session spends at state `0x12`, on every cycle, forever.
//
// # What this counts
//
// The pump and the registration, by name, out of `steam_api64.dll`'s export table. Counts only:
// these are per-frame functions and one line each would bury the transcript. A periodic summary
// says what has been seen since the last one, so a stall shows up as a number that stops moving
// rather than as silence that could equally mean the hook never installed.
//
// Read-only: entry-only interceptors on exported functions. Nothing is replaced, no game memory is
// written, and no lobby is joined, created or modified.
'use strict';

const STEAM = 'steam_api64.dll';

// The classic pump, the manual-dispatch pump that replaced it, and the two registration calls that
// say somebody is waiting for an async result. `SteamAPI_RunCallbacks` is the one that matters: a
// zero there with requests going out is the whole answer.
const WATCH = [
  'SteamAPI_RunCallbacks',
  'SteamAPI_ManualDispatch_RunFrame',
  'SteamAPI_ManualDispatch_GetNextCallback',
  'SteamAPI_ManualDispatch_FreeLastCallback',
  'SteamAPI_RegisterCallResult',
  'SteamAPI_UnregisterCallResult',
  'SteamAPI_RegisterCallback',
];

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  send({ tag: 'fatal', reason: STEAM + ' not loaded' });
} else {
  const counts = {};
  const installed = [];
  const missing = [];

  const exports = new Map();
  for (const e of steam.enumerateExports()) {
    exports.set(e.name, e.address);
  }

  for (const name of WATCH) {
    const address = exports.get(name);
    if (address === undefined) {
      missing.push(name);
      continue;
    }
    counts[name] = 0;
    try {
      Interceptor.attach(address, {
        onEnter() {
          counts[name] += 1;
        },
      });
      installed.push({ name: name, address: address.toString() });
    } catch (error) {
      send({ tag: 'hook-failed', name: name, address: address.toString(), error: String(error) });
    }
  }

  send({ tag: 'hooks', module: STEAM, base: steam.base.toString(), installed: installed, missing: missing });

  // A name absent from the export table is not a silent zero. `SteamAPI_RunCallbacks` missing
  // would mean this build dispatches some other way, and reporting the absence is what keeps that
  // from reading as "the pump never ran".
  //
  // The delta is pulled by the driver rather than pushed on a timer, and it is the same question
  // either way: did the pump move between two moments somebody cared about. A clock inside the
  // agent picks those moments blind -- it straddles the drive it was meant to bracket, and it
  // keeps reporting after the run it was taken for is over.
  let previous = Object.assign({}, counts);
  rpc.exports = {
    pump: function () {
      const delta = {};
      let moved = false;
      for (const name of Object.keys(counts)) {
        const step = counts[name] - previous[name];
        delta[name] = step;
        if (step !== 0) {
          moved = true;
        }
      }
      previous = Object.assign({}, counts);
      return { moved: moved, since_last: delta, total: Object.assign({}, counts) };
    },
  };
}
