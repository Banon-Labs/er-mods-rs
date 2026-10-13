// Read-only: what the game's DirectInput keyboard and mouse reads return. Answers "is a pad press
// also arriving as a key or a mouse move?" (2026-10-06: B opened a menu and backstepped, the left
// stick moved both the character and the camera).
//
// GET_DEVICE_STATE is dinput8's shared IDirectInputDevice8::GetDeviceState body, as logged by
// er-npc-summons ("keyboard suppression armed at 0x...") for this process. Pass it as the first
// line's constant; it changes every launch.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/dinput-device-reads.js
'use strict';

const GET_DEVICE_STATE = ptr('0x6ffffb1d9200');
const KEYBOARD_STATE_SIZE = 256;
const MOUSE_STATE_SIZES = [16, 20];

let lastKeys = '';
// One verdict per pad press: the keyboard keys that went down within PRESS_WINDOW_MS of it.
const PRESS_WINDOW_MS = 300;
let press = null;
function settlePress() {
    if (press !== null && Date.now() - press.t > PRESS_WINDOW_MS) {
        send({ kind: 'pad-press-verdict', buttons: press.buttons, keysDown: Array.from(press.keys) });
        press = null;
    }
}
let lastMouse = '';
const hook = Interceptor.attach(GET_DEVICE_STATE, {
    onEnter(args) {
        this.size = args[1].toInt32();
        this.data = args[2];
    },
    onLeave(ret) {
        if (ret.toInt32() < 0 || this.data.isNull()) return;
        if (this.size === KEYBOARD_STATE_SIZE) {
            const bytes = new Uint8Array(this.data.readByteArray(KEYBOARD_STATE_SIZE));
            const down = [];
            for (let i = 0; i < bytes.length; i++) if (bytes[i] & 0x80) down.push('0x' + i.toString(16));
            if (press !== null) down.forEach((k) => press.keys.add(k));
            settlePress();
            const key = down.join(',');
            if (key !== lastKeys) {
                lastKeys = key;
                send({ kind: 'dinput-keys-down', dik: down });
            }
        } else if (MOUSE_STATE_SIZES.indexOf(this.size) !== -1) {
            const dx = this.data.readS32();
            const dy = this.data.add(4).readS32();
            const buttons = Array.from(new Uint8Array(this.data.add(12).readByteArray(this.size - 12)));
            const key = (dx !== 0 || dy !== 0 ? 'move' : 'still') + buttons.join(',');
            if (key !== lastMouse) {
                lastMouse = key;
                send({ kind: 'dinput-mouse', dx: dx, dy: dy, buttons: buttons });
            }
        } else if (this.size !== 0) {
            send({ kind: 'dinput-other-size', size: this.size });
        }
    },
});

// The followed XInputGetState body, as logged by er-npc-summons ("pad: XInputGetState at 0x...").
const XINPUT_GET_STATE = ptr('0x6fffffbe0a16');
let lastPad = -1;
let lastSticks = '';
const padHook = Interceptor.attach(XINPUT_GET_STATE, {
    onEnter(args) {
        this.index = args[0].toInt32();
        this.state = args[1];
    },
    onLeave(ret) {
        if (ret.toInt32() !== 0 || this.index !== 0 || this.state.isNull()) return;
        const buttons = this.state.add(4).readU16();
        const lx = this.state.add(8).readS16();
        const ly = this.state.add(10).readS16();
        const rx = this.state.add(12).readS16();
        const ry = this.state.add(14).readS16();
        if (buttons !== lastPad) {
            if (buttons !== 0) {
                settlePress();
                if (press === null) {
                    press = { t: Date.now(), buttons: '0x' + buttons.toString(16), keys: new Set() };
                }
            }
            lastPad = buttons;
            send({ kind: 'xinput-buttons', buttons: '0x' + buttons.toString(16) });
        }
        const bucket = (v) => (Math.abs(v) > 8000 ? Math.sign(v) : 0);
        const sticks = [lx, ly, rx, ry].map(bucket).join(',');
        if (sticks !== lastSticks) {
            lastSticks = sticks;
            send({ kind: 'xinput-sticks', l: [lx, ly], r: [rx, ry] });
        }
    },
});

rpc.exports.dispose = () => {
    hook.detach();
    padHook.detach();
};
