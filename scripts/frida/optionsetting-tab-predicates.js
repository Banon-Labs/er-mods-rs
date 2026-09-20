// Why does an injected tab-left never reach the OptionSetting pager?
//
// # The question
//
// `er-input-harness`'s `TabToQuit` phase writes `inputmgr+0x90+0x30` (menu-event tab-left) and
// waits for our cloned **Load Build from URL** row to become walkable, which only happens once the
// Quit pane is up. Measured 2026-09-20 on run br-20260920-163247-834b: 480 frames, the pause menu
// open the whole time, and the pane never came up.
//
// `drive.rs` already says why, statically. `_SettingTabControl`'s only real virtual reads the tab
// `CS::GridControl`'s cursor, calls the pager, re-reads the cursor and forwards to the composite if
// it moved. The pager takes no direction argument -- it decides by asking the menu input
// predicates. So the chain either never runs, or runs and the predicates say no. Those are
// different bugs with different fixes, and a log of the harness's own writes cannot tell them
// apart: it reports what we wrote, not what the game read.
//
// # What it hooks, and what each answer means
//
// * `_SettingTabControl::<virtual>` -- the tab control being pumped at all. Silent means the menu
//   never reaches the tab control, and the press is not the problem.
// * `GridControl::Update` (the pager) -- the thing that would move the cursor. Runs but never
//   moves means the predicates refused.
// * `FUN_140758050` -- the cursor-live gate (`CSMenuManImp::disableMouseCursor == false`).
// * `FUN_14075d970` -- the menu input predicate the pager consults. Its argument and its verdict
//   are the answer: a `false` here with our bit written is the injection landing where the game
//   does not read, which is the shape AGENTS.md names.
//
// Returns are counted per (argument, verdict) rather than streamed. These fire every frame while a
// menu is open, and a per-call `send()` is a firehose that gets the watcher rate-limited.
//
// # Addresses
//
// 1.16.2 `0x966f30` / `0x7392f0` / `0x758050` / `0x75d970`, carried to 1.17.0 through
// `docs/recon/rva-map-1162-to-1170.tsv` as `0x9680d0` / `0x73a140` / `0x758ea0` / `0x75e7c0`. All
// four sit below `0xafefe9`, so the 1.17.0 -> 1.17.1 step leaves them where they are
// (`docs/er-1.17-migration.md`).

'use strict';

const SETTING_TAB_CONTROL_UPDATE = ptr('0x1409680d0');
const GRID_CONTROL_UPDATE = ptr('0x14073a140');
const CURSOR_LIVE_GATE = ptr('0x140758ea0');
const MENU_INPUT_PREDICATE = ptr('0x14075e7c0');

// About 28 percent of function entries on this build open with an Arxan healing stub, and a hook
// on the stub never fires.
//
// The bounds test is not decoration. On the first run of this agent `0x14075e7c0` read `0xe9` and
// the rel32 after it resolved to `0x13ffc0315` -- below the image base, so not a jump inside
// `.text` at all -- and the hook attached to it anyway and tallied thousands of calls to something
// that is not the menu input predicate. A followed address that leaves the module is a stub caught
// mid-heal, not a target: report it and hook the entry itself.
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

// The tab `CS::GridControl`'s selected cell, so a pager call can be reported as moved or not.
const GRID_CURSOR_OFFSET = 0xd4;
// `_SettingTabControl + 0x10` is the tab `CS::GridControl` itself (`drive.rs`, `TabToQuit`).
const TAB_CONTROL_GRID_OFFSET = 0x10;

const tally = new Map();
function count (key) {
  tally.set(key, (tally.get(key) || 0) + 1);
}

let tabControlCalls = 0;
let pagerCalls = 0;
let pagerMoved = 0;

