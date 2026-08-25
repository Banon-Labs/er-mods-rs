# er-invasion-path

A walkable route to every other player in your session, drawn on the ground they would actually
walk. Its own DLL, its own `[[natives]]` entry, no dependency on the product mod.

Press the key (default `;`) during an invasion:

| situation | what you see |
|---|---|
| the navmesh can walk you there | a coloured line from your feet to theirs, along the terrain |
| the navmesh cannot | a glowing arrow out of your body, pointing exactly at them |
| N players | N routes, N colours, each colour stuck to one player |
| they are closer than 10 m **and** you can see them | nothing -- you already know where they are |

The closer a player is, the bolder and more opaque their route. A route that is merely far away
still draws at its faintest weight rather than vanishing, so "distant" and "unreachable" never
look the same.

## Install

Build it, drop the DLL beside the others, and add one entry to the `.me3` profile:

```bash
cargo xwin build --release --target x86_64-pc-windows-msvc -p er-invasion-path
# -> target/x86_64-pc-windows-msvc/release/er_invasion_path.dll
```

```toml
[[natives]]
path = 'er_invasion_path.dll'
```

The bare `cargo xwin build --release --target x86_64-pc-windows-msvc` builds **only**
`er-effects-rs` -- the workspace pins `default-members` -- so the `-p` is not optional. It exits
zero in a fraction of a second having compiled nothing otherwise, which reads exactly like a
successful incremental build. Check the hash before you stage it:

```bash
sha256sum target/x86_64-pc-windows-msvc/release/er_invasion_path.dll
```

On first run it writes a commented `er-invasion-path.toml` beside the game executable and logs to
`er-invasion-path.log` in the same place.

## Configuration

Every setting lives in that generated file. The ones worth knowing:

| key | default | what it does |
|---|---|---|
| `toggle_key` | `"semicolon"` | key that switches the overlay on and off, **by name** (`"]"`, `"KP_Plus"`, `"Insert"`) |
| `trigger_item_id` | `0` | `EquipParamGoods` row whose USE also toggles it; `0` leaves the key as the only way in |
| `near_suppress_meters` | `10.0` | closer than this **with clear line of sight** draws nothing |
| `bold_at_meters` | `20.0` | at or inside this, full width and opacity |
| `faint_at_meters` | `150.0` | at this distance, the faintest a route is drawn |
| `max_targets` | `6` | most players drawn at once |
| `arrow_meters` | `3.0` | length of the no-route arrow |
| `start_enabled` | `false` | begin with the overlay already on |

## Pick a key no other mod in your profile reads

The default was `F7` until a live 15-DLL run found `er-invasion-warp` polling `VK_F7` every frame in the same process: the key warped the player instead of drawing anything, and nothing warned about it. "Elden Ring binds nothing to it" is not the question — the mods loaded beside you are, and a default cannot know them. `;` is clear of everything this workspace's shells poll, which makes it a better default rather than a safe one.

## How the route is found

The route is the engine's own. `CS::CSAiFunc`'s path request (`FUN_1402ea2f0`) is replicated
against the same `CSHkAiWorld` every NPC in the map asks:

1. `FUN_140be4840` fills a request-parameter block with the engine's defaults.
2. `FUN_140bddfe0` snaps each endpoint onto the navmesh and reports the `(section, face)` it
   landed in. An endpoint that lands on nothing is a player standing somewhere no character can
   walk, and that is an arrow.
3. `FUN_140bdd570` refines the start pair.
4. `FUN_140bdec90` enqueues the search under the world's own mutex and returns a request id.
5. `FUN_140bdfde0` polls it; `FUN_140bdf610` drains the finished route, status `2` meaning
   complete.

The search runs on the AI world's job, not ours, so a request spans frames -- which is why the
overlay keeps the previous route on screen while the next one is computed instead of blinking.

"Walkable" is a property of the navmesh the map was authored with, not of the terrain mesh. Any
route computed here instead would be a second opinion about walkability, and it would be wrong
exactly where being wrong costs you the invasion: the ledge that looks crossable and is not.

**Runtime status:** every address and structure offset is from static RE of ER 1.16.2 (dump VA ==
deobf VA == runtime VA, shift 0), cross-checked against two independent call sites. It has not yet
been run against a live game. The container walk validates everything it reads -- capacity a power
of two, count bounded, every element pointer non-null, every coordinate finite -- and refuses
rather than trusting, and a refusal degrades to the arrow.

## What it does to the game

Nothing. No detours, no memory writes, no param edits (patching params is what breaks Seamless
invasions), no input injection, no network traffic. It reads the roster, asks the navmesh a
question, casts one sight ray per player, and draws.

The drawing goes through `er_build_watermark_core::overlay_host`: if another module in this
workspace already hosts the process's imgui context, this DLL registers as a guest and draws
through it; if nobody does, it hosts and dispatches guests itself. Either way there is exactly one
`Present` hook in the process -- two `Hudhook::apply()` calls double-hook it and the second one
silently renders nothing.

## Tests

The parts that can be wrong without crashing anything -- the projection, the colour assignment, the
distance ramp, the config parser -- are host-testable and are covered:

```bash
cargo test -p er-invasion-path
```
