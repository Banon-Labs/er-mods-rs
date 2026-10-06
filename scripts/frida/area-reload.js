// Reload the current map in place: the game's own TriggerAreaReload(false), 1.17.1 0x1405f36e0
// (bd great-jar-sign-flags-1171-2026-10-06), called once on the frame thread.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/area-reload.js
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const FRAME_TICK = va('0x140773900');
const TRIGGER_AREA_RELOAD = new NativeFunction(va('0x1405f36e0'), 'void', ['uint8']);

let done = false;
const tick = Interceptor.attach(FRAME_TICK, {
    onEnter() {
        if (done) return;
        done = true;
        TRIGGER_AREA_RELOAD(0);
        send({ kind: 'area-reload', called: true });
    },
});

rpc.exports.dispose = () => tick.detach();
