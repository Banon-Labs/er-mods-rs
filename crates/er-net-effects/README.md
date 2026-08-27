# er-net-effects

Named SpEffect calls, applied to your own character from the keyboard. This was
the original feature of this repository, and the thing it used to be named after;
it is now a standalone DLL with no dependency on the quick-load product.

Load it through [me3](https://github.com/garyttierney/me3) as its own `[[natives]]`
entry:

<!-- md-test: bash-n -->
```bash
cargo xwin build --release --target x86_64-pc-windows-msvc -p er-net-effects
```

The SpEffect list itself lives in `crates/effects-data` (`data/effects.json`),
embedded at compile time and validated offline against `SpEffectParam`.

## In-game controls

The original feature remains: named SpEffect calls are embedded from
`data/effects.json` and can be applied by runtime trigger logic. They start
inactive by default.

In-game controls:

The selector bar is **open** when all three hold: the bar is shown
(`overlay_visible_on_start`, or toggled back with Alt+Numpad0), it is
**expanded** rather than minimized to its `[+]` button, and a character is
loaded. The bar ships minimized -- click its `[+]` button to expand it.

Keys that move the on-screen cursor need the bar open:

- Left/Right: switch the active effect catalog.
- Up/Down: step through the selected catalog's validated IDs and apply the selected effect.
- Numpad +/-: add or remove the highlighted effect from the always-on stack.

Keys that are deliberate chords or your own bindings work whether the bar is
open or not, because firing an effect while you play is what the DLL is for:

- Alt+': toggle the currently selected effect off/on.
- Alt+Numpad0 (also Alt+0, Alt+Insert): show/hide the selector bar -- the way back to a hidden one.
- Anything you bind in `.er-net-effects-hotkeys.json`.

Only the four arrow keys are ever **taken** from the game, and only while the bar
is open: they are blanked out of the DirectInput keyboard state and swallowed by
the low-level keyboard hook so a cursor move is not also a quick-item switch.
Every other key the selector reads is passed straight through, and with the bar
closed nothing is taken at all -- including at the title screen and on every
loading screen, where the arrow keys are how you navigate.
`er-net-effects-telemetry.json` reports `effect_selector_open` (the gate itself,
which is NOT `effect_selector_visible`), `effect_keys_ignored_while_closed`, and
`effect_input_suppressed_arrow_keys`.

Runtime SpEffect application is gated to the loaded character state: the local player must exist before the DLL calls `apply_speffect`, but standing idle is allowed. Selector/file changes before the player exists may arm the selected effect; once the player is live, direct trigger hotkeys and selected effects apply immediately.

Persisted selector files next to `eldenring.exe`:

- `.effect-catalog-setting.txt`: selected catalog file name.
- `.effect-setting.txt`: selected SpEffect ID. Editing this file while the game is running applies the matching in-catalog effect ID live, moves the catalog cursor to the first catalog containing that ID, and persists effects as ON. If no user catalog contains the ID, the DLL records the ID but has no catalog entry to apply.
- `.effect-enabled-setting.txt`: persistent selector ON/OFF state (`on` or `off`). If it is `on`, the selected persisted effect is re-armed on DLL startup and applied once the local player is live.
- `.effect-hotkeys.json`: user-editable hotkey triggers. If missing, the DLL creates this default file:

<!-- md-test: skip illustrative JSON config example -->
```json
{
  "hotkeys": [
    {
      "name": "deathblight self test",
      "key": "numpad_multiply",
      "effect_id": 8355,
      "count": 1
    }
  ]
}
```

Supported key names include `numpad_multiply`, `numpad_add`, `numpad_subtract`, `numpad_divide`, `numpad_decimal`, `numpad0`..`numpad9`, arrow keys, and optional `alt+` prefixes. `count` is clamped to `1..=200`. Trigger hotkeys apply the configured SpEffect directly to the local player `count` times without removing other effects, and the file is reloaded while the game is running.

User catalogs:

The DLL starts with zero effect selector catalogs. User-provided game-directory catalogs can be placed in `effect-catalogs/*.json` next to `eldenring.exe`; each file is a plain JSON array of SpEffect IDs, with the file name acting as the catalog identity, for example `my-effects.json`. The DLL watches this folder while the game is running and reloads catalogs when JSON files are created/changed/removed.

Master catalog:

`effect-master-catalog.json` can be placed next to `eldenring.exe` when rich SpEffect metadata is available. It is keyed by `SpEffectParam` ID and records names, VFX IDs, derived tags, and meaningful non-default fields such as AI perception, HP/FP/stamina, movement/timing, damage, defense, and lifetime fields. When this file is present, selector/user catalog IDs are validated against it and the HUD uses its names. When it is absent, user catalog IDs still load with generic `SpEffect <id>` names. Selector/user catalogs should reference this file by ID instead of copying field metadata.

Regenerate the master catalog from a local regulation file:

<!-- md-test: bash-n -->
```bash
scripts/generate-effect-master-catalog.py --regulation "$REGULATION_BIN"
```

Validate the list against a regulation file:

<!-- md-test: bash-n -->
```bash
cargo run -p er-param-inspect -- validate "$REGULATION_BIN"
```

Inspect rows:

<!-- md-test: bash-n -->
```bash
cargo run -p er-param-inspect -- rows "$REGULATION_BIN" SpEffectParam 4330 20018100 20018101
```

## Network sync semantics

Each SpEffect call takes a "don't sync" flag. The overlay/control surface exposes
that as `Sync effect calls over the network`:

- **Off (default):** effects are applied with `dont_sync = true`; local-only and
  safer for offline/local testing.
- **On:** effects are applied with `dont_sync = false`, matching the Cheat Engine
  `addNetworked(..., id, 0)` pattern; peers may observe the application.

Leave sync off unless you specifically need peer-visible effect calls in a
controlled environment. Non-standard online behavior can be detectable and may
carry ban risk.

