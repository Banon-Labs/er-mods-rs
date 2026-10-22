// The shader editor's diagnostic exports (design 11.1), called from Frida without hooking anything.
//
// er_cel_shade.dll exports three functions for exactly this:
//
//   er_shader_editor_stats_v1(StatsV1 *out) -> u32   fills 200 bytes and returns 200 (0 for a null
//                                                    pointer or a panic while reading)
//   er_shader_editor_set_mode_v1(u32 mode) -> u32    0 normal, 1 vanilla, 2 fidelity, with the port
//                                                    kinds a fidelity test redirects as bits in the
//                                                    second byte (0 there: every kind); returns the
//                                                    setting before, or 0xffffffff for no such setting
//   er_shader_editor_set_unfaithful_v1(u32 ports) -> u32
//                                                    marks exactly these port-kind bits unfaithful
//                                                    and saves the mark; returns the mark before, or
//                                                    0xffffffff for a bit that names no kind
//
// This agent resolves all three by name from the DLL's export table and calls them through
// NativeFunction. It installs no Interceptor at all, and in particular none on a D3D12 function:
// the DLL has detoured SetPipelineState, Reset, ClearState, Close, CreateCommandList and the
// creation calls with MinHook, and a Frida trampoline on a prologue that is already a jump would
// fight that detour. With nothing installed, attaching, detaching and reloading in place leave the
// game exactly as they found it, so this agent may share the process with a watcher's session.
//
// rpc:
//   stats()            the StatsV1 fields by name, plus `bytes` (what the export returned) and
//                      `mode_name`, the mode in the low byte of `mode`
//   setMode(m)         {requested, before, before_name, refused, stats}: the stats are read after
//                      the switch, so `stats.mode` and `stats.vanilla` say what the DLL made of it
//   setUnfaithful(p)   {requested, before, refused, stats}, the same way for the unfaithful mark
//
// On load it sends one `shader-editor-stats-ready` message with the export addresses and a first
// snapshot, or `shader-editor-stats-error` naming what is missing; each setMode sends one
// `shader-editor-mode` message and each setUnfaithful one `shader-editor-unfaithful`. It has no
// timer and sends nothing else on its own.
//
// Run it as its own background task, without an outer timeout:
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/shader-editor-stats.js
//
// scripts/er-shader-ab-proof.py loads this same file in a session of its own and calls the RPC:
// `--stats` and `--set-mode <m>` for one call each, and the window A/B for the proof.

'use strict';

const MODULE_NAME = 'er_cel_shade.dll';
const STATS_EXPORT = 'er_shader_editor_stats_v1';
const SET_MODE_EXPORT = 'er_shader_editor_set_mode_v1';
const SET_UNFAITHFUL_EXPORT = 'er_shader_editor_set_unfaithful_v1';
// `exports::STATS_V1_BYTES` and `exports::SET_MODE_REFUSED`.
const STATS_V1_BYTES = 200;
const SET_MODE_REFUSED = 0xffffffff;
// `safety::Mode`, by number.
const MODE_NAMES = ['normal', 'vanilla', 'fidelity'];

// `StatsV1::LAYOUT` in crates/er-cel-shade/src/exports.rs, field for field: name, byte offset,
// element type, element count. `scripts/er-shader-ab-proof.py --selftest` holds the two equal.
const LAYOUT = [
  ['presents', 0, 'u64', 1],
  ['mode', 8, 'u32', 1],
  ['vanilla', 12, 'u32', 1],
  ['rs_full', 16, 'u64', 1],
  ['rs_cbv_probe', 24, 'u64', 1],
  ['rs_cbv', 32, 'u64', 1],
  ['rs_none', 40, 'u64', 1],
  ['ports_ready', 48, 'u64', 5],
  ['redirected_binds', 88, 'u64', 1],
  ['passthrough_port_binds', 96, 'u64', 1],
  ['set_pso_calls', 104, 'u64', 1],
  ['hook_ns_sum', 112, 'u64', 1],
  ['hook_ns_samples', 120, 'u64', 1],
  ['list_slot_misses', 128, 'u64', 1],
  ['graveyard_len', 136, 'u64', 1],
  ['leaked', 144, 'u64', 1],
  ['game_psos_released', 152, 'u64', 1],
  ['probe_frame', 160, 'u32', 3],
  ['variant_id', 172, 'u32', 3],
  ['compile_ms_last', 184, 'f32', 1],
  ['create_ms_last', 188, 'f32', 1],
  ['unfaithful_ports', 192, 'u32', 1],
  ['ports_off', 196, 'u32', 1],
];

