// Read the session pointer out of `ersc.dll`'s own global, instead of recognising one by shape.
//
// The shape test has now been narrowed five times and lost again: run br-20260916-040126-e719
// latched `0x1b8ea6c40`, whose `CRITICAL_SECTION` reads `DebugInfo=0x7f7fffee7f7fffee` and
// `OwningThread=0xff7fffeeff7fffee` -- a buffer of floats near `FLT_MAX`, not a lock. Float noise
// satisfies any bit predicate eventually, so every further narrowing buys one run.
//
// A pointer ersc stores is not a resemblance. `ersc+0xc64c88` was identified as the slot it keeps
// the session in; that address is inside the Themida-packed `ERSC` section, which stops a detour
// from being written there but does not stop a read.
const SLOT_RVA = 0xc64c88;
const SESSION_STATE_OFFSET = 0x150;
const SESSION_GUARD_OFFSET = 0x14c;
const SESSION_MUTEX_OFFSET = 0x100;

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  console.log('slot: ersc.dll is not loaded');
} else {
  console.log(`slot: ersc.dll @${ersc.base} size=0x${ersc.size.toString(16)}`);
}

function describe(session, why) {
  try {
    const mutex = session.add(SESSION_MUTEX_OFFSET);
    const cs = mutex.add(0x8);
    console.log(
      `slot: ${why} ${session} state=0x${session.add(SESSION_STATE_OFFSET).readU32().toString(16)} ` +
        `guard=0x${session.add(SESSION_GUARD_OFFSET).readU32().toString(16)} ` +
        `_Type=0x${mutex.readU32().toString(16)} ` +
        `_Thread_id=0x${mutex.add(0x48).readU32().toString(16)} _Count=${mutex.add(0x4c).readS32()}`
    );
    console.log(
      `slot:   CS LockCount=${cs.add(8).readS32()} RecursionCount=${cs.add(0xc).readS32()} ` +
        `OwningThread=${cs.add(0x10).readPointer()}`
    );
  } catch (e) {
    console.log(`slot: ${why} ${session} unreadable: ${e.message}`);
  }
}

if (ersc !== null) {
  const slot = ersc.base.add(SLOT_RVA);
  try {
    const value = slot.readPointer();
    console.log(`slot: ersc+0x${SLOT_RVA.toString(16)} = ${value}`);
    if (!value.isNull()) {
      describe(value, 'as a session,');
      // The invade action takes an owner, not a session: `mov rdi,[rcx+0x58]`. If the slot is the
      // owner rather than the session, `+0x58` is where the session hides.
      try {
        const via58 = value.add(0x58).readPointer();
        console.log(`slot: (that)+0x58 = ${via58}`);
        if (!via58.isNull()) {
          describe(via58, 'as an owner ->');
        }
      } catch (e) {
        console.log(`slot: +0x58 unreadable: ${e.message}`);
      }
    }
  } catch (e) {
    console.log(`slot: ersc+0x${SLOT_RVA.toString(16)} unreadable: ${e.message}`);
  }
}