// What the tab control itself sees. `drive.rs` reads the chain statically: this virtual reads its
// grid's cursor, calls the pager, re-reads, and forwards to the composite only if it moved. So the
// cursor on THIS grid is the number the tab switch turns on, and a pager that runs without moving
// it is the refusal, stated at the one object that matters rather than across every grid.
let tabCursor = null;
let tabGrid = null;
let tabCursorMoves = 0;

Interceptor.attach(follow(SETTING_TAB_CONTROL_UPDATE), {
  onEnter (args) {
    tabControlCalls += 1;
    try {
      const grid = args[0].add(TAB_CONTROL_GRID_OFFSET).readPointer();
      const cursor = grid.add(GRID_CURSOR_OFFSET).readS32();
      if (tabCursor !== null && cursor !== tabCursor) {
        tabCursorMoves += 1;
        send({
          kind: 'tab-cursor-moved',
          line: 'the OptionSetting tab cursor moved ' + tabCursor + ' -> ' + cursor
        });
      }
      tabGrid = grid;
      tabCursor = cursor;
    } catch (e) {
      tabGrid = null;
    }
  }
});

Interceptor.attach(follow(GRID_CONTROL_UPDATE), {
  onEnter (args) {
    pagerCalls += 1;
    this.grid = args[0];
    try {
      this.before = args[0].add(GRID_CURSOR_OFFSET).readS32();
    } catch (e) {
      this.before = null;
    }
  },
  onLeave () {
    if (pagerCalls - talliedAt >= TALLY_EVERY_PAGER_CALLS) {
      talliedAt = pagerCalls;
      emitTally();
    }
    if (this.before === null) return;
    let after;
    try {
      after = this.grid.add(GRID_CURSOR_OFFSET).readS32();
    } catch (e) {
      return;
    }
    if (after === this.before) return;
    pagerMoved += 1;
    send({
      kind: 'cursor-moved',
      line: 'GridControl cursor ' + this.before + ' -> ' + after + ' on grid ' + this.grid
    });
  }
});

Interceptor.attach(follow(CURSOR_LIVE_GATE), {
  onLeave (retval) { count('cursor-live-gate:ret=' + retval.toInt32()); }
});

Interceptor.attach(follow(MENU_INPUT_PREDICATE), {
  onEnter (args) { this.which = args[1].toInt32(); },
  onLeave (retval) {
    count('menu-input-predicate:arg1=' + this.which + ':ret=' + retval.toInt32());
  }
});

// A time series rather than a total, so a run can say whether the numbers moved when the press was
// issued -- but clocked on the game's own pumping, not on wall time. `scripts/check-no-timeouts.py`
// bans `setInterval` for the reason that applies here too: a wall clock keeps printing while the
// game is frozen, and identical lines arriving on schedule read as progress. The pager is the
// right clock because it runs for every grid whenever any menu is open, so silence from this line
// means the menus stopped, which is worth knowing.
const TALLY_EVERY_PAGER_CALLS = 300;
let talliedAt = 0;

function emitTally () {
  const rows = [];
  for (const [key, n] of tally) {
    rows.push(key + '=' + n);
  }
  send({
    kind: 'tally',
    line: 'tab_control_calls=' + tabControlCalls +
      ' tab_grid=' + (tabGrid === null ? 'unreadable' : tabGrid) +
      ' tab_cursor=' + (tabCursor === null ? 'unreadable' : tabCursor) +
      ' tab_cursor_moves=' + tabCursorMoves +
      ' pager_calls=' + pagerCalls +
      ' pager_moved_cursor=' + pagerMoved +
      ' | ' + (rows.length ? rows.join(' ') : 'no predicate calls yet')
  });
}

send({
  kind: 'armed',
  line: 'watching _SettingTabControl at ' + follow(SETTING_TAB_CONTROL_UPDATE) +
    ', GridControl::Update at ' + follow(GRID_CONTROL_UPDATE) +
    ', the cursor-live gate at ' + follow(CURSOR_LIVE_GATE) +
    ', the menu input predicate at ' + follow(MENU_INPUT_PREDICATE)
});
