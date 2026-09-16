// Which button did the player press in the invasion-bounds popup?
//
// `FUN_1407c2ab0` is the goods popup dispatcher: it switches on the item's `opmeMenuType` and,
// for the invasion fingers, raises either the bounds prompt or the cancel prompt through
// `FUN_1407c2490(ctrl, a, b, messageId, 4, kind)`:
//
//   no search active  -> messageId 0x1312d0a (20000010) `Select the bounds for the attempt.`
//   search active     -> messageId 0x1312d0b (20000011) `Cancel invasion of other world?`
//
// The buttons themselves are 20000015 `Nearby only` and 20000016 `Both near and far`. This logs
// the raiser's arguments so the prompt is confirmed live, rather than inferred from a decompile.
const DISPATCH_RVA = 0x7c3930; // 1.17, unique 41B signature, +0xe80 from 1.16.2 0x1407c2ab0
const RAISE_RVA = 0x7c3310; // 1.17, unique 26B signature, +0xe80 from 1.16.2 0x1407c2490

const MESSAGES = {
  0x1312d0a: 'bounds prompt (Select the bounds for the attempt)',
  0x1312d0b: 'cancel prompt (Cancel invasion of other world?)',
  0x1312d0f: 'Nearby only',
  0x1312d10: 'Both near and far',
};

const game = Process.findModuleByName('eldenring.exe');
const shape = (at) =>
  Array.from(new Uint8Array(at.readByteArray(8)), (b) => b.toString(16).padStart(2, '0')).join(' ');

for (const [name, rva] of [['dispatch', DISPATCH_RVA], ['raise', RAISE_RVA]]) {
  console.log(`bounds-popup: ${name} @${game.base.add(rva)} opens ${shape(game.base.add(rva))}`);
}

Interceptor.attach(game.base.add(RAISE_RVA), {
  onEnter(args) {
    // (CSPlayerMenuCtrl*, u8, u8, u32 messageId, u8, u32 kind)
    const message = args[3].toUInt32();
    const kind = args[5].toUInt32();
    const known = MESSAGES[message] || 'other';
    console.log(`bounds-popup: raise message=${message} (0x${message.toString(16)}) kind=${kind}  ${known}`);
  },
});

Interceptor.attach(game.base.add(DISPATCH_RVA), {
  onEnter(args) {
    console.log(`bounds-popup: dispatch ctrl=${args[0]} arg=${args[1].toUInt32() & 0xff}`);
  },
});

console.log('bounds-popup: hooked');
