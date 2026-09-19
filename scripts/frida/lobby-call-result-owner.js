// Who asks for a lobby list, and who ever collects the answer to it?
//
// # The gap this closes
//
// Two things are measured and they do not fit together. On run `br-20260918-002055-bcc4`, across
// 46 search cycles, `invasion-connect-probe.js` saw Seamless read not one lobby -- `GetLobbyByIndex`
// 0, `GetLobbyData` 0, `JoinLobby` 0. Yet this mod's own sweep found the host and named her place
// on screen, so the answer to a lobby query plainly does arrive in this process.
//
// The two use different collection paths, which is the whole reason they can disagree.
// `lobby_preflight` never registers anything: it holds the `SteamAPICall_t` that
// `RequestLobbyList` returned and polls `SteamAPI_ISteamUtils_IsAPICallCompleted`, then takes the
// payload with `GetAPICallResult`. That is consistent with `steam-callback-pump.js` reading
// `SteamAPI_RegisterCallResult` 0 while the sweep works: a poller registers nothing.
//
// So the question is not whether results arrive. It is which handle each caller is holding and
// what happens to that handle's result. `GetAPICallResult` frees the result it returns, and this
// module has already established that an `ISteamMatchmaking` carries one lobby query at a time --
// so a poll on the wrong handle, or a second request that supersedes the first, ends with somebody
// waiting on an answer that no longer exists. Fifteen seconds of waiting is what state `0x12` is.
//
// # What this records
//
// Every request, and every poll, tagged with the caller's module so the two sides are told apart:
//
//   * `RequestLobbyList` -- entry and exit, so the returned `SteamAPICall_t` is captured along with
//     who asked. The vtable slot, not the flat export, because Seamless calls through the interface.
//   * `IsAPICallCompleted` -- the handle polled, who polled it, and the answer. Sampled, since a
//     poller hits this every frame.
//   * `GetAPICallResult` -- the handle collected and by whom. Not sampled: this is the one that
//     consumes, and every occurrence matters.
//
// The caller module comes from the return address, so "ersc.dll asked and er_invasion_warp.dll
// collected" is a sentence this can actually produce rather than infer.
//
// Read-only: entry and exit interceptors. Nothing is replaced and no game memory is written.
'use strict';

const STEAM = 'steam_api64.dll';
const MATCHMAKING_ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const REQUEST_LOBBY_LIST_SLOT = 4;

const POLL_SAMPLE = 200;

function callerOf(context) {
  try {
    const home = Process.findModuleByAddress(context.returnAddress);
    return home === null ? 'unknown' : home.name;
  } catch (error) {
    return 'unreadable';
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

  const counts = { request: 0, polls: 0, collects: 0 };

  // The request side, through the interface vtable. Slot 4 is pinned independently by
  // `lobby_publish.rs`, which holds its own detour there -- so this attaches over a live
  // trampoline, which is safe in the one direction that matters here: an entry/exit observer on
  // a MinHook trampoline sees the call, it does not redirect it.
  const accessor = exports.get(MATCHMAKING_ACCESSOR);
  if (accessor === undefined) {
    send({ tag: 'fatal', reason: MATCHMAKING_ACCESSOR + ' not exported' });
  } else {
    const iface = new NativeFunction(accessor, 'pointer', [])();
    if (iface.isNull()) {
      send({ tag: 'fatal', reason: MATCHMAKING_ACCESSOR + ' returned null' });
    } else {
      const vtable = iface.readPointer();
      const target = vtable.add(REQUEST_LOBBY_LIST_SLOT * Process.pointerSize).readPointer();
      const home = Process.findModuleByAddress(target);
      if (home === null) {
        send({ tag: 'slot-refused', slot: REQUEST_LOBBY_LIST_SLOT, target: target.toString() });
      } else {
        Interceptor.attach(target, {
          onEnter() {
            this.who = callerOf(this.context);
          },
          onLeave(retval) {
            counts.request += 1;
            send({
              tag: 'request',
              n: counts.request,
              caller: this.who,
              call: retval.toString(),
            });
          },
        });
        send({ tag: 'request-hooked', target: target.toString(), module: home.name });
      }
    }
  }

  // The collection side. Both are flat exports of `steam_api64.dll` and neither is detoured by
  // this repo, so there is no trampoline under either.
  const completed = exports.get('SteamAPI_ISteamUtils_IsAPICallCompleted');
  if (completed === undefined) {
    send({ tag: 'missing', name: 'SteamAPI_ISteamUtils_IsAPICallCompleted' });
  } else {
    Interceptor.attach(completed, {
      onEnter(args) {
        counts.polls += 1;
        this.report = counts.polls === 1 || counts.polls % POLL_SAMPLE === 0;
        if (!this.report) return;
        this.who = callerOf(this.context);
        this.call = args[1].toString();
      },
      onLeave(retval) {
        if (!this.report) return;
        send({ tag: 'poll', n: counts.polls, caller: this.who, call: this.call, done: !retval.isNull() });
      },
    });
  }

  const result = exports.get('SteamAPI_ISteamUtils_GetAPICallResult');
  if (result === undefined) {
    send({ tag: 'missing', name: 'SteamAPI_ISteamUtils_GetAPICallResult' });
  } else {
    Interceptor.attach(result, {
      onEnter(args) {
        counts.collects += 1;
        this.who = callerOf(this.context);
        this.call = args[1].toString();
        this.expected = args[4].toInt32();
      },
      onLeave(retval) {
        send({
          tag: 'collect',
          n: counts.collects,
          caller: this.who,
          call: this.call,
          callback_id: this.expected,
          ok: !retval.isNull(),
        });
      },
    });
  }

  // Pulled by the driver, so a stall reads as a number that stopped rather than as silence --
  // which is equally what a hook that never installed looks like. A timer would push the same
  // line on a clock nobody is reading against; the driver asks when it has a reason to.
  rpc.exports = {
    totals: function () {
      return { counts: Object.assign({}, counts) };
    },
  };

  send({ tag: 'ready', module: STEAM, base: steam.base.toString() });
}
