// Catch Seamless's option-menu object as it is handed to the game, instead of scanning for it.
//
// # Why a hook and not a scan
//
// Four scans of `ersc.dll`'s writable data returned four different addresses in one process
// (0x1d2b85af0, 0x16d40038, 0xb7b94824, 0x45ba80), and an object that moves between two reads of
// one process is a filter matching noise rather than an identification. Calling `ersc+0x25850` on
// one of them parked a Frida thread inside a lock helper that acquires without a timeout and cost
// a relaunch. So the object is taken from Seamless's own call instead.
//
// # Where it comes from
//
// `ersc+0x241a0` is `show(osm, menuId)`. At `ersc+0x241cb` it does `lea r14, [rcx + 0x120]` and
// then calls the game's `CS::CSMenuMan::OpenConversationChoicesMenu`. So at that function's entry
// `r14` is `osm + 0x120`, and the object `ersc+0x25850` wants as its first argument is `r14 - 0x120`
// -- read out of the decrypted image, not guessed.
//
// Read-only. Nothing here calls into `ersc.dll`.

'use strict';

const OPEN_CONVERSATION_CHOICES_MENU = ptr('0x140ea0360');
const OSM_FROM_R14 = 0x120;
const OWNER_SESSION = 0x58;
const SESSION_STATE = 0x150;

// The states the four option-row actions read and write, read out of the decrypted image.
// `0` is deliberately absent: zeroed memory is the most common thing in a writable page and this
// repo has latched onto it before.
const SESSION_STATES = [1, 2, 4, 6, 7, 0x0e, 0x0f, 0x12, 0x16, 0x23, 0x24];

const WORLD_CHR_MAN = ptr('0x143d69ff8');
const MAIN_PLAYER = 0x1e508;
const GET_SELECTED_QUICK_SLOT_ITEM_ID = ptr('0x140657410');

const out = { owner: null, session: null, sessionState: null, opens: 0, queries: 0, log: [] };

function note (line) {
  out.log.push(line);
  if (out.log.length > 40) out.log.shift();
  send({ kind: 'owner', line: line });
}

// About 28 percent of function entries on this build open with an Arxan healing stub. A hook on the
// entry address then catches nothing, which is how a zero-call reading gets manufactured.
function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

function player () {
  const world = WORLD_CHR_MAN.readPointer();
  if (world.isNull()) return null;
  const p = world.add(MAIN_PLAYER).readPointer();
  return p.isNull() ? null : p;
}

function describeSession (osm) {
  let session;
  try { session = osm.add(OWNER_SESSION).readPointer(); } catch (e) { return null; }
  if (session.isNull()) return null;
  let st;
  try { st = session.add(SESSION_STATE).readU32(); } catch (e) { return null; }
  return { session: String(session), state: st, known: SESSION_STATES.indexOf(st) >= 0 };
}

Interceptor.attach(follow(OPEN_CONVERSATION_CHOICES_MENU), {
  onEnter () {
    out.opens += 1;
    let osm;
    try { osm = this.context.r14.sub(OSM_FROM_R14); } catch (e) { return; }
    const described = describeSession(osm);
    if (described === null) {
      note('menu open #' + out.opens + ' at osm=' + osm + ' has no readable session at +0x58');
      return;
    }
    note('menu open #' + out.opens + ' osm=' + osm + ' session=' + described.session
      + ' state=0x' + (described.state >>> 0).toString(16)
      + (described.known ? '' : ' (state is not one Seamless writes)'));
    if (!described.known) return;
    out.owner = String(osm);
    out.session = described.session;
    out.sessionState = described.state;
  },
});

// The oracle. A search that never asks Steam for a lobby list is not a search, and this is the only
// reading of that which our own writes cannot manufacture.
const steam = Process.findModuleByName('steam_api64.dll');
for (const exp of steam ? steam.enumerateExports() : []) {
  if (exp.type !== 'function') continue;
  if (exp.name.indexOf('RequestLobbyList') < 0) continue;
  try {
    Interceptor.attach(exp.address, {
      onEnter () {
        out.queries += 1;
        note(exp.name + ' #' + out.queries);
      },
    });
    note('oracle on ' + exp.name);
  } catch (e) {
    note('could not hook ' + exp.name + ': ' + e);
  }
}

rpc.exports = {
  report: function () { return out; },
  // What the quick-item cursor is on, asked of the game rather than tracked by us.
  selected: function () {
    const p = player();
    if (p === null) return null;
    const fn = new NativeFunction(GET_SELECTED_QUICK_SLOT_ITEM_ID, 'pointer', ['pointer', 'pointer']);
    const slot = Memory.alloc(4);
    slot.writeS32(-1);
    fn(p, slot);
    const id = slot.readS32();
    return { id: id, hex: '0x' + (id >>> 0).toString(16) };
  },
  // The captured object, re-validated at call time rather than trusted from capture time.
  owner: function () {
    if (out.owner === null) return { ok: false, why: 'no menu open has been caught yet' };
    const described = describeSession(ptr(out.owner));
    if (described === null) return { ok: false, why: 'the captured object no longer reads a session' };
    return { ok: true, owner: out.owner, session: described.session, state: described.state,
      known: described.known };
  },
};
console.log('capture-seamless-owner: armed');
