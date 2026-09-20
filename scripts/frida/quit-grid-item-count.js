// How wide does the System > Quit grid actually get, and who widened it?
//
// # The decision this measures
//
// `er_quit_menu_core::arm::arm_standalone` serves `GfxServeSet::ALL_PICKER_KEYED` for every
// standalone shell, and that set carries `quit_grid: true` -- the derived six-cell
// `02_040_optionsetting` panel. Vanilla's Quit tab has two cells. A shell that clones rows needs
// the six; a shell that clones none and instead replaces the native first row gets a widened grid
// with four empty cells, which is bd `slim-quickload-still-widened-the-quit-grid-2026-09-12`.
//
// Merging the three row shells into one whose row set comes from a config file means that serve
// set has to be derived from the rows rather than fixed, so the count this agent reads is the fact
// the derivation has to keep right.
//
// # What it reads
//
// `CS::GridControl::SetItemCount` is the single writer of the widget's item count (`+0xd0` of the
// grid, `dialog + 0xb08` for a `PropertyEditDialog`), and `rows.rs` already records that. Each
// call is reported with the grid it wrote and the count it wrote, deduplicated per pair so a
// per-frame rebuild does not flood the stream.
//
// `CS::MatchingDialog::AddCancelButton` is the prologue the row cloner hangs off, so an entry into
// it says a dialog with a cancel row is being built -- which is the moment before the cloner
// appends. Reported separately, and its call ordinal is what lets a count be read as "the Quit tab
// rebuilding" rather than some other list in the same frame.
//
// # Addresses
//
// 1.16.2 `0x738dc0` / `0x920c90`, carried to 1.17.0 through
// `docs/recon/rva-map-1162-to-1170.verified.tsv` as `0x739c10` (byte-identical, 171 instructions)
// and `0x921e30` (identical-whole, 104). Both sit below `0xafefe9`, so the 1.17.0 -> 1.17.1 step
// leaves them where they are (`docs/er-1.17-migration.md`).

'use strict';

const GRID_CONTROL_SET_ITEM_COUNT = ptr('0x140739c10');
const ADD_CANCEL_BUTTON = ptr('0x140921e30');

// About 28 percent of function entries on this build open with an Arxan healing stub, and a hook
// on the stub never fires.
function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

const seen = new Set();
let setItemCountCalls = 0;
let addCancelButtonCalls = 0;

Interceptor.attach(follow(GRID_CONTROL_SET_ITEM_COUNT), {
  onEnter (args) {
    setItemCountCalls += 1;
    const grid = args[0];
    const count = args[1].toInt32();
    const key = grid.toString() + ':' + count;
    if (seen.has(key)) return;
    seen.add(key);
    send({
      kind: 'set-item-count',
      line: 'GridControl::SetItemCount grid=' + grid +
        ' count=' + count +
        ' call=' + setItemCountCalls +
        ' after_add_cancel_button=' + addCancelButtonCalls
    });
  }
});

Interceptor.attach(follow(ADD_CANCEL_BUTTON), {
  onEnter (args) {
    addCancelButtonCalls += 1;
    // Every one, not a sample: the cloner appends on this call and the interesting question is
    // which of them the widened grid follows.
    send({
      kind: 'add-cancel-button',
      line: 'MatchingDialog::AddCancelButton dialog=' + args[0] +
        ' call=' + addCancelButtonCalls
    });
  }
});

send({
  kind: 'armed',
  line: 'watching GridControl::SetItemCount at ' + follow(GRID_CONTROL_SET_ITEM_COUNT) +
    ' and MatchingDialog::AddCancelButton at ' + follow(ADD_CANCEL_BUTTON)
});
