// Issue the Steam lobby query ourselves, shaped the way Seamless shapes its own, and read back
// what comes home.
//
// # Why this exists rather than another observer
//
// Every attempt so far has tried to watch Seamless search. That has now failed with a working
// positive control five times: `lobby_publish`'s detour sits on the body of the function
// `SteamMatchMaking009`'s vtable slot 4 points at, the same interface whose slot 20 catches
// ersc's own `SetLobbyData` writes thirty times in one session, and slot 4 has never once been
// entered. So there is nothing to watch on that path.
//
// A query we send cannot fail to appear. This attaches no detour to Seamless at all: it resolves
// `ISteamMatchmaking` through the ordinary accessor, adds the filters, calls `RequestLobbyList`,
// and polls the call handle through `ISteamUtils` until Steam answers. Every lobby that comes
// back is then read key by key, which is also how the key names Seamless 2.0.x hashes per build
// are recovered -- they are whatever a real advertisement lobby is carrying.
//
// # Where the work runs
//
// On the game's own pad read, `XINPUT1_4.dll!XInputGetState`, about 82 times a second. Steam's
// API is called from a thread the process already owns and already pumps callbacks on, rather
// than from a thread Frida made, and there is no timer anywhere -- `scripts/check-no-timeouts.py`
// bans those here because a timer reports on a clock the game does not share.
//
// # What it never does
//
// It does not join, create, invite, or write lobby data. `RequestLobbyList` and the read-only
// getters are the entire surface. A query narrows our own result set and changes nothing for any
// other player.

'use strict';

// `CS::GetCurrentMapId(BlockId *out)` on 1.17, below the 1.17.0 to 1.17.1 shift boundary at rva
// 0xafefe9, so the same address serves both builds.
//
// Not the 0x5eefb0 that `er_invasion_warp_core::warp::GET_CURRENT_MAP_ID_RVA` carries -- that is a
// 1.16.2 address which the dll translates at runtime, and a frida agent has no translator. Carried
// across with `scripts/map-rvas-1162-to-1170.py` (unique, 44 byte signature) and then read rather
// than trusted: the mapped entry opens `mov [rcx], 0xffffffff`, the sentinel this getter writes
// into its out-parameter before it resolves anything, which is the shape and not a coincidence.
const GET_CURRENT_MAP_ID_RVA = 0x5efe00;

// Our own advertisement key, from `lobby_publish::LOBBY_MAP_KEY`.
const LOBBY_MAP_KEY = 'er_invasion_warp_map';

// `LobbyMatchList_t::k_iCallback` -- `k_iSteamMatchmakingCallbacks` (500) plus 10. The struct is
// one `uint32 m_nLobbiesMatching`.
const LOBBY_MATCH_LIST_CALLBACK = 510;
const LOBBY_MATCH_LIST_SIZE = 4;

// `k_ELobbyComparisonEqual`. The only comparison a map id can use, and the one Seamless attaches
// every string filter with.
const COMPARISON_EQUAL = 0;

// `k_ELobbyDistanceFilterWorldwide`. Steam defaults to a regional filter, which would silently
// hide hosts on another continent and make an empty result unreadable.
const DISTANCE_WORLDWIDE = 3;

// Caps on what one answer may carry back, so a busy result set cannot turn into a message nobody
// can read.
const MAX_LOBBIES_REPORTED = 64;
const MAX_KEYS_PER_LOBBY = 64;
const KEY_BUFFER = 256;
const VALUE_BUFFER = 8192;

const out = {
  ready: false,
  why: null,
  matchmaking: null,
  utils: null,
  block: null,
  queued: 0,
  started: 0,
  finished: 0,
  results: [],
  log: [],
};

function note (kind, line) {
  out.log.push(line);
  if (out.log.length > 400) out.log.shift();
  send({ kind: kind, line: line });
}

// About 28 percent of function entries on this build open with an Arxan healing stub, and a call
// placed on the stub reaches the wrong code.
function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

const steam = Process.findModuleByName('steam_api64.dll');
const game = Process.findModuleByName('eldenring.exe');

function need (name, ret, args) {
  if (steam === null) return null;
  let address;
  try {
    address = steam.getExportByName(name);
  } catch (e) {
    return null;
  }
  if (address === null || address.isNull()) return null;
  return new NativeFunction(address, ret, args);
}

