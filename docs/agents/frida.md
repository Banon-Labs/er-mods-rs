# Frida on Elden Ring

**Frida is the first tool to reach for on this target, and it works -- through a Wine-side server,
never a plain `frida.attach()`.** Proven live 2026-09-08: a non-local invasion was detected and
cancelled through Frida, which `er_invasion_warp.dll` has never once managed. Prototype and
investigate here before writing DLL code, because a hook answers in seconds what a memory scan
cannot answer at all.

**The order is Frida, then Frida, then Frida, and only then a DLL: prototype with it, run the
experiment with it, and fix the thing with it if a hook can. Build a DLL when the mechanism is
already known and the code is the product, never to find something out.** A build is 3 minutes of
the user's session per attempt and it puts a polling oracle where an event belongs; the agent file
reloads in place and costs nothing. Two tests for reaching for the wrong one:

- The question is **who wrote this**, **when did it change**, or **which function ran**. Polling
  can only sample the value afterwards and guess at the cause; `Interceptor` and a watchpoint name
  the instruction. A rebuild is the wrong instrument here by construction, not merely a slow one.
- The answer will arrive through a **log line you then have to read**. That is a poll wearing a
  different hat -- and a `tail -f` of the game's log is not an oracle, it is the absence of one.

Failure this was written from (2026-09-15): asked which field Seamless's Dried Fingers rule writes,
the agent ran four build/teardown/relaunch/tail cycles and produced a diff so unsound it reported
eight qwords moving on a host who had touched nothing. `scripts/er-frida-up.py` was two commands
away the entire time, and the section above already said so.

- **Bring it up with `python3 scripts/er-frida-up.py`.** It runs the Windows `frida-server.exe`
  INSIDE the game's pressure-vessel container via `nsenter`, which is the whole trick. Measured
  namespaces: game `mnt:[4026533261]` / `user:[4026533260]` against the host's `mnt:[4026531832]` /
  `user:[4026531837]`, while `pid:` and `net:` are SHARED -- so entering needs no privilege and the
  port is reachable from the host afterwards. A server started BESIDE the container talks to a
  different wineserver: it accepts the TCP connection and then never answers `enumerate_processes`,
  so a watcher sits silent instead of failing. An open port is not proof of a working server, and
  `--force` replaces one that has gone stale.
- **Attach once and hot-reload the agent file:** `uv run --with frida python3
  scripts/er-frida-watch.py --agent scripts/frida/<agent>.js`. Editing the `.js` reloads it in
  place. That is Frida's intended workflow and it is also the safer one here -- every attach/detach
  cycle installs and reverts trampolines under running game threads.
- **A plain `frida.attach()` still fails**: `NotSupportedError: bootstrapper crashed with signal 11`
  on Frida 17.17.0, because it injects a Linux bootstrapper into a Windows process. The game
  SURVIVES the attempt -- the older claim that it kills the session instantly was overstated -- but
  a crashed bootstrapper can leave the process damaged, so there is no reason to do it.
  `scripts/er-frida-attach-probe.py` re-measures this in 30 seconds rather than asking anyone to
  trust the paragraph.
