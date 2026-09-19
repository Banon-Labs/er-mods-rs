// Watch an invasion happen: the query going out, the session walking its states, the lobby joining.
//
// # What this is the oracle for
//
// A redirect that sets Seamless's session to 0x0e has changed a field, and a field we wrote is not
// a search. The readings that cannot be manufactured by our own write are the ones Steam and the
// game make: a `RequestLobbyList` leaving the process, and `CSSessionManager`'s lobby state
// reaching `Client`, which means the join succeeded and the player is in somebody else's world.
//
// # Why there is no timer
//
// State is sampled on the game's own pad read, `XInputGetState`, which runs about 82 times a
// second and is the same tick `pad-frames.js` counts holds in. A timer would report on a clock the
// game does not share, and `scripts/check-no-timeouts.py` bans those here. Only changes are sent,
// so an idle session is silent rather than 82 lines a second.

'use strict';

const SESSION_STATE = 0x150;
const OWNER_SESSION = 0x58;
const OSM_FROM_R14 = 0x120;
const OPEN_CONVERSATION_CHOICES_MENU = ptr('0x140ea0360');
const SESSION_MANAGER_GLOBAL_RVA = 0x3d7a4d0;
const LOBBY_STATE_OFFSET = 0x0c;
const LOBBY_STATE_NAMES = ['None', 'Creating', 'CreateFailed', 'Host',
                           'Joining', 'JoinFailed', 'Client', 'Closing'];
const STATE_NAMES = {
  1: 'idle', 2: 'state2', 4: 'state4', 6: 'state6', 7: 'state7',
  0x0e: 'searching', 0x0f: 'state0f', 0x12: 'connecting', 0x16: 'in world',
  0x23: 'cancelling', 0x24: 'state24',
};

const out = {
  session: null, owner: null, queries: 0, joins: 0, states: [], lobby: [], log: [],
};

function note (kind, line) {
  out.log.push(line);
  if (out.log.length > 200) out.log.shift();
  send({ kind: kind, line: line });
}

// About 28 percent of function entries on this build open with an Arxan healing stub, and a hook on
// the stub never fires.
function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

const game = Process.findModuleByName('eldenring.exe');
const sessionManagerGlobal = game === null ? null : game.base.add(SESSION_MANAGER_GLOBAL_RVA);

function lobbyState () {
  if (sessionManagerGlobal === null) return null;
  try {
    const manager = sessionManagerGlobal.readPointer();
    if (manager.isNull()) return null;
    return manager.add(LOBBY_STATE_OFFSET).readU32();
  } catch (e) {
    return null;
  }
}

function sessionState () {
  if (out.session === null) return null;
  try { return ptr(out.session).add(SESSION_STATE).readU32(); } catch (e) { return null; }
}

// Take the object from Seamless's own hand-over, so a menu that opens while this is resident
// re-identifies the session rather than leaving a stale pointer in place.
Interceptor.attach(follow(OPEN_CONVERSATION_CHOICES_MENU), {
  onEnter () {
    let osm;
    try { osm = this.context.r14.sub(OSM_FROM_R14); } catch (e) { return; }
    let session;
    try { session = osm.add(OWNER_SESSION).readPointer(); } catch (e) { return; }
    if (session.isNull()) return;
    out.owner = String(osm);
    out.session = String(session);
    note('owner', 'menu open: osm=' + osm + ' session=' + session);
  },
});

let lastState = null;
let lastLobby = null;
const xinput = Process.findModuleByName('XINPUT1_4.dll');
if (xinput !== null) {
  Interceptor.attach(follow(xinput.getExportByName('XInputGetState')), {
    onEnter () {
      const st = sessionState();
      if (st !== null && st !== lastState) {
        lastState = st;
        const line = 'session state 0x' + st.toString(16) + ' ' + (STATE_NAMES[st] || 'unknown');
        out.states.push(line);
        note('state', line);
      }
      const lobby = lobbyState();
      if (lobby !== null && lobby !== lastLobby) {
        lastLobby = lobby;
        const line = 'lobby state ' + lobby + ' ' + (LOBBY_STATE_NAMES[lobby] || '?');
        out.lobby.push(line);
        note('lobby', line);
      }
    },
  });
}

// The query oracle, plus the join. These are the two things our own write cannot fake.
const steam = Process.findModuleByName('steam_api64.dll');
for (const exp of steam ? steam.enumerateExports() : []) {
  if (exp.type !== 'function') continue;
  const isQuery = exp.name.indexOf('_RequestLobbyList') >= 0;
  const isJoin = exp.name.indexOf('_JoinLobby') >= 0;
  if (!isQuery && !isJoin) continue;
  try {
    Interceptor.attach(exp.address, {
      onEnter () {
        if (isQuery) { out.queries += 1; note('query', exp.name + ' #' + out.queries); }
        else { out.joins += 1; note('join', exp.name + ' #' + out.joins); }
      },
    });
  } catch (e) {
    note('hook', 'could not hook ' + exp.name + ': ' + e);
  }
}

rpc.exports = {
  report: function () { return out; },
  // Point it at a session directly, for a watch that starts before any menu has opened.
  watch: function (sessionHex) {
    out.session = String(ptr(sessionHex));
    return { ok: true, session: out.session, state: sessionState() };
  },
};
console.log('invasion-observer: watching');
