// Does `PLAY_REGION_POINT_MAN_GLOBAL_RVA` need translating on the installed build?
//
// # The claim under test
//
// `er-invasion-warp-core`'s `live_points` reads the region-point manager as
// `game_module_base() + er_game_base::rva::PLAY_REGION_POINT_MAN_GLOBAL_RVA`, with no resolve.
// Every rva in this workspace is a 1.16.2 rva and the installed game is 1.17.1, so that addition
// is only correct if the global did not move. `scripts/check-stale-rva-calls.py` says it is a new
// unresolved site; `scripts/map-data-rvas-1162-to-1170.py` says the global moved, 25 references
// agreeing on `0x3d6e388 -> 0x3d723f8`.
//
// Those are both static arguments. This reads the running process, which is the only thing that
// settles it, and it reads BOTH addresses in the same pass so the answer is a comparison rather
// than an assertion about one number.
//
// # What makes an answer here trustworthy
//
// A global that has not been written yet holds zero, and so does an address that was never a
// global -- so "the stale one reads 0" on its own would prove nothing, which is exactly the trap
// the null guard in `live_points` falls into: it returns an empty list and the caller cannot tell
// "no regions loaded" from "wrong address". The discriminator is the mapped one reading a POINTER
// while the stale one does not, and then that pointer's `+0x8` map head being readable too --
// `POINT_MAN_MAP_OFFSET`, the next hop `live_points` takes.
//
// Read-only: three pointer loads and a module-range test. Nothing is written, no hook is installed
// and no watchpoint is armed.
'use strict';

const MODULE = 'eldenring.exe';

// The constant as `crates/er-game-base/src/rva.rs` declares it: a 1.16.2 rva.
const STALE_RVA = 0x3d6e388;

// What `docs/recon/rva-map-1162-to-1170.data.tsv` now records for it, 25 references agreeing.
const MAPPED_RVA = 0x3d723f8;

// `POINT_MAN_MAP_OFFSET` -- the container inside the manager, and `live_points`'s next hop.
const MAP_OFFSET = 0x8;

const game = Process.findModuleByName(MODULE);
if (game === null) {
  send({ tag: 'fatal', reason: MODULE + ' not loaded' });
} else {
  function readPointer(address) {
    try {
      return address.readPointer();
    } catch (error) {
      return null;
    }
  }

  // A manager is a heap pointer, not an address inside the image and not null. Naming the module
  // an address falls in separates "this is some other datum" from "this is a real object".
  function describe(value) {
    if (value === null) return { readable: false };
    if (value.isNull()) return { readable: true, value: '0x0', kind: 'null' };
    const home = Process.findModuleByAddress(value);
    return {
      readable: true,
      value: value.toString(),
      kind: home === null ? 'heap' : 'inside ' + home.name,
    };
  }

  function candidate(label, rva) {
    const slot = game.base.add(rva);
    const manager = readPointer(slot);
    const report = {
      label: label,
      rva: '0x' + rva.toString(16),
      slot: slot.toString(),
      manager: describe(manager),
      map_head: { readable: false },
    };
    // Only worth the second hop when the first produced something that could be an object.
    if (manager !== null && !manager.isNull()) {
      report.map_head = describe(readPointer(manager.add(MAP_OFFSET)));
    }
    return report;
  }

  const stale = candidate('stale 1.16.2 rva, as the code adds it today', STALE_RVA);
  const mapped = candidate('mapped 1.17 rva, as the recon map records it', MAPPED_RVA);

  // The verdict, stated by the agent rather than left for a reader to infer from two objects.
  let verdict;
  if (mapped.manager.kind === 'heap' && stale.manager.kind !== 'heap') {
    verdict = 'the global moved: only the mapped rva holds an object, so the unresolved read is wrong';
  } else if (stale.manager.kind === 'heap' && mapped.manager.kind !== 'heap') {
    verdict = 'the stale rva holds the object and the map is wrong -- do not change the call site';
  } else if (stale.manager.kind === 'heap' && mapped.manager.kind === 'heap') {
    verdict = 'both hold objects; this measurement cannot choose between them';
  } else {
    verdict = 'neither holds an object -- the manager is probably not built yet, so ask again in world';
  }

  send({ tag: 'play-region-global', module: MODULE, base: game.base.toString(), stale, mapped, verdict });
}
