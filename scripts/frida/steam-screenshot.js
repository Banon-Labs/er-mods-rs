// Add image files to the player's Steam screenshot library as Elden Ring screenshots, through the
// game's own Steam API (`ISteamScreenshots::AddScreenshotToLibrary` in `steam_api64.dll`), with a
// caption set through `SetLocation`.
//
// Adding is all the API offers: it puts the image in the local library, and uploading it to the
// Steam community is the client's screenshot manager. The Steam client copies the file
// asynchronously, so the proof is a new file under
// `~/.local/share/Steam/userdata/<account>/760/remote/1245620/screenshots`, not the handle.
//
// Driven by `scripts/er-portrait-batch.py`, which loads it with a config such as
// `{"shots": [{"path": "Z:\\home\\...\\body.jpg", "width": 1253, "height": 1538, "caption": "Thops"}]}`
// and calls `rpc.exports.run`.
'use strict';

const cfg = globalThis.__ER_FRIDA_CONFIG || {};
const say = (tag, fields) => send(Object.assign({ family: 'steam-screenshot', tag }, fields || {}));

function exported(name, ret, args) {
    const at = Process.getModuleByName('steam_api64.dll').findExportByName(name);
    if (at === null) throw new Error('steam_api64.dll does not export ' + name);
    return new NativeFunction(at, ret, args);
}

function main() {
    const user = exported('SteamAPI_GetHSteamUser', 'int32', [])();
    const pipe = exported('SteamAPI_GetHSteamPipe', 'int32', [])();
    const client = exported('SteamClient', 'pointer', [])();
    if (client.isNull() || user === 0 || pipe === 0) throw new Error('the Steam API is not initialised in this process');
    const screenshots = exported('SteamAPI_ISteamClient_GetISteamScreenshots', 'pointer', ['pointer', 'int32', 'int32', 'pointer'])(
        client, user, pipe, Memory.allocUtf8String('STEAMSCREENSHOTS_INTERFACE_VERSION003'));
    if (screenshots.isNull()) throw new Error('GetISteamScreenshots returned null');
    const add = exported('SteamAPI_ISteamScreenshots_AddScreenshotToLibrary', 'uint32', ['pointer', 'pointer', 'pointer', 'int32', 'int32']);
    const setLocation = exported('SteamAPI_ISteamScreenshots_SetLocation', 'bool', ['pointer', 'uint32', 'pointer']);
    for (const shot of cfg.shots || []) {
        const handle = add(screenshots, Memory.allocUtf8String(shot.path), NULL, shot.width, shot.height);
        const captioned = handle !== 0 && shot.caption ? setLocation(screenshots, handle, Memory.allocUtf8String(shot.caption)) : false;
        say('added', { path: shot.path, handle, captioned });
    }
    // Re-caption screenshots this process already added, by the handle `added` reported.
    for (const fix of cfg.recaption || []) {
        say('recaptioned', { handle: fix.handle, ok: setLocation(screenshots, fix.handle, Memory.allocUtf8String(fix.caption)) });
    }
    say('done', {});
}

// Started by the caller once the script has loaded (`script.exports_sync.run()`).
rpc.exports = {
    run() {
        try { main(); } catch (e) { say('error', { message: e.message }); }
    },
};
