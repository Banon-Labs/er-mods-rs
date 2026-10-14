// Where are the threads running inside named modules, right now? One snapshot, sent once.
//
// Answers "which loop is this thread spinning in" without a rebuild: each thread whose program
// counter or backtrace touches a module in `modules` is reported with module+offset frames, which
// `llvm-symbolizer --obj <dll>` or the crate's PDB turns into function names.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/thread-where.js \
//     --config-json '{"modules": ["er_cel_shade.dll"]}'
//
// Read-only: no hook, no watchpoint, nothing to undo on detach.

const config = globalThis.__ER_FRIDA_CONFIG || {};
const modules = (config.modules || ['er_cel_shade.dll']).map((m) => m.toLowerCase());

function where(p) {
  const m = Process.findModuleByAddress(p);
  return m ? m.name + '+0x' + p.sub(m.base).toString(16) : p.toString();
}

const threads = [];
for (const t of Process.enumerateThreads()) {
  let frames;
  try {
    frames = Thread.backtrace(t.context, Backtracer.FUZZY).slice(0, 16).map(where);
  } catch (e) {
    frames = ['backtrace failed: ' + e];
  }
  const pc = where(t.context.pc);
  const touched = [pc].concat(frames).some((s) => modules.some((m) => s.toLowerCase().indexOf(m) >= 0));
  if (touched) {
    threads.push({ id: t.id, state: t.state, pc: pc, frames: frames });
  }
}
send({ event: 'thread-where', modules: modules, threads: threads });
