// Find real `_Mtx_internal_imp_t` objects by their `_Type` word, and ask what sits 0x100 below.
//
// Two readings now disagree and one of them has to be wrong. `ersc+0xc64c88` holds `0x72cffd20`,
// which is the owner our DLL drives with, and `owner+0x58` is `0x1b8ea6c40` -- exactly what
// `ersc+0x25850` computes (`mov rdi,[rcx+0x58]`). But that object's mutex at `+0x100` reads
// `DebugInfo=0x7f7fffee7f7fffee` / `OwningThread=0xff7fffeeff7fffee`: the `±FLT_MAX` pair an
// axis-aligned bounding box is initialised to, not a `CRITICAL_SECTION`.
//
// So stop reasoning about which read is right and go find the real thing. A live session's mutex
// read `_Type=0x100c7` on br-20260916-030928-9e37 and `0x100003` elsewhere; both are `_Mtx_recursive`
// with small flags, and neither pattern occurs in float noise. Whatever this finds, `addr-0x100` is
// a session candidate by construction.
const TYPES = [0x100c7, 0x100003];
const SESSION_MUTEX_OFFSET = 0x100;
const SESSION_STATE_OFFSET = 0x150;

function pattern(value) {
  const bytes = [];
  for (let i = 0; i < 4; i += 1) {
    bytes.push(((value >>> (i * 8)) & 0xff).toString(16).padStart(2, '0'));
  }
  return bytes.join(' ');
}

// Only writable, committed, non-huge ranges: a mutex lives in the heap or in a module's data, and
// scanning the whole address space costs minutes for nothing.
const ranges = Process.enumerateRanges({ protection: 'rw-', coalesce: true }).filter(
  (r) => r.size > 0x1000 && r.size < 0x4000000
);
console.log(`mtx: scanning ${ranges.length} writable range(s)`);

let found = 0;
for (const type of TYPES) {
  const pat = pattern(type);
  for (const range of ranges) {
    if (found >= 40) {
      break;
    }
    let hits = [];
    try {
      hits = Memory.scanSync(range.base, range.size, pat);
    } catch (e) {
      continue;
    }
    for (const hit of hits) {
      if (found >= 40) {
        break;
      }
      const mutex = hit.address;
      // A `_Mtx_internal_imp_t` is 8-byte aligned and its `CRITICAL_SECTION` follows at +8.
      if (!mutex.and(ptr(7)).isNull()) {
        continue;
      }
      const session = mutex.sub(SESSION_MUTEX_OFFSET);
      let state = null;
      let threadId = null;
      let count = null;
      let owning = null;
      try {
        state = session.add(SESSION_STATE_OFFSET).readU32();
        threadId = mutex.add(0x48).readU32();
        count = mutex.add(0x4c).readS32();
        owning = mutex.add(0x8 + 0x10).readPointer();
      } catch (e) {
        continue;
      }
      found += 1;
      console.log(
        `mtx: ${mutex} _Type=0x${type.toString(16)} _Thread_id=0x${threadId.toString(16)} ` +
          `_Count=${count} OwningThread=${owning} -> session ${session} +0x150=0x${state.toString(16)}`
      );
    }
  }
}
console.log(`mtx: ${found} candidate mutex(es)`);
