// Drop network messages whose text came back as the placeholder, and count what was dropped.
//
// # What this suppresses, and what it deliberately does not
//
// ELDEN RING renders `?NetworkMessage?` when a network-message id has no entry in its FMG: the
// category name, wrapped in question marks, standing in for text that does not exist. Measured on
// run br-20260918-174500-8024, the id was 2621201 (`0x27FF11`), and every msgbnd -- base,
// `menu_dlc01`, `menu_dlc02` -- holds `%null%` there. Its neighbour 2621200 is "You died.
// Returning to your world."
//
// So a `?`-prefixed message is one the game had no words for. Refusing to queue it removes a popup
// that says nothing; it cannot hide a real notice, because a real notice has text.
//
// This does NOT fix whatever computes 2621201. If that is an off-by-one on the death notice -- a
// strong reading and not a measured one -- then suppressing the placeholder also suppresses the
// death notice the player was supposed to see, and the off-by-one is still the thing to fix. This
// agent is the measurement that says how often it fires and whether the popup stops; it is not the
// repair.
//
// # Where it cuts
//
// `CS::CSFeMan::ShowNetworkMessage(CSFeManImp*, ushort priority, bool forcePlay, MenuString*,
// bool)` -- 1.16.2 `0x14076e310`, carried to 1.17 `0x14076f190` by a unique 47-byte signature --
// is the single funnel into the summon-message queue:
//
// ```text
//   SummonMsgData::SummonMsgData(&local, priority, forcePlay, message, x)
//   SummonMsgQueue::AddEntry(&fe->summonMsgQueue, &local)
// ```
//
// Returning `false` without calling it is what the game itself does when `GLOBAL_CSFeMan` is null,
// so a refusal here is a shape the callers already handle.
//
// `Interceptor.replace` rather than `attach`, because a skip is the point and `attach` cannot skip.
//
//   python3 scripts/er-frida-up.py
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/suppress-null-network-message.js
const RVA_SHOW_NETWORK_MESSAGE = 0x76f190;

// `CS::MenuString` is `{ wchar_t *rawString; DLString<wchar_t> }` -- text, never an id.
const MENU_STRING_RAW = 0;

// A miss is the category name in question marks; nothing the game ships starts with one.
const PLACEHOLDER_PREFIX = '?';

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
const original = new NativeFunction(target, 'bool', ['pointer', 'uint16', 'bool', 'pointer', 'bool']);

let shown = 0;
let dropped = 0;

Interceptor.replace(target, new NativeCallback(function (fe, priority, forcePlay, message, tail) {
  let text = null;
  try {
    const raw = message.add(MENU_STRING_RAW).readPointer();
    if (!raw.isNull()) text = raw.readUtf16String(64);
  } catch (e) {
    text = null;
  }
  if (text !== null && text.indexOf(PLACEHOLDER_PREFIX) === 0) {
    dropped++;
    send({
      tag: 'dropped-null-network-message',
      dropped: dropped,
      shown: shown,
      text: text,
      priority: priority & 0xffff,
      calledFrom: describe(this.returnAddress),
      note: 'This message had no text, so it was not queued. The popup should not appear.',
    });
    // The same answer the game gives when its own front-end singleton is missing.
    return 0;
  }
  shown++;
  return original(fe, priority, forcePlay, message, tail);
}, 'bool', ['pointer', 'uint16', 'bool', 'pointer', 'bool']));

send({
  tag: 'armed',
  target: target.toString(),
  note: 'Reproduce whatever raised `?NetworkMessage?`. A `dropped-null-network-message` line means the popup was refused; no line and a popup means it came from somewhere else.',
});
