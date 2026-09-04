# er-invasion-path

A walkable route to every other player in your session, drawn on the ground they would actually
walk. Its own DLL, its own `[[natives]]` entry, no dependency on the product mod.

Press the key (default `;`) during an invasion:

| situation | what you see |
|---|---|
| the navmesh can walk you there | a coloured line from your feet to theirs, along the terrain |
| the navmesh cannot | a glowing arrow out of your body, pointing exactly at them |
| N players | N routes, N colours, each colour stuck to one player |
| they are closer than 30 m | nothing -- the game's own compass hides its marker there too |

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
`er-quickload` -- the workspace pins `default-members` -- so the `-p` is not optional. It exits
zero in a fraction of a second having compiled nothing otherwise, which reads exactly like a
successful incremental build. Check the hash before you stage it:

```bash
sha256sum target/x86_64-pc-windows-msvc/release/er_invasion_path.dll
```

On first run it writes a commented `er-invasion-path.toml` beside the game executable and logs to
`er-invasion-path.log` in the same place.

## Configuration

Every setting lives in that generated file, and **every one of them is editable while the game is
running**. Change the key, save the file, press the new key -- no restart, no reload command. The
DLL re-reads the file about once a second and logs what it picked up:

```
config: reloaded -- toggle key semicolon -> F9
```

It compares the file's **contents**, not its timestamp, because `mtime` has one-second granularity
on several filesystems and would silently miss an edit saved in the same second as the previous
read. When the binding changes, the key-press detector is reset, so a key you happen to be holding
at that moment is not counted as a press of the new binding. A key name it cannot parse falls back
to the built-in default and says so -- a typo never leaves the feature unbindable.

The ones worth knowing:

| key | default | what it does |
|---|---|---|
| `toggle_key` | `"semicolon"` | key that switches the overlay on and off, **by name** (`"]"`, `"KP_Plus"`, `"Insert"`) |
| `trigger_item_id` | `0` | `EquipParamGoods` row whose USE also toggles it; `0` leaves the key as the only way in |
| `near_suppress_meters` | `30.0` | closer than this draws nothing. The game's own figure, measured in **3D** |
| `bold_at_meters` | `20.0` | at or inside this, full width and opacity |
| `faint_at_meters` | `150.0` | at this distance, the faintest a route is drawn |
| `max_targets` | `6` | most players drawn at once |
| `arrow_meters` | `3.0` | length of the no-route arrow |
| `start_enabled` | `false` | begin with the overlay already on |
| `marker_fxr_id` | `0` | spawn the game's OWN effects along the route; `302022` is the Rainbow Stone's lingering coloured stone. `0` is off, and off is the only setting here that leaves the game untouched |
| `marker_fxr_ids` | *(unset)* | one effect **per player**, in tracking order — overrides `marker_fxr_id`. Every id must LINGER |
| `marker_variant_a/b/c` | `-1` | the three spare spawn arguments, unset by default. An unproven lead on effect variants |
| `marker_spacing_meters` | `2.7` | metres between markers, measured along the path |
| `marker_keep_behind_meters` | `12.0` | how much already-walked trail is kept behind you before those stones are removed |
| `max_markers` | `144` | most markers one trail may hold |
| `markers_per_pass` | `3` | markers placed per pass (~6 passes a second), so the trail creeps out from your feet rather than appearing whole |
| `search_range_meters` | `0.0` | how far the navmesh search may range; `0` = unlimited, the engine's own default |
| `search_budget` | `100000` | iterations the search may spend — the engine's own default. `CS::CSAiFunc` uses `800`, which fails on spiral descents |

## Pick a key no other mod in your profile reads

The default was `F7` until a live 15-DLL run found `er-invasion-warp` polling `VK_F7` every frame in the same process: the key warped the player instead of drawing anything, and nothing warned about it. "Elden Ring binds nothing to it" is not the question -- the mods loaded beside you are, and a default cannot know them. `;` is clear of everything this workspace's shells poll, which makes it a better default rather than a safe one.

## When a route is NOT drawn

Closer than `near_suppress_meters` and you get nothing, because you already know where they are.

That number is the game's own. `MenuCommonParam` row 0 carries three of them, one per phantom
relationship, and only one is non-zero:

