// Which object gates Seamless Co-op's own voice chat, and is its voice bit set?
//
// Seamless 2.0.1 does not use the game's voice path. It records with ISteamUser itself and plays
// what arrives through Wwise audio inputs, and both halves check bit 8 of one byte first:
//
//   ersc+0x92d40  capture: rsi = [[[rcx+8]+0x88]+0x18]; test [rsi+0x281], 8; cmp [rdx+0x11], 0;
//                 then ISteamUser vtable +0x48 GetAvailableVoice, +0x50 GetVoice, +0x58 Decompress
//   ersc+0xa58e0  playback setup: test [[rcx+0x18]+0x281], 8; done flag [[[rcx]]+0xb68]
//
// This logs the object behind that byte (its RTTI name when it has one), the byte itself, and how
// often each function runs.
//
// With SET_VOICE_BIT true it also sets bit 8 once, on the first playback-setup call, to test
// whether that bit alone is what keeps Seamless's voice off. That is the only write; dispose puts
// the original byte back. Nothing here calls into ersc.dll.

'use strict';

const ersc = Process.getModuleByName('ersc.dll');
const CAPTURE = ersc.base.add(0x92d40);
const PLAYBACK_SETUP = ersc.base.add(0xa58e0);
const FLAGS = 0x281;
const VOICE_BIT = 0x08;

const SET_VOICE_BIT = false;
// Control run: put back the byte Seamless had before the test (measured 0xd3) and keep counting.
const RESTORE_TO = null;

const seen = { capture: 0, playback: 0, lastFlags: null, objects: {}, forced: null };

function forceVoiceBit (object) {
  if (!SET_VOICE_BIT) {
    if (seen.forced === null && RESTORE_TO !== null) {
      seen.forced = { object: object, before: RESTORE_TO };
      const was = object.add(FLAGS).readU8();
      object.add(FLAGS).writeU8(RESTORE_TO);
      send({ kind: 'voice-gate-restored', object: object.toString(), was: '0x' + was.toString(16), now: '0x' + RESTORE_TO.toString(16) });
    }
    return;
  }
  if (seen.forced !== null) return;
  const before = object.add(FLAGS).readU8();
  seen.forced = { object: object, before: before };
  if ((before & VOICE_BIT) === 0) object.add(FLAGS).writeU8(before | VOICE_BIT);
  send({ kind: 'voice-gate-forced', object: object.toString(), before: '0x' + before.toString(16), after: '0x' + object.add(FLAGS).readU8().toString(16) });
}

function rtti (object) {
  try {
    const vtable = object.readPointer();
    const col = vtable.sub(8).readPointer();
    if (col.readU32() !== 1) return null;
    const module = Process.findModuleByAddress(col);
    if (module === null) return null;
    const typeDescriptor = module.base.add(col.add(12).readU32());
    return module.name + ':' + typeDescriptor.add(16).readCString();
  } catch (e) {
    return null;
  }
}

function report (where, object, extra) {
  let flags = null;
  try { flags = object.add(FLAGS).readU8(); } catch (e) {}
  const key = where + '@' + object;
  const changed = flags !== seen.lastFlags;
  if (!(key in seen.objects) || changed) {
    seen.objects[key] = true;
    seen.lastFlags = flags;
    send(Object.assign({
      kind: 'voice-gate',
      where: where,
      object: object.toString(),
      objectModule: (Process.findModuleByAddress(object) || {}).name || 'heap',
      rtti: rtti(object),
      flags: flags === null ? null : '0x' + flags.toString(16),
      voiceBit: flags === null ? null : (flags & VOICE_BIT) !== 0
    }, extra));
  }
}

Interceptor.attach(CAPTURE, {
  onEnter (args) {
    seen.capture++;
    try {
      const r10 = args[0].add(8).readPointer().add(0x88).readPointer();
      const object = r10.add(0x18).readPointer();
      report('capture', object, { holder: r10.toString(), holderRtti: rtti(r10), arg2Byte11: args[1].isNull() ? null : args[1].add(0x11).readU8() });
    } catch (e) {
      send({ kind: 'voice-gate-error', where: 'capture', error: String(e) });
    }
  }
});

Interceptor.attach(PLAYBACK_SETUP, {
  onEnter (args) {
    seen.playback++;
    // Playback setup runs once a frame, so every 300th call is a count report about every 5 s.
    if (seen.playback % 300 === 0) {
      send({ kind: 'voice-gate-counts', capture: seen.capture, playback: seen.playback, flags: seen.lastFlags, steam: steam });
    }
    try {
      const object = args[0].add(0x18).readPointer();
      let done = null;
      try { done = args[0].readPointer().readPointer().add(0xb68).readU8(); } catch (e) {}
      report('playback-setup', object, { setupDone: done });
      forceVoiceBit(object);
    } catch (e) {
      send({ kind: 'voice-gate-error', where: 'playback-setup', error: String(e) });
    }
  }
});

// Steam's own voice calls, whoever makes them: StartVoiceRecording (vtable slot 7),
// GetAvailableVoice (9), GetVoice (10). A GetVoice that returns bytes is the microphone being read
// for sending; the caller's module says whether the game or Seamless asked for it.
const steam = { start: 0, available: 0, availableBytes: 0, getVoice: 0, voiceBytes: 0, callers: {} };
(function hookSteamVoice () {
  const getUser = Process.getModuleByName('steam_api64.dll').findExportByName('SteamAPI_SteamUser_v021');
  if (getUser === null) { send({ kind: 'steam-voice-error', error: 'no SteamAPI_SteamUser_v021 export' }); return; }
  const user = new NativeFunction(getUser, 'pointer', [])();
  if (user.isNull()) { send({ kind: 'steam-voice-error', error: 'SteamUser() returned null' }); return; }
  const vtable = user.readPointer();
  function caller (ctx) {
    const m = Process.findModuleByAddress(ctx.returnAddress);
    const name = m === null ? ctx.returnAddress.toString() : m.name + '+0x' + ctx.returnAddress.sub(m.base).toString(16);
    steam.callers[name] = (steam.callers[name] || 0) + 1;
  }
  Interceptor.attach(vtable.add(7 * 8).readPointer(), {
    onEnter () { steam.start++; caller(this); }
  });
  Interceptor.attach(vtable.add(9 * 8).readPointer(), {
    onEnter (args) { steam.available++; this.out = args[1]; caller(this); },
    onLeave () { try { steam.availableBytes += this.out.readU32(); } catch (e) {} }
  });
  Interceptor.attach(vtable.add(10 * 8).readPointer(), {
    onEnter (args) { steam.getVoice++; this.written = args[4]; caller(this); },
    onLeave () { try { steam.voiceBytes += this.written.readU32(); } catch (e) {} }
  });
  send({ kind: 'steam-voice-armed', user: user.toString() });
})();

send({ kind: 'voice-gate-armed', ersc: ersc.base.toString(), capture: CAPTURE.toString(), playback: PLAYBACK_SETUP.toString() });

rpc.exports = {
  dispose () {
    if (seen.forced !== null) {
      try { seen.forced.object.add(FLAGS).writeU8(seen.forced.before); } catch (e) {}
    }
  }
};
