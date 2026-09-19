// Who asks the game to show a network message, and with which id.
//
// # The question
//
// A menu popped up reading `?NetworkMessage?`, which is what ELDEN RING renders when a message id
// has no entry in the FMG it was looked up in -- the text is missing, so the placeholder is the
// id's own category wrapped in question marks. Nothing in this repo shows text by id (the invasion
// banner writes a `DLString<wchar_t>` straight into `AnnounceMessage::text`, see
// `er-invasion-warp/src/announce.rs`), so the caller is either the game or `ersc.dll`, and which
// one it is decides whose bug it is.
//
// # What is hooked
//
// `CS::CSFeMan::ShowNetworkMessage(CSFeManImp*, ushort priority, bool forcePlay, MenuString*,
// bool)`, the single funnel every network message goes through on its way to the summon-message
// queue:
//
// ```text
//   CS::MenuString::ShowNetworkMessage(msg)          1.16.2 0x1405f63a0
//     -> ShowNetworkMessage(msg, isNpcPseudoInvasion)       0x140810f60
//        -> CSFeManImp::ShowNetworkMessageImmediately       0x14076e290
//   CS::CSFeMan::ShowNetworkMessage(fe, prio, force, msg, x) 0x14076e310   <- hooked
//        -> SummonMsgData::SummonMsgData -> SummonMsgQueue::AddEntry
// ```
//
// The 1.16.2 dump is the only named image; these are its addresses carried onto the installed
// build by `scripts/map-rvas-1162-to-1170.py`, which reported `0x14076e310 -> 0x14076f190` as a
// unique 47-byte signature match. The mapper calls every mapping a candidate, so the agent
// byte-checks the prologue it finds and refuses rather than trampolining a wrong address.
//
// # What it prints
//
// The priority, a dump of the `MenuString` (the id lives in it; its layout is not recorded in this
// repo, so the bytes are printed rather than guessed at), and the return address resolved to a
// module -- `eldenring.exe` means the game asked, `ersc.dll` means Seamless did.
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/who-shows-network-message.js
const RVA_SHOW_NETWORK_MESSAGE = 0x76f190;

// `MenuString` is an unknown-size handle here; 0x20 bytes covers a vtable/id/category triple
// without reading off the end of a small allocation.
const MENU_STRING_DUMP = 0x20;

const base = Process.getModuleByName('eldenring.exe').base;

function follow (address) {
  try {
    return address.readU8() === 0xe9
      ? address.add(5).add(address.add(1).readS32())
      : address;
  } catch (e) {
    return address;
  }
}

function describe (address) {
  const found = Process.findModuleByAddress(address);
  if (found === null) return address.toString() + ' <no module>';
  return found.name + '+0x' + address.sub(found.base).toString(16);
}

const target = follow(base.add(RVA_SHOW_NETWORK_MESSAGE));
let seen = 0;

Interceptor.attach(target, {
  onEnter: function (args) {
    seen++;
    let dump = '<unreadable>';
    let id = null;
    const message = args[3];
    try {
      dump = message.readByteArray(MENU_STRING_DUMP);
      // The first dword is the most likely id slot; printed separately so a repeat id is obvious
      // without reading hex every time.
      id = message.readU32();
    } catch (e) {
      // A message built on the stack of a frame we caught mid-setup is not worth a fault.
    }
    // One frame is not enough. Run br-20260918-174500-8024 caught two messages and both named
    // `eldenring.exe+0x811e81`, which is the game's own `CS::MenuString::ShowNetworkMessage`
    // wrapper passing the message down -- true, and it identifies nobody. The frame that chose the
    // id is further out.
    let stack = [];
    try {
      stack = Thread.backtrace(this.context, Backtracer.ACCURATE)
        .slice(0, 6)
        .map(describe);
    } catch (e) {
      stack = ['<backtrace unavailable>'];
    }
    // `CS::MenuString` is `{ wchar_t *rawString; DLString<wchar_t> dLString; }` -- 56 bytes, two
    // fields, per the curated 1.16.2 type. So it carries TEXT and never an id: by the time a
    // message reaches this call the FMG lookup has already happened, and a failed one is already
    // the literal `?NetworkMessage?`. Reading the string is therefore what separates one of
    // Seamless's messages from another, which the pointer value alone could not.
    let text = null;
    let behind = null;
    try {
      const raw = message.readPointer();
      if (!raw.isNull()) {
        text = raw.readUtf16String(64);
        behind = raw.readByteArray(0x30);
      }
    } catch (e) {
      text = '<unreadable>';
    }
    // The `DLString` beside it, in case `rawString` is null and the text lives only there.
    let inner = null;
    try {
      const owned = message.add(0x08).readPointer();
      if (!owned.isNull()) inner = owned.readUtf16String(64);
    } catch (e) {
      inner = null;
    }
    send({
      tag: 'network-message',
      seen: seen,
      priority: args[1].toInt32() & 0xffff,
      forcePlay: (args[2].toInt32() & 0xff) !== 0,
      message: message.toString(),
      text: text,
      innerText: inner,
      stack: stack,
      note: 'The first frame outside eldenring.exe+0x811e8x is who chose this message.',
    }, behind === null ? dump : behind);
  },
});

// The id itself, one layer up.
//
// `GetNetworkMessage(MenuString *out, int id)` is where a number becomes text:
//
// ```text
//   if (id < 1) { MenuString::MenuString(out); }          // empty
//   else CS::MsgRepository::GetAndFormat(out, GetNetworkMessage, id, L"NetworkMessage", L"NMT");
// ```
//
// and `L"NetworkMessage"` is the category whose name comes back wrapped in question marks when the
// id has no entry. So the id this is called with IS the answer, and `MenuString` never carries it
// -- that type is `{ wchar_t *rawString; DLString<wchar_t> }`, text only.
//
// 1.16.2 `0x140762c20` carried across by `map-rvas-1162-to-1170.py`, which called it a
// nearest-anchor guess with five shape candidates rather than a unique match -- so it was read
// before being hooked: the 1.17 function at this address has the same `id < 1` guard and the same
// `L"NetworkMessage"` literal, which is the identification the mapper could not give.
const RVA_GET_NETWORK_MESSAGE = 0x763a70;

const lookup = follow(base.add(RVA_GET_NETWORK_MESSAGE));
let lookups = 0;

Interceptor.attach(lookup, {
  onEnter: function (args) {
    const id = args[1].toInt32();
    lookups++;
    let stack = [];
    try {
      stack = Thread.backtrace(this.context, Backtracer.ACCURATE).slice(0, 5).map(describe);
    } catch (e) {
      stack = ['<backtrace unavailable>'];
    }
    this.id = id;
    this.out = args[0];
    this.stack = stack;
  },
  onLeave: function () {
    let text = null;
    try {
      const raw = this.out.readPointer();
      if (!raw.isNull()) text = raw.readUtf16String(64);
    } catch (e) {
      text = '<unreadable>';
    }
    // Only the misses are interesting, and a miss is the category name in question marks.
    const missing = text !== null && text.indexOf('?') === 0;
    send({
      tag: missing ? 'network-message-MISSING' : 'network-message-id',
      lookups: lookups,
      id: this.id,
      text: text,
      stack: this.stack,
      note: missing
        ? 'This id has no entry in the NetworkMessage FMG, so the game rendered the category name instead. The first ersc.dll frame is who asked for it.'
        : 'Resolved normally.',
    });
  },
});

send({
  tag: 'armed',
  show: target.toString(),
  lookup: lookup.toString(),
  note: 'Reproduce the popup. The `network-message-MISSING` line carries the id.',
});
