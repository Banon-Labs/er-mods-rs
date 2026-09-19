// Find Seamless's owner object without opening its menu and without a shape scan.
//
// `er_invasion_warp` learns the owner only when `ersc+0x241a0` (`show`) runs, which needs Seamless's
// own dialog, which needs one of its items to be consumed. Every run that never gets there logs
// `ersc_session=SessionNotIdentified` and can drive nothing. The fallback is a shape scan that
// answers with tens of thousands of candidates, which is the scan failing rather than succeeding.
//
// There is an exact alternative. Seamless registers its item handler at `ersc+0x7c423` as a
// two-word closure laid out `{captured object, fn}`:
//
//   0x18007c41c   mov [rbp+0xe8], rsi            <- the captured object
//   0x18007c423   lea rax,[rip+0x163f6]          -> 0x180092820
//   0x18007c42a   mov [rbp+0xf0], rax            <- the function, one qword after it
//
// and the handler reads `owner = [captured+0x58]`, `session = [owner+0x58]`. So the function
// pointer is a unique 8-byte needle: find it in writable memory, read the qword BEFORE it, and the
// owner chain follows. No menu, no item, no heuristic.
//
// This agent only reads. It reports every candidate with the session state it resolves to, so a
// wrong hit is visible as an implausible state rather than being adopted silently.
'use strict';

const HANDLER_RVA = 0x92820;
const OWNER_AT_CAPTURED_OFFSET = 0x58;
const SESSION_AT_OWNER_OFFSET = 0x58;
const SESSION_STATE_OFFSET = 0x150;
const CLOSURE_FN_AT_OFFSET = 8;

// The states `ersc.dll` is known to write into `session+0x150`, taken from the writes this repo has
// read out of the image: 0/1 idle, 2, 4, 5, 6, 7, 0xa, 0xe searching, 0x16 in-world, 0x23/0x24
// cancelling. A candidate resolving outside this set is reported and not believed.
const PLAUSIBLE_STATES = [0, 1, 2, 3, 4, 5, 6, 7, 0xa, 0xe, 0xf, 0x10, 0x12, 0x13, 0x16, 0x23, 0x24];

const ersc = Process.findModuleByName('ersc.dll');

function hex(p) { return p === null || p.isNull() ? 'null' : '0x' + p.toString(16); }
function follow(a) { try { return a.readPointer(); } catch (e) { return null; } }
function u32(a) { try { return a.readU32(); } catch (e) { return null; } }

function scan() {
    if (ersc === null) return { ok: false, why: 'ersc.dll is not loaded' };
    const handler = ersc.base.add(HANDLER_RVA);
    const bytes = handler.toMatchPattern();

    // Writable memory, executable or not. The first pass asked for `rw-` and capped ranges at 4 MB,
    // which answered zero -- and could not have answered anything else: `rw-` excludes the 11.4 MB
    // `rwx` region ersc unpacks itself into, and the cap excluded 71 of 972 ranges, which is where
    // a game's large heaps live. A zero from a scan that cannot reach the memory in question is not
    // evidence of absence, so both restrictions are gone.
    const ranges = Process.enumerateRanges('rw-').concat(Process.enumerateRanges('rwx'));
    const hits = [];
    let scanned = 0;
    let bytesScanned = 0;
    for (let i = 0; i < ranges.length && hits.length < 64; i += 1) {
        const r = ranges[i];
        scanned += 1;
        bytesScanned += r.size;
        let found = [];
        try { found = Memory.scanSync(r.base, r.size, bytes); } catch (e) { continue; }
        for (let j = 0; j < found.length && hits.length < 64; j += 1) {
            const at = found[j].address;
            if (at.toUInt32() % 8 !== 0) continue;
            const captured = follow(at.sub(CLOSURE_FN_AT_OFFSET));
            if (captured === null || captured.isNull()) continue;
            const owner = follow(captured.add(OWNER_AT_CAPTURED_OFFSET));
            if (owner === null || owner.isNull()) continue;
            const session = follow(owner.add(SESSION_AT_OWNER_OFFSET));
            if (session === null || session.isNull()) continue;
            const state = u32(session.add(SESSION_STATE_OFFSET));
            hits.push({
                closure: hex(at),
                captured: hex(captured),
                owner: hex(owner),
                session: hex(session),
                state: state === null ? null : '0x' + (state >>> 0).toString(16),
                plausible: state !== null && PLAUSIBLE_STATES.indexOf(state) !== -1
            });
        }
    }
    return {
        ok: true,
        needle: hex(handler),
        rangesScanned: scanned,
        rangesTotal: ranges.length,
        mbScanned: Math.round(bytesScanned / (1024 * 1024)),
        hits: hits,
        believable: hits.filter(function (h) { return h.plausible; })
    };
}

send({ kind: 'ready', ersc: hex(ersc === null ? null : ersc.base) });
send({ kind: 'scan', result: scan() });

// Re-read the chain from a known closure address, with no scan.
//
// This is the question the DLL's cache actually has to answer: not "does this look like a session"
// -- which is state-sensitive and says no while a search is running -- but "is this still the same
// object". Reporting the state beside the pointers is what makes the difference visible: if the
// pointers hold constant across a state change, then revalidating by pointer is sound and
// revalidating by predicate is what was throwing the cache away.
function chain(at) {
    const start = ptr(at);
    const captured = follow(start.sub(CLOSURE_FN_AT_OFFSET));
    if (captured === null || captured.isNull()) return { ok: false, why: 'captured unreadable' };
    const owner = follow(captured.add(OWNER_AT_CAPTURED_OFFSET));
    if (owner === null || owner.isNull()) return { ok: false, why: 'owner unreadable' };
    const session = follow(owner.add(SESSION_AT_OWNER_OFFSET));
    if (session === null || session.isNull()) return { ok: false, why: 'session unreadable' };
    return {
        ok: true,
        captured: hex(captured),
        owner: hex(owner),
        session: hex(session),
        state: sessionState(session)
    };
}

function sessionState(session) {
    const value = u32(session.add(SESSION_STATE_OFFSET));
    return value === null ? null : '0x' + (value >>> 0).toString(16);
}

rpc.exports = { scan: scan, chain: chain };
