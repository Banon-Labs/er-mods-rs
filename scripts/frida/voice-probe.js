// Voice probe: count the Steam voice calls in the running game, so "we can't hear each other"
// splits into capture (GetAvailableVoice/GetVoice: our mic read), and receive (DecompressVoice:
// a peer's voice packet arriving and being decoded). Seamless Co-op 2.0.1 calls these through
// ISteamUser's vtable, so the hooks go on the vtable targets, found by reading which slot each
// flat export `SteamAPI_ISteamUser_<name>` jumps through.
// Reports per function calls and bytes, plus the calling module+offset of the first caller seen.
// A report goes out on the game's frame tick (1.17.1 0x140773900 -> 2.7.2.0 0x140773db0,
// CSFeManImp::UpdatePlayerComponents, the main-thread HUD pass spawn-npc.js uses) when a hooked
// call has changed the counts since the last report and that report is at least REPORT_MS old: a
// quiet session sends nothing, a busy one is not flooded.
'use strict';

const steamApi = Process.findModuleByName('steam_api64.dll');
const NAMES = ['StartVoiceRecording', 'StopVoiceRecording', 'GetAvailableVoice', 'GetVoice', 'DecompressVoice'];

function slotOf(exportName) {
  let p = steamApi.findExportByName(exportName);
  if (p === null) return null;
  for (let i = 0; i < 12; i++) {
    const ins = Instruction.parse(p);
    const m = /(?:call|jmp) qword ptr \[(r\w+)(?: \+ (0x[0-9a-f]+))?\]/.exec(ins.toString());
    if (m) return m[2] ? parseInt(m[2], 16) : 0;
    p = ins.next;
  }
  return null;
}

const userGetter = ['SteamAPI_SteamUser_v023', 'SteamAPI_SteamUser_v022', 'SteamAPI_SteamUser_v021']
  .map((n) => steamApi.findExportByName(n)).find((p) => p !== null);
const user = new NativeFunction(userGetter, 'pointer', [])();
const vtable = user.readPointer();
const stats = {};
const hooks = [];
let dirty = false;

// Every received voice frame after decoding, as one WAV per probe session. The header's two length
// fields are rewritten after each append, so the file is playable at any moment, even mid-session.
const WAV_PATH = 'Z:\\home\\banon\\.cache\\er-frida\\voice-received-' + Date.now() + '.wav';
let wav = null;
let wavBytes = 0;
let wavRate = 0;

function wavHeader(rate, dataBytes) {
  const h = new DataView(new ArrayBuffer(44));
  const tag = (o, t) => { for (let i = 0; i < 4; i++) h.setUint8(o + i, t.charCodeAt(i)); };
  tag(0, 'RIFF'); h.setUint32(4, 36 + dataBytes, true); tag(8, 'WAVE');
  tag(12, 'fmt '); h.setUint32(16, 16, true); h.setUint16(20, 1, true); h.setUint16(22, 1, true);
  h.setUint32(24, rate, true); h.setUint32(28, rate * 2, true); h.setUint16(32, 2, true); h.setUint16(34, 16, true);
  tag(36, 'data'); h.setUint32(40, dataBytes, true);
  return h.buffer;
}

function record(pcm, n, rate) {
  if (wav === null) { wav = new File(WAV_PATH, 'w+b'); wavRate = rate; wav.write(wavHeader(rate, 0)); }
  wav.write(pcm.readByteArray(n));
  wavBytes += n;
  wav.seek(0);
  wav.write(wavHeader(wavRate, wavBytes));
  wav.seek(0, File.SEEK_END);
  wav.flush();
}

function caller(ctx) {
  const m = Process.findModuleByAddress(ctx.returnAddress);
  return m ? m.name + '+0x' + ctx.returnAddress.sub(m.base).toString(16) : ctx.returnAddress.toString();
}

for (const name of NAMES) {
  const off = slotOf('SteamAPI_ISteamUser_' + name);
  if (off === null) { send({ kind: 'error', message: 'no slot for ' + name }); continue; }
  const target = vtable.add(off).readPointer();
  const s = stats[name] = { slot: off / 8, calls: 0, okBytes: 0, results: {}, firstCaller: null };
  hooks.push(Interceptor.attach(target, {
    onEnter (args) {
      s.calls++;
      if (s.firstCaller === null) s.firstCaller = caller(this);
      // GetVoice(this, bWantCompressed, pDest, cbDest, nBytesWritten*, ...): written count at args[4].
      // DecompressVoice(this, pCompressed, cbCompressed, pDest, cbDest, nBytesWritten*, rate): args[5].
      if (name === 'GetVoice') this.out = args[4];
      if (name === 'DecompressVoice') {
        this.out = args[5];
        this.pcm = args[3];
        this.rate = args[6].toUInt32();
        s.inBytes = (s.inBytes || 0) + args[2].toInt32();
      }
    },
    onLeave (ret) {
      dirty = true;
      const r = ret.toInt32();
      s.results[r] = (s.results[r] || 0) + 1;
      if (this.out && !this.out.isNull() && r === 0) {
        const n = this.out.readU32();
        s.okBytes += n;
        // The decoded voice is 16-bit mono PCM at `rate`; keep it, so a session can be listened to.
        if (name === 'DecompressVoice' && n > 0) record(this.pcm, n, this.rate);
      }
    },
  }));
}

send({ kind: 'ready', user: user.toString(), slots: Object.fromEntries(Object.entries(stats).map(([k, v]) => [k, v.slot])) });

const game = Process.findModuleByName('eldenring.exe');
// 2.7.2.0, carried from 1.17.1 0x140773900 with scripts/map-rvas-1171-to-1172.py.
const FRAME_TICK = game.base.add(ptr('0x140773db0').sub(ptr('0x140000000')));
const REPORT_MS = 2000;
let reportedAt = 0;
hooks.push(Interceptor.attach(FRAME_TICK, {
  onEnter () {
    if (!dirty) return;
    const now = Date.now();
    if (now - reportedAt < REPORT_MS) return;
    reportedAt = now;
    dirty = false;
    send({ kind: 'stats', stats: stats });
  },
}));

rpc.exports = { dispose () { hooks.forEach((h) => h.detach()); if (wav !== null) wav.close(); } };
