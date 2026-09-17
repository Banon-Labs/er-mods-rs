// Is this host's own advertisement in the pool a vanilla Seamless invader would query?
//
// The host side is measured healthy: `lobby-publish` read back `er_invasion_warp_map` and
// `er_invasion_warp_effects` on lobby `0x1860000343828c4`, session flags `0x100011`. And an
// invasion attempt against it produced no inbound Steam traffic at all --
// `SteamNetworkingMessages002_ReceiveMessagesOnChannel` polled at ~21/s and returned zero on every
// one of 1052 sampled calls, while `AcceptSessionWithUser`, `SendMessageToUser`, `ConnectP2P` and
// `AcceptConnection` never fired.
//
// Those two facts together leave one question: can anybody SEE this world? A host that advertises
// into a pool nobody queries looks exactly like a host nobody can reach, and the difference is one
// lobby query.
//
// # The call path, and the wait that is not optional
//
// Recorded 2026-09-16 (bd fifty-seamless-hosts-are-advertising-and-share-our-lobby-key-2026-09-16).
// `lsteamclient.dll` exports none of the flat names but `steam_api64.dll` exports all of them, and
// calling them ourselves works even though Seamless does not use them.
//
// `GetLobbyByIndex` returns 0 for every index if it is called straight after `RequestLobbyList`,
// which reads exactly like an empty pool and cost one wrong "nobody is advertising" already. The
// result has to be waited for: poll `IsAPICallCompleted` on the returned `SteamAPICall_t`, then
// `GetAPICallResult` into a four-byte buffer with `iCallbackExpected = 510` (`LobbyMatchList_t`,
// `k_iSteamMatchmakingCallbacks` 500 + 10). That dword is `m_nLobbiesMatching`.
//
// The positive control goes first: `GetPersonaName` through `steam_api64.dll` returns this user's
// own name, which proves the flat call path before any negative result is believed.
//
// Read-only: queries Steam and reads lobby metadata. Nothing is hooked, nothing is written, and no
// lobby is joined.

'use strict';

// This host's own advertisement, from the run's `lobby-publish` line. Reported separately from the
// pool so "the pool is empty" and "the pool does not contain us" stay distinguishable.
const OUR_LOBBY = '0x1860000343828c4';

const LOBBY_MATCH_LIST_CALLBACK = 510;
const POLL_LIMIT = 200;

function exportsOf(moduleName) {
  const table = new Map();
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() !== moduleName) continue;
    for (const e of m.enumerateExports()) table.set(e.name, e.address);
    return table;
  }
  return table;
}

const api = exportsOf('steam_api64.dll');

function fn(name, ret, args) {
  const address = api.get(name);
  if (address === undefined) {
    send({ tag: 'missing-export', name });
    return null;
  }
  return new NativeFunction(address, ret, args);
}

const SteamFriends = fn('SteamAPI_SteamFriends_v017', 'pointer', []);
const GetPersonaName = fn('SteamAPI_ISteamFriends_GetPersonaName', 'pointer', ['pointer']);
const SteamMatchmaking = fn('SteamAPI_SteamMatchmaking_v009', 'pointer', []);
const SteamUtils = fn('SteamAPI_SteamUtils_v010', 'pointer', []);
const RequestLobbyList = fn('SteamAPI_ISteamMatchmaking_RequestLobbyList', 'uint64', ['pointer']);
const GetLobbyByIndex = fn('SteamAPI_ISteamMatchmaking_GetLobbyByIndex', 'uint64', ['pointer', 'int']);
const GetLobbyDataCount = fn('SteamAPI_ISteamMatchmaking_GetLobbyDataCount', 'int', ['pointer', 'uint64']);
const GetLobbyData = fn('SteamAPI_ISteamMatchmaking_GetLobbyData', 'pointer', ['pointer', 'uint64', 'pointer']);
const IsAPICallCompleted = fn('SteamAPI_ISteamUtils_IsAPICallCompleted', 'bool', ['pointer', 'uint64', 'pointer']);
const GetAPICallResult = fn('SteamAPI_ISteamUtils_GetAPICallResult', 'bool', [
  'pointer', 'uint64', 'pointer', 'int', 'int', 'pointer',
]);

if (SteamFriends === null || SteamMatchmaking === null || SteamUtils === null) {
  send({ tag: 'fatal', reason: 'an accessor is missing from steam_api64.dll' });
} else {
  // Positive control first. A negative pool result is not believable until this returns a name.
  const friends = SteamFriends();
  const persona = friends.isNull() ? null : GetPersonaName(friends).readUtf8String();
  send({ tag: 'control', persona });

  const matchmaking = SteamMatchmaking();
  const utils = SteamUtils();
  if (matchmaking.isNull() || utils.isNull() || persona === null) {
    send({ tag: 'fatal', reason: 'steam interfaces are not up yet' });
  } else {
    // No filters at all: every Seamless advertisement, not just ones matching our own keys. A
    // filtered query cannot tell "we are absent from the pool" from "our filter excluded us".
    const call = RequestLobbyList(matchmaking);
    send({ tag: 'query', call: String(call) });

    const failed = Memory.alloc(1);
    const result = Memory.alloc(4);
    let completed = false;
    for (let i = 0; i < POLL_LIMIT; i += 1) {
      if (IsAPICallCompleted(utils, call, failed)) {
        completed = true;
        break;
      }
      Thread.sleep(0.05);
    }
    if (!completed) {
      send({ tag: 'timeout', polls: POLL_LIMIT });
    } else {
      GetAPICallResult(utils, call, result, 4, LOBBY_MATCH_LIST_CALLBACK, failed);
      const matching = result.readU32();
      send({ tag: 'matching', matching, failed: failed.readU8() !== 0 });

      const ourKey = Memory.allocUtf8String('er_invasion_warp_map');
      const lobbies = [];
      let sawOurs = false;
      for (let i = 0; i < matching; i += 1) {
        const lobby = GetLobbyByIndex(matchmaking, i);
        const id = '0x' + lobby.toString(16);
        const map = GetLobbyData(matchmaking, lobby, ourKey).readUtf8String();
        if (id === OUR_LOBBY) sawOurs = true;
        lobbies.push({ id, keys: GetLobbyDataCount(matchmaking, lobby), map });
      }
      send({ tag: 'pool', ours: OUR_LOBBY, sawOurs, lobbies });
    }
  }
}
