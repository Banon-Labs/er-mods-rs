// What does Seamless actually ask Steam for when it searches for an invasion target?
//
// # The question
//
// The player joined a host's lobby by hand and arrived as a co-op guest, not an invader. That
// rules out "the join is the missing step": a bare `JoinLobby` is not an invasion, so whatever
// marks a connection hostile happens on Seamless's side, before or around its own join.
//
// Seamless never reaches a join at all. Measured on `br-20260918-002055-bcc4`: 46 search cycles,
// `GetLobbyByIndex` 0, `GetLobbyData` 0, `JoinLobby` 0. A search that fetches nothing matched
// nothing, and a query that matches nothing is a query whose filters exclude every lobby on the
// list. `lobby_key` is the filter that can do that by construction -- it is a fingerprint of the
// loaded param tables, compared for equality, so two players with different mod sets are in
// disjoint pools and each is invisible to the other's search.
//
// So: record the filter set, verbatim, at the moment Seamless sends it, and read the host's
// published lobby data in the same breath. Either the keys agree and the exclusion is somewhere
// else, or they disagree and the whole failure is one string.
//
// # What this records
//
//   * every `AddRequestLobbyList*Filter` call, accumulated per thread;
//   * the `RequestLobbyList` that consumes them, tagged with the calling module, so this mod's
//     sweep and ersc's search are told apart rather than averaged;
//   * `GetLobbyByIndex` / `GetLobbyData` / `JoinLobby`, also caller-tagged, so "ersc matched and
//     then declined to join" and "ersc matched nothing" stop looking alike.
//
// The rpc export `lobbyData(lobbyHex)` enumerates a lobby's published keys with
// `GetLobbyDataCount` / `GetLobbyDataByIndex`, which needs no membership.
//
// Hooks are entry/exit observers over the interface vtable. `lobby_publish.rs` holds its own
// MinHook detour on slot 4; an observer on a live trampoline sees the call without redirecting it.
// Read-only: nothing is written and no lobby is joined.
'use strict';

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';

const SLOT = {
  requestLobbyList: 4,
  stringFilter: 5,
  numericalFilter: 6,
  nearValueFilter: 7,
  slotsAvailableFilter: 8,
  distanceFilter: 9,
  resultCountFilter: 10,
  getLobbyByIndex: 12,
  joinLobby: 14,
  getLobbyData: 19,
};

const COMPARISON = {
  '-2': 'EqualToOrLessThan',
  '-1': 'LessThan',
  '0': 'Equal',
  '1': 'GreaterThan',
  '2': 'EqualToOrGreaterThan',
  '3': 'NotEqual',
};

const counts = {
  request: 0,
  filters: 0,
  byIndex: 0,
  lobbyData: 0,
  join: 0,
};

function callerOf(context) {
  try {
    const home = Process.findModuleByAddress(context.returnAddress);
    return home === null ? 'unknown' : home.name;
  } catch (error) {
    return 'unreadable';
  }
}

