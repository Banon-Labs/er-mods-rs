// Voice mock: play an audio clip through Seamless Co-op 2.0.1's voice playback as if a peer had
// said it, so the playback path can be tested alone, without a second player.
//
// What Seamless does with a received voice frame (static, ersc.dll 2.0.1):
//   ersc+0x9351f  ISteamUser::DecompressVoice -> 16-bit mono PCM at 48000 Hz
//   ersc+0x9354f  call ersc+0xA5EC0(ctx, peer index, &pcm, bytes)
//   ersc+0xA5EC0  obj = [[ctx]]; if obj: ersc+0xA5EF0(?, obj, index, &pcm, bytes)
//   ersc+0xA5EF0  if obj+0xb68 (playback built) and index < (end-begin)/8 of the slot list
//                 [obj+0xb50, obj+0xb58): slot = list[index]; id = slot+0x114; hand the PCM to the
//                 audio input with that id.
//   ersc+0xA58E0  the playback setup, called with the same ctx: builds two audio inputs into that
//                 list and sets obj+0xb68. It only runs when Seamless's voice bit is set (or the
//                 er-voice-chat table's ersc+A5921 patch is on).
// On 2026-10-17 a real peer's frame crashed both games at ersc+0xA5F32 with the list start
// reading 0x3f800000, so before feeding anything this agent checks the list itself and refuses.
//
// The feed runs inside the setup function's onLeave, on the thread Seamless itself calls it from.
// Commands in target/voice-mock/cmd.json with a new `seq`:
//   state {}                    ctx seen, obj, the +0xb50..+0xb68 fields, list entries, real feeds
//   say   {file, index?}        play a raw s16le 48 kHz mono file as peer `index` (default 0)
//   stop  {}                    drop what is still queued
'use strict';

const ersc = Process.findModuleByName('ersc.dll');
const SETUP = ersc.base.add(0xa58e0);
const FEED = ersc.base.add(0xa5ec0);
const CMD_PATH = 'Z:\\home\\banon\\projects\\er-mods-rs\\target\\voice-mock\\cmd.json';
const FRAME = 1920; // 20 ms at 48 kHz, 16-bit mono

const feed = new NativeFunction(FEED, 'void', ['pointer', 'uint32', 'pointer', 'uint32']);
let ctx = null;
let setupCalls = 0;
const realFeeds = { calls: 0, bytes: 0, indices: {} };
let queue = null; // { data: ArrayBuffer, off, index, start, sent }
const holder = Memory.alloc(8);
const chunk = Memory.alloc(FRAME);

function emit(kind, fields) { send(Object.assign({ kind: kind }, fields || {})); }

function readable(p, n) {
  const r = Process.findRangeByAddress(p);
  return r !== null && r.protection.indexOf('r') === 0 && p.add(n).compare(r.base.add(r.size)) <= 0;
}

// The playback object and its slot list, read defensively: any field that does not point at
// readable memory is reported as such instead of followed.
function inspect() {
  if (ctx === null) return { ctx: null };
  const out = { ctx: ctx.toString() };
  if (!readable(ctx, 8)) return Object.assign(out, { error: 'ctx unreadable' });
  const p0 = ctx.readPointer();
  if (p0.isNull() || !readable(p0, 8)) return Object.assign(out, { error: '[ctx] ' + p0 });
  const obj = p0.readPointer();
  out.obj = obj.toString();
  if (obj.isNull() || !readable(obj.add(0xb48), 0x28)) return Object.assign(out, { error: 'obj unreadable' });
  out.built = obj.add(0xb68).readU8();
  const begin = obj.add(0xb50).readPointer();
  const end = obj.add(0xb58).readPointer();
  out.begin = begin.toString();
  out.end = end.toString();
  const count = end.sub(begin).toInt32() / 8;
  out.count = count;
  out.entries = [];
  if (count > 0 && count <= 16 && readable(begin, count * 8)) {
    for (let i = 0; i < count; i++) {
      const s = begin.add(i * 8).readPointer();
      const ok = !s.isNull() && readable(s.add(0x114), 4);
      out.entries.push({ slot: s.toString(), id: ok ? s.add(0x114).readU32() : null });
    }
  } else {
    out.listError = 'list start is not a readable array of pointers';
  }
  return out;
}

function safeToFeed(index) {
  const st = inspect();
  if (!st.obj) return [false, st];
  if (!st.built) return [false, Object.assign(st, { why: 'playback not built (+0xb68 = 0)' })];
  const e = st.entries[index];
  if (!e || e.id === null) return [false, Object.assign(st, { why: 'no usable slot ' + index })];
  return [true, st];
}

const hooks = [];
let lastSetup = 0;

// Keep real time: send every frame whose start time has passed.
function pump() {
  if (queue === null) return;
  const due = Math.floor((Date.now() - queue.start) / 20) + 2;
  while (queue !== null && queue.sent < due) {
    const n = Math.min(FRAME, queue.data.byteLength - queue.off);
    if (n <= 0) { emit('said', { frames: queue.sent, from: queue.from }); queue = null; break; }
    chunk.writeByteArray(queue.data.slice(queue.off, queue.off + n));
    holder.writePointer(chunk);
    feed(ctx, queue.index, holder, n);
    queue.off += n;
    queue.sent++;
  }
}

