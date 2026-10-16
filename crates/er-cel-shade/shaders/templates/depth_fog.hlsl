// Depth fog: the light on surfaces fades towards a haze with distance.
//
// Made for the Lighting stage, the one stage that binds depth: `ctx.view_pos` is the pixel's
// position from the camera in metres. The stage's colour is the diffuse light, which the game
// multiplies by each surface's colour afterwards, so the haze takes on the colour of what it
// covers. The sky keeps its own light: its depth gives no finite position.

uniform float3 FogColour < ui_type = "color"; ui_label = "Fog light";
    ui_tooltip = "The light distant surfaces fade towards."; > = float3(0.55, 0.6, 0.7);
uniform float Brightness < ui_type = "slider"; ui_min = 0.0; ui_max = 4.0; ui_step = 0.01;
    ui_label = "Brightness"; ui_tooltip = "Scales the fog light."; > = 1.0;
uniform float Start < ui_type = "slider"; ui_min = 0.0; ui_max = 200.0; ui_step = 0.5;
    ui_category = "Distance"; ui_label = "Start"; ui_units = " m";
    ui_tooltip = "Distance from the camera where the fog begins."; > = 20.0;
uniform float Density < ui_type = "slider"; ui_min = 0.001; ui_max = 0.2; ui_scale = "log";
    ui_category = "Distance"; ui_label = "Density";
    ui_tooltip = "How quickly the fog thickens past the start, per metre."; > = 0.02;
uniform float Maximum < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
    ui_category = "Distance"; ui_label = "Maximum";
    ui_tooltip = "The most fog any distance gets: 1 hides far surfaces entirely."; > = 0.85;

float3 apply(float3 c, LightingCtx ctx)
{
    float distance = length(ctx.view_pos);
    if (!isfinite(distance))
    {
        return c;
    }
    float fog = 1.0 - exp(-max(distance - Start, 0.0) * Density);
    return lerp(c, FogColour * Brightness, saturate(fog) * Maximum);
}
