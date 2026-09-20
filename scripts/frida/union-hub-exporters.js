// Which loaded modules export the hook union's registrar, and which one the election would pick.
//
// # Why this is worth a session
//
// `scripts/me3-dll-conflicts.toml` and `scripts/check-shared-hook-rvas.py` both now claim, as of
// the 2026-09-20 deletion of the `er-quit-rows` fork, that this workspace has exactly ONE hub:
// `er_quickload.dll` is the only image left defining `er_effects_union_register`. That claim was
// read out of the source tree, and a source read cannot see what is actually in the process --
// Seamless, me3's own natives, and any companion a profile happens to carry are all loaded images
// this repo does not compile.
//
// `er_hook::elect_union_host` asks the loader the same question at runtime and takes the lowest
// module base among the answers. So the honest check is to ask the loader too, which is what this
// does: enumerate every module, resolve both registrar spellings against each, and report the
// winner the election rule would produce.
//
// A companion that reports `HookRoute::LocalUnion` while a second exporter is in the list is the
// failure this is looking for. Nothing is hooked and nothing is written: it is a read of the
// module table.

'use strict';

const REGISTRARS = ['er_effects_union_register', 'er_effects_union_register5'];

// Count the failures, do not swallow them.
//
// The first reading this agent produced said "0 exporters among 95 modules" on a process that had
// `er_quickload.dll` in it, whose export table contains both names -- checked offline with
// `strings` on the same artifact the launcher staged. The cause was the lookup, not the tree:
// the static `Module.findExportByName(moduleName, symbolName)` is gone in Frida 17, and a
// `try/catch` around it turned "this API does not exist" into "this module does not export it".
// An instrument that reports an absence it cannot detect is worse than no instrument, so every
// lookup outcome is now one of three: found, genuinely absent, or errored -- and errored is
// reported.
function exportersOf (name) {
  const found = [];
  const errors = [];
  let absent = 0;
  for (const module of Process.enumerateModules()) {
    let address = null;
    try {
      // The instance method, which is the Frida 17 spelling. `getExportByName` throws on absence;
      // `findExportByName` returns null, which is the distinction this loop is built on.
      address = module.findExportByName(name);
    } catch (error) {
      errors.push({ module: module.name, error: String(error) });
      continue;
    }
    if (address === null) {
      absent += 1;
      continue;
    }
    found.push({ module: module.name, base: module.base.toString(), export: address.toString() });
  }
  return { found: found, absent: absent, errors: errors };
}

function survey () {
  const out = { modules: Process.enumerateModules().length, registrars: {} };
  for (const name of REGISTRARS) {
    const scan = exportersOf(name);
    // The election rule, restated here rather than assumed: lowest module base wins, and a single
    // exporter wins trivially. If this disagrees with what a companion logs, the companion's route
    // is the thing that is wrong, not this reading.
    let winner = null;
    for (const entry of scan.found) {
      if (winner === null || ptr(entry.base).compare(ptr(winner.base)) < 0) {
        winner = entry;
      }
    }
    // `readable` is the count this reading is entitled to speak about. An empty `exporters` list
    // means "no module exports it" only when it equals the module count; short of that the scan
    // failed on the difference and the answer is unknown.
    out.registrars[name] = {
      exporters: scan.found,
      elected: winner,
      readable: scan.found.length + scan.absent,
      errors: scan.errors.slice(0, 3),
      errorCount: scan.errors.length,
    };
  }
  return out;
}

send(survey());

rpc.exports = {
  survey: survey,
};
console.log('union-hub-exporters: read the module table');
