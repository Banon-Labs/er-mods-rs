// Find every function entry the game owns that has been detoured into ersc.dll.
//
// Seamless has to watch something to know its item was used, and the consume path is not it:
// `TAE_ConsumeCurrentGoods`, `TaeUseContext::Update`, `GetSelectedGoodsUseAnim`,
// `GetItemInventoryIdx` and `SetQuickSlotItem` all read pristine in live memory, and a consumed
// Lynchpin writes nothing new anywhere in ersc's data. So the question is which entries it does
// own, and this answers it by looking only where a detour can actually be installed.
//
// # Why function entries and not a byte scan
//
// Scanning the game's executable pages for `e9` at every byte offset produces pure noise: any
// random `0xe9` followed by four bytes that happen to compute into ersc's 13.8 MB lands inside it
// about once in every few hundred candidates, and 80 MB of code has millions of candidates. A scan
// done that way returned dozens of `hits` that were not instructions at all. Function starts come
// from the PE's own `.pdata`, so every address checked is somewhere a hook could really go.
const JMP_REL32 = 0xe9;
const JMP_INDIRECT_0 = 0xff;
const JMP_INDIRECT_1 = 0x25;

function sections (base) {
  const peOffset = base.add(0x3c).readU32();
  const pe = base.add(peOffset);
  const sectionCount = pe.add(6).readU16();
  const optionalSize = pe.add(20).readU16();
  const first = pe.add(24).add(optionalSize);
  const out = [];
  for (let i = 0; i < sectionCount; i++) {
    const s = first.add(i * 40);
    out.push({
      name: s.readUtf8String(8).replace(/\0+$/, ''),
      rva: s.add(12).readU32(),
      size: s.add(8).readU32(),
    });
  }
  return out;
}

rpc.exports = {
  detours () {
    const game = Process.findModuleByName('eldenring.exe');
    const ersc = Process.findModuleByName('ersc.dll');
    if (game === null || ersc === null) return { error: 'a module is missing' };
    const pdata = sections(game.base).find(s => s.name === '.pdata');
    if (pdata === undefined) return { error: 'no .pdata' };
    const lo = ersc.base, hi = ersc.base.add(ersc.size);
    const entries = Math.floor(pdata.size / 12);
    const out = { entries, intoErsc: [], indirect: 0, stubbed: 0 };
    const table = game.base.add(pdata.rva);
    for (let i = 0; i < entries; i++) {
      const start = table.add(i * 12).readU32();
      if (start === 0) continue;
      const at = game.base.add(start);
      let first;
      try { first = at.readU8(); } catch (e) { continue; }
      if (first === JMP_INDIRECT_0) {
        try { if (at.add(1).readU8() === JMP_INDIRECT_1) out.indirect += 1; } catch (e) {}
        continue;
      }
      if (first !== JMP_REL32) continue;
      let to;
      try { to = at.add(5).add(at.add(1).readS32()); } catch (e) { continue; }
      if (to.compare(lo) >= 0 && to.compare(hi) < 0) {
        out.intoErsc.push({ rva: '0x' + start.toString(16), to: '+0x' + to.sub(ersc.base).toString(16) });
      } else {
        // Arxan replaces entries with a jump into memory it decrypted itself, which belongs to no
        // module. Counted rather than listed, so the ersc list stays readable.
        out.stubbed += 1;
      }
    }
    return out;
  },
};
