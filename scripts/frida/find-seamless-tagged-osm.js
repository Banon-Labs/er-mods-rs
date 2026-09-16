// Find Seamless's option-menu object by the literal tag it carries, not by numeric resemblance.
//
// Six numeric signatures have now been beaten in a row, the last by an axis-aligned bounding box
// whose `CRITICAL_SECTION` read `DebugInfo=0x7f7fffee7f7fffee` (run br-20260916-040126-e719). Four
// small integers at known offsets cannot survive being tested against 2^18 candidate qwords, and
// no further narrowing changes that.
//
// The eight bytes `seamless` can. `crate::local_invasion_filter::ersc` already records the tag at
// `OSM_TAG_OFFSET = 0x68` and the session at `NEXT_OBJECT_OFFSET = 0x58`, and `osm_tag_matches`
// already tests it -- the sweep just never required it. This measures whether requiring it would
// find anything at all.
const TAG = 'seamless';
const OSM_TAG_OFFSET = 0x68;
const OSM_SESSION_OFFSET = 0x58;
const SESSION_MUTEX_OFFSET = 0x100;
const SESSION_STATE_OFFSET = 0x150;

const pattern = TAG.split('')
  .map((c) => c.charCodeAt(0).toString(16).padStart(2, '0'))
  .join(' ');

const ranges = Process.enumerateRanges({ protection: 'rw-', coalesce: true }).filter(
  (r) => r.size > 0x1000 && r.size < 0x8000000
);
console.log(`osm: scanning ${ranges.length} writable range(s) for "${TAG}"`);

let hits = 0;
let tagged = 0;
for (const range of ranges) {
  if (tagged >= 16) {
    break;
  }
  let found = [];
  try {
    found = Memory.scanSync(range.base, range.size, pattern);
  } catch (e) {
    continue;
  }
  for (const hit of found) {
    hits += 1;
    if (tagged >= 16) {
      break;
    }
    // The tag sits at +0x68 of the object, so the object starts 0x68 below the match.
    const osm = hit.address.sub(OSM_TAG_OFFSET);
    let session = null;
    try {
      session = osm.add(OSM_SESSION_OFFSET).readPointer();
    } catch (e) {
      continue;
    }
    if (session.isNull()) {
      continue;
    }
    let line = `osm: ${osm} tag@+0x68 -> session ${session}`;
    try {
      const mutex = session.add(SESSION_MUTEX_OFFSET);
      const cs = mutex.add(0x8);
      line +=
        ` state=0x${session.add(SESSION_STATE_OFFSET).readU32().toString(16)}` +
        ` _Type=0x${mutex.readU32().toString(16)}` +
        ` LockCount=${cs.add(8).readS32()} RecursionCount=${cs.add(0xc).readS32()}` +
        ` OwningThread=${cs.add(0x10).readPointer()}`;
    } catch (e) {
      line += ' (session unreadable)';
    }
    tagged += 1;
    console.log(line);
  }
}
console.log(`osm: ${hits} occurrence(s) of the tag, ${tagged} with a readable pointer at +0x58`);
