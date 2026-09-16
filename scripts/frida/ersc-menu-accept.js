// Who calls Seamless's menu-accept handler, and what rows does its action list hold?
//
// # What the handler is
//
// `ersc.dll+0x82600` runs the selected row of a menu Seamless built, then tears the whole list
// down.  Read out of the decrypted image:
//
//   rbx  = [rcx+0x58]            the session
//   rsi  = rdx                   the dialog, whose selected row index is [rsi+0xb0c]
//   vector [rbx+0x108, rbx+0x110) of 0x90-byte rows
//   row+0x88 -> callable, vslot 0x10 returns a bool; true means the row is done
//   row+0x48 -> callable, vslot 0x10 is the action -- this is the one that reached `+0x25850`
//
// Nothing in either module holds its address and no rel32 reaches it, so the caller lives in the
// Themida-virtualized region.  A hook is the only way to name it, and it names it exactly.
const HANDLER = 0x82600;
const SESSION_OWNER = 0x58;
const ROWS_BEGIN = 0x108;
const ROWS_END = 0x110;
const ROW_STRIDE = 0x90;
const ROW_ACTION = 0x48;
const ROW_PREDICATE = 0x88;
const DIALOG_SELECTED_ROW = 0xb0c;
const SESSION_STATE = 0x150;

const out = { calls: [] };
const ersc = Process.findModuleByName('ersc.dll');

function where (address) {
  const m = Process.findModuleByAddress(address);
  return m === null ? String(address) : m.name + '+0x' + address.sub(m.base).toString(16);
}

// A callable's identity is the function in its vtable slot 0x10, which is what actually runs.
function callable (slot) {
  try {
    if (slot.isNull()) return null;
    const vtable = slot.readPointer();
    return { object: String(slot), action: where(vtable.add(0x10).readPointer()) };
  } catch (e) { return { object: String(slot), action: 'unreadable' }; }
}

if (ersc !== null) {
  Interceptor.attach(ersc.base.add(HANDLER), {
    onEnter (args) {
      const record = { caller: where(this.returnAddress), rows: [] };
      try {
        const session = args[0].add(SESSION_OWNER).readPointer();
        record.session = String(session);
        record.state = '0x' + session.add(SESSION_STATE).readU32().toString(16);
        record.selectedRow = args[1].add(DIALOG_SELECTED_ROW).readU32();
        const begin = session.add(ROWS_BEGIN).readPointer();
        const end = session.add(ROWS_END).readPointer();
        const count = end.sub(begin).toInt32() / ROW_STRIDE;
        record.rowCount = count;
        for (let i = 0; i < Math.min(count, 16); i++) {
          const row = begin.add(i * ROW_STRIDE);
          record.rows.push({
            index: i,
            predicate: callable(row.add(ROW_PREDICATE).readPointer()),
            action: callable(row.add(ROW_ACTION).readPointer()),
          });
        }
      } catch (e) { record.error = String(e); }
      out.calls.push(record);
      send({ kind: 'menu-accept', record: record });
    },
  });
}

rpc.exports = { report: function () { return out; } };
console.log('ersc-menu-accept: armed' + (ersc === null ? ' (ersc.dll missing!)' : ''));
