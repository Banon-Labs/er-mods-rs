// Does Seamless ask Steam for lobbies while its session is SEARCHING?
//
// Deliberately hooks nothing inside `ersc.dll`. `er_invasion_warp.dll` byte-checks the prologue of
// every ersc action before calling it, and an `Interceptor` writes a trampoline over exactly those
// bytes -- run br-20260916-041743-b429 lost a press that way, and `scripts/frida/ersc-handoff.js`
// had the warning written in it the whole time. `lsteamclient.dll` carries no such gate.
//
// The four slots are the ones measured on this build: both `eldenring.exe` and `ersc.dll` resolve
// `SteamMatchMaking009`, so there is one vtable and one set of addresses, and the flat
// `steam_api64.dll` wrappers are never entered.
const SLOTS = {
  RequestLobbyList: 0x8ba60,
  AddRequestLobbyListStringFilter: 0x8ac80,
  CreateLobby: 0x8ad50,
  JoinLobby: 0x8b7e0,
};

const lsteam = Process.findModuleByName('lsteamclient.dll');
if (lsteam === null) {
  console.log('steam: lsteamclient.dll is not loaded');
} else {
  console.log(`steam: lsteamclient.dll @${lsteam.base}`);
  for (const [name, rva] of Object.entries(SLOTS)) {
    let n = 0;
    try {
      Interceptor.attach(lsteam.base.add(rva), {
        onEnter(args) {
          n += 1;
          if (n <= 4) {
            console.log(`steam: ${name} #${n}`);
          }
        },
      });
      console.log(`steam: watching ${name} @+0x${rva.toString(16)}`);
    } catch (e) {
      console.log(`steam: could not watch ${name}: ${e.message}`);
    }
  }
}

// The session's state, read once so the window is anchored: a run with no calls means nothing if
// the search had already ended.
const ersc = Process.findModuleByName('ersc.dll');
const SESSION = ptr('0xa1fdce0');
try {
  console.log(`steam: session ${SESSION} state=0x${SESSION.add(0x150).readU32().toString(16)}`);
} catch (e) {
  console.log(`steam: session unreadable: ${e.message}`);
}
