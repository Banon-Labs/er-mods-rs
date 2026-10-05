// The chainsaw glitch: a held repeating skill (Wild Strikes) that survives a mid-skill right-hand
// equip and keeps repeating on the new weapon. Notes: docs/er-mechanics/chainsaw/.
//
// Per event, with a millisecond timestamp so the order can be read back:
//   hit     every CalculateDamage2 call whose dealer is the main player: attack id, bullet id, the
//           launching equip slot (ADI+0x48) and weapon (ADI+0x144), the damage (ADI+0x228), and the
//           right/left weapons actually held at the hit
//   equip   EquipItemToChrAsmSlot (the menu's equip), with the slot and the held weapons after it
//   switch  the d-pad weapon switch, with the held weapons before and after
//   tae     SetChrAsmEquipmentState (the TimeAct event that applies an equipment state)
//   bcast   BroadCastEquipmentChange
//   extra   any hook named in EXTRA below: its first args on entry and its return value
//
// Run with the watcher, never a plain frida.attach (AGENTS.md):
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/chainsaw-probe.js
//
// Addresses are 1.17.1. All are below the 0xafefe9 shift boundary, so 1.17.0 == 1.17.1; mapped from
// the named 1.16.2 dump with scripts/map-rvas-1162-to-1170.py (signatures from :8765):
//   CalculateDamage2           1.16.2 0x1404483b0 -> 0x140448910
//   EquipItemToChrAsmSlot      1.16.2 0x140787c30 -> 0x140788ab0  (ChrAsmSlot, MenuGaitem*)
//   weapon switch FUN_14042cf80 1.16.2 0x14042cf80 -> 0x14042d4d0
//   SetChrAsmEquipmentState    1.16.2 0x140426520 -> 0x140426a70  (CSChrTaeAnimEvent*, TaeAnimEventParams*)
//   BroadCastEquipmentChange   1.16.2 0x140658c90 -> 0x140659ae0  (PlayerIns*)
//   GetEquipmentEntryParamId   1.16.2 0x140656960 -> 0x1406577b0  (PlayerIns*, slot -6..11)
//
// Uses only Interceptor (no watchpoints), so a detach or reload leaves nothing behind.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const CALC_DAMAGE2 = va('0x140448910');
const EQUIP_TO_SLOT = va('0x140788ab0');
const WEAPON_SWITCH = va('0x14042d4d0');
const SET_EQUIP_STATE = va('0x140426a70');
const BROADCAST_EQUIP = va('0x140659ae0');
const GET_EQUIP = new NativeFunction(va('0x1406577b0'), 'int', ['pointer', 'int']);
const WORLD_CHR_MAN = game.base.add(0x3d69ff8);

// Hooks the static notes name later: the equipment-change gate, the reader of the active skill,
// the useMagicPoint read. Each entry: { name, va: '0x14...', args: <count to log> }.
// From docs/er-mechanics/chainsaw/damage-and-fp.md: the reservation (behaviour act 2016, cast
// number and hand) and the charge (TimeAct event 330) of a skill's FP.
const EXTRA = [
    { name: 'UpdateActiveAowFpStats', va: '0x140480070', args: 3 },  // 1.16.2 0x14047fb10
    { name: 'ConsumeFp', va: '0x14047fba0', args: 2 },               // 1.16.2 0x14047f640
];

function mainPlayer() {
    try {
        const wcm = WORLD_CHR_MAN.readPointer();
        return wcm.isNull() ? null : wcm.add(0x1e508).readPointer();
    } catch (e) {
        return null;
    }
}

function held(player) {
    if (player === null) return { r: null, l: null };
    try {
        return { r: GET_EQUIP(player, -1), l: GET_EQUIP(player, -2) };
    } catch (e) {
        return { r: null, l: null };
    }
}

function rd(p, off, kind) {
    try {
        const a = p.add(off);
        if (kind === 'u8') return a.readU8();
        return a.readS32();
    } catch (e) {
        return null;
    }
}

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now() }, fields));
}

const hooks = [];
const perThread = {};

hooks.push(Interceptor.attach(CALC_DAMAGE2, {
    onEnter(args) {
        const player = mainPlayer();
        if (player === null || !args[1].equals(player)) return;
        const adi = args[2];
        perThread[this.threadId] = { adi: adi, player: player };
    },
    onLeave() {
        const ctx = perThread[this.threadId];
        delete perThread[this.threadId];
        if (!ctx) return;
        const adi = ctx.adi;
        const h = held(ctx.player);
        emit('hit', {
            atk: rd(adi, 0x40, 's32'),
            bullet: rd(adi, 0x10c, 's32'),
            slot: rd(adi, 0x48, 's32'),
            launch_weapon: rd(adi, 0x144, 's32'),
            damage: rd(adi, 0x228, 's32'),
            ctx: rd(adi, 0xda, 'u8'),
            held_r: h.r,
            held_l: h.l,
        });
    },
}));

hooks.push(Interceptor.attach(EQUIP_TO_SLOT, {
    onEnter(args) {
        this.slot = args[0].toInt32();
    },
    onLeave() {
        const h = held(mainPlayer());
        emit('equip', { slot: this.slot, held_r: h.r, held_l: h.l });
    },
}));

hooks.push(Interceptor.attach(WEAPON_SWITCH, {
    onEnter() {
        this.before = held(mainPlayer());
    },
    onLeave() {
        const h = held(mainPlayer());
        emit('switch', { before_r: this.before.r, before_l: this.before.l, held_r: h.r, held_l: h.l });
    },
}));

hooks.push(Interceptor.attach(SET_EQUIP_STATE, {
    onEnter(args) {
        let p0 = null;
        try { p0 = args[1].readS32(); } catch (e) { p0 = null; }
        emit('tae', { event: args[0].toString(), param0: p0 });
    },
}));

hooks.push(Interceptor.attach(BROADCAST_EQUIP, {
    onEnter(args) {
        const player = mainPlayer();
        if (player === null || !args[0].equals(player)) return;
        const h = held(player);
        emit('bcast', { held_r: h.r, held_l: h.l });
    },
}));

// The equipment-change gate (docs/er-mechanics/chainsaw/equip-gate.md): CSPlayerMenuCtrl slot
// +0x108, 1.17.1 0x1407c1fb0 [1.16.2 0x1407c1130]. allowed = (f & 1) && !(f & 0x10) &&
// ((CSChrActionFlagModule+0x10 & 1) || (f & 6)). RCX is CSChrMenuFlags*, f at +0x8 (= CSPlayerMenuCtrl+0x20).
const EQUIP_GATE = va('0x1407c1fb0');
hooks.push(Interceptor.attach(EQUIP_GATE, {
    onEnter(args) {
        try { this.f = args[0].add(0x8).readU32(); } catch (e) { this.f = null; }
    },
    onLeave(ret) {
        emit('gate', { f: this.f, allowed: ret.toInt32() & 0xff });
    },
}));

EXTRA.forEach(function (x) {
    hooks.push(Interceptor.attach(va(x.va), {
        onEnter(args) {
            const a = [];
            for (let i = 0; i < (x.args || 0); i++) a.push(args[i].toString());
            this.a = a;
        },
        onLeave(ret) {
            emit('extra', { name: x.name, args: this.a, ret: ret.toString() });
        },
    }));
});

emit('armed', { hooks: hooks.length, extra: EXTRA.map((x) => x.name), held: held(mainPlayer()) });

rpc.exports = {
    dispose() {
        hooks.forEach(function (h) { h.detach(); });
    },
};
