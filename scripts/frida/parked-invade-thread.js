// Where the invade call is parked, and on what.
//
// `er-invasion-warp` drives `ersc+0x25850` on a worker thread of its own, and run
// br-20260916-025336-64fc shows that call never returning: the DLL logs `the previous invade call
// has not returned from ersc.dll yet` on every tick after it. That it does not return is measured.
// Why is not, and a guess about a Themida-virtualised function is worth nothing -- so read the
// thread.
//
// Two readings, both cheap and neither touching the game's state:
//
//   * the thread's own context and backtrace, which names the frame it is sitting in;
//   * the session mutex `_Thread_id` and `_Count`, which say whether it is parked on that lock and
//     who holds it. The DLL's own precondition line reported both as clear immediately before the
//     call, so if they are non-zero now the call took the lock and stopped inside.
const WORKER_NAME = 'er-invasion-warp invade';
const game = Process.findModuleByName('eldenring.exe');
const ersc = Process.findModuleByName('ersc.dll');
const ours = Process.findModuleByName('er_invasion_warp.dll');

function whereFrom(address) {
  const owner = Process.findModuleByAddress(address);
  return owner ? `${owner.name}+0x${address.sub(owner.base).toString(16)}` : `${address}`;
}

const threads = Process.enumerateThreads();
console.log(`parked: ${threads.length} thread(s); ersc.dll @${ersc === null ? 'absent' : ersc.base}`);

for (const thread of threads) {
  const pc = thread.context.pc;
  const owner = Process.findModuleByAddress(pc);
  const inOurs = ours !== null && owner !== null && owner.name === ours.name;
  const inErsc = ersc !== null && owner !== null && owner.name === ersc.name;
  // Only the threads that could be ours. Printing 120 idle game threads buries the one that is.
  if (!inOurs && !inErsc && thread.name !== WORKER_NAME) {
    continue;
  }
  console.log(
    `parked: tid=${thread.id} name=${thread.name || '<unnamed>'} state=${thread.state} ` +
      `pc=${whereFrom(pc)}`
  );
  try {
    const frames = Thread.backtrace(thread.context, Backtracer.FUZZY)
      .slice(0, 12)
      .map(whereFrom);
    console.log(`parked:   ${frames.join('\n parked:   <- ')}`);
  } catch (e) {
    console.log(`parked:   backtrace failed: ${e.message}`);
  }
}

// Which lock, and who holds it.
//
// The backtrace puts the worker at `ersc.dll+0x25876` -- 0x26 into the invade action at
// `ersc+0x25850`, immediately past the mutex acquire this repo already records at `ersc+0x25871` --
// then through `ersc.dll+0xf98c0` into a wait inside `ntdll`. So it is parked on a lock. The DLL's
// own precondition line read that mutex `_Thread_id=0x0 _Count=0x0` in the instruction before the
// call, so whoever holds it now took it afterwards, and its owner is the answer.
//
// `_Mtx_internal_imp_t` is `{ int _Type; ... void* _Thread_id at +0x48; int _Count at +0x4c }` in
// the layout this repo already reads, and the session's lives at `session+0x100`.
const SESSION = ptr('0x45da73c8');
const MUTEX_OFFSET = 0x100;
try {
  const mutex = SESSION.add(MUTEX_OFFSET);
  const type = mutex.readU32();
  const thread = mutex.add(0x48).readS32();
  const count = mutex.add(0x4c).readS32();
  console.log(
    `parked: session ${SESSION} mutex ${mutex} _Type=0x${type.toString(16)} ` +
      `_Thread_id=0x${(thread >>> 0).toString(16)} _Count=${count}`
  );
  const holder = Process.enumerateThreads().find((t) => t.id === thread);
  console.log(
    holder
      ? `parked: held by tid=${holder.id} name=${holder.name || '<unnamed>'} ` +
          `pc=${whereFrom(holder.context.pc)}`
      : thread === 0
        ? 'parked: held by no thread -- so the wait is on something else, not this mutex'
        : `parked: holder tid 0x${(thread >>> 0).toString(16)} is not a live thread`
  );
} catch (e) {
  console.log(`parked: session mutex unreadable: ${e.message}`);
}

// What is `ersc+0xf98c0`? Its opening bytes say whether it is a lock wrapper or something else.
if (ersc !== null) {
  const at = ersc.base.add(0xf98c0);
  const bytes = Array.from(new Uint8Array(at.readByteArray(16)), (b) =>
    b.toString(16).padStart(2, '0')
  ).join(' ');
  console.log(`parked: ersc+0xf98c0 @${at} opens ${bytes}`);
}

// What object is the wait predicated on?
//
// `ersc+0xf98c0` opens `eb 5b` into a loop that tests `qword [rdi]` and `dword [rdi+8]` -- a
// wait-until-predicate, not a mutex acquire, and the session's own mutex reads free while the
// worker sits here. So the invade action is waiting to be signalled by something, and `rdi` names
// it. Read the worker's own registers rather than guessing.
const worker = Process.enumerateThreads().find((t) => t.name === WORKER_NAME);
if (!worker) {
  console.log('parked: the invade worker is gone -- the call returned or the thread exited');
} else {
  const ctx = worker.context;
  for (const reg of ['rcx', 'rdx', 'rdi', 'rsi', 'rbx', 'rbp', 'r12', 'r13']) {
    const value = ctx[reg];
    if (value === undefined) {
      continue;
    }
    let head = 'unreadable';
    try {
      head = Array.from(new Uint8Array(value.readByteArray(16)), (b) =>
        b.toString(16).padStart(2, '0')
      ).join(' ');
    } catch (e) {
      head = `unreadable (${e.message.split(' ')[0]})`;
    }
    console.log(`parked: ${reg}=${value} (${whereFrom(value)}) -> ${head}`);
  }
}
