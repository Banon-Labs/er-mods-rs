// Who sets a menu grid's cell count, and from which module.
//
// # Why this and not the predicate table
//
// The first version of this agent read the 61-entry menu-availability predicate table at 1.17.1
// `0x143b38f60` and compared every entry against `eldenring-deobf-1.17.1.bin`. Measured on a live
// modded process: all 61 pointers and all 61 leading byte-triples matched the vanilla image
// exactly, entry 16 (`0x140765d40`, the 1.16.1 dump's `EnableCovenantSlot`) included, still
// `32 c0 c3`. So nothing in the loaded DLLs rewrites that table or its thunks, and an extra cell
// in the equipment grid cannot be coming from that gate. (The `Interceptor` on entry 16 also
// failed -- `unable to intercept function`, because a three-byte function has no room for a
// five-byte detour. The byte comparison had already answered the question.)
//
// The cell count is the thing that decides how many cells a grid has.
// `CS::GridControl::SetItemCount` writes `+0xd0` and recomputes the embedded scroll control at
// `+0x1a8`; 1.16.2 `0x738dc0` pairs BYTE-IDENTICAL to 1.17 `0x739c10`, which `map-rvas-1170-to-1171`
// reports unchanged on 1.17.1 (below the `0xafefe9` boundary).
//
// # What it reports
//
// Every call: the grid pointer, the requested count, the previous `+0xd0`, and -- the point --
// whether the return address lies inside the game image or inside one of the loaded mod DLLs.
// er_quickload's own row cloner calls this deliberately for the System>Quit dialog's grid, so a
// hit attributed to `er_quickload.dll` is only interesting if its grid is not that dialog's.

const SET_ITEM_COUNT_RVA = 0x739c10;
const GRID_ITEM_COUNT_OFFSET = 0xd0;
const IMAGE_BASE = ptr('0x140000000');

// Calls are frequent once menus start opening; report the first burst in full, then only when a
// (module, count) pair is new, so the log stays readable without hiding a change.
const MAX_VERBOSE = 40;
let calls = 0;
const seen = new Set();

function moduleOf(address) {
  const m = Process.findModuleByAddress(address);
  if (!m) {
    return { name: '<unmapped>', offset: address.toString() };
  }
  return { name: m.name, offset: address.sub(m.base).toString() };
}

function main() {
  const game = Process.enumerateModules().find((m) => m.name.toLowerCase() === 'eldenring.exe');
  if (!game) {
    send({ tag: 'grid-item-count', error: 'eldenring.exe not in the module list' });
    return;
  }
  const slide = game.base.sub(IMAGE_BASE);
  const setItemCount = game.base.add(SET_ITEM_COUNT_RVA);

  Interceptor.attach(setItemCount, {
    onEnter(args) {
      calls += 1;
      const grid = args[0];
      const count = args[1].toInt32();
      let before = -1;
      try {
        before = grid.add(GRID_ITEM_COUNT_OFFSET).readS32();
      } catch (e) {
        before = -2;
      }
      const caller = moduleOf(this.returnAddress);
      const key = `${caller.name}:${caller.offset}:${count}`;
      if (calls <= MAX_VERBOSE || !seen.has(key)) {
        seen.add(key);
        send({
          tag: 'grid-item-count',
          call: calls,
          grid: grid.toString(),
          count,
          before,
          caller_module: caller.name,
          caller_offset: caller.offset,
        });
      }
    },
  });

  send({
    tag: 'grid-item-count',
    action: 'armed',
    address: setItemCount.toString(),
    slide: slide.toString(),
    modules: Process.enumerateModules()
      .filter((m) => m.name.toLowerCase().endsWith('.dll') && m.name.toLowerCase().startsWith('er'))
      .map((m) => `${m.name}@${m.base}`),
  });

  // No heartbeat timer. The armed message above is the liveness signal, and the watcher's own
  // detach line says when the session ended; a periodic tick would be a clock standing in for a
  // readiness signal, which `scripts/check-no-timeouts.py` refuses for good reason.
}

main();
