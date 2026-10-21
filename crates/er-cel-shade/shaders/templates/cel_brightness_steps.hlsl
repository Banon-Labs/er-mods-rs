// Cel brightness steps: the finished picture's brightness rounded to steps, the look's "Brightness
// steps" knob.
//
// Made for the Tonemap post stage, the one place every pixel passes after all lighting, so it
// reaches forward-lit hair, fur and translucent cloth and glowing things, which the lighting
// passes never see. The luminance is rounded to that many steps between black and white, in
// display space where steps are even to the eye, and the channels are scaled by one factor so
// hue survives. The darkest step is left as it was, so shadow detail is not crushed to black.
// This is the maths the tonemap port ran before cel became layers; below 2 gives back the stage's
// colour.

uniform int Steps < ui_type = "slider"; ui_min = 0; ui_max = 16; ui_label = "Brightness steps";
    ui_tooltip = "Rounds the finished picture's brightness to this many steps. 0 is off."; > = 6;

static const float3 LUMA = float3(0.2126, 0.7152, 0.0722);

float3 apply(float3 c, TonemapCtx ctx)
{
    float levels = (float)Steps;
    // One step is a flat picture; below two is off.
    if (!(levels >= 2.0))
    {
        return c;
    }
    float luma = dot(c, LUMA);
    float banded = floor(luma * levels + 0.5) / levels;
    return banded > 0.0 && luma > 1e-4 ? c * (banded / luma) : c;
}
