// Who writes CS::ProfileSummary's records during a System > Quit character switch?
//
// `picked-summary` in er-profile-summary-core says, every time it re-asserts, that "something else
// writes these records after we do (the game's own boot ProfileSummary read is the known one)". That
// sentence has been in the log for weeks and names no function. This agent names one or clears it.
//
// The candidate is `CS::ProfileSummary::UpdateRecordFromLivePlayer`, 1.16.2 0x140262270 -> 1.17
// 0x140262280 (docs/recon/rva-map-1162-to-1170.verified.tsv, IDENTICAL-WHOLE over 99 insns). It
// writes one record from `GameDataMan->mainPlayerGameData` -- name, level +0x24, runeMemory +0x2c,
// playtime +0x28 -- and its only two native callers are the GameMan save lanes FUN_14067b750 and
// FUN_14067b940. A switch runs a return-title save, so those lanes do fire; the open question is
// whether the record they write is the slot the switch just loaded.
//
// Read it against run br-20260917-153344-7356, where the drift watch reported slot 6 "overwritten
// with level=150" while defending level=266. Offline decode says slot 6 of the picked container is
// level 150, so the record was right and the watch was stale -- this agent is the runtime half of
// that: if no call here lands on slot 6, nothing overwrote it and the alarm was manufactured.
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py \
//       --agent scripts/frida/profile-summary-record-writer.js

'use strict';

// 1.17 addresses. `.text` below rva 0xafefe9 is unmoved on 1.17.1, so these need no further step.
const UPDATE_RECORD_FROM_LIVE_PLAYER_RVA = 0x262280;
// `.data` globals keep their 1.16.2 addresses across 1.17: the section table is identical.
const GAME_DATA_MAN_GLOBAL_RVA = 0x3d5df38;
const GAME_DATA_MAN_PROFILE_SUMMARY_OFFSET = 0x78;
const PROFILE_SUMMARY_RECORD_BASE = 0x18;
const PROFILE_SUMMARY_RECORD_STRIDE = 0x2a0;
const PROFILE_SUMMARY_RECORD_LEVEL_OFFSET = 0x24;
const PROFILE_SUMMARY_SLOT_COUNT = 10;
const CHARACTER_NAME_UNITS = 0x11;

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
  send({ tag: 'record-writer', fatal: 'eldenring.exe is not loaded in this process' });
} else {
  send({ tag: 'record-writer', base: game.base.toString(), size: game.size });
}

function readPtr(at) {
  try {
    return at.readPointer();
  } catch (e) {
    return null;
  }
}

function summaryBase() {
  if (game === null) return null;
  const global = readPtr(game.base.add(GAME_DATA_MAN_GLOBAL_RVA));
  if (global === null || global.isNull()) return null;
  const summary = readPtr(global.add(GAME_DATA_MAN_PROFILE_SUMMARY_OFFSET));
  if (summary === null || summary.isNull()) return null;
  return summary;
}

// Which record does `p` point into, if any? The writer may be handed the record rather than the
// container, so both shapes are resolved rather than guessed at from the argument position.
function slotForPointer(p) {
  const summary = summaryBase();
  if (summary === null || p === null || p.isNull()) return -1;
  const delta = p.sub(summary).toInt32();
  if (delta < PROFILE_SUMMARY_RECORD_BASE) return -1;
  const off = delta - PROFILE_SUMMARY_RECORD_BASE;
  if (off % PROFILE_SUMMARY_RECORD_STRIDE !== 0) return -1;
  const slot = off / PROFILE_SUMMARY_RECORD_STRIDE;
  return slot < PROFILE_SUMMARY_SLOT_COUNT ? slot : -1;
}

function recordAt(slot) {
  const summary = summaryBase();
  if (summary === null || slot < 0 || slot >= PROFILE_SUMMARY_SLOT_COUNT) return null;
  return summary.add(PROFILE_SUMMARY_RECORD_BASE + slot * PROFILE_SUMMARY_RECORD_STRIDE);
}

function identityAt(slot) {
  const rec = recordAt(slot);
  if (rec === null) return null;
  try {
    return {
      slot: slot,
      name: rec.readUtf16String(CHARACTER_NAME_UNITS),
      level: rec.add(PROFILE_SUMMARY_RECORD_LEVEL_OFFSET).readS32(),
    };
  } catch (e) {
    return null;
  }
}

// Every record, so a call's before/after can be read against the whole table rather than one slot.
function snapshot() {
  const out = [];
  for (let slot = 0; slot < PROFILE_SUMMARY_SLOT_COUNT; slot += 1) {
    const id = identityAt(slot);
    if (id !== null && id.level > 0) out.push(id);
  }
  return out;
}

function caller(ctx) {
  try {
    const frames = Thread.backtrace(ctx, Backtracer.FUZZY).slice(0, 4);
    return frames.map(function (f) {
      if (game !== null && f.compare(game.base) >= 0 && f.compare(game.base.add(game.size)) < 0) {
        return 'game+0x' + f.sub(game.base).toString(16);
      }
      return f.toString();
    });
  } catch (e) {
    return [];
  }
}

if (game !== null) {
  let calls = 0;
  Interceptor.attach(game.base.add(UPDATE_RECORD_FROM_LIVE_PLAYER_RVA), {
    onEnter: function (args) {
      calls += 1;
      this.call = calls;
      // Both plausible argument shapes, reported rather than assumed.
      this.slotFromRcx = slotForPointer(args[0]);
      this.slotFromRdx = slotForPointer(args[1]);
      this.argInt = args[1].toInt32();
      this.before = snapshot();
      this.frames = caller(this.context);
    },
    onLeave: function () {
      send({
        tag: 'record-writer',
        call: this.call,
        slot_from_rcx: this.slotFromRcx,
        slot_from_rdx: this.slotFromRdx,
        rdx_as_int: this.argInt,
        before: this.before,
        after: snapshot(),
        callers: this.frames,
      });
    },
  });
  send({
    tag: 'record-writer',
    armed: 'UpdateRecordFromLivePlayer',
    at: game.base.add(UPDATE_RECORD_FROM_LIVE_PLAYER_RVA).toString(),
    summary: (summaryBase() || ptr(0)).toString(),
    records_now: snapshot(),
  });
}
