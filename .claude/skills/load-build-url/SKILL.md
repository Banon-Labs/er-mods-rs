---
name: load-build-url
description: Start Elden Ring as a build planner build. Use whenever the user hands over an er-build-planner.nyasu.business/?b=<id> link and asks to start up, load, or play "this build" or "this save". The link is the character; there is nothing else to find.
---

# Start the game as a build planner link

The whole mechanism is two lines in the game-directory config,
`$HOME/.local/share/Steam/steamapps/common/ELDEN RING/Game/er-quickload.toml`:

```toml
save_file = '<one of the base-level saves below>'
build_url = '<the link the user gave>'
```

`build_url` imports the build the first frame a character is in the world: it levels the
character, grants and equips the gear, memorises the spells. Nothing has to be pressed, and nothing
has to be searched for -- do not go looking for tools, rows, harness drives or save writers.

## The base save must be base level

The import levels a character up to the build; it cannot level one down. So the character it lands
on must be a fresh, base-level one. Use slot 0 of any one of these (user directive 2026-10-07):

- `/mnt/net/librus-home/Code Projects/Elden Ring Save Manager/data/save-files/25r`
- `/mnt/net/librus-home/Code Projects/Elden Ring Save Manager/data/save-files/2`
- `/mnt/net/librus-home/Code Projects/Elden Ring Save Manager/data/save-files/0`

Never the save already in the config, never the default APPDATA save, never a named character
(Leo, Banon BigD, ...): those are levelled, and a levelled base is the wrong character with the
build pasted over it. Set `slot = 0` beside `save_file`, and decode it before launch
(`python3 scripts/save-slot-oracle.py --save <file> --slot 0`) to satisfy the Autoload Identity
Launch Gate -- the decode is a check of the base level, not a search for a character.

## Launch

```bash
export ME3_PROFILE=/home/banon/Elden/all-mods-enabled.me3; \
python3 /home/banon/projects/er-mods-rs/scripts/er-teardown.py --reason=load-build-url > /dev/null 2>&1; \
bash /home/banon/Elden/launch.sh
```

`all-mods-enabled.me3` is "most of my mods". Background the launch (it outlives the 30s shell
cap); the inline `ME3_PROFILE=... bash launch.sh` spelling is refused by the teardown guard, the
`export` one is not.

## Keep "all" meaning all

`all-mods-enabled.me3` is generated, not hand-written, and it goes stale whenever a new DLL lands
(on 2026-10-07 it predated `er-npc-summons`, so the Mimic Tear summoned no turtles). Before using
it, compare its `[[natives]]` against `python3 scripts/me3-dll-list.py --pairs`; if a shippable
DLL is missing, regenerate it: `er-dll-closure.py --no-fetch --with <every package> --json`
(drop only what the closure reports as unresolvable, plus the opt-in-only and present-compositor
shells it lists as excluded), build with `scripts/er-build-dlls.sh <its packages>`, then
`er-gen-me3-profile.py --closure ... --save <er-pick-save.py --root <base save dir> --seed N
--json> --run-id all-mods-enabled --profile /home/banon/Elden/all-mods-enabled.me3`.

The turtles' AI is not a DLL: it is the AI lab (`scripts/er-ai-lab.py` running
`scripts/frida/ai-lua/mods/brain_turtles.lua`), a Frida attach made after the game is up.
