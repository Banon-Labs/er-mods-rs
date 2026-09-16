// Use a vanilla invasion finger, agent-driven, through er_invasion_warp.dll's own export.
//
// AGENTS.md's 2026-07-22 order is that the agent drives every required input. Reaching the
// Festering Bloody Finger through the real menus is an inventory route whose row count nothing
// here can know, and a route guessed one row off uses the wrong item silently. So the press is
// not simulated at all: `er_invasion_warp_use_item` records the id, and the DLL's own game task
// performs the engine's `Use` command -- four stores into `CSMenuMan->menuData->menuGaitemUseState`
// plus the `ChrIns+0x168` repeat count, the mechanism `lynchpin_use` measured live on 2026-09-09.
//
// Nothing in `ersc.dll` is hooked here. A Frida trampoline on that module breaks the prologue
// byte-check `er_invasion_warp.dll` makes before every call into it, which cost a real press on
// 2026-09-16.
const EXPORT_MODULE = 'er_invasion_warp.dll';
const USE_ITEM = 'er_invasion_warp_use_item';

// Goods row -> the id the menu spells, `(row & 0x0fffffff) | 0x40000000`.
const FINGERS = {
  102: { id: 0x40000066, name: 'Bloody Finger' },
  111: { id: 0x4000006f, name: 'Festering Bloody Finger' },
  112: { id: 0x40000070, name: 'Recusant Finger' },
};

function resolveExport (moduleName, exportName) {
  // Frida 17 removed the free function `Module.getExportByName`.
  const mod = Process.findModuleByName(moduleName);
  if (mod === null) {
    return { error: `${moduleName} is not loaded` };
  }
  const address = mod.getExportByName(exportName);
  if (address === null || address.isNull()) {
    return { error: `${moduleName} has no export ${exportName}` };
  }
  return { address };
}

rpc.exports = {
  useFinger (row) {
    const finger = FINGERS[row];
    if (finger === undefined) {
      return { ok: false, why: `${row} is not one of the three invasion fingers` };
    }
    const found = resolveExport(EXPORT_MODULE, USE_ITEM);
    if (found.error !== undefined) {
      return { ok: false, why: found.error };
    }
    const use = new NativeFunction(found.address, 'int', ['uint32']);
    const recorded = use(finger.id);
    return {
      ok: recorded === 1,
      item: finger.name,
      item_id: `0x${finger.id.toString(16)}`,
      recorded,
    };
  },
};

const probe = resolveExport(EXPORT_MODULE, USE_ITEM);
if (probe.error !== undefined) {
  console.log(`finger-use: ${probe.error}`);
} else {
  console.log(`finger-use: ${USE_ITEM} at ${probe.address} -- ready`);
}
