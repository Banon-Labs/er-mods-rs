// Does picking a save file in System > Quit > "Load Character from File" leave the PICKED
// container in place, or is the ACTIVE one written back over it before the character list is
// built?
//
// The user reports the reopened list showing the characters of the save they are already playing.
// er_quickload's own debug log says a rollback (`restore-real-profile-native-finalizer`) runs one
// millisecond after the list is reopened -- but that is our code narrating itself. This measures
// the same claim from outside the DLL, at the Win32 boundary, where neither our hooks nor our
// logging can flatter the answer.
//
// The game's own ProfileSelect list builder cannot be hooked here: er_quickload detours
// `FUN_140875590` and `FUN_1408752c0` already, and Frida refuses to intercept a prologue that is
// a trampoline ("unable to intercept function at 0000000140875590"). That is fine, because the
// question is answerable without a list marker at all:
//
//   READ  of  ...\Downloads\ER0000.co2       the pick, staging the chosen container
//   WRITE of  ...\EldenRing\<id>\ER0000.co2  the rollback, putting the live container back
//
// A writable handle on the ACTIVE container after a readable handle on the PICKED one, with no
// further read of the pick, is the defect: whatever the list is filled from, it is not the file
// the user chose. Handle identity is carried through so a read and a write cannot be confused for
// one interleaved access.

'use strict';

const GENERIC_WRITE = 0x40000000;
const GENERIC_READ = 0x80000000;

const t0 = Date.now();
function at() {
    return Date.now() - t0;
}

// Which container is this -- the one the user is playing, or the one they picked?
function classify(path) {
    const lower = path.toLowerCase();
    if (lower.indexOf('\\eldenring\\') >= 0 && /\\[0-9]{5,}\\/.test(lower)) {
        return 'ACTIVE';
    }
    return 'PICKED';
}

const game = Process.findModuleByName('eldenring.exe');
send({
    type: 'hello',
    base: game === null ? null : game.base.toString(),
});

// Frida 17 removed the static `Module.findExportByName(module, name)` two-argument form; the
// lookup is a method on a resolved module now, and calling the old spelling throws
// `TypeError: not a function` from agent load, which reads as "no events" rather than as an error.
function resolveExport(moduleNames, exportName) {
    for (const name of moduleNames) {
        const mod = Process.findModuleByName(name);
        if (mod === null) {
            continue;
        }
        const found = mod.findExportByName(exportName);
        if (found !== null) {
            return found;
        }
    }
    return null;
}

const createFileW = resolveExport(['kernelbase.dll', 'kernel32.dll'], 'CreateFileW');

if (createFileW === null) {
    send({ type: 'fatal', what: 'CreateFileW not resolvable' });
} else {
    let opens = 0;
    Interceptor.attach(createFileW, {
        onEnter(args) {
            this.path = null;
            let path;
            try {
                path = args[0].readUtf16String();
            } catch (e) {
                return;
            }
            if (path === null || path.indexOf('ER0000') < 0) {
                return;
            }
            this.path = path;
            this.access = args[1].toUInt32();
        },
        onLeave(retval) {
            if (this.path === null || this.path === undefined) {
                return;
            }
            opens += 1;
            send({
                type: 'open',
                n: opens,
                ms: at(),
                which: classify(this.path),
                path: this.path,
                read: (this.access & GENERIC_READ) !== 0,
                write: (this.access & GENERIC_WRITE) !== 0,
                handle: retval.toString(),
            });
        },
    });
}
