// Does `live_menu_window`'s vtable screen ever reject a window that is still in use?
//
// `menu_pump.rs::profile_select_window_run` reads `job+0x130`, screens it with a "is the vtable
// inside the game image" test, and stores the RESULT in the tracker. When the screen says no it
// stores 0 -- and 0 is also what a genuinely closed picker stores, so the two are indistinguishable
// downstream. The owner-cleared arm then restores the System windows, which clears both terms of
// `system_quit_profile_load_job_run_hook`'s block gate (`profile_window != 0 &&
// REAL_WINDOWS_HIDDEN != 0`) and lets the NATIVE load job through.
//
// Run br-20260917-192652-b918 shows the consequence but not the cause: `restore real windows
// source=standalone-profile-owner-cleared profile=0x0` with the picker's own row lines continuing
// after it, and `system_quit_profile_load_activate_count = 0` against
// `system_quit_profile_load_job_run_last_profile_id = 7`.
//
// The cause is one of exactly two things and the log cannot separate them:
//
//   A. `job+0x130` really went to 0 for a frame -- the job stopped naming its window.
//   B. `job+0x130` stayed non-null and the VTABLE READ failed or read something out of image.
//
// This reports every transition of `job+0x130` per resource, and for every non-null owner whose
// vtable fails the in-image test it reports the pointer and the vtable it actually read. Whichever
// of A or B shows up is the one the fix has to be written against.
//
// Read-only: one Interceptor on the menu pump, three reads per call, no writes.

'use strict';

const MENU_WINDOW_JOB_RUN_RVA = 0x7ad1c0;
const MENU_WINDOW_JOB_WINDOW_130_OFFSET = 0x130;
const MENU_WINDOW_JOB_RESOURCE_NAME_OFFSET = 0x138; // probed below, reported either way

function gameModule() {
  for (const m of Process.enumerateModules()) {
    if (m.name.toLowerCase() === 'eldenring.exe') return m;
  }
  return null;
}

const mod = gameModule();
if (mod === null) {
  send({ tag: 'fatal', why: 'eldenring.exe not in the module list' });
} else {
  const lo = mod.base;
  const hi = mod.base.add(mod.size);
  send({ tag: 'base', base: lo.toString(), size: mod.size });

  const inImage = (p) => !p.isNull() && p.compare(lo) >= 0 && p.compare(hi) < 0;

  // Per-job memory of the last owner seen, so only CHANGES are reported.
  const lastOwner = new Map();
  let nullTransitions = 0;
  let deadVtables = 0;
  let calls = 0;

  const readWide = (p) => {
    try {
      if (p.isNull()) return null;
      return p.readUtf16String(64);
    } catch (e) {
      return null;
    }
  };

  Interceptor.attach(mod.base.add(MENU_WINDOW_JOB_RUN_RVA), {
    onEnter(args) {
      calls += 1;
      const job = args[0];
      if (job.isNull()) return;
      let owner;
      try {
        owner = job.add(MENU_WINDOW_JOB_WINDOW_130_OFFSET).readPointer();
      } catch (e) {
        return;
      }
      const key = job.toString();
      const prev = lastOwner.get(key);
      const now = owner.toString();
      if (prev === now) return;
      lastOwner.set(key, now);

      let name = null;
      try {
        name = readWide(job.add(MENU_WINDOW_JOB_RESOURCE_NAME_OFFSET).readPointer());
      } catch (e) {
        name = null;
      }

      if (owner.isNull()) {
        nullTransitions += 1;
        send({
          tag: 'owner-null',
          job: key,
          from: prev,
          name,
          nullTransitions,
          note: 'case A -- the job stopped naming a window',
        });
        return;
      }

      // Non-null owner: does it pass the screen the Rust uses?
      let vt = null;
      try {
        vt = owner.readPointer();
      } catch (e) {
        vt = null;
      }
      const passes = vt !== null && inImage(vt);
      if (!passes) {
        deadVtables += 1;
        send({
          tag: 'owner-fails-screen',
          job: key,
          owner: now,
          vtable: vt === null ? 'unreadable' : vt.toString(),
          name,
          deadVtables,
          note: 'case B -- non-null owner, vtable outside the image; the Rust stores 0 for this',
        });
        return;
      }
      send({ tag: 'owner-live', job: key, owner: now, vtable: vt.toString(), name });
    },
  });

  send({
    tag: 'armed',
    at: mod.base.add(MENU_WINDOW_JOB_RUN_RVA).toString(),
    note: 'reporting owner CHANGES only; case A = owner-null, case B = owner-fails-screen',
  });
}
