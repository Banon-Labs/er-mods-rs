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
      // Section names are eight bytes, NUL-padded. `readUtf8String(8)` refuses to decode the
      // padding, so the name is assembled a byte at a time and stops at the first NUL.
      name: (() => { let n = ''; for (let b = 0; b < 8; b++) {
        const c = s.add(b).readU8(); if (c === 0) break; n += String.fromCharCode(c); } return n; })(),
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
    const out = { entries, intoErsc: [], viaTrampoline: [], indirect: 0, stubbed: 0 };
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
        // A jump into memory that belongs to no module is either an Arxan stub or a detour whose
        // trampoline was allocated rather than placed inside its own module -- which is what
        // MinHook and most hooking libraries do. They are told apart by following the jump: a
        // trampoline hands control onward to the hooking module within a hop or two, an Arxan
        // stub does not.
        out.stubbed += 1;
        let cursor = to;
        for (let hop = 0; hop < 4; hop++) {
          let head;
          try { head = cursor.readU8(); } catch (e) { break; }
          if (head !== JMP_REL32) break;
          let next;
          try { next = cursor.add(5).add(cursor.add(1).readS32()); } catch (e) { break; }
          if (next.compare(lo) >= 0 && next.compare(hi) < 0) {
            out.viaTrampoline.push({ rva: '0x' + start.toString(16),
                                     to: '+0x' + next.sub(ersc.base).toString(16), hops: hop + 1 });
            break;
          }
          cursor = next;
        }
      }
    }
    return out;
  },
};
