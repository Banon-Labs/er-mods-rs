// Fetch the lobby, read its keys, find its owner, and see whether Seamless has anywhere to put it.
//
// # Why this exists before any Rust
//
// The fetch half is already proven. `scripts/er-lobby-search-proof.py` runs the whole chain --
// resolve `ISteamMatchmaking` v009, add the filters, `RequestLobbyList`, poll
// `IsAPICallCompleted`, take the payload with `GetAPICallResult`, then `GetLobbyByIndex` and
// `GetLobbyData` -- and on run `br-20260918-002055-bcc4` it printed a real host's every key,
// including `er_invasion_warp_map = m61_48_45_00`. So a Rust port of that is transcription.
//
// The third step is the one nothing has proven: handing the lobby to Seamless. This module has
// measured what Seamless does NOT do -- across 46 search cycles it called `GetLobbyByIndex` 0
// times, `GetLobbyData` 0, `JoinLobby` 0 -- so there is no observed call site to copy, and no
// field is known to be where a target goes. Writing Rust against a guess about that field is how
// a manual write ends up in the product pretending to be an integration.
//
// # What this does
//
// Read-only, in three stages, each reporting before the next.
//
//   1. run the query and fetch: count, then every lobby's id, keys and owner `CSteamID`.
//   2. read the Seamless session object's fields around its state word, so the shape is on record
//      BEFORE a host is in hand. Nothing is written.
//   3. report which of those fields could hold a `CSteamID` -- a qword matching the owner id the
//      query just returned is the field a target would live in, and finding one is the evidence
//      that has been missing. Finding none is equally informative and is reported as such.
//
// Nothing is written to game memory and no lobby is joined. `rpc.exports.look()` runs the whole
// thing on demand so it can be re-run against a live search without reattaching.
'use strict';

const MATCHMAKING_ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const UTILS_ACCESSOR = 'SteamAPI_SteamUtils_v010';

// Flat exports for everything the read chain needs. `lobby_preflight.rs` already uses these exact
// names for its own query, so the agent and the mod are calling identical entry points.
const FLAT = {
  addString: 'SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter',
  addDistance: 'SteamAPI_ISteamMatchmaking_AddRequestLobbyListDistanceFilter',
  addCount: 'SteamAPI_ISteamMatchmaking_AddRequestLobbyListResultCountFilter',
  request: 'SteamAPI_ISteamMatchmaking_RequestLobbyList',
  byIndex: 'SteamAPI_ISteamMatchmaking_GetLobbyByIndex',
  getData: 'SteamAPI_ISteamMatchmaking_GetLobbyData',
  getOwner: 'SteamAPI_ISteamMatchmaking_GetLobbyOwner',
  completed: 'SteamAPI_ISteamUtils_IsAPICallCompleted',
  result: 'SteamAPI_ISteamUtils_GetAPICallResult',
  join: 'SteamAPI_ISteamMatchmaking_JoinLobby',
  leave: 'SteamAPI_ISteamMatchmaking_LeaveLobby',
  numMembers: 'SteamAPI_ISteamMatchmaking_GetNumLobbyMembers',
  memberByIndex: 'SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex',
};

// `LobbyEnter_t::k_iCallback` is 500 + 4. The struct is `uint64 m_ulSteamIDLobby`, `uint32
// m_rgfChatPermissions`, `bool m_bLocked`, then `uint32 m_EChatRoomEnterResponse` at +16 after
// padding, so 24 bytes with 8-byte alignment.
const LOBBY_ENTER_CALLBACK = 504;
const LOBBY_ENTER_SIZE = 24;
const LOBBY_ENTER_RESPONSE_OFFSET = 16;

// `k_EChatRoomEnterResponseSuccess`. Anything else names why Steam refused the join.
const ENTER_SUCCESS = 1;

// `LobbyMatchList_t::k_iCallback` is 500 + 10, and the struct is one uint32.
const LOBBY_MATCH_LIST_CALLBACK = 510;
const LOBBY_MATCH_LIST_SIZE = 4;
const DISTANCE_WORLDWIDE = 3;
const RESULT_COUNT = 50;

// The keys worth reading off a host, plus Seamless's own pool key.
const KEYS = ['er_invasion_warp_map', 'er_invasion_warp_effects', 'lobby_key'];

// Seamless session layout, as `local_invasion_filter` pins it for v2.0.1.
const SESSION_STATE = 0x150;
const SESSION_SCAN_FROM = 0x100;
const SESSION_SCAN_TO = 0x300;

const steam = Process.findModuleByName('steam_api64.dll');
const ersc = Process.findModuleByName('ersc.dll');

function exportsOf(mod) {
  const table = new Map();
  for (const e of mod.enumerateExports()) table.set(e.name, e.address);
  return table;
}

