// Mimic Tear Ashes (any +N) summon the three turtles instead of the Mimic Tear, through the game's
// own spirit-ash flow: the item use, FP cost, summoning-pool range check, one-summon replacement,
// dismissal and packet 78 to Seamless peers all stay native; only the BuddyParam values that
// SummonBuddyManager::BuddyGenerator reads change, and only inside a Mimic Tear BuddyGenerator call.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/mimic-tear-turtles.js
//
// Standalone on purpose (scripts/frida/spawn-npc.js is owned elsewhere); it can also be pasted into
// that agent, where `created` is what its dressing code (gear, face, names) should be fed.
// Static RE behind it: bd mimic-tear-summon-hijack-1171-2026-10-06. Addresses are 1.17.1, read out
// of eldenring-deobf-1.17.1.bin; the two marked `shifted` sit above 0xafefe9 and carry the +0x70.
//
// How the native flow runs (1.17.1):
//   goods 2070NN (goodsType 8, refCategory 2, refId_default 2070NN = the SpEffect) is used;
//   ApplyBuddySpawnSpEffect 0x1404b85b0 checks the stone range and writes mgr+0x3c stone,
//   +0xe4 activeSummonGoodsId and +0x20 requestSummonSpEffect = 2070NN;
//   SummonBuddyManager::Update 0x1404b86d0 removes the old summons and calls BuddyGenerator;
//   BuddyGenerator 0x1404bbdd0 -> GetBuddyListBySpEffectId(mgr, 207000) = [20700000, 20700001]
//   -> per row GetBuddyParam (ret 0x1404bbfa3) reads npc/think/charaInit/x/z/yaw -> spawn requests
//   -> per request CreateSummonChr 0x1404baea0, net-sync setup, CopyAnother 0x140654610 when any
//   request carried 20700000 (cmp at 0x1404bc384) and the chr is an NPC, packet 78 0x140ca00f0.
'use strict';

const game = Process.findModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

const BUDDY_GENERATOR = va('0x1404bbdd0');   // void(SummonBuddyManager* rcx)
const GET_BUDDY_LIST = va('0x1404bce80');    // void(mgr rcx, int spEffect edx, List<int>* r8)
// std::list node insert the list fill uses: node*(List*, next, prev, int* value); node {+0 next,
// +8 prev, +0x10 value}; the caller then does size++, head->prev = node, node->prev->next = node.
const LIST_INSERT = new NativeFunction(va('0x1404b4fb0'), 'pointer', ['pointer', 'pointer', 'pointer', 'pointer']);
const GET_BUDDY_PARAM = va('0x140d28010');   // shifted; void(BuddyParamLookup* rcx {+0 id, +8 row}, int id edx)
const LOOP1_RET = va('0x1404bbfa3');         // BuddyGenerator's per-row GetBuddyParam call returns here
const CREATE_SUMMON_CHR = va('0x1404baea0');
const BROADCAST_BUDDY = va('0x140ca00f0');   // shifted; void(void*, BuddyPacketEntry* rdx)
const SOLO_PARAM_REPOSITORY = va('0x143d85f58');
const BUDDY_PARAM_INDEX = 128;               // GetBuddyParam passes edx 0x80 at 0x140d2807a

const MIMIC_TRIGGER = 207000;                // BuddyParam triggerSpEffectId of both Mimic Tear rows
const HUMAN_ROW = 20700001;                  // the c0000 row (charaInit 26050, generateAnimId 60500)

// BUDDY_PARAM_ST offsets (paramdef, no drift against the 1.17.1 regulation).
const ROW = { NPC: 0x08, THINK: 0x0c, X: 0x18, Z: 0x1c, YAW: 0x20, CHARA_INIT: 0x54, SAVE: 0x58 };

// Turtle k and its spot around the summon point (the Lone Wolf Ashes rows 21400000..2).
const TURTLES = [
    { npc: 523590024, think: 523590100, charaInit: 23590, x: 1.2, z: 1.3, yaw: 0 },
    { npc: 523590024, think: 523590100, charaInit: 23590, x: 1.5, z: -1.2, yaw: 55 },
    { npc: 523590024, think: 523590100, charaInit: 23590, x: -1.4, z: 0.8, yaw: -10 },
];

function emit(kind, fields) {
    send(Object.assign({ kind: 'mimic-' + kind }, fields || {}));
}

// Row `id` of solo param `index`, as spawn-npc.js paramRow reads it (holder +0x80 count, +0x88 ptr,
// stride 72; checked against the getter 0x140d4ea00).
function paramRow(index, id) {
    const repo = SOLO_PARAM_REPOSITORY.readPointer();
    if (repo.isNull() || repo.add(0x80 + index * 72).readS32() <= 0) return null;
    const blob = repo.add(0x88 + index * 72).readPointer().add(0x80).readPointer().add(0x80).readPointer();
    let lo = 0;
    let hi = blob.add(0x0a).readU16() - 1;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const entry = blob.add(0x40 + mid * 24);
        const at = entry.readU32();
        if (at === id) return blob.add(entry.add(8).readU64().toNumber());
        if (at < id) lo = mid + 1;
        else hi = mid - 1;
    }
    return null;
}

let hijackTid = null;   // the thread inside a Mimic Tear BuddyGenerator call
let saved = null;       // { row, bytes } of HUMAN_ROW to put back
let rowIndex = 0;       // loop-1 GetBuddyParam calls seen in this BuddyGenerator call
const created = [];     // ChrIns* of the last hijacked summon, in creation order
// Kept alive for the life of the script: Frida frees a Memory.alloc block once its JS object is gone.
const insertValue = Memory.alloc(4);
insertValue.writeS32(HUMAN_ROW);

