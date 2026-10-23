// Sets and locks the time of day and the weather through the game's own calls, and reports the
// clock and weather state twice a second, as the prototype for the Sky tab (er-effects-rs-pjyh).
//
// Addresses are 2.7.2.0, carried from the 1.17.1 ones the bd notes give and written old -> new
// below. Each function's bytes match the
// 1.17.1 one once rip-relative and branch operands are masked, every masked target carrying to the
// new operand; each global is the carried target of those same readers.
//
// Config ({} reports only; edit CFG below and save to apply again, the watcher reloads in place):
//   hour, minute, second   RequestTime(WorldAreaTime, h, m, s) 0x1406442a0 -> 0x140644750 once per
//                          load of this script and again whenever WorldAreaTime is rebuilt (each
//                          area load)
//   freeze                 true/false through SetTimeFrozen 0x1405f6240 -> 0x1405f66f0, the EMEVD
//                          "freeze time" flag 0x143d6d370 -> 0x143d70390; set then freeze pins the
//                          hour
//   speed                  game seconds per real second, written to WorldAreaTime +0x38 and +0x34
//                          (vanilla 600? the report prints the live value)
//   weather                internal WeatherParam type (0 default, 1 cloudless, 10 flat clouds,
//                          11 puffy clouds, 20 rain, 21 rainy clouds, 30 windy rain, 31 windy fog,
//                          40 snow, 41 heavy snow, 50 fog, 51 heavy fog, 52 rainy heavy fog,
//                          60 windy puffy clouds, 81 snowy heavy fog, 82 scattered rain), asked
//                          for through SetWeatherType 0x1405ecb30 -> 0x1405ecfe0 with lifespan -1
//                          (never expires) and immediate
//   lockWeather            asks again whenever the current type (+0x2a) drifts from `weather`
//
// Both requests are made from inside the game's own per-frame updates (WorldAreaTimeImp::Update
// 0x140644a10 -> 0x140644ec0, WorldAreaWeather::Update 0x1406489d0 -> 0x140648e80), so they run on
// the thread that owns them.
// Static map: bd er-time-of-day-worldareatime-1171-2026-10-15, er-weather-worldareaweather-1171-2026-10-15.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const va = (s) => game.base.add(ptr(s).sub(ptr('0x140000000')));
const CFG = Object.assign(
  { hour: null, minute: 0, second: 0, freeze: false, speed: 24, weather: -1, lockWeather: false },
  globalThis.__ER_FRIDA_CONFIG || {},
);

const WAT_GLOBAL = va('0x143d70388'); // WorldAreaTime* 0x143d6d368 -> 0x143d70388
const TIME_FROZEN = va('0x143d70390'); // 0x143d6d370 -> 0x143d70390
const TIME_STOP_PRIVATE = va('0x143d70391'); // 0x143d6d371 -> 0x143d70391
const WEATHER_GLOBAL = va('0x143d70410'); // WorldAreaWeather* 0x143d6d3f0 -> 0x143d70410
const WAT_UPDATE = va('0x140644ec0'); // 0x140644a10 -> 0x140644ec0
const WEATHER_UPDATE = va('0x140648e80'); // 0x1406489d0 -> 0x140648e80
const REQUEST_TIME = new NativeFunction(va('0x140644750'), 'void', ['pointer', 'uint32', 'uint32', 'uint32']); // 0x1406442a0 -> 0x140644750
const SET_TIME_FROZEN = new NativeFunction(va('0x1405f66f0'), 'void', ['uint8']); // 0x1405f6240 -> 0x1405f66f0
const SET_WEATHER_TYPE = new NativeFunction(va('0x1405ecfe0'), 'void', ['int16', 'float', 'uint8']); // 0x1405ecb30 -> 0x1405ecfe0

const S = { wat: ptr(0), weather: ptr(0), timeDone: false, weatherAsks: 0, speedWas: null, frozeIt: false, lastReport: 0, frames: 0 };

function clock(wat) {
  const packed = wat.add(0x08).readU64();
  const bits = (lo, n) => packed.shr(lo).and(uint64((1 << n) - 1)).toNumber();
  return { h: bits(34, 5), m: bits(39, 6), s: bits(45, 6) };
}

