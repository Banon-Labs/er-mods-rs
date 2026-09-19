// Every string filter that goes onto a lobby query, and the request that consumes them.
//
// # The question
//
// `er-effects-rs-0wej`: the nearby sweep counted hosts Seamless's own search can never return,
// because Seamless filters its invasion search on `lobby_key` -- a SHA-256 over the loaded param
// tables, compared for equality -- and the sweep did not. A fix is in the tree
// (`lobby_preflight.rs` `send_query` pushes that filter), and it is SILENTLY OPTIONAL: the key
// comes from `lobby_publish::seamless_match_key`, which answers nothing until Seamless has handed
// the key to Steam, and a query missing the filter looks exactly like the unfixed one. No run has
// ever distinguished the two -- no sweep has yet found a host, so the reachable/unreachable lines
// that would have shown it have never printed.
//
// So: watch the wire. Every `(key, value, comparison)` written onto the matchmaking interface,
// grouped by the `RequestLobbyList` that consumes them. A group carrying
// `er_invasion_warp_lobby_key` is a query confined to this player's pool; a group carrying
// `er_invasion_warp_map` without it is the defect, live.
//
// # Why this is not a DLL change
//
// It answers whether a value is present at a moment, which is a measurement. Adding a log line to
// the DLL to find that out costs a build, a teardown and a relaunch, and puts the answer behind a
// log line someone then has to read -- the exact shape AGENTS.md names as reaching for the wrong
// instrument.
//
// # What to run
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/lobby-query-filters.js
//
// Then use an invasion finger and pick a row. Lines are emitted per request, not per filter.
const EXPORTS = {
  addString: 'SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter',
  request: 'SteamAPI_ISteamMatchmaking_RequestLobbyList',
};

// The keys this mod and Seamless publish, named so an unknown key is obvious at a glance.
// `lobby_key` is Seamless's own key name and the one this repo publishes under
// (`lobby_publish::LOBBY_KEY_NAME`), not an `er_invasion_warp_`-prefixed one. The first version of
// this file guessed `er_invasion_warp_lobby_key`, so its verdict called a correctly pooled query
// the unfixed one on run br-20260918-174500-8024 -- a wrong answer with the right data beside it.
const POOL_KEY = 'lobby_key';
const PLACE_KEY = 'er_invasion_warp_map';
const KNOWN = {
  er_invasion_warp_map: 'our location filter -- hosts running this DLL only',
  lobby_key: "the param-pool filter -- Seamless compares this for equality, so a host outside it is unreachable",
  er_invasion_warp_advertisement: 'our advertisement marker',
};

const steam = Process.findModuleByName('steam_api64.dll');
if (steam === null) {
  send({ tag: 'error', note: 'steam_api64.dll is not loaded in this process; nothing to watch.' });
} else {
  const addString = steam.findExportByName(EXPORTS.addString);
  const request = steam.findExportByName(EXPORTS.request);

  // Filters accumulate on the interface and are consumed by the next request, so they are
  // collected per interface pointer rather than globally: two threads building queries at once
  // would otherwise blame one for the other's keys.
  const pending = {};
  let queries = 0;

  const readCString = function (pointer) {
    try {
      return pointer.readCString();
    } catch (e) {
      return '<unreadable>';
    }
  };

  if (addString !== null) {
    Interceptor.attach(addString, {
      onEnter: function (args) {
        const iface = args[0].toString();
        const key = readCString(args[1]);
        const value = readCString(args[2]);
        const comparison = args[3].toInt32();
        if (pending[iface] === undefined) pending[iface] = [];
        pending[iface].push({ key: key, value: value, comparison: comparison });
      },
    });
  }

  if (request !== null) {
    Interceptor.attach(request, {
      onEnter: function (args) {
        const iface = args[0].toString();
        const filters = pending[iface] === undefined ? [] : pending[iface];
        pending[iface] = [];
        queries++;
        const keys = filters.map(function (f) { return f.key; });
        const pooled = keys.indexOf(POOL_KEY) !== -1;
        const located = keys.indexOf(PLACE_KEY) !== -1;
        let verdict;
        if (pooled && located) {
          verdict = 'confined to this pool AND to one place -- the fixed sweep';
        } else if (located) {
          verdict = 'ONE PLACE, ANY POOL -- er-effects-rs-0wej live: this counts hosts the search that follows can never return';
        } else if (pooled) {
          verdict = 'this pool, anywhere';
        } else if (filters.length === 0) {
          verdict = "no string filter at all -- Seamless's own query, or ours handed back unnarrowed";
        } else {
          verdict = 'neither of our keys';
        }
        send({
          tag: 'lobby-query',
          query: queries,
          verdict: verdict,
          filters: filters.map(function (f) {
            const note = KNOWN[f.key] === undefined ? '' : ' (' + KNOWN[f.key] + ')';
            return f.key + '=' + f.value + ' cmp=' + f.comparison + note;
          }),
        });
      },
    });
  }

  send({
    tag: 'armed',
    addString: addString === null ? 'MISSING' : addString.toString(),
    request: request === null ? 'MISSING' : request.toString(),
    note: 'Use an invasion finger and pick a row. One line per query, listing every filter that went out with it.',
  });
}