const api = {
  matchmaking: need('SteamAPI_SteamMatchmaking_v009', 'pointer', []),
  utils: need('SteamAPI_SteamUtils_v010', 'pointer', []),
  addString: need('SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter',
    'void', ['pointer', 'pointer', 'pointer', 'int']),
  addNumerical: need('SteamAPI_ISteamMatchmaking_AddRequestLobbyListNumericalFilter',
    'void', ['pointer', 'pointer', 'int', 'int']),
  addResultCount: need('SteamAPI_ISteamMatchmaking_AddRequestLobbyListResultCountFilter',
    'void', ['pointer', 'int']),
  addDistance: need('SteamAPI_ISteamMatchmaking_AddRequestLobbyListDistanceFilter',
    'void', ['pointer', 'int']),
  request: need('SteamAPI_ISteamMatchmaking_RequestLobbyList', 'uint64', ['pointer']),
  lobbyByIndex: need('SteamAPI_ISteamMatchmaking_GetLobbyByIndex', 'uint64', ['pointer', 'int']),
  lobbyDataCount: need('SteamAPI_ISteamMatchmaking_GetLobbyDataCount', 'int',
    ['pointer', 'uint64']),
  lobbyDataByIndex: need('SteamAPI_ISteamMatchmaking_GetLobbyDataByIndex', 'bool',
    ['pointer', 'uint64', 'int', 'pointer', 'int', 'pointer', 'int']),
  lobbyOwner: need('SteamAPI_ISteamMatchmaking_GetLobbyOwner', 'uint64', ['pointer', 'uint64']),
  lobbyMembers: need('SteamAPI_ISteamMatchmaking_GetNumLobbyMembers', 'int',
    ['pointer', 'uint64']),
  completed: need('SteamAPI_ISteamUtils_IsAPICallCompleted', 'bool',
    ['pointer', 'uint64', 'pointer']),
  result: need('SteamAPI_ISteamUtils_GetAPICallResult', 'bool',
    ['pointer', 'uint64', 'pointer', 'int', 'int', 'pointer']),
};

const missing = Object.keys(api).filter(function (k) { return api[k] === null; });
if (steam === null) {
  out.why = 'steam_api64.dll is not loaded in this process';
} else if (missing.length > 0) {
  out.why = 'these exports did not resolve: ' + missing.join(', ');
} else {
  out.ready = true;
}

let matchmaking = null;
let utils = null;

// The interface accessors are resolved on the game thread rather than at load, because a call
// into Steam before the api is initialised returns null and would leave a null cached forever.
function interfaces () {
  if (matchmaking === null || matchmaking.isNull()) {
    matchmaking = api.matchmaking();
    out.matchmaking = matchmaking.isNull() ? null : String(matchmaking);
  }
  if (utils === null || utils.isNull()) {
    utils = api.utils();
    out.utils = utils.isNull() ? null : String(utils);
  }
  return matchmaking !== null && !matchmaking.isNull()
    && utils !== null && !utils.isNull();
}

// The engine's own spelling of the block the player is standing in, for a caller that wants the
// centre of the search without knowing where that is.
const getCurrentMapId = game === null
  ? null
  : new NativeFunction(follow(game.base.add(GET_CURRENT_MAP_ID_RVA)), 'void', ['pointer']);

function currentBlock () {
  if (getCurrentMapId === null) return null;
  const slot = Memory.alloc(4);
  slot.writeU32(0xffffffff);
  try {
    getCurrentMapId(slot);
  } catch (e) {
    return null;
  }
  const raw = slot.readU32();
  if (raw === 0xffffffff) return null;
  return { raw: raw, name: spellBlock(raw) };
}

// `m{area}_{block}_{region}_{index}`, with the index byte decoded out of the engine's binary
// coded decimal packing for the overworld areas that use it. Both sides of an equality filter
// have to spell a block identically or it matches nobody.
function spellBlock (raw) {
  const area = (raw >>> 24) & 0xff;
  const block = (raw >>> 16) & 0xff;
  const region = (raw >>> 8) & 0xff;
  const packed = raw & 0xff;
  const index = (area >= 60 && area <= 61) ? ((packed >> 4) * 10 + (packed & 0x0f)) : packed;
  function two (n) { return (n < 10 ? '0' : '') + n; }
  return 'm' + two(area) + '_' + two(block) + '_' + two(region) + '_' + two(index);
}

const queue = [];
let active = null;

function startJob (job) {
  const spec = job.spec;
  if (spec.distance !== null && spec.distance !== undefined) {
    api.addDistance(matchmaking, spec.distance);
  }
  if (spec.resultCount !== null && spec.resultCount !== undefined) {
    api.addResultCount(matchmaking, spec.resultCount);
  }
  for (const pair of spec.strings || []) {
    api.addString(matchmaking,
      Memory.allocUtf8String(pair[0]), Memory.allocUtf8String(pair[1]),
      pair.length > 2 ? pair[2] : COMPARISON_EQUAL);
  }
  for (const pair of spec.numerics || []) {
    api.addNumerical(matchmaking,
      Memory.allocUtf8String(pair[0]), pair[1],
      pair.length > 2 ? pair[2] : COMPARISON_EQUAL);
  }
  job.call = api.request(matchmaking);
  job.failed = Memory.alloc(1);
  out.started += 1;
  note('query', 'sent "' + job.label + '" call=' + job.call
    + ' filters=' + JSON.stringify(spec.strings || [])
    + ' numerics=' + JSON.stringify(spec.numerics || []));
}