if (steam === null) {
  send({ tag: 'fatal', reason: 'steam_api64.dll not loaded' });
} else {
  const table = exportsOf(steam);
  const missing = [];
  for (const name of Object.values(FLAT).concat([MATCHMAKING_ACCESSOR, UTILS_ACCESSOR])) {
    if (!table.has(name)) missing.push(name);
  }
  if (missing.length !== 0) {
    send({ tag: 'fatal', reason: 'exports missing', missing: missing });
  } else {
    const fn = {
      matchmaking: new NativeFunction(table.get(MATCHMAKING_ACCESSOR), 'pointer', []),
      utils: new NativeFunction(table.get(UTILS_ACCESSOR), 'pointer', []),
      addString: new NativeFunction(table.get(FLAT.addString), 'void', ['pointer', 'pointer', 'pointer', 'int']),
      addDistance: new NativeFunction(table.get(FLAT.addDistance), 'void', ['pointer', 'int']),
      addCount: new NativeFunction(table.get(FLAT.addCount), 'void', ['pointer', 'int']),
      request: new NativeFunction(table.get(FLAT.request), 'uint64', ['pointer']),
      byIndex: new NativeFunction(table.get(FLAT.byIndex), 'uint64', ['pointer', 'int']),
      getData: new NativeFunction(table.get(FLAT.getData), 'pointer', ['pointer', 'uint64', 'pointer']),
      getOwner: new NativeFunction(table.get(FLAT.getOwner), 'uint64', ['pointer', 'uint64']),
      completed: new NativeFunction(table.get(FLAT.completed), 'bool', ['pointer', 'uint64', 'pointer']),
      result: new NativeFunction(table.get(FLAT.result), 'bool', ['pointer', 'uint64', 'pointer', 'int', 'int', 'pointer']),
      join: new NativeFunction(table.get(FLAT.join), 'uint64', ['pointer', 'uint64']),
      leave: new NativeFunction(table.get(FLAT.leave), 'void', ['pointer', 'uint64']),
      numMembers: new NativeFunction(table.get(FLAT.numMembers), 'int', ['pointer', 'uint64']),
      memberByIndex: new NativeFunction(table.get(FLAT.memberByIndex), 'uint64', ['pointer', 'uint64', 'int']),
    };

    function cstr(text) {
      return Memory.allocUtf8String(text);
    }

    // Stage 1. Ask, wait for the answer, and fetch what it names.
    function fetch(filters) {
      const mm = fn.matchmaking();
      const ut = fn.utils();
      if (mm.isNull() || ut.isNull()) return { error: 'interface null' };

      fn.addDistance(mm, DISTANCE_WORLDWIDE);
      fn.addCount(mm, RESULT_COUNT);
      for (const [key, value] of filters) {
        fn.addString(mm, cstr(key), cstr(value), 0);
      }
      const call = fn.request(mm);

      // Poll rather than register, which is what `lobby_preflight::poll_query` does and is why it
      // works while nothing registers a call result in this process.
      const failed = Memory.alloc(1);
      const payload = Memory.alloc(LOBBY_MATCH_LIST_SIZE);
      let waited = 0;
      while (!fn.completed(ut, call, failed) && waited < 8000) {
        Thread.sleep(0.05);
        waited += 50;
      }
      if (!fn.completed(ut, call, failed)) return { error: 'query never completed', waited_ms: waited };
      if (!fn.result(ut, call, payload, LOBBY_MATCH_LIST_SIZE, LOBBY_MATCH_LIST_CALLBACK, failed)) {
        return { error: 'GetAPICallResult refused' };
      }
      const matching = payload.readU32();

      const lobbies = [];
      for (let i = 0; i < matching; i += 1) {
        const id = fn.byIndex(mm, i);
        if (id.toString() === '0') continue;
        const entry = { id: id.toString(), owner: fn.getOwner(mm, id).toString(), keys: {} };
        for (const key of KEYS) {
          const raw = fn.getData(mm, id, cstr(key));
          entry.keys[key] = raw.isNull() ? null : raw.readUtf8String();
        }
        lobbies.push(entry);
      }
      return { matching: matching, waited_ms: waited, lobbies: lobbies };
    }

    // Stage 2/3. Read the session's neighbourhood and say whether any qword in it already holds a
    // CSteamID the query just named. A match is where a target lives; no match is the evidence
    // that Seamless keeps its target somewhere this window does not cover.
    function sessionFields(sessionHex, owners) {
      if (ersc === null) return { error: 'ersc.dll not loaded' };
      const session = ptr(sessionHex);
      let state;
      try {
        state = session.add(SESSION_STATE).readU32();
      } catch (error) {
        return { error: 'session unreadable at ' + sessionHex };
      }
      const wanted = new Set(owners);
      const hits = [];
      const nonzero = [];
      for (let offset = SESSION_SCAN_FROM; offset < SESSION_SCAN_TO; offset += 8) {
        let value;
        try {
          value = session.add(offset).readU64().toString();
        } catch (error) {
          continue;
        }
        if (value === '0') continue;
        nonzero.push({ offset: '0x' + offset.toString(16), value: value });
        if (wanted.has(value)) hits.push({ offset: '0x' + offset.toString(16), value: value });
      }
      return { state: state, holds_a_returned_steamid: hits, nonzero_qwords: nonzero.length };
    }

    // Join the advertisement lobby, which is the one thing Seamless never does -- measured across
    // 46 search cycles with `JoinLobby` at zero. It is also the only way to learn who the host is:
    // `GetLobbyOwner` returned `0` for this same lobby while we were not a member, because Steam
    // does not name an owner to a stranger.
    //
    // This is a state change, and a reversible one: `LeaveLobby` undoes it and is called here
    // unless the caller asks to stay.
    function joinLobby(lobbyHex, stay) {
      const mm = fn.matchmaking();
      const ut = fn.utils();
      if (mm.isNull() || ut.isNull()) return { error: 'interface null' };
      const lobby = uint64(lobbyHex);

      const call = fn.join(mm, lobby);
      const failed = Memory.alloc(1);
      const payload = Memory.alloc(LOBBY_ENTER_SIZE);
      let waited = 0;
      while (!fn.completed(ut, call, failed) && waited < 15000) {
        Thread.sleep(0.05);
        waited += 50;
      }
      if (!fn.completed(ut, call, failed)) {
        return { error: 'join never completed', waited_ms: waited };
      }
      if (!fn.result(ut, call, payload, LOBBY_ENTER_SIZE, LOBBY_ENTER_CALLBACK, failed)) {
        return { error: 'GetAPICallResult refused the LobbyEnter_t', waited_ms: waited };
      }
      const response = payload.add(LOBBY_ENTER_RESPONSE_OFFSET).readU32();
      const entered = response === ENTER_SUCCESS;

      const answer = {
        lobby: lobby.toString(),
        waited_ms: waited,
        enter_response: response,
        entered: entered,
        owner: null,
        members: [],
      };
      if (entered) {
        answer.owner = fn.getOwner(mm, lobby).toString();
        const total = fn.numMembers(mm, lobby);
        for (let i = 0; i < total; i += 1) {
          answer.members.push(fn.memberByIndex(mm, lobby, i).toString());
        }
        // Re-read the keys now that we are inside. A lobby shows a member more than it shows a
        // stranger, so a key that was empty from outside may not be.
        answer.keys = {};
        for (const key of KEYS) {
          const raw = fn.getData(mm, lobby, cstr(key));
          answer.keys[key] = raw.isNull() ? null : raw.readUtf8String();
        }
      }
      if (!stay) {
        fn.leave(mm, lobby);
        answer.left = true;
      }
      return answer;
    }

    rpc.exports = {
      // Get out of a lobby this agent joined.
      //
      // Needed because a bare `JoinLobby` on a Seamless advertisement lobby enters as a CO-OP
      // guest, not as an invader -- observed by the player on run `br-20260918-003310-606c`,
      // live, the moment the join landed. So a join with no further signal is a wrong and
      // intrusive state to leave anyone in, and leaving has to be one call away.
      leave: function (lobbyHex) {
        const mm = fn.matchmaking();
        if (mm.isNull()) return { error: 'interface null' };
        fn.leave(mm, uint64(lobbyHex));
        send({ tag: 'left', lobby: lobbyHex });
        return { left: lobbyHex };
      },

      join: function (lobbyHex, stay) {
        const answer = joinLobby(lobbyHex, stay === true);
        send({ tag: 'join', answer: answer });
        return answer;
      },

      look: function (sessionHex, filters) {
        const answer = fetch(filters === undefined ? [] : filters);
        send({ tag: 'fetch', answer: answer });
        // `null` as well as `undefined`: a Python caller passing `None` arrives as null, and
        // `ptr(null)` throws "expected a pointer" from inside the scan rather than skipping it.
        if (answer.lobbies === undefined || sessionHex === undefined || sessionHex === null) {
          return answer;
        }
        const owners = answer.lobbies.map(function (l) { return l.owner; })
          .concat(answer.lobbies.map(function (l) { return l.id; }));
        const fields = sessionFields(sessionHex, owners);
        send({ tag: 'session', fields: fields });
        return { fetch: answer, session: fields };
      },
    };

    send({
      tag: 'ready',
      steam: steam.base.toString(),
      ersc: ersc === null ? null : ersc.base.toString(),
    });
  }
}
