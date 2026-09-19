// Is `ersc.dll` executing its own session code, or is Seamless inert in this process?
//
// Several weak signals have been stacked on top of each other to argue that Seamless has no
// session -- no `seamless`-tagged option-menu object in memory, `lobby=0` from our own observer,
// `_Mtx_lock` silent. Each has an innocent explanation: the option menu exists only while a
// Seamless dialog is open, our observer admits it may have missed the lobby's creation, and
// `ersc+0xf9828` is reached only from the session actions, which idle play never runs.
//
// This asks the question directly. `ersc+0xad6e0` builds Seamless's lobby key and `ersc+0x241a0`
// builds its option menu; both are its own code on its own schedule. A counter on each separates
// "Seamless is not advertising" from "we are not watching the right thing", and `this` from either
// one is a Seamless-owned object to walk rather than a candidate to resemble.
const BUILD_LOBBY_KEY_RVA = 0xad6e0;
const SHOW_RVA = 0x241a0;
const INVADE_RVA = 0x25850;

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  console.log('alive: ersc.dll is not loaded at all');
} else {
  console.log(`alive: ersc.dll @${ersc.base}`);
}

const counts = { lobbyKey: 0, show: 0, invade: 0 };

function watch(name, rva, key) {
  if (ersc === null) {
    return;
  }
  try {
    Interceptor.attach(ersc.base.add(rva), {
      onEnter(args) {
        counts[key] += 1;
        if (counts[key] <= 3) {
          const self = args[0];
          let carried = 'unreadable';
          try {
            carried = `${self.add(0x58).readPointer()}`;
          } catch (e) {
            carried = 'unreadable';
          }
          console.log(`alive: ${name} #${counts[key]} this=${self} this+0x58=${carried}`);
        }
      },
    });
    console.log(`alive: watching ${name} @ersc+0x${rva.toString(16)}`);
  } catch (e) {
    console.log(`alive: could not watch ${name}: ${e.message}`);
  }
}

watch('buildLobbyKey', BUILD_LOBBY_KEY_RVA, 'lobbyKey');
watch('show', SHOW_RVA, 'show');
watch('invade', INVADE_RVA, 'invade');

// A positive control that cannot be confused with the subject: the game's own Steam matchmaking
// interface. If Seamless is advertising at all, something asks Steam about lobbies; if nothing
// here fires either, the process is not talking to Steam and that is the finding.
const lsteam = Process.findModuleByName('lsteamclient.dll');
if (lsteam !== null) {
  const slots = { RequestLobbyList: 0x8ba60, CreateLobby: 0x8ad50, JoinLobby: 0x8b7e0 };
  for (const [name, rva] of Object.entries(slots)) {
    let n = 0;
    try {
      Interceptor.attach(lsteam.base.add(rva), {
        onEnter() {
          n += 1;
          if (n <= 2) {
            console.log(`alive: lsteamclient ${name} #${n}`);
          }
        },
      });
    } catch (e) {
      console.log(`alive: could not watch ${name}: ${e.message}`);
    }
  }
  console.log('alive: watching RequestLobbyList / CreateLobby / JoinLobby');
} else {
  console.log('alive: lsteamclient.dll is not loaded');
}

// No periodic report: `scripts/check-no-timeouts.py` bans timers in these agents, and the counters
// above already print their first three hits each. A run that prints nothing after the watch lines
// counted zero of everything, which is the finding this agent exists to produce.
