// Read the local Steam persona name through steam_api64's flat exports, which is what the duel
// referee prints in place of "(you)". Read-only; writes its answer beside the game.
'use strict';

const steam = Process.getModuleByName('steam_api64.dll');
const fn = (name, ret, args) => new NativeFunction(steam.getExportByName(name), ret, args);
const friends = fn('SteamAPI_SteamFriends_v017', 'pointer', [])();
const persona = friends.isNull()
    ? null
    : fn('SteamAPI_ISteamFriends_GetPersonaName', 'pointer', ['pointer'])(friends).readUtf8String();

const game = Process.getModuleByName('eldenring.exe');
const out = new File(game.path.replace(/[^\\/]*$/, '') + 'steam-persona-probe.log', 'a');
out.write(`[${new Date().toISOString()}] friends=${friends} persona=${JSON.stringify(persona)}\n`);
out.flush();
console.log(`steam-persona-probe: friends=${friends} persona=${JSON.stringify(persona)}`);