| field | offset | value |
|---|---|---|
| `compassEnemyHostInnerDistance` | `+0xa4` | **30.0** — an invader's marker for the host, hidden inside this |
| `compassFriendHostInnerDistance` | `+0xa0` | `0.0` — never hidden |
| `compassFriendGuestInnerDistance` | `+0xa8` | `0.0` — never hidden |

`FUN_140775f30` squares it at runtime and compares. Two deliberate departures from that rule:

- **This compares in 3D. The compass compares in 2D.** Its `dx`/`dy` are map-plane components with
  no vertical term, so a player five metres away and forty metres straight down reads as five
  metres and loses their marker. That is exactly when the walk down is the thing worth drawing,
  and whether one exists at all is the question.
- **No line-of-sight test.** The compass path has none. The one this crate used to apply -- close
  AND visible -- was its own stricter invention, and it is gone along with the raycast that
  served it.

## Colour, and why the stones do not have one

The route colours were the point of the original feature: N players, N colours. The imgui line does
that by generating a hue per palette slot. The stones cannot.

An FXR carries no tint the spawn can set -- `SpawnFfxInstance`'s spare arguments feed
`FUN_140d94af0`, which builds a parameter table out of time-of-day and weather. And `302022` is the
**Rainbow** Stone's lingering stone: cycling colour is what that effect *is*, so every trail looks
the same and none of them holds still.

Telling players apart therefore means a different EFFECT per player, not a different shade of one --
and every id in `marker_fxr_ids` must LINGER. `302020` (held), `302021` (projectile) and `302023`
(burst) are momentary Rainbow Stone stages: they flash once and vanish. Shipping those three as
defaults gave one player a trail and everyone else nothing, which read as the colours changing.

The likely answer needs no patching at all: **the game already ships this effect in several
colours.** `302464`, `302465` and `302466` are 71056 / 71040 / 71056 bytes, share `302022`'s
`s84005` resource family, and `302464` vs `302466` differ only at the id and then on a regular
64-byte stride -- the signature of a colour-table swap rather than a different effect. All are in
`sfxbnd_commoneffects`, hence always resident and spawnable today. Whether they *read* as ground
markers is a visual question, so `302022` stays first in the list: the first player gets the one
known to linger regardless.

