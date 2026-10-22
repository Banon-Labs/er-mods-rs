// Colour grade: lift, gamma and gain, then saturation and a warm or cool shift.
//
// Made for the Tonemap post stage, where the colour is the finished picture from 0 to 1. Lift
// raises the shadows towards its colour, gain scales the highlights by its colour, and gamma bends
// the midtones between them.

uniform float3 Lift < ui_type = "color"; ui_category = "Lift, gamma, gain"; ui_label = "Lift";
    ui_tooltip = "Colour the shadows are raised towards. Black leaves them alone."; >
    = float3(0.0, 0.0, 0.0);
uniform float Gamma < ui_type = "slider"; ui_min = 0.5; ui_max = 2.0; ui_step = 0.01;
    ui_category = "Lift, gamma, gain"; ui_label = "Gamma";
    ui_tooltip = "Above 1 brightens the midtones, below 1 darkens them."; > = 1.0;
uniform float3 Gain < ui_type = "color"; ui_category = "Lift, gamma, gain"; ui_label = "Gain";
    ui_tooltip = "Colour the highlights are multiplied by. White leaves them alone."; >
    = float3(1.0, 1.0, 1.0);
uniform float Saturation < ui_type = "slider"; ui_min = 0.0; ui_max = 2.0; ui_step = 0.01;
    ui_category = "Colour"; ui_label = "Saturation";
    ui_tooltip = "Colour strength: 0 is grey, 1 unchanged, 2 doubled."; > = 1.0;
uniform float Temperature < ui_type = "slider"; ui_min = -1.0; ui_max = 1.0; ui_step = 0.01;
    ui_category = "Colour"; ui_label = "Temperature";
    ui_tooltip = "Below 0 is cooler and bluer, above 0 warmer and redder."; > = 0.0;
uniform bool KeepBrightness < ui_category = "Colour"; ui_label = "Keep brightness";
    ui_tooltip = "Scale the graded colour back to the brightness it had before."; > = false;
uniform float Strength < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
    ui_label = "Strength"; ui_tooltip = "How much of the grade is mixed in."; > = 1.0;

static const float3 LUMA = float3(0.2126, 0.7152, 0.0722);

float3 grade(float3 c)
{
    float3 graded = c * Gain + Lift * (1.0 - c);
    graded = pow(max(graded, 0.0), 1.0 / max(Gamma, 0.01));
    float grey = dot(graded, LUMA);
    graded = max(lerp(grey.xxx, graded, Saturation), 0.0);
    graded *= float3(1.0 + 0.1 * Temperature, 1.0, 1.0 - 0.1 * Temperature);
    if (KeepBrightness)
    {
        float before = dot(c, LUMA);
        float after = dot(graded, LUMA);
        graded *= after > 1e-5 ? before / after : 1.0;
    }
    return graded;
}

float3 apply(float3 c, TonemapCtx ctx)
{
    return lerp(c, grade(c), Strength);
}
