// Find the matchmaking interface Seamless actually uses, by making it identify itself.
//
// # Why not pick a vtable and hope
//
// Two guesses have now been measured wrong on this build. The vtable parked at `ersc+0x21b610` --
// which this repo aims every matchmaking hook at -- recorded ZERO calls on all 38 entries while a
// real invasion landed. The flat `/Lobby/` exports of `steam_api64.dll` recorded zero as well over
// the same window. A third guess is not worth making.
//
// So this hooks the FACTORY instead: whoever asks Steam for the matchmaking interface gets caught
// handing back a pointer, and that pointer's vtable is the one to hook. The interface identifies
// itself and nothing is assumed.
const FACTORY_NAMES = [
  'SteamAPI_ISteamClient_GetISteamMatchmaking',
  'SteamInternal_CreateInterface',
  'SteamInternal_FindOrCreateUserInterface',
  'SteamAPI_ISteamClient_GetISteamMatchmakingServers',
];
const VTABLE_SLOTS = 48;
const LOBBY_NAMES = { 4: 'RequestLobbyList', 5: 'AddStringFilter', 6: 'GetLobbyByIndex', 13: 'CreateLobby', 14: 'JoinLobby' };

const out = { factories: [], interfaces: [], calls: {}, trail: [], hookedVtables: 0 };
const seen = new Set();

function where (address) {
  const m = Process.findModuleByAddress(address);
  return m === null ? `${address}` : `${m.name}+0x${address.sub(m.base).toString(16)}`;
}

function hookVtable (iface, label) {
  const key = iface.toString();
  if (seen.has(key)) return;
  seen.add(key);
  let vtable;
  try { vtable = iface.readPointer(); } catch (e) { return; }
  let hooked = 0;
  for (let slot = 0; slot < VTABLE_SLOTS; slot++) {
    let fn;
    try { fn = vtable.add(slot * Process.pointerSize).readPointer(); } catch (e) { break; }
    if (fn.isNull() || Process.findModuleByAddress(fn) === null) continue;
    const name = `${label}:${LOBBY_NAMES[slot] || slot}`;
    Interceptor.attach(fn, {
      onEnter () {
        out.calls[name] = (out.calls[name] || 0) + 1;
        if (out.trail.length < 300) {
          // The caller is the finding. `ersc.dll+0x...` names Seamless; `eldenring.exe+0x...`
          // names the game's own matchmaking.
          const line = `${name} <- ${where(this.returnAddress)}`;
          out.trail.push(line);
          send({ kind: 'call', line });
        }
      },
    });
    hooked++;
  }
  out.hookedVtables++;
  out.interfaces.push(`${label} @${key} -- ${hooked} slot(s) hooked`);
  send({ kind: 'iface', line: `INTERFACE ${label} @${key}, ${hooked} slot(s) hooked` });
}

for (const m of Process.enumerateModules()) {
  if (!/steam/i.test(m.name)) continue;
  for (const e of m.enumerateExports()) {
    if (!FACTORY_NAMES.includes(e.name)) continue;
    out.factories.push(`${m.name}!${e.name}`);
    Interceptor.attach(e.address, {
      onLeave (retval) {
        if (retval.isNull()) return;
        hookVtable(retval, e.name.replace('SteamAPI_ISteamClient_GetISteam', '').replace('SteamInternal_', ''));
      },
    });
  }
}

rpc.exports = {
  report () { return out; },
  // If the interface was created before this attached, the factory never fires again. Seamless
  // caches one at `ersc+0x21b610`; hooking it by hand covers that case without assuming it is the
  // one in use.
  adoptCached () {
    const ersc = Process.findModuleByName('ersc.dll');
    if (ersc === null) return false;
    try { hookVtable(ersc.base.add(0x21b610).readPointer(), 'ersc-cached'); return true; }
    catch (e) { return false; }
  },
};
console.log(`steam-matchmaking-truth: ${out.factories.length} factory hook(s) armed`);
