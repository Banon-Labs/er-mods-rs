// What does Seamless's lobby-key input "A" read, straight from the field?
//
// `BuildLobbyKey(settings, out)` (v2.0.2 `ersc+0xaec50`) hashes B + A + salt, where A is bit 2 of
// the byte at `settings+0x281` written as "0" or "1". The patch notes had A only from the hash
// match; this reads the byte itself on every call, with the settings pointer it came from.
//
// An `Interceptor` on the entry: it reads the first argument and returns, writing nothing.
'use strict';

const RVA = 0xaec50;
const FIELD = 0x281;

const ersc = Process.getModuleByName('ersc.dll');
const target = ersc.base.add(RVA);
let calls = 0;

Interceptor.attach(target, {
  onEnter (args) {
    calls += 1;
    const settings = args[0];
    let byte = null;
    try {
      byte = settings.add(FIELD).readU8();
    } catch (error) {
      byte = null;
    }
    send({
      event: 'build-lobby-key',
      calls,
      settings: settings.toString(),
      byte281: byte === null ? null : '0x' + byte.toString(16),
      bit2: byte === null ? null : (byte >> 2) & 1,
    });
  },
});

send({ event: 'armed', at: target.toString() });
