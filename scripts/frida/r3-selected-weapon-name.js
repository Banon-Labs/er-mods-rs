// Which code asks for a weapon's name while the item list is open, and with which id, so the R3
// board can follow the highlighted weapon instead of always showing Misericorde.
//
// Measured 2026-10-03 with the first version of this agent: the detail panel names the highlighted
// weapon through `0x99a5a0(this, record)` (return address 0x99a607), which builds the name from
// `record+0x80`. This version hooks that function and reports, for each new weapon id:
//   - the RTTI of `this` and of `this+0x10` (the step's parts point at the dialog there);
//   - every u32 offset of `record` holding the id the name lookup is then asked for;
//   - every offset inside the dialog (`this+0x10`, 0x2000 bytes) holding `record` itself or the id.
//
// `MsgRepositoryImp::GetWeaponName(msg, u32 id)` is 1.16.2 0xd11370, 1.17.1 0xd12ab0 (first 20
// bytes identical in both images). No timers, no watchpoints.
'use strict';

const GET_WEAPON_NAME_RVA_1171 = 0xd12ab0;
const DETAIL_NAME_RVA_1171 = 0x99a5a0;
const DIALOG_SCAN = 0x2000;
const mod = Process.findModuleByName('eldenring.exe');
const inImage = (p) => !p.isNull() && p.compare(mod.base) >= 0 && p.compare(mod.base.add(mod.size)) < 0;

function rtti (obj) {
  try {
    const vt = obj.readPointer();
    if (!inImage(vt)) return null;
    const col = vt.sub(8).readPointer();
    if (!inImage(col) || col.readU32() !== 1) return null;
    const image = col.sub(col.add(0x14).readU32());
    return image.add(col.add(0x0c).readU32()).add(0x10).readCString();
  } catch (e) {
    return null;
  }
}

function offsetsOf (base, len, test) {
  const hits = [];
  for (let off = 0; off + 8 <= len; off += 4) {
    try {
      if (test(base.add(off))) hits.push('0x' + off.toString(16));
    } catch (e) {
      break;
    }
  }
  return hits;
}

const pending = new Map();
const seen = new Set();

Interceptor.attach(mod.base.add(DETAIL_NAME_RVA_1171), {
  onEnter (args) {
    const chain = Thread.backtrace(this.context, Backtracer.ACCURATE).slice(0, 8)
      .map((p) => inImage(p) ? '0x' + p.sub(mod.base).toString(16) : p.toString());
    pending.set(Process.getCurrentThreadId(), { self: args[0], record: args[1], chain });
  },
  onLeave () {
    pending.delete(Process.getCurrentThreadId());
  },
});

Interceptor.attach(mod.base.add(GET_WEAPON_NAME_RVA_1171), {
  onEnter (args) {
    const ctx = pending.get(Process.getCurrentThreadId());
    if (ctx === undefined) return;
    const id = args[1].toUInt32();
    if (seen.has(id)) return;
    seen.add(id);
    const dialog = ctx.self.add(0x10).readPointer();
    send({
      id,
      chain: ctx.chain,
      id_in_self: offsetsOf(ctx.self, 0x400, (p) => p.readU32() === id),
      self: ctx.self.toString(),
      self_class: rtti(ctx.self),
      dialog: dialog.toString(),
      dialog_class: rtti(dialog),
      record: ctx.record.toString(),
      record_class: rtti(ctx.record),
      id_in_record: offsetsOf(ctx.record, 0x100, (p) => p.readU32() === id),
      record_in_dialog: offsetsOf(dialog, DIALOG_SCAN, (p) => p.readPointer().equals(ctx.record)),
      id_in_dialog: offsetsOf(dialog, DIALOG_SCAN, (p) => p.readU32() === id),
    });
  },
});
send({ armed: ['0x' + DETAIL_NAME_RVA_1171.toString(16), '0x' + GET_WEAPON_NAME_RVA_1171.toString(16)] });
