// Where every thread is standing, so "ersc is quiet" and "ersc is wedged" stop looking alike.
//
// `find-real-session.js` armed a counter on `ersc+0xf9828` (`_Mtx_lock`) and it printed nothing in
// 28 seconds, in a process where the same hook had previously counted 600 calls. Silence from a
// hook is not evidence on its own -- an attach that quietly failed reads the same way. A thread
// list does not have that ambiguity: it says which instruction each thread is stopped on, and a
// worker parked inside the lock is a fact rather than an inference from an absence.
const ersc = Process.findModuleByName('ersc.dll');
const kernelbase = Process.findModuleByName('KERNELBASE.dll');

function moduleOf(address) {
  const m = Process.findModuleByAddress(address);
  if (m === null) {
    return `${address} (no module)`;
  }
  return `${m.name}+0x${address.sub(m.base).toString(16)}`;
}

const threads = Process.enumerateThreads();
console.log(`parked: ${threads.length} thread(s)`);
let inErsc = 0;
let waiting = 0;
for (const t of threads) {
  const pc = t.context.pc;
  const where = moduleOf(pc);
  const isErsc =
    ersc !== null && pc.compare(ersc.base) >= 0 && pc.compare(ersc.base.add(ersc.size)) < 0;
  const isWait =
    kernelbase !== null &&
    pc.compare(kernelbase.base) >= 0 &&
    pc.compare(kernelbase.base.add(kernelbase.size)) < 0;
  if (isErsc) {
    inErsc += 1;
  }
  if (isWait) {
    waiting += 1;
  }
  // Only the interesting ones, or the list is 127 lines of game threads asleep in the scheduler.
  if (isErsc) {
    console.log(`parked: tid=${t.id} state=${t.state} pc=${where}  <-- inside ersc.dll`);
  }
}
console.log(`parked: ${inErsc} thread(s) stopped inside ersc.dll, ${waiting} inside KERNELBASE`);

// The candidate our DLL settled on this run, and the critical section underneath it.
//
// An `_Mtx_internal_imp_t` is `int _Type` at +0, a 40-byte CRITICAL_SECTION at +8, `long
// _Thread_id` at +0x48 and `int _Count` at +0x4c. A CRITICAL_SECTION whose OwningThread is set and
// whose LockCount says contended is a lock somebody is really holding; one full of unrelated bytes
// is the impostor the sweep keeps latching.
const CANDIDATE = ptr('0x1b8ea6c40');
try {
  const mutex = CANDIDATE.add(0x100);
  const cs = mutex.add(0x8);
  console.log(
    `parked: candidate ${CANDIDATE} state=0x${CANDIDATE.add(0x150).readU32().toString(16)} ` +
      `_Type=0x${mutex.readU32().toString(16)} _Thread_id=0x${mutex.add(0x48).readU32().toString(16)} ` +
      `_Count=${mutex.add(0x4c).readS32()}`
  );
  console.log(
    `parked: its CRITICAL_SECTION DebugInfo=${cs.readPointer()} ` +
      `LockCount=${cs.add(8).readS32()} RecursionCount=${cs.add(0xc).readS32()} ` +
      `OwningThread=${cs.add(0x10).readPointer()} LockSemaphore=${cs.add(0x18).readPointer()}`
  );
} catch (e) {
  console.log(`parked: candidate unreadable: ${e.message}`);
}