function str(pointer) {
  try {
    return pointer.isNull() ? null : pointer.readUtf8String();
  } catch (error) {
    return '<unreadable>';
  }
}

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  send({ tag: 'fatal', reason: STEAM + ' not loaded' });
} else {
  const exports = new Map();
  for (const e of steam.enumerateExports()) {
    exports.set(e.name, e.address);
  }

  const accessor = exports.get(ACCESSOR);
  if (accessor === undefined) {
    send({ tag: 'fatal', reason: ACCESSOR + ' not exported' });
  } else {
    const iface = new NativeFunction(accessor, 'pointer', [])();
    if (iface.isNull()) {
      send({ tag: 'fatal', reason: ACCESSOR + ' returned null' });
    } else {
      const vtable = iface.readPointer();
      const slotAddress = function (index) {
        return vtable.add(index * Process.pointerSize).readPointer();
      };

      // Filters accumulate on the calling thread between one `RequestLobbyList` and the next, so
      // they are collected per thread rather than globally -- ersc and this mod can be mid-query
      // at the same time and their filter sets must not merge.
      const pending = new Map();
      const take = function (threadId) {
        const held = pending.get(threadId) || [];
        pending.delete(threadId);
        return held;
      };
      const push = function (threadId, entry) {
        counts.filters += 1;
        const held = pending.get(threadId) || [];
        held.push(entry);
        pending.set(threadId, held);
      };

      Interceptor.attach(slotAddress(SLOT.stringFilter), {
        onEnter(args) {
          push(this.threadId, {
            kind: 'string',
            key: str(args[1]),
            value: str(args[2]),
            comparison: COMPARISON[String(args[3].toInt32())] || args[3].toInt32(),
          });
        },
      });

      Interceptor.attach(slotAddress(SLOT.numericalFilter), {
        onEnter(args) {
          push(this.threadId, {
            kind: 'numerical',
            key: str(args[1]),
            value: args[2].toInt32(),
            comparison: COMPARISON[String(args[3].toInt32())] || args[3].toInt32(),
          });
        },
      });

      Interceptor.attach(slotAddress(SLOT.nearValueFilter), {
        onEnter(args) {
          push(this.threadId, { kind: 'near', key: str(args[1]), value: args[2].toInt32() });
        },
      });

      Interceptor.attach(slotAddress(SLOT.slotsAvailableFilter), {
        onEnter(args) {
          push(this.threadId, { kind: 'slots', value: args[1].toInt32() });
        },
      });

      Interceptor.attach(slotAddress(SLOT.distanceFilter), {
        onEnter(args) {
          push(this.threadId, { kind: 'distance', value: args[1].toInt32() });
        },
      });

      Interceptor.attach(slotAddress(SLOT.resultCountFilter), {
        onEnter(args) {
          push(this.threadId, { kind: 'count', value: args[1].toInt32() });
        },
      });

      Interceptor.attach(slotAddress(SLOT.requestLobbyList), {
        onEnter() {
          this.who = callerOf(this.context);
          this.held = take(this.threadId);
        },
        onLeave(retval) {
          counts.request += 1;
          send({
            tag: 'search',
            n: counts.request,
            caller: this.who,
            call: retval.toString(),
            filters: this.held,
          });
        },
      });

      Interceptor.attach(slotAddress(SLOT.getLobbyByIndex), {
        onEnter(args) {
          this.who = callerOf(this.context);
          this.index = args[1].toInt32();
        },
        onLeave(retval) {
          counts.byIndex += 1;
          send({
            tag: 'by-index',
            n: counts.byIndex,
            caller: this.who,
            index: this.index,
            lobby: retval.toString(),
          });
        },
      });

      Interceptor.attach(slotAddress(SLOT.getLobbyData), {
        onEnter(args) {
          counts.lobbyData += 1;
          this.report = counts.lobbyData <= 40;
          if (!this.report) return;
          this.who = callerOf(this.context);
          this.lobby = args[1].toString();
          this.key = str(args[2]);
        },
        onLeave(retval) {
          if (!this.report) return;
          send({
            tag: 'lobby-data',
            n: counts.lobbyData,
            caller: this.who,
            lobby: this.lobby,
            key: this.key,
            value: str(retval),
          });
        },
      });

      Interceptor.attach(slotAddress(SLOT.joinLobby), {
        onEnter(args) {
          counts.join += 1;
          send({
            tag: 'join',
            n: counts.join,
            caller: callerOf(this.context),
            lobby: args[1].toString(),
          });
        },
      });

      // Reading a lobby's published data needs no membership, so the host's advertisement can be
      // compared against the filter set above without touching her session.
      const dataCount = new NativeFunction(
        exports.get('SteamAPI_ISteamMatchmaking_GetLobbyDataCount'),
        'int',
        ['pointer', 'uint64']
      );
      const dataByIndex = new NativeFunction(
        exports.get('SteamAPI_ISteamMatchmaking_GetLobbyDataByIndex'),
        'bool',
        ['pointer', 'uint64', 'int', 'pointer', 'int', 'pointer', 'int']
      );
      const requestData = new NativeFunction(
        exports.get('SteamAPI_ISteamMatchmaking_RequestLobbyData'),
        'bool',
        ['pointer', 'uint64']
      );

      rpc.exports = {
        lobbyData: function (lobbyHex) {
          const lobby = uint64(lobbyHex);
          requestData(iface, lobby);
          const total = dataCount(iface, lobby);
          const keyBuffer = Memory.alloc(256);
          const valueBuffer = Memory.alloc(8192);
          const entries = {};
          for (let i = 0; i < total; i += 1) {
            if (!dataByIndex(iface, lobby, i, keyBuffer, 256, valueBuffer, 8192)) continue;
            entries[keyBuffer.readUtf8String()] = valueBuffer.readUtf8String();
          }
          return { lobby: lobbyHex, count: total, keys: entries };
        },
        totals: function () {
          return Object.assign({}, counts);
        },
      };

      send({ tag: 'ready', module: STEAM, vtable: vtable.toString() });
    }
  }
}
