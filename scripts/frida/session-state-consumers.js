// Does anything in ersc.dll read the session state while it says SEARCHING?
//
// Calling Seamless's own invade action sets `[session+0x150] = 0x0e` and returns -- proven on run
// br-20260916-030928-9e37 -- and then nothing happens: the state holds for minutes and no
// `ISteamMatchmaking::RequestLobbyList` goes out, with two integrity controls firing in the same
// attach. So the state is set and its consumer is not acting on it.
//
// A byte-scan of ersc's unpacked `.text` for memory operands at `+0x150` finds 335 sites, and
// three of them read the field through `rsi` in one region rather than writing it through `rdi` the
// way the actions do. Those are the candidate consumers. Hooking them says whether that code runs
// at all while a search is nominally in flight, which separates "the state is the wrong signal"
// from "the worker that watches it is not running".
const READERS = [0x41edd, 0x42974, 0x429ef];
const WRITERS = { 0x25886: 'invade sets 0x0e', 0x25806: 'sets 0x07', 0x24f26: 'sets 0x02' };
const SESSION = ptr('0x952a1350');
const STATE_OFFSET = 0x150;

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  console.log('consumers: ersc.dll not loaded');
} else {
  console.log(`consumers: ersc.dll @${ersc.base}; session ${SESSION} state=0x${SESSION.add(STATE_OFFSET).readU32().toString(16)}`);
  const counts = {};
  for (const rva of READERS) {
    const at = ersc.base.add(rva);
    counts[rva] = 0;
    try {
      Interceptor.attach(at, {
        onEnter() {
          counts[rva] += 1;
          const n = counts[rva];
          if (n === 1 || n === 100) {
            const self = this.context.rsi;
            let state = 'unreadable';
            try {
              state = `0x${self.add(STATE_OFFSET).readU32().toString(16)}`;
            } catch (e) {
              state = 'unreadable';
            }
            console.log(
              `consumers: reader ersc+0x${rva.toString(16)} fired ${n}x, rsi=${self} ` +
                `state=${state}${self.equals(SESSION) ? '  <- OUR SESSION' : ''}`
            );
          }
        },
      });
      console.log(`consumers: hooked reader ersc+0x${rva.toString(16)} @${at}`);
    } catch (e) {
      console.log(`consumers: could not hook ersc+0x${rva.toString(16)}: ${e.message}`);
    }
  }
  // The writers, so a state change by ersc's own code is attributed rather than inferred.
  for (const [rva, what] of Object.entries(WRITERS)) {
    const at = ersc.base.add(parseInt(rva, 10));
    try {
      Interceptor.attach(at, {
        onEnter() {
          console.log(`consumers: WRITER ersc+0x${Number(rva).toString(16)} (${what}) rdi=${this.context.rdi}`);
        },
      });
    } catch (e) {
      console.log(`consumers: could not hook writer 0x${Number(rva).toString(16)}: ${e.message}`);
    }
  }
}

// Integrity control, on this module. A silent reader hook is not evidence on its own.
let mtxHits = 0;
if (ersc !== null) {
  Interceptor.attach(ersc.base.add(0xf9828), {
    onEnter() {
      mtxHits += 1;
      if (mtxHits === 1 || mtxHits === 500) {
        console.log(`consumers: control -- ersc _Mtx_lock fired ${mtxHits}x, hooks on ersc.dll are live`);
      }
    },
  });
}

console.log('consumers: reloaded on a single attach');
