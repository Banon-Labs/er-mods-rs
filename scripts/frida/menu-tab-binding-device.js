// Which device does the game ask about when it decides a menu tab switch?
//
// # The question
//
// `er-input-harness`'s `TabToQuit` phase derails on every run, and bd `er-effects-rs-9vyy` read
// the cause out of a `DumpMenuBindings` dump as "menu code `0x30` has no pad button bound". That
// reading came from `game_mem::menu_code_pad_binding`, which reads the row at `+0x0c`/`+0x10`.
//
// Those two dwords are the MOUSE pair. The row is five `s32`, and its first field is `padKeyId`:
// FromSoft's own paramdef says so (`KEY_ASSIGN_PARAM_ST`, field 1 `s32 padKeyId = -1`, enum
// `CS_PAD_KEY`), and so does bd `er-keybinding-table-cspckeyconfig-1162-2026-08-25`. So the dump
// reported `tab_left`'s mouse binding under the name `pad`, and `tab_left` has no mouse binding --
// which is why it read as unbound. Under the right labels that same log line says `padKeyId=3000`
// and `keyboardKeyId=115`.
//
// That correction is static. What it does not say is where an injection has to land, and this
// agent is for that half.
//
// # What it hooks, and what each answer means
//
// * The two binding tables, read once. `cfg+0x008` is the default loaded from
//   `KeyAssignParam_TypeA`; `cfg+0x440` is what the player is playing with. Dumping both for the
//   four menu codes the drive cares about confirms the column order against a live process rather
//   than against two disagreeing tools in this repo, and says whether this player's rows have
//   drifted from the defaults the way action `0x0f` had on 2026-09-19.
// * `GetAssign` (`0x242ab0`), whose fourth argument is the device mode `FUN_140242b00` selects on:
//   mode 0 takes the single `padKeyId`, mode 1 the keyboard pair, mode 2 the mouse pair. A call
//   carrying `idx=0x30` names the device the tab query actually resolves through. If it never
//   fires while the menu is up, the per-frame path reads a table built ahead of time -- `0x243200`
//   rebuilds the `FD4PadManager` bindings after a change -- and the injection target is the device
//   state, not this accessor.
// * The page-left and page-right predicates. They are what `GridControl::Update` consults, and
//   each builds a `std::function<bool(CS::CSEzMenuViewerPad const&)>` over `{code, kind}` =
//   `{0x30, 2}` / `{0x31, 2}`. Counting them separates "the chain never ran" from "the chain ran
//   and the predicate said no", which a log of our own writes cannot do.
//
// Counts, not a stream. These fire every frame a menu is open, and a `send()` per call is a
// firehose that gets the watcher rate-limited.
//
// # Addresses
//
// 1.16.2 `0x7586d0` / `0x758c90` / `0x242ab0`, carried to 1.17.0 by
// `scripts/map-rvas-1162-to-1170.py` as `0x759520` / `0x759ae0` / `0x242ab0` (the accessor does not
// move; its 43-byte signature is unique). All three sit below `0xafefe9`, so the 1.17.0 -> 1.17.1
// step leaves them where they are (`docs/er-1.17-migration.md`). The singleton rva `0x3d61f08` is
// the mapped 1.17 value recorded at `docs/recon/rva-map-1162-to-1170.data.tsv:87`, agreed by 82
// references.

'use strict';

const PAGE_LEFT_PREDICATE = ptr('0x140759520');
const PAGE_RIGHT_PREDICATE = ptr('0x140759ae0');
const GET_ASSIGN = ptr('0x140242ab0');

const KEY_CONFIG_SINGLETON_RVA = 0x3d61f08;
const DEFAULT_TABLE_OFFSET = 0x008;
const CURRENT_TABLE_OFFSET = 0x440;
const ROW_STRIDE = 0x14;
const ROW_COUNT = 0x36;

// The runtime row, which is not the paramdef's field order: the paramdef lists each device's
// modifier before its key, and the runtime struct lists the key first. Reading a live row in
// paramdef order puts `193` in a `CS_MODIFIER_KEY` field whose documented bits stop at bit 5.
const FIELD_NAMES = ['padKeyId', 'keyboardKeyId', 'keyboardModify', 'mouseKeyId', 'mouseModify'];
// `-1`. Not `0`: `0` is a real id, and reading it as absent is how a bound action reports unbound.
const UNBOUND = -1;

const MENU_CODES = [
  { name: 'list_down', code: 0x2c },
  { name: 'list_up', code: 0x2d },
  { name: 'tab_left', code: 0x30 },
  { name: 'tab_right', code: 0x31 }
];

// About 28 percent of function entries on this build open with an Arxan healing stub, and a hook
// on the stub never fires. A followed address that leaves the module is a stub caught mid-heal,
// not a target -- on 2026-09-20 one resolved below the image base and was hooked anyway, tallying
// thousands of calls to something that was not the function asked for.
const MODULE = Process.getModuleByName('eldenring.exe');
const MODULE_START = MODULE.base;
const MODULE_END = MODULE.base.add(MODULE.size);

function follow (address) {
  if (address.readU8() !== 0xe9) return address;
  const target = address.add(5).add(address.add(1).readS32());
  if (target.compare(MODULE_START) < 0 || target.compare(MODULE_END) >= 0) {
    send({
      kind: 'stub-unresolved',
      line: 'the entry at ' + address + ' opens `e9` but its target ' + target +
        ' is outside eldenring.exe; hooking the entry itself'
    });
    return address;
  }
  return target;
}

