// Put back the two online predicates er_quickload stubs, live, without a rebuild.
//
// Both are patched to `xor eax,eax; ret` at DllMain so the autoload reaches the title with no
// login attempt. The product restores the first one once a player exists; the second has never
// been restored, and it is the one that greys out the multiplayer menu, the message rows and the
// `Use` action on every multiplayer item.
//
// Addresses are 1.17.1 RVAs, both read out of this run's own log rather than carried from 1.16.2:
//   IsOnlineMode            0x67ae80   original 48 8b 05   (mov rax,[rip+disp32])
//   Menu_IsEnableOnlineMode 0xe58180   original 40 53 48   (push rbx; ...)
// The originals were read out of eldenring-deobf-1.17.1.bin at those offsets, not guessed.
const STUB = [0x31, 0xc0, 0xc3];

const SITES = [
  { name: 'IsOnlineMode', rva: 0x67ae80, original: [0x48, 0x8b, 0x05] },
  { name: 'Menu_IsEnableOnlineMode', rva: 0xe58180, original: [0x40, 0x53, 0x48] },
];

function hex(ptr, n) {
  const bytes = new Uint8Array(ptr.readByteArray(n));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join(' ');
}

function looksLikeStub(ptr) {
  const bytes = new Uint8Array(ptr.readByteArray(STUB.length));
  return STUB.every((b, i) => bytes[i] === b);
}

const game = Process.findModuleByName('eldenring.exe');
if (game === null) {
  console.log('online-restore: eldenring.exe is not loaded');
} else {
  console.log('online-restore: eldenring.exe @ ' + game.base);
  for (const site of SITES) {
    const at = game.base.add(site.rva);
    const before = hex(at, 8);
    if (!looksLikeStub(at)) {
      // Declining is the point: if these are not the stub's bytes, they are not ours to write.
      console.log(`online-restore: ${site.name} @${at} is ${before} -- not the stub, DECLINED`);
      continue;
    }
    Memory.protect(at, STUB.length, 'rwx');
    at.writeByteArray(site.original);
    console.log(`online-restore: ${site.name} @${at}  ${before}  ->  ${hex(at, 8)}  RESTORED`);
  }
  console.log('online-restore: done -- check whether the menu and the item Use rows ungrey');
}
