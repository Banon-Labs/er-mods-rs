// How much D3D12 command-list work does Elden Ring record per frame, and on how many threads?
//
// Sizes the per-draw hooks the shader editor would add (er-effects-rs-zxlx): a detour on every
// draw pays a trampoline and a lookup, so the count per frame decides whether that is free or a
// frame-time problem. Counted, per second and per frame:
//
//   DrawInstanced / DrawIndexedInstanced (calls and summed InstanceCount), ExecuteIndirect (calls
//   and summed MaxCommandCount), SetPipelineState, OMSetRenderTargets, ExecuteBundle,
//   SetDescriptorHeaps, Close, Reset, and Present (both the 8 and 22 slots), plus the distinct
//   threads that record draws.
//
// The command-list vtable is found from the direct-list SetGraphicsRootSignature address
// er-cel-shade logs ("detoured SetGraphicsRootSignature (direct) at 0x..."): MinHook patches the
// function in place, so the vtable slot still holds that address, and the slot sits at +0xf0.
// Present comes from er-quickload's "present-overlay: VMT-swap game swapchain" line.
//
//   uv run --with frida python3 scripts/er-frida-watch.py --agent scripts/frida/d3d12-draw-census.js \
//     --config-json '{"set_root_signature": "0x6ffff87062b0", "present8": "0x6ffffb67b330",
//                     "present22": "0x6ffffb67b880", "seconds": 10}'
//
// Counting runs in a CModule so a callback costs a few instructions, not a trip into JS. The
// counters are plain increments from several threads, so a lost update undercounts slightly; the
// numbers are magnitudes, not exact totals. After `seconds` samples every hook is detached and the
// agent stays loaded but idle. Nothing is written to game memory.

const config = globalThis.__ER_FRIDA_CONFIG || {};
const seconds = config.seconds || 10;
if (!config.present8 && !config.present22) {
  // The per-second report is taken on a present, so without one nothing would ever report.
  send({ event: 'census-error', reason: 'present8 or present22 is required' });
  throw new Error('census: no present address');
}

// ID3D12GraphicsCommandList vtable offsets (method index * 8).
const SLOTS = {
  DrawInstanced: 0x60,
  DrawIndexedInstanced: 0x68,
  OMSetStencilRef: 0xc0,
  SetPipelineState: 0xc8,
  ExecuteBundle: 0xd8,
  SetDescriptorHeaps: 0xe0,
  OMSetRenderTargets: 0x170,
  ExecuteIndirect: 0x1d8,
  Close: 0x48,
  Reset: 0x50,
};
const SET_GRAPHICS_ROOT_SIGNATURE_SLOT = 0xf0;

// Counter layout, in u64 slots of the shared block.
const C = {
  draw: 0, drawInst: 1, drawIdx: 2, drawIdxInst: 3, pso: 4, indirect: 5, indirectMax: 6,
  rtv: 7, bundle: 8, heaps: 9, close: 10, reset: 11, present: 12, stencilRef: 13,
};
const THREAD_IDS = 32; // slots 32..63 hold thread ids, 64..95 their draw counts
const BLOCK_SLOTS = 96;

function vtableFromSlot(target, slotOffset) {
  const module = Process.findModuleByAddress(target);
  if (module === null) {
    throw new Error('no module holds ' + target);
  }
  const hex = [];
  let value = target;
  for (let i = 0; i < 8; i++) {
    hex.push(('0' + value.and(0xff).toString(16)).slice(-2));
    value = value.shr(8);
  }
  const pattern = hex.join(' ');
  const hits = [];
  for (const range of module.enumerateRanges('r--')) {
    for (const hit of Memory.scanSync(range.base, range.size, pattern)) {
      hits.push(hit.address.sub(slotOffset));
    }
  }
  return { module: module.name, vtables: hits };
}