function keyConfig () {
  const slot = MODULE_START.add(KEY_CONFIG_SINGLETON_RVA);
  if (slot.compare(MODULE_START) < 0 || slot.compare(MODULE_END) >= 0) return null;
  let value;
  try {
    value = slot.readPointer();
  } catch (error) {
    return null;
  }
  // Null until the game initialises its key config. That is "not checked yet", never "nothing
  // bound", and a reader that conflates them reports an absence it cannot see.
  return value.isNull() ? null : value;
}

function readRow (config, tableOffset, code) {
  const entry = config.add(tableOffset + ROW_STRIDE * code);
  const row = {};
  for (let index = 0; index < FIELD_NAMES.length; index += 1) {
    row[FIELD_NAMES[index]] = entry.add(index * 4).readS32();
  }
  return row;
}

function describeRow (row) {
  return FIELD_NAMES
    .map(function (name) {
      return name + '=' + (row[name] === UNBOUND ? 'unbound' : row[name]);
    })
    .join(' ');
}

function dumpBindings () {
  const config = keyConfig();
  if (config === null) {
    send({ kind: 'keyconfig', line: 'CSPcKeyConfig is null -- the key config is not up yet' });
    return false;
  }
  const rows = [];
  MENU_CODES.forEach(function (entry) {
    const current = readRow(config, CURRENT_TABLE_OFFSET, entry.code);
    const fallback = readRow(config, DEFAULT_TABLE_OFFSET, entry.code);
    const drifted = FIELD_NAMES.some(function (name) {
      return current[name] !== fallback[name];
    });
    rows.push({
      name: entry.name,
      code: entry.code,
      current: current,
      default: fallback,
      drifted: drifted
    });
    send({
      kind: 'binding',
      line: entry.name + ' code=0x' + entry.code.toString(16) +
        ' current[' + describeRow(current) + ']' +
        ' default[' + describeRow(fallback) + ']' +
        (drifted ? ' DRIFTED-from-default' : '')
    });
  });
  send({ kind: 'bindings', config: config.toString(), rows: rows });
  return true;
}

// Per (action, mode) tallies rather than a line per call.
const assignCalls = {};
let assignTotal = 0;
let pageLeftCalls = 0;
let pageRightCalls = 0;

function armGetAssign () {
  Interceptor.attach(follow(GET_ASSIGN), {
    onEnter: function (args) {
      assignTotal += 1;
      const action = args[2].toInt32() & 0xffffffff;
      if (action < 0 || action >= ROW_COUNT) return;
      const mode = args[3].toInt32();
      const key = '0x' + action.toString(16) + '/mode' + mode;
      assignCalls[key] = (assignCalls[key] || 0) + 1;
    }
  });
}

// A time series rather than a total, clocked on the game's own pumping and not on wall time.
// `scripts/check-no-timeouts.py` bans `setInterval`, and the reason applies here: a wall clock
// keeps printing while the game is frozen, and identical lines arriving on schedule read as
// progress. The page predicate is the right clock because it runs for every grid whenever a menu
// is open, so silence from this line means the menus closed, which is itself worth knowing.
const TALLY_EVERY_PAGE_CALLS = 300;
let talliedAt = 0;

function emitTally () {
  // The modes seen for the tab codes are the whole point, so they are named rather than folded
  // into an anonymous total.
  const interesting = Object.keys(assignCalls)
    .filter(function (key) {
      return key.indexOf('0x30/') === 0 || key.indexOf('0x31/') === 0;
    })
    .map(function (key) {
      return key + '=' + assignCalls[key];
    });
  send({
    kind: 'tally',
    line: 'get_assign_total=' + assignTotal +
      ' page_left=' + pageLeftCalls +
      ' page_right=' + pageRightCalls +
      ' tab_assign_modes=[' + (interesting.length ? interesting.join(' ') : 'none yet') + ']',
    getAssignTotal: assignTotal,
    getAssignByActionMode: assignCalls,
    pageLeftCalls: pageLeftCalls,
    pageRightCalls: pageRightCalls
  });
}

function onPageCall () {
  const total = pageLeftCalls + pageRightCalls;
  if (total - talliedAt < TALLY_EVERY_PAGE_CALLS) return;
  talliedAt = total;
  // The key config is null until the game initialises it, so a watcher attached at boot gets its
  // binding dump on the first tally after a menu exists rather than never.
  if (!bindingsDumped) bindingsDumped = dumpBindings();
  emitTally();
}

function armPredicates () {
  Interceptor.attach(follow(PAGE_LEFT_PREDICATE), {
    onEnter: function () {
      pageLeftCalls += 1;
      onPageCall();
    }
  });
  Interceptor.attach(follow(PAGE_RIGHT_PREDICATE), {
    onEnter: function () {
      pageRightCalls += 1;
      onPageCall();
    }
  });
}

let bindingsDumped = dumpBindings();
armGetAssign();
armPredicates();

send({
  kind: 'armed',
  line: 'watching GetAssign at ' + follow(GET_ASSIGN) +
    ', page-left at ' + follow(PAGE_LEFT_PREDICATE) +
    ', page-right at ' + follow(PAGE_RIGHT_PREDICATE) +
    '; bindings ' + (bindingsDumped ? 'read' : 'pending -- the key config was not up yet')
});