- **Never arm `MemoryAccessMonitor` on a live game object.** It revokes access to a whole 4 KB page
  and turns every access by every thread into a fault Frida must resume; on this target one was
  not. Measured 2026-09-15: a guard page on Seamless's session killed the game with `0xc0000005`
  at `ersc+0x89e23` -- one of ersc's own readers of that page -- while the player used an item.
  Use a hardware watchpoint instead: four per thread, eight bytes each, no protection change, and
  only the address asked for traps. When the field is unknown, an `Interceptor` on the writer's
  caller is cheaper than widening the watch.
  - **It is a method on a THREAD OBJECT, not on `Thread`.** This line said
    `Thread.setHardwareWatchpoint` until 2026-09-19 and that spelling does not exist: on Frida
    17.17.0 `Object.getOwnPropertyNames(Thread)` is `length, name, prototype, _backtrace, sleep,
    backtrace`, and the call raises `TypeError: not a function`. The real one comes off
    `Process.enumerateThreads()`, whose entries carry `setHardwareBreakpoint`,
    `unsetHardwareBreakpoint`, `setHardwareWatchpoint`, `unsetHardwareWatchpoint`, with the slot id
    (0..3) first:

    ```js
    for (const thread of Process.enumerateThreads()) {
      thread.setHardwareWatchpoint(0, address, 4, 'w');   // slot, address, size, 'r'|'w'|'rw'
    }
    Process.setExceptionHandler(function (details) { /* details.address is the writer */ });
    ```

    Measured the same day: 116 of 116 threads armed with the correct spelling, 0 of 116 with the
    old one. **Count the successes and report the failures** -- an agent that only counted armed
    threads printed `threadsArmed: 0` while the player pressed the button, which reads as "nothing
    writes this field" when it means "nothing is watching". An instrument that reports an absence
    it cannot detect is worse than no instrument.
  - **Arm ONE thread -- the one that would do the write. Arming every thread kills the game.**
    This is the mistake to avoid, and it is not an argument against watchpoints: four registers per
    thread is cheap, but `Process.enumerateThreads()` on this target returns 115+, and putting a
    debug register on all of them means every thread traps into a handler whose resume has to be
    correct, which it only has to get wrong once. Measured 2026-09-19 on two consecutive sessions:
    115 and 116 threads armed, both games dead, the second **24.6 seconds after arming** with the
    watcher still alive and attached, and no crash record either time. Inside an `Interceptor`,
    `Process.getCurrentThreadId()` is the thread that just ran the function you care about, and it
    is usually the only one worth asking. If the writer turns out to be a different thread, that is
    a second, narrower question -- not a reason to widen the watch to the whole process.
  - **A watchpoint that catches nothing has told you nothing. Rule out the near end first.** Zero
    writes means either the writer is broken or the chain that would reach it never began, and the
    instrument cannot tell those apart -- so an agent that reads it as the first spends a session
    under the wrong half of the question. Measured 2026-09-19: a player's right-hand weapon stopped
    switching, `ChrAsm.right_weapon_slot` never moved, a single-thread write watchpoint on it saw
    nothing, and the cause was that the d-pad direction had no button bound to it at all
    (`CSPcKeyConfig+0x440` action `0x0f` held `-1` against a default of `2003`). Before arming
    anything for an input that "does nothing", diff the player's binding table against the game's
    own defaults: `python3 scripts/er-keybind-repair.py --diff --pad-only` is one read of a live
    process, needs no server and no hook, and it answers in a line what the watchpoint cannot
    answer at all. The same shape generalises -- confirm the press becomes a logical event before
    asking which instruction wrote a field. `CSMenuMan+0x90` does NOT answer that: it is a
    shown-menu-window bitmap, so a world action leaves no trace there and an empty diff of it is
    not evidence.
  - **A watchpoint outlives the agent that set it, so NEVER hard-kill a watcher holding one.** It
    lives in the thread's debug registers, not in the script. Once the agent is gone nothing
    services the exception, and the next write to that address kills the game leaving NOTHING in
    the crash log -- an unhandled hardware-debug exception is not a fault `er-crash-logging` can
    catch, so it reads as a spontaneous crash. Measured 2026-09-19: a watcher with 116 armed
    threads was launched as `timeout 900 uv run ... er-frida-watch.py`, hit that cap at 18:38:50,
    and the game's last log write is 18:38:49 -- one line after a healthy heartbeat at tick 67200,
    with the player touching nothing. Both halves are required: the agent exports `dispose` and
    unsets every slot there (Frida calls it on unload, covering a clean detach, a reload in place
    and the watcher's own `SIGTERM`), and the CALLER does not wrap the watcher in `timeout` or any
    other hard kill. `er-frida-watch.py` already ends on detach and handles `SIGTERM` itself; an
    outer cap only bypasses the thing that would have cleaned up. Nothing in-process survives
    `SIGKILL`, so there is no code fix for the second half -- only not doing it.
  - **Give the watcher its own background task. Never chain it after anything.** The hard kill does
    not only arrive as an explicit `timeout`: a command that waits for something and THEN starts the
    watcher is one command, so the harness's own cap applies to the whole chain and lands on the
    watcher at the end of it. Measured 2026-09-19, minutes after the rule above was written: an
    `until` loop waiting for `oracle_player_present` followed by `er-frida-up.py` followed by
    `er-frida-watch.py` was killed with `SIGTERM` at the two-minute cap, seconds after the probe
    attached. That game survived only because the watchpoint had not armed yet -- it arms on the
    first pad poll. Wait in one backgrounded command that EXITS when the condition is true, then
    start the watcher as a second, separate background task with no cap on it.

- **For a read-only question with no game running**, `/proc/<pid>/mem` via
  `scripts/er-live-fields.py` is still the cheapest answer and needs no server at all.
- **Watch the event stream with the `Monitor` tool** rather than asking the user what happened.
