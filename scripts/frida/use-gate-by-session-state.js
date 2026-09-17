// Is item use refused while a Seamless search is in flight?
//
// User ground truth, 2026-09-16: "I cannot press my square button to activate an item while we are
// searching or until I reach the host's world." Square is the use-item binding. If that is the
// rule, then every `queued but not consumed` run this repo recorded was a use refused at the gate
// rather than a flaky drive -- the counters already show the shape (request latched, item reaches
// `ChrIns+0x160`, `GetSelectedGoodsUseAnim` never asked), and what they lacked was the session
// state beside them.
//
// This agent supplies that. It counts the two functions that mark a use actually starting, and
// reports the Seamless session state at the moment each one fires, so a use from idle and a use
// during a search are distinguishable in the same run.
'use strict';

// `GetSelectedGoodsUseAnim` -- the engine asking which animation to play for the use. Never reached
// on any driven Lynchpin press so far (bd use-animation-path-never-entered-controlled-counters).
const USE_ANIM_RVA = 0x3f0030;
// TAE event 65, `ConsumeCurrentGoods` -- the consume itself.
const CONSUME_RVA = 0x42ca40;
// The control. Extraordinarily hot (~1M calls in 7s), so it is counted and never logged from.
const GOODS_GET_ENTRY_RVA = 0xd3b5b0;

const ERSC_ITEM_HANDLER_RVA = 0x92820;
const NEXT_OBJECT_OFFSET = 0x58;
const SESSION_STATE_OFFSET = 0x150;

const game = Process.findModuleByName('eldenring.exe');
const ersc = Process.findModuleByName('ersc.dll');

function hex(p) { return p === null || p.isNull() ? 'null' : '0x' + p.toString(16); }
function follow(a) { try { return a.readPointer(); } catch (e) { return null; } }
function u32(a) { try { return a.readU32(); } catch (e) { return null; } }

// The session, found the way `er_invasion_warp` finds it: the item-handler closure holds the object
// two `+0x58` hops above the session, and the function pointer is a unique needle.
let session = null;
function findSession() {
    if (ersc === null) return null;
    const needle = ersc.base.add(ERSC_ITEM_HANDLER_RVA).toMatchPattern();
    const ranges = Process.enumerateRanges('rw-').concat(Process.enumerateRanges('rwx'));
    for (let i = 0; i < ranges.length; i += 1) {
        let found = [];
        try { found = Memory.scanSync(ranges[i].base, ranges[i].size, needle); } catch (e) { continue; }
        for (let j = 0; j < found.length; j += 1) {
            const captured = follow(found[j].address.sub(8));
            if (captured === null || captured.isNull()) continue;
            const owner = follow(captured.add(NEXT_OBJECT_OFFSET));
            if (owner === null || owner.isNull()) continue;
            const s = follow(owner.add(NEXT_OBJECT_OFFSET));
            if (s === null || s.isNull()) continue;
            if (u32(s.add(SESSION_STATE_OFFSET)) === null) continue;
            return s;
        }
    }
    return null;
}

function state() {
    if (session === null) session = findSession();
    if (session === null) return 'no-session';
    const v = u32(session.add(SESSION_STATE_OFFSET));
    return v === null ? 'unreadable' : '0x' + (v >>> 0).toString(16);
}

const seen = { useAnim: 0, consume: 0, control: 0, atStates: {}, session: null };

Interceptor.attach(game.base.add(USE_ANIM_RVA), {
    onEnter: function () {
        seen.useAnim += 1;
        const s = state();
        seen.atStates['useAnim@' + s] = (seen.atStates['useAnim@' + s] || 0) + 1;
        send({ kind: 'use-anim', state: s, count: seen.useAnim });
    }
});
Interceptor.attach(game.base.add(CONSUME_RVA), {
    onEnter: function () {
        seen.consume += 1;
        const s = state();
        seen.atStates['consume@' + s] = (seen.atStates['consume@' + s] || 0) + 1;
        send({ kind: 'consume', state: s, count: seen.consume });
    }
});
// No `send()` here -- this one runs about a million times in seven seconds.
Interceptor.attach(game.base.add(GOODS_GET_ENTRY_RVA), {
    onEnter: function () { seen.control += 1; }
});

send({ kind: 'armed', game: hex(game.base), ersc: hex(ersc === null ? null : ersc.base) });

rpc.exports = {
    report: function () {
        seen.session = hex(session === null ? (session = findSession()) : session);
        seen.state = state();
        return seen;
    },
    reset: function () { seen.useAnim = 0; seen.consume = 0; seen.control = 0; seen.atStates = {}; return true; }
};