const block = Memory.alloc(BLOCK_SLOTS * 8);
const cm = new CModule(`
#include <gum/guminterceptor.h>
#include <stdint.h>

extern uint64_t blk[];
extern unsigned int GetCurrentThreadId (void);

static void note_thread (void) {
  uint64_t tid = GetCurrentThreadId ();
  for (int i = 0; i < 32; i++) {
    if (blk[32 + i] == tid) { blk[64 + i]++; return; }
    if (blk[32 + i] == 0) { blk[32 + i] = tid; blk[64 + i]++; return; }
  }
}

void onEnter (GumInvocationContext * ic) {
  uintptr_t kind = (uintptr_t) gum_invocation_context_get_listener_function_data (ic);
  blk[kind]++;
  if (kind == 0) {
    blk[1] += (uint32_t) (uintptr_t) gum_invocation_context_get_nth_argument (ic, 2);
    note_thread ();
  } else if (kind == 2) {
    blk[3] += (uint32_t) (uintptr_t) gum_invocation_context_get_nth_argument (ic, 2);
    note_thread ();
  } else if (kind == 5) {
    blk[6] += (uint32_t) (uintptr_t) gum_invocation_context_get_nth_argument (ic, 2);
    note_thread ();
  }
}
`, {
  blk: block,
  GetCurrentThreadId: Process.getModuleByName('kernel32.dll').getExportByName('GetCurrentThreadId'),
});

const found = vtableFromSlot(ptr(config.set_root_signature), SET_GRAPHICS_ROOT_SIGNATURE_SLOT);
if (found.vtables.length === 0) {
  send({ event: 'census-error', reason: 'no vtable slot holds the SetGraphicsRootSignature address', module: found.module });
  throw new Error('census: vtable not found');
}

const kindOf = {
  DrawInstanced: C.draw, DrawIndexedInstanced: C.drawIdx, SetPipelineState: C.pso,
  ExecuteIndirect: C.indirect, OMSetRenderTargets: C.rtv, ExecuteBundle: C.bundle,
  SetDescriptorHeaps: C.heaps, Close: C.close, Reset: C.reset, OMSetStencilRef: C.stencilRef,
};

const attached = [];
const targets = {};
for (const vtable of found.vtables) {
  for (const [name, offset] of Object.entries(SLOTS)) {
    const fn = vtable.add(offset).readPointer();
    const key = fn.toString();
    if (targets[key] !== undefined) {
      continue;
    }
    targets[key] = name;
    try {
      attached.push(Interceptor.attach(fn, cm, ptr(kindOf[name])));
    } catch (e) {
      send({ event: 'census-attach-failed', name: name, fn: key, error: String(e) });
    }
  }
}
for (const p of [config.present8, config.present22]) {
  if (p) {
    try {
      attached.push(Interceptor.attach(ptr(p), cm, ptr(C.present)));
      targets[p] = 'Present';
    } catch (e) {
      send({ event: 'census-attach-failed', name: 'Present', fn: p, error: String(e) });
    }
  }
}
Interceptor.flush();

send({ event: 'census-armed', module: found.module, vtables: found.vtables.map(String), targets: targets });

function read() {
  const out = {};
  for (const [name, slot] of Object.entries(C)) {
    out[name] = block.add(slot * 8).readU64().toNumber();
  }
  const threads = [];
  for (let i = 0; i < THREAD_IDS; i++) {
    const tid = block.add((32 + i) * 8).readU64().toNumber();
    if (tid !== 0) {
      threads.push([tid, block.add((64 + i) * 8).readU64().toNumber()]);
    }
  }
  out.threads = threads;
  return out;
}

// The report is paced by the game's own presents: a JS listener on Present, beside the CModule
// counter, takes a sample once a second has passed since the last one.
let previous = read();
let previousAt = Date.now();
let samples = 0;
let done = false;

function sample() {
  const now = read();
  const delta = {};
  for (const name of Object.keys(C)) {
    delta[name] = now[name] - previous[name];
  }
  const frames = Math.max(delta.present, 1);
  const perFrame = {};
  for (const name of Object.keys(C)) {
    perFrame[name] = Math.round((delta[name] / frames) * 10) / 10;
  }
  send({ event: 'census-second', presents: delta.present, per_second: delta, per_frame: perFrame });
  previous = now;
  samples++;
  if (samples >= seconds) {
    done = true;
    // Detach after the Present callback that got here has returned.
    Script.nextTick(() => {
      for (const listener of attached) {
        listener.detach();
      }
      Interceptor.flush();
      send({ event: 'census-done', totals: now });
    });
  }
}

for (const p of [config.present8, config.present22]) {
  if (p) {
    attached.push(Interceptor.attach(ptr(p), {
      onEnter() {
        const at = Date.now();
        if (!done && at - previousAt >= 1000) {
          previousAt = at;
          sample();
        }
      },
    }));
  }
}
Interceptor.flush();

rpc.exports = {
  dispose() {
    done = true;
    for (const listener of attached) {
      try {
        listener.detach();
      } catch (e) {
        // already detached at the end of the sample window
      }
    }
  },
};