function restoreRow() {
    if (saved === null) return;
    saved.row.writeByteArray(saved.bytes);
    saved = null;
}

const hooks = [];

// Scope everything to a BuddyGenerator whose request is a Mimic Tear: requestSummonSpEffect at
// mgr+0x20 (read at 0x1404bbed9) is 207000 + upgrade level.
hooks.push(Interceptor.attach(BUDDY_GENERATOR, {
    onEnter(args) {
        this.hijacked = false;
        const request = args[0].add(0x20).readS32();
        if (request < 0 || Math.floor(request / 100) * 100 !== MIMIC_TRIGGER) return;
        const row = paramRow(BUDDY_PARAM_INDEX, HUMAN_ROW);
        if (row === null) {
            emit('error', { why: 'no BuddyParam row ' + HUMAN_ROW });
            return;
        }
        saved = { row: row, bytes: row.readByteArray(ROW.SAVE) };
        hijackTid = this.threadId;
        rowIndex = 0;
        created.length = 0;
        this.hijacked = true;
        emit('enter', { request: request, level: request % 100 });
    },
    onLeave() {
        if (!this.hijacked) return;
        restoreRow();
        hijackTid = null;
        emit('leave', { chrs: created.map(String), rows: rowIndex });
    },
}));

// Every list node becomes HUMAN_ROW, so no request carries 20700000 and the copy-the-player branch
// (cmpl $0x13bdb60,0x50(%rax) at 0x1404bc384 -> CopyAnother at 0x1404bc5cf) never arms; then the
// list grows to three with the game's own insert, the sequence at 0x1404bcef0..0x1404bcf26.
hooks.push(Interceptor.attach(GET_BUDDY_LIST, {
    onEnter(args) {
        this.list = (this.threadId === hijackTid && args[1].toInt32() === MIMIC_TRIGGER) ? args[2] : null;
    },
    onLeave() {
        const list = this.list;
        if (list === null) return;
        const head = list.add(8).readPointer();
        for (let n = head.readPointer(); !n.equals(head); n = n.readPointer()) n.add(0x10).writeS32(HUMAN_ROW);
        while (list.add(0x10).readU64().toNumber() < TURTLES.length) {
            const node = LIST_INSERT(list, head, head.add(8).readPointer(), insertValue);
            list.add(0x10).writeU64(list.add(0x10).readU64().add(1));
            head.add(8).writePointer(node);
            node.add(8).readPointer().writePointer(node);
        }
        emit('list', { size: list.add(0x10).readU64().toNumber() });
    },
}));

// Each loop-1 read of HUMAN_ROW gets turtle k written into the row before BuddyGenerator copies
// npc/think/charaInit/x/z/yaw out of it (0x1404bbfa3 onward). From there the NpcParam hit radius,
// the navmesh-projected spot, CreateSummonChr's arguments and packet 78 all carry turtle values.
hooks.push(Interceptor.attach(GET_BUDDY_PARAM, {
    onEnter(args) {
        this.res = (this.threadId === hijackTid) ? args[0] : null;
    },
    onLeave() {
        if (this.res === null || !this.returnAddress.equals(LOOP1_RET) || saved === null) return;
        const row = this.res.add(8).readPointer();
        if (!row.equals(saved.row)) return;
        const t = TURTLES[Math.min(rowIndex, TURTLES.length - 1)];
        row.add(ROW.NPC).writeS32(t.npc);
        row.add(ROW.THINK).writeS32(t.think);
        row.add(ROW.CHARA_INIT).writeS32(t.charaInit);
        row.add(ROW.X).writeFloat(t.x);
        row.add(ROW.Z).writeFloat(t.z);
        row.add(ROW.YAW).writeFloat(t.yaw);
        rowIndex += 1;
    },
}));

// Record what was created; arguments are read, never written.
hooks.push(Interceptor.attach(CREATE_SUMMON_CHR, {
    onEnter(args) {
        this.mine = this.threadId === hijackTid;
        if (this.mine) {
            this.what = { npc: args[6].toInt32(), think: args[7].toInt32(), charaInit: args[8].toInt32(),
                buddyParam: args[14].toInt32(), doping: args[15].toInt32() };
        }
    },
    onLeave(ret) {
        if (!this.mine) return;
        if (!ret.isNull()) created.push(ptr(ret.toString()));
        emit('created', Object.assign({ chr: ret.toString() }, this.what));
    },
}));

// What the peers are told (BuddyPacketEntry +0x2c npc, +0x30 think, +0x34 charaInit, +0x38 buddyParam).
hooks.push(Interceptor.attach(BROADCAST_BUDDY, {
    onEnter(args) {
        if (this.threadId !== hijackTid) return;
        const p = args[1];
        emit('packet78', { npc: p.add(0x2c).readS32(), think: p.add(0x30).readS32(),
            charaInit: p.add(0x34).readS32(), buddyParam: p.add(0x38).readS32() });
    },
}));

rpc.exports = {
    // The ChrIns pointers of the last hijacked summon.
    mimicCreated() {
        return created.map(String);
    },
    // Frida calls this on unload and on a hot reload: never leave the row patched.
    dispose() {
        restoreRow();
        hooks.forEach((h) => h.detach());
    },
};

emit('armed', { buddyParamRow: String(paramRow(BUDDY_PARAM_INDEX, HUMAN_ROW)) });