function readLobby (id) {
  const keys = {};
  const count = api.lobbyDataCount(matchmaking, id);
  const keyBuf = Memory.alloc(KEY_BUFFER);
  const valueBuf = Memory.alloc(VALUE_BUFFER);
  const shown = Math.min(count, MAX_KEYS_PER_LOBBY);
  for (let i = 0; i < shown; i += 1) {
    if (!api.lobbyDataByIndex(matchmaking, id, i, keyBuf, KEY_BUFFER, valueBuf, VALUE_BUFFER)) {
      continue;
    }
    keys[keyBuf.readUtf8String()] = valueBuf.readUtf8String();
  }
  return {
    id: '0x' + id.toString(16),
    owner: '0x' + api.lobbyOwner(matchmaking, id).toString(16),
    members: api.lobbyMembers(matchmaking, id),
    keyCount: count,
    keysShown: shown,
    keys: keys,
  };
}

function finishJob (job) {
  const payload = Memory.alloc(LOBBY_MATCH_LIST_SIZE);
  const ok = api.result(utils, job.call, payload, LOBBY_MATCH_LIST_SIZE,
    LOBBY_MATCH_LIST_CALLBACK, job.failed);
  const failed = job.failed.readU8() !== 0;
  const count = ok ? payload.readU32() : 0;
  const lobbies = [];
  if (ok && !failed) {
    const shown = Math.min(count, MAX_LOBBIES_REPORTED);
    for (let i = 0; i < shown; i += 1) {
      const id = api.lobbyByIndex(matchmaking, i);
      if (id.toNumber() === 0) continue;
      try {
        lobbies.push(readLobby(id));
      } catch (e) {
        lobbies.push({ id: '0x' + id.toString(16), error: String(e) });
      }
    }
  }
  const record = {
    label: job.label,
    spec: job.spec,
    ok: ok,
    failed: failed,
    matching: count,
    reported: lobbies.length,
    lobbies: lobbies,
  };
  out.results.push(record);
  out.finished += 1;
  note('answer', '"' + job.label + '" matching=' + count
    + ' read=' + lobbies.length + (failed ? ' (steam reported failure)' : ''));
  send({ kind: 'result', line: '"' + job.label + '" matching=' + count, result: record });
}

let blockSaid = null;

function tick () {
  if (!out.ready) return;
  if (!interfaces()) return;
  const here = currentBlock();
  if (here !== null) {
    out.block = here;
    if (here.name !== blockSaid) {
      blockSaid = here.name;
      note('block', 'standing in ' + here.name);
    }
  }
  if (active !== null) {
    if (!api.completed(utils, active.call, active.failed)) return;
    const job = active;
    active = null;
    try {
      finishJob(job);
    } catch (e) {
      note('error', 'reading the answer for "' + job.label + '" threw: ' + e);
    }
    return;
  }
  if (queue.length === 0) return;
  const job = queue.shift();
  try {
    startJob(job);
    active = job;
  } catch (e) {
    note('error', 'sending "' + job.label + '" threw: ' + e);
  }
}

const xinput = Process.findModuleByName('XINPUT1_4.dll');
if (xinput === null) {
  out.ready = false;
  out.why = 'XINPUT1_4.dll is not loaded, so there is no game tick to run on';
} else {
  Interceptor.attach(follow(xinput.getExportByName('XInputGetState')), { onEnter: tick });
}

rpc.exports = {
  report: function () { return out; },

  // Queue one query. `spec` carries `strings`, `numerics`, `resultCount` and `distance`; every
  // one is optional, and a spec with none of them is the unfiltered control.
  query: function (label, spec) {
    queue.push({ label: label, spec: spec || {}, call: null, failed: null });
    out.queued += 1;
    return { queued: out.queued, pending: queue.length };
  },

  // The same query with our block key appended, which is the one difference this is here to show.
  queryBlock: function (label, spec, blockName) {
    const copy = JSON.parse(JSON.stringify(spec || {}));
    copy.strings = (copy.strings || []).concat([[LOBBY_MAP_KEY, blockName]]);
    queue.push({ label: label, spec: copy, call: null, failed: null });
    out.queued += 1;
    return { queued: out.queued, pending: queue.length, block: blockName };
  },

  // What the last answer came back with, for a caller polling rather than reading messages.
  results: function () { return out.results; },

  idle: function () { return active === null && queue.length === 0; },

  block: function () { return out.block; },

  constants: function () {
    return {
      mapKey: LOBBY_MAP_KEY,
      comparisonEqual: COMPARISON_EQUAL,
      distanceWorldwide: DISTANCE_WORLDWIDE,
    };
  },
};

console.log('lobby-search-proof: ' + (out.ready ? 'ready' : 'inert -- ' + out.why));