function exportAddress(name) {
  const module = Process.findModuleByName(MODULE_NAME);
  if (module === null) {
    throw new Error(MODULE_NAME + ' is not loaded in this process; the profile must include er-cel-shade');
  }
  // The module object's lookup. Frida 17 removed the static `Module.getExportByName(module, name)`
  // form (the server here is pinned at 17.17.0 by er-frida-up.py), and Frida 16 has this one too.
  return module.getExportByName(name);
}

let api = null;

function bind() {
  if (api === null) {
    const statsAt = exportAddress(STATS_EXPORT);
    const setModeAt = exportAddress(SET_MODE_EXPORT);
    const setUnfaithfulAt = exportAddress(SET_UNFAITHFUL_EXPORT);
    api = {
      stats: new NativeFunction(statsAt, 'uint32', ['pointer']),
      setMode: new NativeFunction(setModeAt, 'uint32', ['uint32']),
      setUnfaithful: new NativeFunction(setUnfaithfulAt, 'uint32', ['uint32']),
      // Allocated once and kept: every call writes the same 200 bytes.
      out: Memory.alloc(STATS_V1_BYTES),
      addresses: {
        stats: statsAt.toString(),
        set_mode: setModeAt.toString(),
        set_unfaithful: setUnfaithfulAt.toString(),
      },
    };
  }
  return api;
}

// The fields of one StatsV1, little-endian, at the offsets the layout gives. The u64 counters are
// far below 2^53, so a JS number holds them exactly.
function decodeStats(buffer) {
  const view = new DataView(buffer);
  const out = {};
  for (const [name, offset, type, count] of LAYOUT) {
    const values = [];
    for (let i = 0; i < count; i++) {
      if (type === 'u64') {
        values.push(Number(view.getBigUint64(offset + 8 * i, true)));
      } else if (type === 'u32') {
        values.push(view.getUint32(offset + 4 * i, true));
      } else {
        values.push(view.getFloat32(offset + 4 * i, true));
      }
    }
    out[name] = count === 1 ? values[0] : values;
  }
  return out;
}

function readStats() {
  const bound = bind();
  const written = bound.stats(bound.out);
  if (written !== STATS_V1_BYTES) {
    throw new Error(STATS_EXPORT + ' returned ' + written + ', not ' + STATS_V1_BYTES);
  }
  const stats = decodeStats(bound.out.readByteArray(STATS_V1_BYTES));
  stats.bytes = written;
  stats.mode_name = MODE_NAMES[stats.mode & 0xff] || 'unknown';
  return stats;
}

function setMode(mode) {
  const requested = mode >>> 0;
  const before = bind().setMode(requested);
  const refused = before === SET_MODE_REFUSED;
  const stats = readStats();
  const result = {
    requested: requested,
    before: refused ? null : before,
    before_name: refused ? null : (MODE_NAMES[before & 0xff] || 'unknown'),
    refused: refused,
    stats: stats,
  };
  send({
    event: 'shader-editor-mode',
    requested: requested,
    before: result.before,
    refused: refused,
    presents: stats.presents,
    mode: stats.mode,
    vanilla: stats.vanilla,
  });
  return result;
}

function setUnfaithful(ports) {
  const requested = ports >>> 0;
  const before = bind().setUnfaithful(requested);
  const refused = before === SET_MODE_REFUSED;
  const stats = readStats();
  send({
    event: 'shader-editor-unfaithful',
    requested: requested,
    before: refused ? null : before,
    refused: refused,
    unfaithful_ports: stats.unfaithful_ports,
  });
  return {
    requested: requested,
    before: refused ? null : before,
    refused: refused,
    stats: stats,
  };
}

rpc.exports = {
  stats: readStats,
  setMode: setMode,
  setUnfaithful: setUnfaithful,
};

try {
  const first = readStats();
  send({
    event: 'shader-editor-stats-ready',
    module: MODULE_NAME,
    exports: bind().addresses,
    stats: first,
  });
} catch (error) {
  send({ event: 'shader-editor-stats-error', error: String(error) });
}