function report() {
  const wat = WAT_GLOBAL.readPointer();
  const w = WEATHER_GLOBAL.readPointer();
  const out = { kind: 'sky', frames: S.frames, weatherAsks: S.weatherAsks };
  if (!wat.isNull()) {
    const c = clock(wat);
    Object.assign(out, {
      time: `${String(c.h).padStart(2, '0')}:${String(c.m).padStart(2, '0')}:${String(c.s).padStart(2, '0')}`,
      speedBase: wat.add(0x34).readFloat(),
      speed: wat.add(0x38).readFloat(),
      stopCount: wat.add(0x40).readS32(),
      flowing: wat.add(0x46).readU8(),
      cutscene: wat.add(0x45).readU8(),
      lightZone: wat.add(0xb8).readS32(),
      blend: [wat.add(0xc0).readS32(), wat.add(0xc4).readS32(), wat.add(0xc8).readFloat()],
      frozen: TIME_FROZEN.readU8(),
      stopPrivate: TIME_STOP_PRIVATE.readU8(),
      setLock: wat.add(0x114).readU8(),
    });
  }
  if (!w.isNull()) {
    Object.assign(out, {
      weather: w.add(0x2a).readS16(),
      weatherArea: w.add(0x28).readS16(),
      weatherState: w.add(0x18).readS32(),
      weatherLife: w.add(0x48).readS32(),
      weatherEvent: w.add(0xf9).readU8(),
      weatherFixed: w.add(0x21c).readS16(),
      wetness: w.add(0x178).readFloat(),
    });
  }
  send(out);
}

Interceptor.attach(WAT_UPDATE, {
  onEnter(args) {
    const wat = args[0];
    S.frames++;
    if (!wat.equals(S.wat)) {
      S.wat = wat;
      S.timeDone = false;
      S.speedWas = null;
    }
    if (!S.timeDone) {
      S.timeDone = true;
      if (CFG.hour !== null) {
        REQUEST_TIME(wat, CFG.hour, CFG.minute, CFG.second);
        send({ kind: 'request-time', h: CFG.hour, m: CFG.minute, s: CFG.second });
      }
      if (CFG.freeze !== null) {
        SET_TIME_FROZEN(CFG.freeze ? 1 : 0);
        S.frozeIt = !!CFG.freeze;
        send({ kind: 'freeze', on: !!CFG.freeze });
      }
      if (CFG.speed !== null) {
        S.speedWas = [wat.add(0x34).readFloat(), wat.add(0x38).readFloat()];
        wat.add(0x34).writeFloat(CFG.speed);
        wat.add(0x38).writeFloat(CFG.speed);
        send({ kind: 'speed', was: S.speedWas, now: CFG.speed });
      }
    }
    if (S.frames - S.lastReport >= 30) {
      S.lastReport = S.frames;
      report();
    }
  },
});

Interceptor.attach(WEATHER_UPDATE, {
  onEnter(args) {
    const w = args[0];
    if (CFG.weather === null) return;
    const fresh = !w.equals(S.weather);
    S.weather = w;
    const drifted = CFG.lockWeather && w.add(0x18).readS32() === 0 && w.add(0x2a).readS16() !== CFG.weather;
    if (fresh || (drifted && S.weatherAsks < 50)) {
      SET_WEATHER_TYPE(CFG.weather, -1.0, 1);
      S.weatherAsks++;
      send({ kind: 'request-weather', type: CFG.weather, fresh, drifted });
    }
  },
});

send({ kind: 'loaded', cfg: CFG, wat: WAT_GLOBAL.readPointer(), weather: WEATHER_GLOBAL.readPointer() });

rpc.exports.dispose = function () {
  const wat = WAT_GLOBAL.readPointer();
  if (CFG.restoreSpeed && S.speedWas !== null && !wat.isNull() && wat.equals(S.wat)) {
    wat.add(0x34).writeFloat(S.speedWas[0]);
    wat.add(0x38).writeFloat(S.speedWas[1]);
  }
  if (S.frozeIt) TIME_FROZEN.writeU8(0);
};
