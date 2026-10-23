// Read-only check of er-npc-summons' companion tag hooks (bd er-effects-rs-7xbs.3, 2026-10-19).
//
// The DLL's three ilhook mid hooks put each live Mimic Tear companion in the per-player friendly
// tag list (CSFeManImp +0x5c60, 7 x 0xb0), give its row roleNameColor 1, and keep it out of the
// enemy tag list (CSFeManImp +0x59f0, 8 x 0x40) when it is hit. This agent reads those lists and
// says, whenever the picture changes, whether the companions' handles are where they should be.
// It mirrors spawn-npc.js's tagCheck, with one difference: it cannot ask the DLL which characters
// are companions, so it finds them itself, as the characters in the player's spirit-summon groups
// (SummonBuddyManager +0x70, the tree BuddyGenerator fills; bd
// summon-groups-hud-bar-and-removechrins-1171-static-2026-10-06). That set is right while the
// summons out are the Mimic Tear's configured companions; any other spirit ash would be counted
// too and read as missing from the list, and a companion at 0 HP is left out by the DLL on purpose.
//
// Nothing is written and no behaviour changes. The one Interceptor is on FRAME_TICK, the
// per-frame function spawn-npc.js drives its own frame from, and only paces the reads; there are
// no timers, because a pre-commit gate refuses them in agents. Addresses are 2.7.2.0, carried from
// 1.17.1 and written `1.17.1 -> 2.7.2.0` below.
//
// Proof the bd issue asks for: every companion's handle in a friendly row at or past the party's
// member count with colour (+0x13) 1, and, after a companion is hit, no enemy row (+0x0) holding
// a companion's handle.
'use strict';

const game = Process.getModuleByName('eldenring.exe');
const BASE = ptr('0x140000000');
const va = (s) => game.base.add(ptr(s).sub(BASE));

// FRAME_TICK 0x140773900 -> 0x140773db0: the whole body matches eldenring-deobf-1.17.2.bin apart
// from rip-relative and branch operands. The globals were checked through their readers: of the
// 1.17.1 instructions that read each one, the same instruction at the carried address reads the
// carried global for CSFeManImp 140 of 141 readers, WorldChrMan 2506 of 2552, GameMan 341 of 344;
// the rest are refused by the carry or decode differently in 2.7.2.0.
const FRAME_TICK = va('0x140773db0');
const CS_FE_MAN = va('0x143d72910');       // 0x143d6f8f0 -> 0x143d72910
const WORLD_CHR_MAN = va('0x143d6d018');   // 0x143d69ff8 -> 0x143d6d018
const GAME_MAN = va('0x143d709a8');        // 0x143d6d988 -> 0x143d709a8

// The producer that writes the friendly rows and reads PartyMemberInfo +0x14 (0x140778200 ->
// 0x1407786b0) and CSChrDamageModule::UpdateUI (0x1404497e0 -> 0x1404498c0) have the same bodies
// in 2.7.2.0, so these row layouts did not move.
const FRIENDLY_ROWS = 0x5c60;
const FRIENDLY_ROW_SIZE = 0xb0;
const FRIENDLY_ROW_COUNT = 7;
const ROW_SHOWN = 0x10;
const ROW_DOCKED = 0x12;
const ROW_COLOUR = 0x13;
const ROW_HP = 0x7c;
const ROW_HANDLE = 0x98;
const ROW_TEAM = 0xa2;
const ENEMY_ROWS = 0x59f0;
const ENEMY_ROW_SIZE = 0x40;
const ENEMY_ROW_COUNT = 8;
const ENEMY_LOCK_ON = 0x20;
const ENEMY_DAMAGE = 0x24;

// WorldChrManImp grew 0x10 bytes past +0x1e5e8 in 2.7.2.0; +0x1e538 is below that, and the 11 of
// its 13 1.17.1 readers that the carry accepts are unchanged at their carried place.
const SUMMON_BUDDY_MANAGER = 0x1e538;
const GROUP_TREE_HEAD = 0x78;
const NODE_LEFT = 0x0;
const NODE_RIGHT = 0x10;
const NODE_IS_NIL = 0x19;
const NODE_PARENT = 0x8;
const NODE_LIST_HEAD = 0x30;
const ENTRY_NEXT = 0x0;
const ENTRY_CHR = 0x10;
const CHR_HANDLE = 0x8;

const GAME_MAN_PARTY = 0xd90;
const PARTY_MEMBER_COUNT = 0x14;

const FRAMES_PER_READ = 30;
const WALK_BOUND = 64;

let frames = 0;
let sent = null;
const lastHp = new Map();
let hits = 0;

function emit(kind, fields) {
    send(Object.assign({ kind: kind, t: Date.now() }, fields));
}

