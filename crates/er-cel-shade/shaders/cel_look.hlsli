// The look, as the in-game menu set it: `cel_light_band_stops` and the other knobs of
// `src/settings.rs` `KNOBS`.
//
// The knobs used to be a constant buffer of their own here. They are now the first three registers
// of the editor's parameter block, which the DLL binds at `b0, space42` and rewrites every present,
// so `editor_abi.hlsli` declares them, as `#define`s onto `er_p`. A port includes this file for
// the look and reads the same names as before. Every knob at its `off` value is the game's own
// picture.

#include "editor_abi.hlsli"
