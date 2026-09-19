// The four lobby slots, plus a hook that MUST fire, so silence means something.
//
// Three measurements this session read zero and were treated as findings before anything in the
// same attach was shown to fire: `_Mtx_lock` counted 0 in 110s, the lobby slots counted 0 in 28s.
// A hook that failed to install reads exactly the same as a function nobody calls, and
// `scripts/frida/find-real-session.js` already says so in its own header. So: a control.
//
// `SteamAPI_RunCallbacks` is pumped every frame by any process using the Steam API. If the control
// counts zero too, the attach is the problem and no conclusion about the lobby slots is available.
const SLOTS = {
  RequestLobbyList: 0x8ba60,
  AddRequestLobbyListStringFilter: 0x8ac80,
  CreateLobby: 0x8ad50,
  JoinLobby: 0x8b7e0,
};

const counts = {};
const lsteam = Process.findModuleByName('lsteamclient.dll');
if (lsteam === null) {
  console.log('steam: lsteamclient.dll is not loaded');
} else {
  for (const [name, rva] of Object.entries(SLOTS)) {
    counts[name] = 0;
    try {
      Interceptor.attach(lsteam.base.add(rva), {
        onEnter() {
          counts[name] += 1;
          if (counts[name] <= 3) {
            console.log(`steam: ${name} #${counts[name]}`);
          }
        },
      });
    } catch (e) {
      console.log(`steam: could not watch ${name}: ${e.message}`);
    }
  }
  console.log(`steam: watching ${Object.keys(SLOTS).length} lobby slot(s) in lsteamclient.dll`);
}

// The control. Taken by export name rather than by offset so it cannot be wrong the way an RVA can.
let control = 0;
let controlName = 'none';
for (const mod of ['steam_api64.dll', 'lsteamclient.dll']) {
  const m = Process.findModuleByName(mod);
  if (m === null) {
    continue;
  }
  const fn = m.findExportByName('SteamAPI_RunCallbacks');
  if (fn === null) {
    continue;
  }
  controlName = `${mod}!SteamAPI_RunCallbacks`;
  try {
    Interceptor.attach(fn, {
      onEnter() {
        control += 1;
        if (control === 1 || control === 200) {
          console.log(`steam: CONTROL ${controlName} has fired ${control}x -- hooks in this attach work`);
        }
      },
    });
    console.log(`steam: control armed on ${controlName} @${fn}`);
    break;
  } catch (e) {
    console.log(`steam: could not arm the control: ${e.message}`);
  }
}
if (controlName === 'none') {
  console.log('steam: no SteamAPI_RunCallbacks export found -- this run has no control');
}

const SESSION = ptr('0xa1fdce0');
try {
  console.log(`steam: session ${SESSION} state=0x${SESSION.add(0x150).readU32().toString(16)}`);
} catch (e) {
  console.log(`steam: session unreadable: ${e.message}`);
}