// The ChrIns of every spirit summon in every summon group, as handle strings.
function companionHandles() {
    const wcm = WORLD_CHR_MAN.readPointer();
    if (wcm.isNull()) return [];
    const mgr = wcm.add(SUMMON_BUDDY_MANAGER).readPointer();
    if (mgr.isNull()) return [];
    const head = mgr.add(GROUP_TREE_HEAD).readPointer();
    if (head.isNull()) return [];
    const out = [];
    const stack = [head.add(NODE_PARENT).readPointer()];
    let visited = 0;
    while (stack.length > 0 && visited < WALK_BOUND) {
        const node = stack.pop();
        if (node.isNull() || node.add(NODE_IS_NIL).readU8() !== 0) continue;
        visited += 1;
        stack.push(node.add(NODE_LEFT).readPointer(), node.add(NODE_RIGHT).readPointer());
        const listHead = node.add(NODE_LIST_HEAD).readPointer();
        if (listHead.isNull()) continue;
        let guard = 0;
        for (let e = listHead.add(ENTRY_NEXT).readPointer(); !e.equals(listHead) && !e.isNull() && guard < WALK_BOUND;
            e = e.add(ENTRY_NEXT).readPointer(), guard++) {
            const chr = e.add(ENTRY_CHR).readPointer();
            if (!chr.isNull()) out.push(chr.add(CHR_HANDLE).readU64().toString());
        }
    }
    return out;
}

function partyMembers() {
    const gm = GAME_MAN.readPointer();
    if (gm.isNull()) return null;
    const pmi = gm.add(GAME_MAN_PARTY).readPointer();
    return pmi.isNull() ? null : pmi.add(PARTY_MEMBER_COUNT).readS32();
}

function check() {
    const fe = CS_FE_MAN.readPointer();
    if (fe.isNull()) return;
    const companions = companionHandles();
    const members = partyMembers();
    const friendly = [];
    for (let i = 0; i < FRIENDLY_ROW_COUNT; i++) {
        const row = fe.add(FRIENDLY_ROWS + i * FRIENDLY_ROW_SIZE);
        const handle = row.add(ROW_HANDLE).readU64().toString();
        friendly.push({
            slot: i,
            handle,
            companion: companions.includes(handle),
            pastMembers: members !== null && i >= members,
            hp: row.add(ROW_HP).readS32(),
            shown: row.add(ROW_SHOWN).readU8(),
            docked: row.add(ROW_DOCKED).readU8(),
            colour: row.add(ROW_COLOUR).readU8(),
            team: row.add(ROW_TEAM).readU8(),
        });
    }
    const enemy = [];
    for (let i = 0; i < ENEMY_ROW_COUNT; i++) {
        const row = fe.add(ENEMY_ROWS + i * ENEMY_ROW_SIZE);
        const handle = row.readU64().toString();
        if (!companions.includes(handle)) continue;
        enemy.push({ slot: i, handle, lockOn: row.add(ENEMY_LOCK_ON).readU8(), damage: row.add(ENEMY_DAMAGE).readS32() });
    }
    const listed = friendly.filter((r) => r.companion);
    const verdict = {
        companions: companions.length,
        inFriendlyList: listed.length,
        friendlyColour: listed.filter((r) => r.colour === 1).length,
        inEnemyList: enemy.length,
        ok: companions.length > 0 && listed.length === Math.min(companions.length, FRIENDLY_ROW_COUNT - (members || 0))
            && listed.every((r) => r.colour === 1 && r.pastMembers) && enemy.length === 0,
    };
    // A companion's row HP falling is the hit the enemy-bar hook exists for; without it, an empty
    // enemy list says nothing about that hook. Each drop is reported with the enemy rows read in the
    // same pass.
    for (const r of listed) {
        const before = lastHp.get(r.handle);
        lastHp.set(r.handle, r.hp);
        if (before === undefined || r.hp >= before) continue;
        hits += 1;
        emit('companion-hit', { handle: r.handle, hpBefore: before, hpAfter: r.hp, hits, inEnemyList: enemy.length, enemy });
    }
    const key = JSON.stringify([verdict, friendly.map((r) => [r.slot, r.handle, r.shown, r.docked, r.colour]), enemy]);
    if (key === sent) return;
    sent = key;
    emit('tag-check', { members, companions, verdict, friendly, enemy });
}

Interceptor.attach(FRAME_TICK, {
    onLeave() {
        frames += 1;
        if (frames % FRAMES_PER_READ !== 0) return;
        try {
            check();
        } catch (e) {
            emit('tag-check-error', { error: e.message });
        }
    },
});

emit('armed', { frameTick: FRAME_TICK.toString(), feMan: CS_FE_MAN.toString(), every: FRAMES_PER_READ });
