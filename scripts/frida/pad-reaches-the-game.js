// Is injected pad state actually reaching the game, or is the driver pressing into a void?
//
// `scripts/er-drive-finger-bounds.py` refuses with "input is not reaching the game (window focus?)"
// whenever a D-pad Down leaves the selected quick slot unchanged. That one symptom covers at least
// four different causes and the message guesses at one of them:
//
//   1. the game is not polling `XInputGetState` at all, so nothing injected is ever read;
//   2. it polls, but `er_quickload_hold_xinput_pad` is not putting the mask where the poll reads;
//   3. both work and the character cannot act yet, so the press is read and discarded;
//   4. the window really is unfocused and the game drops pad input on purpose.
//
// This separates them. It counts real `XInputGetState` calls, reports the `wButtons` field the game
// receives on each one, and reports the selected quick slot beside it -- so the caller can see the
// mask arrive (or not) independently of whether the game acts on it.
'use strict';

const XINPUT = 'XINPUT1_4.dll';
const XINPUT_READ = 'XInputGetState';
const BUILDER_A_RVA = 0x240e70;
// `XINPUT_STATE` is `{ DWORD dwPacketNumber; XINPUT_GAMEPAD Gamepad; }` and `XINPUT_GAMEPAD` opens
// with `WORD wButtons`, so the button mask the game is handed sits one dword in.
const BUTTONS_OFFSET = 4;

function follow(address) {
    return address.readU8() === 0xe9
        ? address.add(5).add(address.add(1).readS32())
        : address;
}

const game = Process.findModuleByName('eldenring.exe');
const xinput = Process.findModuleByName(XINPUT);
const quickload = Process.findModuleByName('er_quickload.dll');

const seen = {
    polls: 0,
    nonZeroMasks: 0,
    lastMask: null,
    masks: {},
    xinputPresent: xinput !== null,
    quickloadPresent: quickload !== null,
    injector: null
};

if (quickload !== null) {
    try {
        seen.injector = '0x' + quickload.getExportByName('er_quickload_hold_xinput_pad').toString(16);
    } catch (e) {
        seen.injector = 'export missing: ' + e.message;
    }
}

const tick = xinput === null
    ? follow(game.base.add(BUILDER_A_RVA))
    : follow(xinput.getExportByName(XINPUT_READ));

// `onLeave`, not `onEnter`: the buffer is an out-parameter, so the mask the game will act on only
// exists once the call has filled it in. Reading it on entry measures the previous frame at best.
Interceptor.attach(tick, {
    onEnter: function (args) {
        this.state = args[1];
    },
    onLeave: function () {
        seen.polls += 1;
        if (this.state === undefined || this.state.isNull()) return;
        let mask = null;
        try { mask = this.state.add(BUTTONS_OFFSET).readU16(); } catch (e) { return; }
        seen.lastMask = mask;
        if (mask !== 0) {
            seen.nonZeroMasks += 1;
            seen.masks['0x' + mask.toString(16)] = (seen.masks['0x' + mask.toString(16)] || 0) + 1;
        }
    }
});

send({ kind: 'armed', tick: '0x' + tick.toString(16), xinput: seen.xinputPresent, injector: seen.injector });

rpc.exports = {
    report: function () { return seen; },
    reset: function () { seen.polls = 0; seen.nonZeroMasks = 0; seen.masks = {}; return true; }
};
