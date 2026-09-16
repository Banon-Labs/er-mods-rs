// Which row of a two-option popup is highlighted RIGHT NOW, before anything is confirmed.
//
// # Where it lives
//
// `FUN_1407ee550(owner, &result, &row)` -- the function the goods dialog reads its answer out of --
// does not compute either value.  It calls two getters, and both read the same object:
//
//   FUN_14077bc30:  popupState = [CSMenuMan + 0x80];  return *(u32*)(popupState + 0x1a0)   result
//   FUN_14077bc60:  popupState = [CSMenuMan + 0x80];  return *(u32*)(popupState + 0x1a4)   row
//
// with `CSMenuMan` at `0x143d6f820` (1.17.0 deobf; below rva 0xafefe9, so also the live 1.17.1
// address).  Reading them directly is what turns a D-pad press from an open loop into a closed one:
// press, read the row back, and only confirm once it is the row that was aimed at.
//
// The two rows of the invasion-bounds prompt are laid out LEFT/RIGHT -- D-pad down does nothing on
// it -- so the press to move is `0x0008`, and the proof it moved is this reading, not the press.
const CS_MENU_MAN = ptr('0x143d6f820');
const POPUP_STATE = 0x80;
const POPUP_RESULT = 0x1a0;
const POPUP_ROW = 0x1a4;

function state () {
  const man = CS_MENU_MAN.readPointer();
  if (man.isNull()) return null;
  const st = man.add(POPUP_STATE).readPointer();
  return st.isNull() ? null : st;
}

rpc.exports = {
  row () {
    const st = state();
    if (st === null) return { ok: false, why: 'CSMenuMan or its popup state is null' };
    return {
      ok: true,
      result: st.add(POPUP_RESULT).readS32(),
      row: st.add(POPUP_ROW).readS32(),
    };
  },
  // A window around the two known fields, for finding anything else that tracks the cursor.
  window (size) {
    const st = state();
    if (st === null) return null;
    const words = [];
    const count = (size || 0x80) / 4;
    for (let i = 0; i < count; i++) {
      try { words.push(st.add(POPUP_RESULT - 0x40 + i * 4).readU32()); } catch (e) { words.push(null); }
    }
    return { base: '0x' + (POPUP_RESULT - 0x40).toString(16), words: words };
  },
};
console.log('popup-row-oracle: ready (reads the live highlighted row; presses nothing)');
