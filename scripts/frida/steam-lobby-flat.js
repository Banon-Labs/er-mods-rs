// Every Steam lobby call the game makes, hooked at `steam_api64.dll`'s flat exports.
//
// # Why not ersc's own interface pointer
//
// The previous instrument read the vtable ersc parks at `ersc+0x21b610` and hooked all 38 of its
// entries. Measured 2026-09-16 with an invasion ACTUALLY LANDING: zero calls on any of them. So
// that pointer is not the path Seamless's invasion takes, and a silence there says nothing about
// Steam. `lobby_publish.rs` says the same thing in its own words -- it detours `RequestLobbyList`
// "in `steamclient64.dll`'s interface vtable, not in `ersc.dll`".
//
// The flat exports are the reliable surface: `steam_api64.dll` exports all of them, and every
// caller in the process funnels through them regardless of which interface pointer it cached.
const WANTED = /Lobby/i;
const out = { hooked: [], calls: {}, trail: [] };

function where (address) {
  const m = Process.findModuleByAddress(address);
  return m === null ? `${address}` : `${m.name}+0x${address.sub(m.base).toString(16)}`;
}

const api = Process.findModuleByName('steam_api64.dll');
if (api !== null) {
  for (const e of api.enumerateExports()) {
    if (!WANTED.test(e.name)) continue;
    const short = e.name.replace('SteamAPI_ISteamMatchmaking_', '').replace('SteamAPI_', '');
    out.hooked.push(short);
    Interceptor.attach(e.address, {
      onEnter () {
        out.calls[short] = (out.calls[short] || 0) + 1;
        // The caller is the finding: `ersc.dll+0x...` names Seamless asking, `eldenring.exe+0x...`
        // names the game's own matchmaking doing it.
        if (out.trail.length < 200) {
          const line = `${short} <- ${where(this.returnAddress)}`;
          out.trail.push(line);
          send({ kind: 'steam', line });
        }
      },
    });
  }
}

rpc.exports = { report () { return out; } };
console.log(`steam-lobby-flat: armed on ${out.hooked.length} lobby export(s)`);
