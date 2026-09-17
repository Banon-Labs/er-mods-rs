// Where does Seamless keep the owner object, without opening its menu and without scanning?
//
// Static read of `ersc-2.0.1.runtime.bin` (base 0x180000000) found one handler that reaches the
// owner on an ordinary item use, registered at 0x18007c423 as a closure over its captured object:
//
//   0x180092820  endbr64 / push rsi,rdi,rbp,rbx / sub rsp,0x78
//                rsi = [rcx+0x58]          <- the OWNER
//                ebp = [r8+0x8]            <- the id it dispatches on
//                call [rsi+0x100]          <- a virtual on the owner, gates everything after
//                rax = [rsi+0x58]          <- the SESSION, which is owner+0x58 as this repo pins
//                ... cmp ebp,0x19d3|0x19d4|0x19d5 -> show(rcx=rsi, edx=8|9|0xa)
//
// So `rcx` at entry is Seamless's mod object, `[rcx+0x58]` is the owner that `show` and the invade
// action both take as their first argument, and `[[rcx+0x58]+0x58]` is the session whose `+0x150`
// this repo already reads as the state.
//
// This agent only watches. It prints the chain and the id dispatched on, so the ids can be named
// from what the character actually used rather than guessed from their numeric value.
'use strict';

const ITEM_USE_HANDLER_RVA = 0x92820;
const SHOW_RVA = 0x241a0;
const OWNER_AT_MOD_OFFSET = 0x58;
const SESSION_AT_OWNER_OFFSET = 0x58;
const SESSION_STATE_OFFSET = 0x150;
const DISPATCH_ID_OFFSET = 0x8;

// The prologue the static read was taken from. A mismatch means this RVA is not that function in
// the loaded build, and the agent says so instead of reporting a chain read off the wrong object.
const EXPECTED_PROLOGUE = 'f30f1efa56575553';

const ersc = Process.findModuleByName('ersc.dll');

function hex(p) { return p === null || p.isNull() ? 'null' : '0x' + p.toString(16); }
function follow(a) { try { return a.readPointer(); } catch (e) { return NULL; } }
function u32(a) { try { return a.readU32(); } catch (e) { return null; } }
function state(session) {
    if (session === null || session.isNull()) return null;
    const v = u32(session.add(SESSION_STATE_OFFSET));
    return v === null ? null : '0x' + (v >>> 0).toString(16);
}

const seen = { calls: 0, ids: {}, owner: null, session: null };

if (ersc === null) {
    send({ kind: 'refused', why: 'ersc.dll is not loaded' });
} else {
    const entry = ersc.base.add(ITEM_USE_HANDLER_RVA);
    let prologue = null;
    try { prologue = entry.readByteArray(8); } catch (e) { prologue = null; }
    const got = prologue === null
        ? 'unreadable'
        : Array.prototype.map.call(new Uint8Array(prologue), function (b) {
            return ('0' + b.toString(16)).slice(-2);
        }).join('');

    send({ kind: 'anchor', ersc: hex(ersc.base), entry: hex(entry), prologue: got, expected: EXPECTED_PROLOGUE });

    if (got !== EXPECTED_PROLOGUE) {
        send({ kind: 'refused', why: 'prologue mismatch -- not attaching', got: got });
    } else {
        Interceptor.attach(entry, {
            onEnter: function (args) {
                const mod = args[0];
                const owner = follow(mod.add(OWNER_AT_MOD_OFFSET));
                const session = follow(owner.add(SESSION_AT_OWNER_OFFSET));
                let id = null;
                try { id = args[2].add(DISPATCH_ID_OFFSET).readU32(); } catch (e) { id = null; }
                seen.calls += 1;
                if (id !== null) seen.ids[id] = (seen.ids[id] || 0) + 1;
                seen.owner = hex(owner);
                seen.session = hex(session);
                send({
                    kind: 'item-use',
                    mod: hex(mod),
                    owner: hex(owner),
                    session: hex(session),
                    state: state(session),
                    id: id,
                    idHex: id === null ? null : '0x' + id.toString(16)
                });
            }
        });
        // The control. `show` is the one function this handler is already known to call, so a run
        // where the handler fires and `show` never does is a run that reached the dispatch and took
        // a different branch -- distinguishable from a run where nothing fired at all.
        Interceptor.attach(ersc.base.add(SHOW_RVA), {
            onEnter: function (args) {
                send({ kind: 'show', osm: hex(args[0]), group: args[1].toInt32() });
            }
        });
    }
}

rpc.exports = {
    report: function () { return seen; }
};
