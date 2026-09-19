// Which lsteamclient vtables does `ersc.dll` hold pointers to?
//
// The interface handout happens once, during `SteamAPI_Init`, minutes before a Frida attach is
// possible -- so catching it live is not an option and the stored pointer has to be found instead.
// Any Steam interface `ersc.dll` kept is a qword somewhere in its writable data whose target's
// first qword points into `lsteamclient.dll`. That is a two-hop shape random bytes do not produce
// often, and it needs no timing at all.
//
// The question it settles: the earlier trace hooked the vtable at 0x6ffffad05d78, resolved by this
// agent rather than by Seamless. If `ersc.dll` holds a DIFFERENT matchmaking vtable, those hooks
// were blind to Seamless by construction and the zeros they produced mean nothing.
const OURS = ptr('0x6ffffad05d78');
const ersc = Process.findModuleByName('ersc.dll');
const lsteam = Process.findModuleByName('lsteamclient.dll');
if (ersc === null || lsteam === null) {
  console.log('vt: ersc.dll or lsteamclient.dll is not loaded');
} else {
  const lo = lsteam.base;
  const hi = lsteam.base.add(lsteam.size);
  console.log(`vt: lsteamclient ${lo}..${hi}`);
  const found = new Map();
  for (const range of Process.enumerateRanges({ protection: 'rw-', coalesce: false })) {
    // Only ersc's own writable image, not the whole heap: a stored interface lives in its data.
    if (range.base.compare(ersc.base) < 0 || range.base.compare(ersc.base.add(ersc.size)) >= 0) {
      continue;
    }
    const words = range.size / Process.pointerSize;
    for (let i = 0; i < words; i++) {
      let obj;
      try {
        obj = range.base.add(i * Process.pointerSize).readPointer();
      } catch (e) {
        break;
      }
      if (obj.isNull() || obj.compare(ptr(0x10000)) < 0) {
        continue;
      }
      let vtable;
      try {
        vtable = obj.readPointer();
      } catch (e) {
        continue;
      }
      if (vtable.compare(lo) < 0 || vtable.compare(hi) >= 0) {
        continue;
      }
      const key = vtable.toString();
      if (!found.has(key)) {
        found.set(key, { vtable, obj, at: range.base.add(i * Process.pointerSize), count: 0 });
      }
      found.get(key).count += 1;
    }
  }
  console.log(`vt: ${found.size} distinct lsteamclient vtable(s) reachable from ersc.dll data`);
  for (const rec of found.values()) {
    const mark = rec.vtable.equals(OURS) ? '  <-- the vtable the earlier trace hooked' : '';
    console.log(
      `vt: ${rec.vtable} (lsteamclient+0x${rec.vtable.sub(lo).toString(16)}) ` +
        `obj=${rec.obj} held at ersc+0x${rec.at.sub(ersc.base).toString(16)} x${rec.count}${mark}`
    );
  }
}
