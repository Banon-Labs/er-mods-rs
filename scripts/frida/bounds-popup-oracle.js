// A native detector for the vanilla invasion-bounds popup, so its appearance stops being something
// the user has to report.
//
// # Why three sites and why each is followed
//
// Measured live on 1.17.1: the popup ("Attempting to invade another world. / Select the bounds for
// the attempt.") is raised by the VANILLA goods path, not by ersc.dll -- dispatcher
// `FUN_1407c3930`, raiser `FUN_1407c3310`, whose shape is
// `(CSPlayerMenuCtrl*, u8, u8, u32 messageId, u8, u32 kind)` with kind=4 for the two-option bounds
// prompt and kind=1 for yes/no. `CS::CSPlayerMenuCtrl::StartInvasionFromBoundsPopup` at
// `0x1407c1dd0` is where the product takes the answer over.
//
// Every one is read for a leading `e9` first. About 28 percent of function entries on this build
// are Arxan stubs -- a rel32 jump with the original bytes 5..15 intact -- and a hook placed on the
// stub is overwritten rather than installed, which reads as "the function never ran". That is the
// same trap the Wine XInput thunk set, and the same fix: follow it.
const SITES = {
  dispatcher: ptr('0x1407c3930'),
  raiser: ptr('0x1407c3310'),
  startInvasion: ptr('0x1407c1dd0'),
};
// The two-option bounds prompt. Recorded so a yes/no prompt (kind=1) reaching the same raiser is
// not mistaken for it.
const KIND_TWO_OPTION = 4;

function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

const info = {};
const hits = {};

for (const name of Object.keys(SITES)) {
  const entry = SITES[name];
  const body = follow(entry);
  info[name] = {
    entry: entry.toString(),
    body: body.toString(),
    stubbed: !body.equals(entry),
  };
  hits[name] = 0;
  Interceptor.attach(body, {
    onEnter (args) {
      hits[name] += 1;
      if (name !== 'raiser') {
        send({ kind: name, line: name + ' fired' });
        return;
      }
      // The ids go out as FIELDS, not only inside the sentence. A caller that has to parse the
      // sentence to learn which prompt is up is a caller that will eventually answer the wrong
      // one, and answering the wrong one here either arms `Nearby only` by accident or cancels an
      // invasion in progress. Both have happened.
      lastCtrl = args[0];
      const message = args[3].toUInt32();
      const kind = args[5].toUInt32();
      const which = kind === KIND_TWO_OPTION ? ' <- the two-option bounds prompt' : '';
      // The CALLER is the finding when an unexpected prompt goes up. `?NetworkMessage?` is an
      // FMG id with no entry, so the only thing that names the code which decided to raise it is
      // the chain that reached here.
      let chain = [];
      try {
        chain = Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 14).map(function (a) {
          const m = Process.findModuleByAddress(a);
          return m === null ? String(a) : m.name + '+0x' + a.sub(m.base).toString(16);
        });
      } catch (e) { /* fault-closed: a fuzzy walk can fail on a packed frame */ }
      send({
        kind: name,
        message: message,
        promptKind: kind,
        chain: chain,
        line: 'raiser message=' + message + ' kind=' + kind + which,
      });
    },
  });
}

// The `CSPlayerMenuCtrl*` the raiser was called with, kept so the dialog object can be read while
// it is on screen. The two rows are laid out LEFT/RIGHT, not up/down -- D-pad down does nothing on
// this prompt -- so a driver has to prove the cursor moved before it confirms, and that proof has
// to come from the object rather than from having pressed a button.
let lastCtrl = null;

rpc.exports = {
  info: function () { return info; },
  hits: function () { return hits; },
  ctrl: function () { return lastCtrl === null ? null : lastCtrl.toString(); },
  // A window of the dialog object, for diffing across a cursor press. Returned as an array of
  // dwords because the selected index is a small integer and a byte diff buries it in pointers.
  window: function (size) {
    if (lastCtrl === null) return null;
    const words = [];
    const count = (size || 0x200) / 4;
    for (let i = 0; i < count; i++) {
      try { words.push(lastCtrl.add(i * 4).readU32()); } catch (e) { words.push(null); }
    }
    return words;
  },
};
console.log('bounds-popup-oracle: armed on ' + Object.keys(SITES).length + ' site(s)');
