// Great Jar trial (north Caelid, m60_47_41_00) event flags on ER 1.17.1: read them, and clear the
// ones that stop the three Knight of the Great Jar summon signs from being placed again.
//
// Where the ids come from (bd great-jar-sign-flags-1171-2026-10-06): m60_47_41_00.emevd event
// 1047412300 places the three red signs (2003[25] PlaceNPCSummonSign) for each knight that is
// alive, but first ends if 1047419201 is set; event 1047412301 sets 1047419201 once 1047419200 is
// set and all three knights are dead. 1047419200 is set by the Great Jar's talk (t221006000).
//
// Paste into an agent, then call GJ.read() / GJ.reset(). The signs only come back after a map
// reload (rest at a grace), because the knights stay dead until then.
const GJ = (() => {
  const base = Process.getModuleByName('eldenring.exe').base;
  const EVENT_FLAG_MAN = base.add(0x3d6c4b8);          // CSEventFlagMan* global (1.16.2 0x143d68448)
  const GET_RVA = 0x5fa250, SET_RVA = 0x5d2f60;
  // Prologue checks, read out of eldenring-deobf-1.17.1.bin.
  const want = { [GET_RVA]: '448b411c448bda33d2', [SET_RVA]: '48895c240848897424' };
  for (const [rva, hex] of Object.entries(want)) {
    const got = Array.from(new Uint8Array(base.add(Number(rva)).readByteArray(hex.length / 2)))
      .map((b) => b.toString(16).padStart(2, '0')).join('');
    // An e9 detour over the first 5 bytes is accepted: live 1.17.1 GetEventFlag is already hooked.
    const ok = got === hex || (got.startsWith('e9') && got.slice(10) === hex.slice(10));
    if (!ok) throw new Error(`prologue mismatch at +0x${Number(rva).toString(16)}: ${got}`);
  }
  // int CSFD4VirtualMemoryFlag::GetEventFlag(CSEventFlagMan*, u32 flagId)
  const getFlag = new NativeFunction(base.add(GET_RVA), 'int', ['pointer', 'uint32']);
  // void CSFD4VirtualMemoryFlag::SetEventFlag(CSEventFlagMan*, u32* flagId, u8 state,
  //                                           EventFlagCaller* (unused), u8 netSync)
  const setFlag = new NativeFunction(base.add(SET_RVA), 'void',
    ['pointer', 'pointer', 'uint8', 'pointer', 'uint8']);
  const man = () => {
    const m = EVENT_FLAG_MAN.readPointer();
    if (m.isNull()) throw new Error('CSEventFlagMan null');
    return m;
  };
  const FLAGS = {
    trialAccepted: 1047419200,   // set by the Great Jar's talk; keep it set
    trialComplete: 1047419201,   // all three knights dead; ends the sign event at its top
    knightDataReady: 1047412350, // set after Copy Character Data; host skips the setup while set
    summoned1: 1047412220, summoned2: 1047412221, summoned3: 1047412222,      // sign summon flags
    dismissed1: 1047410230, dismissed2: 1047410231, dismissed3: 1047410232,   // sign dismissal flags
    arsenalObtained: 400470,     // ItemLotParam_map 104700 getItemFlagId (Great-Jar's Arsenal), read only
  };
  const CLEAR = ['trialComplete', 'knightDataReady', 'summoned1', 'summoned2', 'summoned3',
    'dismissed1', 'dismissed2', 'dismissed3'];
  function read() {
    const m = man(), out = {};
    for (const [k, id] of Object.entries(FLAGS)) out[k] = getFlag(m, id);
    return out;
  }
  function reset() {
    const m = man(), id = Memory.alloc(4), before = read();
    // netSync 0: no append to the manager's sync list from a foreign thread. With state 0 the call
    // reduces to the bit clear in SetEventFlagBit (0x1405faa40).
    for (const k of CLEAR) { id.writeU32(FLAGS[k]); setFlag(m, id, 0, NULL, 0); }
    return { before, after: read() };
  }
  return { read, reset };
})();
