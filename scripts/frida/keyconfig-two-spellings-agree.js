// Do the two spellings of the key-config singleton name the same object?
//
// # The disagreement
//
// `CS_PC_KEY_CONFIG_GLOBAL_RVA` is declared twice in this workspace with different values:
//
//   crates/er-invasion-warp/src/vanilla_invasion_items.rs   0x3d61f08   a 1.17 literal, added by hand
//   crates/er-input-harness/src/game_mem.rs                 0x3d5dea8   er_game_base's 1.16.2 constant
//
// The second is translated at use through `er_game_base::mem`; the first is not translated at all,
// because it is already spelled for the installed build. `scripts/audit-1170-readiness.py` reports
// the name as unresolvable for exactly this reason -- one name, two values, so neither wins -- and
// it is right to: a constant that means two addresses is a constant nobody can check.
//
// The fix is to delete the local literal and take the shared one through the gate. That is only
// safe if both spellings land on the same object on the running build, which is a claim about the
// live process, not about the two files.
//
// # What this reads
//
// Both addresses, in one pass, plus the first row of each one's current binding table. Two
// pointers being equal would be suggestive on its own; two pointers being equal AND handing back
// the same table contents is the thing the edit actually depends on.
//
// `cfg + 0x440` is the current table, `0x36` rows of `0x14`, from the accessor at rva 0x242ab0:
// `cmp r8d,0x35; ja fail; lea rcx,[rcx + idx*0x14 + 0x440]`.
//
// Read-only: two pointer loads and two small reads behind them. Nothing is written, no hook is
// installed and no watchpoint is armed.
'use strict';

const MODULE = 'eldenring.exe';

// The literal `er-invasion-warp` carries, already spelled for 1.17.
const LOCAL_LITERAL_RVA = 0x3d61f08;

// `er_game_base::rva::CS_PC_KEY_CONFIG_SINGLETON_RVA`, a 1.16.2 rva, and what
// `docs/recon/rva-map-1162-to-1170.data.tsv` carries it to at 82 of 82 references.
const SHARED_CONSTANT_RVA = 0x3d5dea8;
const SHARED_CONSTANT_MAPPED_RVA = 0x3d61f08;

const CURRENT_TABLE_OFFSET = 0x440;
const ROW_STRIDE = 0x14;

// Switch right armament, the row this session has been reading all along.
const WATCHED_ACTION = 0x0f;

const game = Process.findModuleByName(MODULE);
if (game === null) {
  send({ tag: 'fatal', reason: MODULE + ' not loaded' });
} else {
  function load(rva) {
    try {
      const value = game.base.add(rva).readPointer();
      return value.isNull() ? null : value;
    } catch (error) {
      return null;
    }
  }

  // The row as the game's own accessor would hand it back, so "same pointer" is backed by "same
  // contents" rather than standing alone.
  function row(cfg) {
    if (cfg === null) return null;
    try {
      const at = cfg.add(CURRENT_TABLE_OFFSET + WATCHED_ACTION * ROW_STRIDE);
      return {
        pad: at.readS32(),
        keyboard: at.add(4).readS32(),
        mouse: at.add(12).readS32(),
      };
    } catch (error) {
      return null;
    }
  }

  const local = load(LOCAL_LITERAL_RVA);
  const untranslated = load(SHARED_CONSTANT_RVA);
  const translated = load(SHARED_CONSTANT_MAPPED_RVA);

  const localRow = row(local);
  const translatedRow = row(translated);

  send({
    tag: 'keyconfig-spellings',
    module: MODULE,
    base: game.base.toString(),
    // What `er-invasion-warp` reads today.
    local_literal: { rva: '0x' + LOCAL_LITERAL_RVA.toString(16), cfg: local === null ? null : local.toString(), row: localRow },
    // What the shared constant would read if nothing translated it -- the failure mode being
    // argued about, measured rather than asserted.
    shared_untranslated: { rva: '0x' + SHARED_CONSTANT_RVA.toString(16), cfg: untranslated === null ? null : untranslated.toString() },
    // What the shared constant reads once `er_game_base::mem` carries it to this build.
    shared_translated: { rva: '0x' + SHARED_CONSTANT_MAPPED_RVA.toString(16), cfg: translated === null ? null : translated.toString(), row: translatedRow },
    same_object: local !== null && translated !== null && local.equals(translated),
    same_row:
      localRow !== null &&
      translatedRow !== null &&
      localRow.pad === translatedRow.pad &&
      localRow.keyboard === translatedRow.keyboard &&
      localRow.mouse === translatedRow.mouse,
  });
}
