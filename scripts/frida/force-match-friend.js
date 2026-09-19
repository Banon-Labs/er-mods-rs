// Aim this client's invasion search at one specific host, and say whether Steam returns them.
//
// # Why this exists rather than `drop-map-filter.js`
//
// That agent proved the mechanism on 2026-09-17 and again on 2026-09-18 (`result n:1 index:0
// lobby:109775241915076357 target:true`), but it installs itself by writing the interface vtable's
// slot 5 to point at a `NativeCallback` the script owns. The script owning that pointer is the
// defect: a watcher killed between `armed` and `restore()` leaves slot 5 aimed at a callback whose
// heap allocation died with the script, and every thread that then adds a lobby filter jumps into
// freed memory. Measured 2026-09-18 on run `br-20260918-203709-f795` -- the agent was killed with
// SIGTERM, `restore()` never ran, `er-invasion-warp.log` stopped mid-search, `frida.attach` began
// timing out, and the game froze with its threads still burning CPU. The player was in it.
//
// `Interceptor` has no such failure mode. Frida reverts every trampoline it installed when the
// script unloads or the session detaches -- including an abrupt detach, because the revert runs
// inside frida-agent in the target rather than in the watcher process. So the worst a killed
// watcher can do here is stop rewriting.
//
// Rewriting arguments in `onEnter` is enough for this job: `AddRequestLobbyListStringFilter` reads
// the value pointer during the call, so replacing `args[2]` before the call reaches the real
// function substitutes the filter without owning any code address.
//
// # What it does
//
//   * `er_invasion_warp_map` -- ours, published by this mod and nothing else -- is aimed at the
//     block the host actually publishes, instead of whichever tile our ring has reached.
//   * Seamless's band field is asked as the host's value instead of this character's. Rewriting
//     what this client SEARCHES FOR transmits nothing about this client; it asks for a set. What
//     stays refused is rewriting what this client PUBLISHES, which would misrepresent the player
//     to everyone else (bd never-forge-a-matchmaking-field-to-make-a-host-match-2026-09-17, as
//     corrected by bd seamless-21c40388-is-level-band-underscore-weapon-band-2026-09-17).
//   * `GetLobbyByIndex` is observed, so "the host was returned" is a fact the search produces
//     rather than an inference from the session advancing.
//
// # Downstream interference this cannot remove
//
// `lobby_publish.rs` holds a MinHook detour on this same function and applies its own band climb
// to whatever value arrives. Measured: this agent substituted `2_1` and the DLL logged
// `band-ladder: asking for 2_2 instead of 2_1`, so Steam received `2_2`. The substitution here is
// therefore the value the DLL RECEIVES, not the value Steam sees, whenever the DLL's rung is not
// its own. Read `er-invasion-warp.log` alongside this agent's events; the `instead of` half of
// that line names exactly what this agent passed.
//
// Configure through the watcher rather than by editing constants:
//   --config-json '{"lobby":"1097...","band":"2_1","block":"m32_02_00_00"}'
'use strict';

const STEAM = 'steam_api64.dll';
const ACCESSOR = 'SteamAPI_SteamMatchmaking_v009';
const STRING_FILTER_SLOT = 5;
const REQUEST_SLOT = 4;
const BY_INDEX_SLOT = 12;
const OUR_MAP_KEY = 'er_invasion_warp_map';

// Seamless's band field, hashed like the rest of its key names. Matched by name here only because
// the name is stable for the one build this repo supports; the value's `<digits>_<digits>` shape
// is what identifies it across builds.
const SEAMLESS_BAND_KEY =
  '21c40388cba69692c865c11604f6e340fb8f0df83bebea279e802ccc0d46de8e';

const config = typeof globalThis.__ER_FRIDA_CONFIG === 'object' && globalThis.__ER_FRIDA_CONFIG
  ? globalThis.__ER_FRIDA_CONFIG
  : {};
const TARGET_LOBBY = String(config.lobby || '');
const WANTED_BAND = config.band ? String(config.band) : null;
const TARGET_BLOCK = config.block ? String(config.block) : null;

// Substituted strings must outlive the forwarded call -- Steam copies during it, not before.
const held = [];

const counts = { filters: 0, aimed: 0, banded: 0, requests: 0, results: 0, target: 0 };

function str (pointer) {
  try {
    return pointer.isNull() ? null : pointer.readUtf8String();
  } catch (error) {
    return null;
  }
}

function substitute (text) {
  const allocated = Memory.allocUtf8String(text);
  held.push(allocated);
  return allocated;
}

const steam = Process.findModuleByName(STEAM);
if (steam === null) {
  send({ tag: 'fatal', reason: STEAM + ' not loaded' });
} else {
  const accessor = steam.findExportByName(ACCESSOR);
  if (accessor === null) {
    send({ tag: 'fatal', reason: ACCESSOR + ' not exported' });
  } else {
    const iface = new NativeFunction(accessor, 'pointer', [])();
    if (iface.isNull()) {
      send({ tag: 'fatal', reason: ACCESSOR + ' returned null' });
    } else {
      const vtable = iface.readPointer();
      const slot = (n) => vtable.add(n * Process.pointerSize).readPointer();

      Interceptor.attach(slot(STRING_FILTER_SLOT), {
        onEnter (args) {
          counts.filters += 1;
          const name = str(args[1]);
          const was = str(args[2]);
          if (name === OUR_MAP_KEY && TARGET_BLOCK !== null && was !== TARGET_BLOCK) {
            args[2] = substitute(TARGET_BLOCK);
            counts.aimed += 1;
            send({ tag: 'aimed', was: was, now: TARGET_BLOCK, n: counts.aimed });
            return;
          }
          if (name === SEAMLESS_BAND_KEY && WANTED_BAND !== null && was !== WANTED_BAND) {
            args[2] = substitute(WANTED_BAND);
            counts.banded += 1;
            send({ tag: 'band', was: was, asked: WANTED_BAND, n: counts.banded });
          }
        },
      });

      Interceptor.attach(slot(REQUEST_SLOT), {
        onLeave (retval) {
          counts.requests += 1;
          send({
            tag: 'request',
            n: counts.requests,
            call: retval.toString(),
            counts: Object.assign({}, counts),
          });
        },
      });

      // `GetLobbyByIndex` returns a `CSteamID` BY VALUE, so MSVC passes a hidden return buffer:
      // the real argument order is `(this, retbuf, index)`, not the header's `(this, index)`.
      // Reading `args[1]` as the index is what once reported `index: 1113200`, which was the
      // interface pointer counted as a number.
      Interceptor.attach(slot(BY_INDEX_SLOT), {
        onEnter (args) {
          this.buffer = args[1];
          this.index = args[2].toInt32();
        },
        onLeave () {
          counts.results += 1;
          let lobby = '<unreadable>';
          try {
            lobby = this.buffer.readU64().toString();
          } catch (error) {
            lobby = 'unreadable: ' + error.message;
          }
          if (lobby === TARGET_LOBBY) counts.target += 1;
          send({
            tag: 'result',
            n: counts.results,
            index: this.index,
            lobby: lobby,
            target: lobby === TARGET_LOBBY,
          });
        },
      });

      send({
        tag: 'armed',
        via: 'Interceptor',
        lobby: TARGET_LOBBY || '<none>',
        band: WANTED_BAND || '<unchanged>',
        block: TARGET_BLOCK || '<unchanged>',
      });
    }
  }
}
