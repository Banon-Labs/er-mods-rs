// How long does the nearby sweep actually take, and how many Steam round-trips does it cost?
//
// `er-invasion-warp.log` carries no timestamps, so the player's report that the near-to-far
// transition is "delayed by some time" cannot be measured from the log that records the sweep.
// This agent puts a clock on the only thing that can be slow: the queries themselves.
//
// `lobby_preflight::sweep_tick` sends one `RequestLobbyList` per place, filtered to one block by
// `AddRequestLobbyListStringFilter(er_invasion_warp_map, <block>, EQUAL)`, and does not send the
// next until the previous has answered. At radius three that is 49 serial round-trips before the
// near half of `Both near and far` can conclude. Hooking both exports shows each filter as it is
// staged, each request as it goes out, and the wall time between consecutive requests -- which is
// the per-round-trip latency the whole delay is made of.
//
// It also distinguishes the two query shapes without guessing: the existence pre-flight sends
// `er_invasion_warp_map != ""` (comparison 3), the sweep sends `er_invasion_warp_map == <block>`
// (comparison 0). A run where the pre-flight answers zero and the sweep still fires 49 times is
// the defect the fix addresses, and this agent is what shows it.
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py \
//       --agent scripts/frida/lobby-query-cadence.js

'use strict';

const STEAM_MODULE = 'steam_api64.dll';
const ADD_STRING_FILTER = 'SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter';
const REQUEST_LOBBY_LIST = 'SteamAPI_ISteamMatchmaking_RequestLobbyList';
const MAP_KEY = 'er_invasion_warp_map';

const steam = Process.findModuleByName(STEAM_MODULE);
if (steam === null) {
  send({ tag: 'lobby-cadence', fatal: STEAM_MODULE + ' is not loaded in this process' });
}

function cstr(p) {
  try {
    return p.isNull() ? null : p.readCString();
  } catch (e) {
    return null;
  }
}

function resolve(name) {
  if (steam === null) return null;
  const addr = steam.findExportByName(name);
  if (addr === null) send({ tag: 'lobby-cadence', missing: name });
  return addr;
}

if (steam !== null) {
  // Filters staged since the last request went out. Steam consumes them on `RequestLobbyList`, so
  // this list is exactly the shape of the query that is about to leave.
  let staged = [];
  let requests = 0;
  let lastRequestMs = 0;
  // Only the sweep's own queries are counted as a series: an unrelated Seamless query in between
  // would otherwise make the gap look like our latency.
  let mapQueries = 0;
  let seriesStartMs = 0;

  const addFilter = resolve(ADD_STRING_FILTER);
  if (addFilter !== null) {
    Interceptor.attach(addFilter, {
      onEnter: function (args) {
        staged.push({
          key: cstr(args[1]),
          value: cstr(args[2]),
          comparison: args[3].toInt32(),
        });
      },
    });
  }

  const request = resolve(REQUEST_LOBBY_LIST);
  if (request !== null) {
    Interceptor.attach(request, {
      onEnter: function () {
        const now = Date.now();
        requests += 1;
        const filters = staged;
        staged = [];
        const ours = filters.filter(function (f) {
          return f.key === MAP_KEY;
        });
        // Comparison 3 is `k_ELobbyComparisonNotEqual` -- the existence pre-flight. Comparison 0
        // is equality -- one place of the sweep's ring.
        const existence = ours.some(function (f) {
          return f.comparison !== 0;
        });
        const place = ours.find(function (f) {
          return f.comparison === 0;
        });
        if (place !== undefined) {
          if (mapQueries === 0) seriesStartMs = now;
          mapQueries += 1;
        }
        send({
          tag: 'lobby-cadence',
          request: requests,
          since_previous_ms: lastRequestMs === 0 ? null : now - lastRequestMs,
          kind: existence ? 'existence-preflight' : place !== undefined ? 'sweep-place' : 'other',
          place: place === undefined ? null : place.value,
          sweep_place_number: place === undefined ? null : mapQueries,
          sweep_elapsed_ms: place === undefined ? null : now - seriesStartMs,
          filters: filters,
        });
        lastRequestMs = now;
      },
    });
  }

  send({
    tag: 'lobby-cadence',
    armed: [ADD_STRING_FILTER, REQUEST_LOBBY_LIST],
    module: steam.base.toString(),
  });
}
