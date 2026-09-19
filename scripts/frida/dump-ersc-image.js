// Dump ersc.dll out of live memory, where Themida has already decrypted it.
//
// # Why a runtime dump and not the file
//
// The on-disk `ersc.dll` is Themida-packed: 1.6 MB of `.text` against an 11 MB packed `ERSC`
// section at rva 0x240000, so every static question about Seamless's networking dead-ends in
// compressed bytes. The packer decrypts into the same addresses at load, so by the time the game
// is in a world the module's own pages hold the real code. Reading them back is the only way this
// workspace gets a disassemblable Seamless.
//
// # What it refuses to do
//
// Pages are read one at a time and a failure is RECORDED, not skipped silently: a dump with
// unmarked holes reads as a complete image and produces disassembly of zeroes that looks like
// data. The manifest names every gap, so the consumer can tell absent from empty.
const PAGE = 0x1000;

function pageRanges (module) {
  // Ranges, not a flat sweep: a module's image contains unmapped guard pages, and asking for them
  // by address throws once per page rather than returning short.
  return Process.enumerateRanges('---').filter((range) => {
    const start = range.base;
    const end = start.add(range.size);
    return start.compare(module.base.add(module.size)) < 0 && end.compare(module.base) > 0;
  });
}

rpc.exports = {
  info () {
    const m = Process.findModuleByName('ersc.dll');
    if (m === null) return null;
    return {
      base: m.base.toString(),
      size: m.size,
      path: m.path,
      ranges: pageRanges(m).map((r) => ({
        base: r.base.toString(),
        size: r.size,
        protection: r.protection,
      })),
    };
  },
  // One page at a time, addressed by offset from the module base, so the driver owns the loop and
  // this process never holds a multi-megabyte buffer.
  page (offset) {
    const m = Process.findModuleByName('ersc.dll');
    if (m === null) return null;
    try {
      return m.base.add(offset).readByteArray(PAGE);
    } catch (e) {
      return null;
    }
  },
  // A whole run of pages when they are known good, because one RPC per 4 KB across 13.8 MB is
  // 3,400 round trips and the round trip dominates.
  chunk (offset, size) {
    const m = Process.findModuleByName('ersc.dll');
    if (m === null) return null;
    try {
      return m.base.add(offset).readByteArray(size);
    } catch (e) {
      return null;
    }
  },
};
