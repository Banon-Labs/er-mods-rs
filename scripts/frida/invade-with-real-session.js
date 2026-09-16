// Call Seamless's invade action with a session ersc itself handed us, and report what happens.
//
// The action is four instructions of work: require `[session+0x150] == 1`, lock `session+0x100`,
// set the state to `0x0e`, tail-jump to unlock. Our DLL's worker has been parked inside that lock
// since it called it, with `_Thread_id` and `_Count` both reading zero -- so either the object is
// not a live mutex, or the lock underneath is held by something that never released it.
//
// This does not write any field. It calls the game's own action and reads the state the action is
// supposed to set, which is the only evidence that the native owner advanced rather than that we
// pushed it.
const INVADE_RVA = 0x25850;
const SESSION_STATE_OFFSET = 0x150;
const SESSION_MUTEX_OFFSET = 0x100;
const OWNER_SESSION_OFFSET = 0x58;

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  console.log('invade: ersc.dll not loaded');
} else {
  // The session this run's heartbeat reports, read out of `ersc_session=` rather than guessed.
  const session = ptr('0x952a1350');
  const readState = () => {
    try {
      return `0x${session.add(SESSION_STATE_OFFSET).readU32().toString(16)}`;
    } catch (e) {
      return 'unreadable';
    }
  };
  const mutex = session.add(SESSION_MUTEX_OFFSET);
  console.log(
    `invade: before -- state=${readState()} mutex _Type=0x${mutex.readU32().toString(16)} ` +
      `_Thread_id=0x${mutex.add(0x48).readU32().toString(16)} _Count=${mutex.add(0x4c).readS32()}`
  );
  // A shim carrying the session where the action reads it, exactly as the DLL does. Allocated by
  // Frida and kept alive by this reference for as long as the agent is loaded.
  const shim = Memory.alloc(0x60);
  shim.add(OWNER_SESSION_OFFSET).writePointer(session);
  const invade = new NativeFunction(ersc.base.add(INVADE_RVA), 'void', [
    'pointer',
    'int',
    'int',
    'int',
  ]);
  console.log(`invade: calling ersc+0x${INVADE_RVA.toString(16)} with owner ${shim} -> session ${session}`);
  invade(shim, 0, 1, 1);
  console.log(`invade: RETURNED -- state=${readState()}`);
}
