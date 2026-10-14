// The look, as the in-game menu set it. One float per knob in `src/settings.rs` `KNOBS`, in the
// same order -- a host test reads the declarations below and fails if the two drift.
//
// The DLL appends a root constant buffer view at `b0, space42` to every root signature the game
// creates and binds a small upload buffer holding these values to it whenever the game sets a
// root signature, so no game binding is moved and the values change the moment a slider does.
// Every knob at its `off` value is the game's own picture.

cbuffer CelLook : register(b0, space42)
{
    float cel_light_band_stops;
    float cel_outline_ink;
    float cel_outline_width;
    float cel_outline_depth;
    float cel_outline_crease;
    float cel_paint_radius;
    float cel_colour_levels;
    float cel_screen_levels;
    float cel_saturation;
};
