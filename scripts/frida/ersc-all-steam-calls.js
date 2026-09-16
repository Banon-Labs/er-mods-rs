// Every Steam interface `ersc.dll` holds, traced by slot, so its real dependency names itself.
//
// Seamless matchmaking dies when Steam goes offline, so Seamless plainly depends on Steam. The
// matchmaking trace nevertheless counted zero on all 38 slots during a live invasion, and the
// blindness explanation is now ruled out: `ersc+0x21b610` holds object 0x45dcbce0 with vtable
// 0x6ffffad05d78, which is the exact object and vtable that trace hooked. So the dependency runs
// through one of the other eight interfaces it keeps, and this watches all nine at once.
//
// The pointers are read from `ersc.dll`'s own data rather than resolved here, so each one is the
// interface Seamless itself was handed.
const HELD = [0x21b570, 0x21b590, 0x21b5b0, 0x21b5d0, 0x21b5f0, 0x21b610, 0x21b630, 0x21b650, 0x21b670];
const SLOTS_PER_IFACE = 30;

const ersc = Process.findModuleByName('ersc.dll');
const lsteam = Process.findModuleByName('lsteamclient.dll');
const counts = {};
let armed = 0;
let probe = null;

if (ersc !== null && lsteam !== null) {
  for (const held of HELD) {
    let obj;
    let vtable;
    try {
      obj = ersc.base.add(held).readPointer();
      vtable = obj.readPointer();
    } catch (e) {
      continue;
    }
    const tag = `ersc+0x${held.toString(16)}`;
    for (let i = 0; i < SLOTS_PER_IFACE; i++) {
      let fn;
      try {
        fn = vtable.add(i * Process.pointerSize).readPointer();
      } catch (e) {
        break;
      }
      if (fn.isNull() || Process.findModuleByAddress(fn) === null) {
        continue;
      }
      const name = `${tag}[${i}]`;
      counts[name] = 0;
      try {
        Interceptor.attach(fn, {
          onEnter() {
            counts[name] += 1;
            if (counts[name] <= 2) {
              console.log(`call: ${name} #${counts[name]}  (lsteamclient+0x${fn.sub(lsteam.base).toString(16)})`);
            }
          },
        });
        armed += 1;
        // The FIRST slot that arms anywhere, so the control cannot be lost to a slot-specific
        // condition. A previous version keyed it to one interface and one index and simply never
        // ran, leaving a 265-slot zero with nothing to say whether the hooks worked.
        if (probe === null) {
          probe = { fn, obj, name };
        }
      } catch (e) {
        // A slot that cannot be hooked is reported by its absence from the armed count.
      }
    }
  }
  console.log(`call: ${armed} slot(s) armed across ${HELD.length} interface(s) ersc.dll holds`);
  // Self-test, so a silent run is a finding rather than an unhooked agent.
  if (probe !== null) {
    try {
      // `this` only; no argument is read by a getter, and passing none cannot corrupt state.
      new NativeFunction(probe.fn, 'int', ['pointer'])(probe.obj);
      console.log(`call: SELF-TEST ${probe.name} counter now ${counts[probe.name]} (1 or more means these hooks fire)`);
    } catch (e) {
      console.log(`call: self-test could not run: ${e.message}`);
    }
  }
}
