// How long is the ENGINE prepared to wait for a join, and is its connect still running when this
// mod calls one lost?
//
// `attempt_verdict::CONNECT_DEADLINE_MS` is 1500ms, derived from 313 successes all measured on one
// machine reaching hosts it could almost touch. Run br-20260917-195855-46cf found a real remote
// host and cancelled the connect at that deadline, repeatedly:
//
//   sweep: a host is in m61_48_45_00 -- the search points there and stops widening
//   local-invasion: session state 0x0e SEARCHING -> 0x0f -> 0x12
//   local-invasion: the connection at state 0x0012 has not landed in 1500ms ... Calling it lost
//
// The player's report: "I found a host in Highroad Cross and it says -- invading, but it doesn't
// invade."
//
// `CSSessionManagerImp` holds the engine's own answer in two `FD4Time` fields that count DOWN from
// whatever the engine arms them to. If that is 30.0 then the engine waits twenty times longer than
// this mod, and a deadline of ours that fires first is racing the thing it claims to observe.
//
// The sampling event is Steam's own matchmaking call rather than a game function: those exports
// attach cleanly, whereas `Interceptor.attach` on `MenuWindowJob::Run` fails with `unable to
// intercept function at 00000001407AD1C0` because our own union detour already owns that address,
// and `scripts/map-rvas-1162-to-1170.py` cannot sign `CSSessionManager::JoinSession` (3 shape
// matches, no anchor). A search calls Steam constantly, which is exactly the window the connect
// lives in. No timer of this script's own, so nothing here can go stale or flood.
//
// Read-only: six reads of one singleton per sampled call.

'use strict';

const SESSION_MANAGER_GLOBAL_RVA = 0x3d7a4d0;
const LOBBY_STATE_OFFSET = 0x0c;
const PROTOCOL_STATE_OFFSET = 0x10;
const JOIN_REQUEST_HANDLE_OFFSET = 0x28;
const JOIN_CHECK_REMAIN_OFFSET = 0x1a0;
const WAIT_INIT_REMAIN_OFFSET = 0x1b0;

function moduleNamed(want) {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === want) return m;
  }
  return null;
}

const game = moduleNamed('eldenring.exe');
const steam = moduleNamed('steam_api64.dll');

if (game === null || steam === null) {
  send({ tag: 'fatal', game: game !== null, steam: steam !== null });
} else {
  const managerSlot = game.base.add(SESSION_MANAGER_GLOBAL_RVA);
  send({ tag: 'base', game: game.base.toString(), managerSlot: managerSlot.toString() });

  const read = () => {
    try {
      const mgr = managerSlot.readPointer();
      if (mgr.isNull()) return null;
      return {
        lobbyState: mgr.add(LOBBY_STATE_OFFSET).readS32(),
        protocolState: mgr.add(PROTOCOL_STATE_OFFSET).readS32(),
        joinHandle: mgr.add(JOIN_REQUEST_HANDLE_OFFSET).readS32(),
        joinCheckRemain: mgr.add(JOIN_CHECK_REMAIN_OFFSET).readFloat(),
        waitInitRemain: mgr.add(WAIT_INIT_REMAIN_OFFSET).readFloat(),
      };
    } catch (e) {
      return null;
    }
  };

  // The number this exists to find: the largest value either countdown has ever held. An `FD4Time`
  // counts down, so its maximum is what the engine armed it to.
  let enginePatience = 0;
  let samples = 0;
  let lastShape = '';

  const sample = (why) => {
    const s = read();
    samples += 1;
    if (s === null) {
      if (samples <= 3) send({ tag: 'sample', why, state: 'manager not readable' });
      return;
    }
    const armed = Math.max(s.joinCheckRemain, s.waitInitRemain);
    if (armed > enginePatience) {
      enginePatience = armed;
      send({
        tag: 'engine-patience',
        why,
        seconds: enginePatience,
        sample: s,
        note: 'the engine armed a join timer to this; CONNECT_DEADLINE_MS is 1.5s',
      });
    }
    // One line per distinct state shape, so a whole search is a handful of lines rather than a
    // stream, and a state that never changes says so by its absence.
    const shape = s.lobbyState + '/' + s.protocolState + '/' + (s.joinHandle !== 0 ? 'handle' : '-');
    if (shape !== lastShape) {
      lastShape = shape;
      send({ tag: 'state', why, sample: s, enginePatience });
    }
  };

  sample('at attach');

  let hooked = 0;
  for (const e of steam.enumerateExports()) {
    if (e.type !== 'function') continue;
    if (!/RequestLobbyList|AddRequestLobbyList|JoinLobby|GetLobbyData/.test(e.name)) continue;
    Interceptor.attach(e.address, {
      onEnter() {
        sample(e.name);
      },
    });
    hooked += 1;
  }

  send({
    tag: 'armed',
    hooked,
    note: 'engine-patience in seconds is the number CONNECT_DEADLINE_MS should have been derived from',
  });
}