Failing that, the fallback is runtime FXR patching -- replacing an effect's definition in memory so
new instances use recoloured bytes. The mechanism is a pointer repoint, not an overwrite, so the
replacement may be any size: load the new bytes through the game's own `FUN_1420fbd90` (which
validates the magic and version, allocates, and runs both fix-up passes), then point the definition
node's wrapper at the result. The node lives at `CSSfx -> +0x60 GXFfxSceneCtrl -> +0x28
GXFfxGraphicsResourceManager -> +0x160 FxrResourceContainer -> +0x20`, and `fromsoftware-rs`
already types every one of those structs. The machinery is about forty lines and no new
dependencies; the expensive part is authoring recoloured bytes, since `302022` cycles its colour by
design and that is a keyframed sequence rather than one constant.

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

**Runtime status: the chain runs.** Every address and structure offset is from static RE of ER
1.16.2 (dump VA == deobf VA == runtime VA, shift 0), cross-checked against two independent call
sites -- and on 2026-08-25 a live run answered `selfcheck: PASS -- 13 waypoints over 9m`, which is
all eleven function pointers, the async poll and the container walk executing for real. The
container walk validates everything it reads -- capacity a power of two, count bounded, every
element pointer non-null, every coordinate finite -- and refuses rather than trusting, and a
refusal degrades to the arrow.

**A request is never dropped, only drained.** Each one occupies a slot in a fixed ring on the
`CSHkAiWorld` -- measured live at **128 slots** -- and the ONLY thing that frees a slot is the
fetch in `poll`. There is no cancel. That ring belongs to the world, so filling it stops the
game's own NPCs pathfinding; at six targets refreshing six times a second, dropping instead of
draining would exhaust all 128 in under four seconds. Giving up on an answer moves the request to
a drain list that is polled every frame, on or off.

### Proving the route works without a second player

The chain above only ever ran for a remote player, which meant the first real execution of eleven
raw function pointers and a container walk would have been mid-invasion -- where an access
violation costs the session it was meant to prove, and a silent refusal is indistinguishable from
"the navmesh says there is no way to walk to them".

So the first time the overlay is switched on, it asks for one route to the nearest ordinary map
character -- an `Npc` stands on the navmesh by construction -- and writes the answer to the log.
Nothing is drawn from it. Switch the overlay on anywhere with enemies in it, solo, and read one
line:

```
selfcheck: PASS -- 14 waypoints over 23m       navmesh chain works end to end
selfcheck: no route to a map character 23m away -- the chain ran and answered
selfcheck: REFUSED at 23m -- <reason>          globals resolved, endpoint snap said no
```

Toggling off re-arms it, because the answer is a property of where you are standing.

### Finding the other players at all

The roster reads `WorldChrMan::player_chr_set`, which the engine documents as the set holding the
players. If that comes back empty it walks **every** ChrSet the world holds and picks characters by
kind instead, because this workspace's own enemy sweep already hedges that a co-op session may put
other players somewhere else, and a roster that trusts one set and finds nobody looks exactly like
a navmesh that found no route.

How much an unfamiliar `chr_type` is trusted depends on **where the character was found**, and the
asymmetry is deliberate:

| found in | rule | why |
|---|---|---|
| `player_chr_set` | exclusion list, fails **open** | membership is the evidence; an unfamiliar type there is likelier a session kind this build has not catalogued than a mistake |
| the wide sweep | allow-list of **named** player kinds, fails **closed** | those are the map's own ChrSets, where an unfamiliar type is scenery |

That split was forced by a live run. With the exclusion list applied everywhere, a single
`ChrType 7` sitting among 582 map NPCs read as a player and was drawn a permanent arrow, outside
any invasion. The same run showed the real people arriving exactly where the engine documents:

```
roster: remotes=1 sets=3 characters=584 widened=true types=[0:1 5:582 7:1]   <- the false arrow
roster: remotes=3 sets=1 characters=4 widened=false types=[2:1 0:3]          <- a Duelist and three Locals
```

`remotes=0` with a small `characters` count is "you are alone". `remotes=0` with a fat
`types=[...]` is a bug in this DLL, and names the type it failed on.

## What it does to the game

With `marker_fxr_id = 0`, the shipped default: **nothing**. No detours, no memory writes, no param
edits (patching params is what breaks Seamless invasions), no input injection, no network traffic.
It reads the roster, asks the navmesh a question, casts one sight ray per player, and draws.

Set `marker_fxr_id` and that stops being true in exactly one way: the DLL spawns real engine
effects in the world, through the game's own SFX manager, at the positions along your route. Still
no detours and no writes to game memory — but these are real objects with real lifetimes, and
"this mod only reads" is no longer an accurate description of it. That is why it is opt-in.

**They are yours alone.** Measured live on 2026-08-25 with a second player present: the markers
appear on the spawning client and on no other. A trail pointing at an invader does not point back
at you, and nothing about your position reaches their game.

**They are removed again.** Each marker is spawned through `CS::CSSfxImp::SpawnFfxInstance`
directly — not the fire-and-forget wrapper, which discards the control block and leaves an effect
nothing can ever take back — and the block is kept so the effect can be torn down. Stones go when
the route changes, when you walk past them, when a player leaves, and when the overlay is switched
off.

Removing one follows `CS::SosSignMan::ClearSignsWithSfxSetting_`, the game's own summon-sign
cleanup: stop flag, push, finalise, release, unlink. The guard in front of it is
`FUN_1420b6370`, and using anything weaker is what crashed a live session on 2026-08-25 — that
function is **two levels deep**, requiring both a non-null instance pointer at `ctrl+0x08` *and*
`FUN_1420b6280` reporting the instance still alive. A marker held for a few seconds, which every
real trail marker is, can have its effect finish in the meantime; reading the pointer alone passes
for that case and pushes parameters into a dead object.

### Proving the effects without a second player

The marker path only ever ran for a remote player's route, so its first real execution was in a
live session — and it took the game down. Now, whenever markers are enabled, the overlay's
self-check also spawns one at your feet, **holds it for 300 frames**, and removes it, logging each
step:

```
marker-selfcheck: spawning fxr 302022 at the player
marker-selfcheck: spawned, holding it
marker-selfcheck: despawning after 300 frames held
marker-selfcheck: PASS -- a held marker spawned and despawned without faulting
```

The hold is the point. An earlier version of this probe spawned and despawned in the same tick,
passed, and proved nothing — the gap during which an effect can finish on its own is the entire
bug.

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
