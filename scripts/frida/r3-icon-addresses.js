// Reads the two addresses er-r3-view's icon lookup uses, as the 1.16.2 -> 1.17.1 maps translate
// them, so the switch from hand-written 1.17.1 rvas to `game_rva` / `game_data_addr` rests on a
// measurement: the repository global (1.16.2 0x3d82510 -> 1.17 0x3d86580) must hold an object with
// a game vtable, and the lookup (1.16.2 0xd63e50 -> 1.17.0 0xd65b90 -> 1.17.1 0xd65c00) must start
// with the seven-push prologue read out of eldenring-deobf-1.17.1.bin. One read at load, no hooks.
'use strict';

const REPO_GLOBAL_RVA_1171 = 0x3d86580;
const LOOKUP_RVA_1171 = 0xd65c00;
const LOOKUP_ENTRY = '40 55 56 57 41 54 41 55 41 56 41 57 48 8d 6c 24 d9 48 81 ec a0 00 00 00';

const mod = Process.findModuleByName('eldenring.exe');
const inImage = (p) => !p.isNull() && p.compare(mod.base) >= 0 && p.compare(mod.base.add(mod.size)) < 0;

function rtti (vt) {
  try {
    const col = vt.sub(8).readPointer();
    if (!inImage(col) || col.readU32() !== 1) return null;
    const image = col.sub(col.add(0x14).readU32());
    return image.add(col.add(0x0c).readU32()).add(0x10).readCString();
  } catch (e) {
    return null;
  }
}

const repo = mod.base.add(REPO_GLOBAL_RVA_1171).readPointer();
const vt = repo.isNull() ? ptr(0) : repo.readPointer();
const bytes = Array.from(new Uint8Array(mod.base.add(LOOKUP_RVA_1171).readByteArray(24)))
  .map((b) => b.toString(16).padStart(2, '0')).join(' ');
send({
  repo: repo.toString(),
  repo_vtable_in_image: inImage(vt),
  repo_class: inImage(vt) ? rtti(vt) : null,
  lookup_entry: bytes,
  lookup_entry_matches: bytes === LOOKUP_ENTRY,
});
