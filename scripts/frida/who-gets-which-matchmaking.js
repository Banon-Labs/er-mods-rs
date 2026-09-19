// Which `ISteamMatchmaking` does `ersc.dll` actually hold, and is its vtable the one we hooked?
//
// The trace that said "Seamless never touches Steam matchmaking" resolved the interface itself,
// with `SteamInternal_FindOrCreateUserInterface(1, "SteamMatchMaking009")`, and hooked the function
// pointers out of THAT vtable. If Seamless is handed a different wrapper -- a different vtable of
// per-instance thunks -- then every one of those hooks was watching the game's calls and was blind
// to Seamless's by construction, and the zero means nothing.
//
// The user's objection is what forced this: Seamless matchmaking dies when Steam goes offline, so
// Seamless plainly depends on Steam, and a trace saying otherwise is the trace being wrong.
//
// So watch the handout itself and record who asked, what version they asked for, what they got,
// and the vtable underneath it.
const OURS = ptr('0x6ffffad05d78');

function describe(result) {
  try {
    return `${result} vtable=${result.readPointer()}`;
  } catch (e) {
    return `${result} (vtable unreadable)`;
  }
}

function caller(context) {
  try {
    const ret = Thread.backtrace(context, Backtracer.FUZZY)[0];
    const m = Process.findModuleByAddress(ret);
    return m === null ? `${ret}` : `${m.name}+0x${ret.sub(m.base).toString(16)}`;
  } catch (e) {
    return 'unknown';
  }
}

let seen = 0;
for (const mod of ['steam_api64.dll', 'lsteamclient.dll']) {
  const m = Process.findModuleByName(mod);
  if (m === null) {
    continue;
  }
  for (const name of [
    'SteamInternal_FindOrCreateUserInterface',
    'SteamInternal_CreateInterface',
    'SteamInternal_FindOrCreateGameServerInterface',
  ]) {
    const fn = m.findExportByName(name);
    if (fn === null) {
      continue;
    }
    try {
      Interceptor.attach(fn, {
        onEnter(args) {
          this.version = 'unreadable';
          try {
            // The version string is the last argument on every one of these entry points.
            this.version = (name === 'SteamInternal_CreateInterface' ? args[0] : args[1]).readUtf8String();
          } catch (e) {
            this.version = 'unreadable';
          }
          this.from = caller(this.context);
        },
        onLeave(retval) {
          if (seen >= 24) {
            return;
          }
          seen += 1;
          const same = retval.equals(ptr(0)) ? '' : (() => {
            try {
              return retval.readPointer().equals(OURS) ? '  <-- SAME vtable we hooked' : '  <-- DIFFERENT vtable';
            } catch (e) {
              return '';
            }
          })();
          console.log(`iface: ${mod}!${name}("${this.version}") from ${this.from} -> ${describe(retval)}${same}`);
        },
      });
      console.log(`iface: watching ${mod}!${name}`);
    } catch (e) {
      console.log(`iface: could not watch ${mod}!${name}: ${e.message}`);
    }
  }
}
console.log(`iface: the vtable the earlier trace hooked was ${OURS}`);
