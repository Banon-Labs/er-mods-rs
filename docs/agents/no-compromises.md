# No Compromises: the load objective and button names

We accept **no compromises** on the stated objective: a same-character repeat load
(System->Quit->**Load Character**) that reaches **genuine world readiness** (character rendered
AND the player can move).

**THE BUTTON NAMES, AND THE OLD ONES THIS REPO IS STILL FULL OF.** Both load rows on the
Quit Game tab are OURS -- vanilla ships only *Save Game* and *Return to Desktop*, and the
mod clones the row twice. They were renamed on 2026-07-31 after a review found the original
pair indistinguishable, but the old words survive in prose, in `bd` memories and in the
constant names, so read them as synonyms rather than as a second feature:

| old name (still in memories/symbols) | ON SCREEN since 2026-07-31 | takes as input |
|---|---|---|
| Load Profile | **Load Character** | a character from the save container already loaded |
| Load Save Profiles | **Load Character from File** | a save file off the disk |

A THIRD cloned row, **Load Build from URL**, was added later and has never had another name.
It is the odd one out on the tab: it neither returns to the title nor touches a save container,
but rebuilds the character you are already playing from the `build_url` set in the game-directory
`er-quickload.toml` (items granted, gear worn, spells memorised, level and attributes matched). The
importer behind it is `er-build-import-runtime`, shared with the standalone `er-build-import`
shell -- which must therefore never be loaded in the same me3 profile as the product DLL. Its label
bytes live in `SYSTEM_QUIT_LOAD_BUILD_URL_LABEL_W`, beside the two above.

The label bytes live in `SYSTEM_QUIT_LOAD_PROFILE_LABEL_W` and
`SYSTEM_QUIT_LOAD_SAVE_PROFILES_LABEL_W` (`system_quit_dialog_handlers.rs`) -- the symbols
kept the old names while their contents changed, which is exactly the trap this table
exists to close. An agent that quotes the old name "Load Profile" at the user is naming a
button that has not been on screen since July. Do not settle for a weaker solution that technically "works"
but does not actually reach that bar. When a path looks blocked, that is a signal to
find the *real* solution at a deeper layer -- not to lower the bar.

**Validate the way a USER would (reframe 2026-07-19, supersedes the old "zero-input"
framing -- do NOT reintroduce `simulated_button_presses_total = 0` / "no simulated input"
as the goal).** The point is confidence that a real user can drive the feature to world
readiness, so the TEST must follow a path CLOSE to how a user does it: navigate the menu
**with input**, respecting the game's real input-enable **delays** (the game gates when
you can move / open the menu / press a button after a load). Drive that navigation via the
input-harness DLL's **direct-memory native-binding injection** (`inputmgr+0x90+eventId`,
which mimics a user's presses at the native binding) -- NOT a menu-bypassing direct-arm
shortcut (that skips the exact user path being validated) and NOT synthesized OS-layer
input (XInput/DInput/SendInput, which native ER does not route). The deliverable is still a
single DLL loaded through me3 as a `[[natives]]` profile entry (LazyLoader removed
2026-07-04), compatible with offline-vanilla, Seamless Co-op, and other mods (see bd memory
`autoload-dll-product-requirements`), with the load2 flow decoupled into the input-harness
DLL (toggled by profile inclusion). "Architecturally hard" is not "impossible" -- keep
reverse-engineering until the real mechanism is found. Surface trade-offs honestly, but the
bar is the actual goal, never a fallback.

When a native menu/load path appears to need a manually pumped `MenuJob`, treat that as a red flag
that the integration boundary is wrong. Do **not** build a recurring private pump as the product fix.
Instead, reverse the native ownership path: create/build the correct job, store/retain it in the same
kind of native slot the game uses, enqueue/submit it through the proper MenuJob queue/owner, and trigger
that queued job from the native OK/confirm transition when the verified semaphores say the press would
hit the intended option. Manual per-frame pumping is only a bounded diagnostic to prove job behavior;
the product path must be native enqueue + native pump ownership.