hooks.push(Interceptor.attach(SETUP, {
  onEnter (args) { setupCalls++; ctx = args[0]; lastSetup = Date.now(); },
  onLeave () { if (queue !== null) { queue.from = 'setup-thread'; pump(); } },
}));
hooks.push(Interceptor.attach(FEED, {
  onEnter (args) {
    if (this.returnAddress.equals(ersc.base.add(0x93554))) {
      if (ctx === null) ctx = args[0];
      realFeeds.calls++;
      realFeeds.bytes += args[3].toUInt32();
      const i = args[1].toUInt32();
      realFeeds.indices[i] = (realFeeds.indices[i] || 0) + 1;
    }
  },
}));

function run(cmd) {
  if (cmd.op === 'state') {
    emit('state', { setupCalls: setupCalls, realFeeds: realFeeds, queued: queue !== null, playback: inspect() });
  } else if (cmd.op === 'say') {
    const index = cmd.index || 0;
    const [ok, st] = safeToFeed(index);
    if (!ok) { emit('say', { refused: true, playback: st }); return; }
    const data = File.readAllBytes(cmd.file);
    queue = { data: data, off: 0, index: index, start: Date.now(), sent: 0 };
    emit('say', { queued: data.byteLength, seconds: data.byteLength / 96000, index: index, playback: st });
  } else if (cmd.op === 'stop') {
    queue = null;
    emit('stop', {});
  } else {
    emit('error', { message: 'unknown op ' + cmd.op });
  }
}

let lastSeq = null;
try { lastSeq = JSON.parse(File.readAllText(CMD_PATH)).seq; } catch (e) { /* no file yet */ }

function readCommand() {
  let cmd;
  try { cmd = JSON.parse(File.readAllText(CMD_PATH)); } catch (e) { return; }
  if (cmd.seq === lastSeq) return;
  lastSeq = cmd.seq;
  try { run(cmd); } catch (e) { emit('error', { seq: cmd.seq, message: String(e) }); }
}

// A new command is noticed through a directory change notification on the command file's folder
// (Wine backs FindFirstChangeNotificationW with inotify), so the file is read only after a write.
// The handle is tested without waiting on the frame tick below; FindNextChangeNotification re-arms it.
// Wine's kernel32 forwards these to kernelbase, so look there when kernel32 does not resolve one.
const k32 = Process.getModuleByName('kernel32.dll');
const kbase = Process.findModuleByName('kernelbase.dll');
const k32fn = (name, ret, args) => new NativeFunction(
  k32.findExportByName(name) || kbase.getExportByName(name), ret, args);
const findFirstChange = k32fn('FindFirstChangeNotificationW', 'pointer', ['pointer', 'int', 'uint32']);
const findNextChange = k32fn('FindNextChangeNotification', 'int', ['pointer']);
const findCloseChange = k32fn('FindCloseChangeNotification', 'int', ['pointer']);
const waitForSingleObject = k32fn('WaitForSingleObject', 'uint32', ['pointer', 'uint32']);
const FILE_NOTIFY_CHANGE_FILE_NAME = 0x1;
const FILE_NOTIFY_CHANGE_LAST_WRITE = 0x10;
const WAIT_OBJECT_0 = 0;
const CMD_DIR = CMD_PATH.replace(/\\[^\\]*$/, '');
let change = findFirstChange(Memory.allocUtf16String(CMD_DIR), 0,
  FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE);
if (change.equals(ptr('-1'))) {
  change = null;
  emit('error', { message: 'no change notification on ' + CMD_DIR + '; create that folder and reload the agent' });
}

// The game's frame tick (1.17.1 0x140773900 -> 2.7.2.0 0x140773db0, carried with
// scripts/map-rvas-1171-to-1172.py; CSFeManImp::UpdatePlayerComponents, the main-thread HUD pass
// spawn-npc.js uses) carries two jobs: picking up a command whose notification has fired, and
// feeding the clip when Seamless has stopped calling the setup function (it may only run while
// voice is active). The pump keeps real time from Date.now(), so a frame of 16 or 33 ms only
// changes how many 20 ms voice frames each call sends; `said` reports which caller carried the clip.
const game = Process.findModuleByName('eldenring.exe');
const FRAME_TICK = game.base.add(ptr('0x140773db0').sub(ptr('0x140000000')));
hooks.push(Interceptor.attach(FRAME_TICK, {
  onEnter () {
    if (change !== null && waitForSingleObject(change, 0) === WAIT_OBJECT_0) {
      findNextChange(change);
      readCommand();
    }
    if (queue !== null && Date.now() - lastSetup > 200) { queue.from = 'frame-tick'; pump(); }
  },
}));

emit('ready', { ersc: ersc.base.toString(), setup: SETUP.toString(), feed: FEED.toString(), watching: change !== null });

rpc.exports = {
  dispose () {
    queue = null;
    hooks.forEach((h) => h.detach());
    if (change !== null) { findCloseChange(change); change = null; }
  },
};
