// Posterize light: the light on surfaces rounded to bands, the core of a cel look.
//
// Made for the Lighting stage, whose colour is the diffuse light before the game multiplies each
// surface's colour in, so the bands follow the light and the textures keep their detail. The
// brightness is banded and the colour scaled by one factor, so hues survive.

uniform int Mode < ui_type = "combo"; ui_items = "Exposure stops\0Even steps\0"; ui_label = "Bands";
    ui_tooltip = "Exposure stops band evenly to the eye at any brightness; even steps band the light linearly."; >
    = 0;
uniform float Stops < ui_type = "slider"; ui_min = 0.25; ui_max = 4.0; ui_step = 0.05;
    ui_label = "Band width"; ui_units = " stops";
    ui_tooltip = "With exposure stops: each band is this many stops wide."; > = 1.0;
uniform int Steps < ui_type = "slider"; ui_min = 2; ui_max = 16; ui_label = "Steps";
    ui_tooltip = "With even steps: how many bands between dark and Ceiling."; > = 4;
uniform float Ceiling < ui_type = "slider"; ui_min = 0.25; ui_max = 8.0; ui_step = 0.05;
    ui_label = "Ceiling"; ui_tooltip = "With even steps: the brightest band's light."; > = 2.0;
uniform float Softness < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
    ui_label = "Softness"; ui_tooltip = "Mixes the smooth light back in: 0 is hard bands."; > = 0.0;

static const float3 LUMA = float3(0.2126, 0.7152, 0.0722);

float banded(float luma)
{
    if (Mode == 0)
    {
        float stops = max(Stops, 0.01);
        return exp2(stops * floor(log2(luma) / stops + 0.5));
    }
    float steps = (float)max(Steps, 1);
    float ceiling = max(Ceiling, 1e-3);
    return min(floor(luma / ceiling * steps + 0.5), steps) * ceiling / steps;
}

float3 apply(float3 c, LightingCtx ctx)
{
    float luma = max(dot(c, LUMA), 1e-6);
    float3 bands = c * (banded(luma) / luma);
    return lerp(bands, c, Softness);
}
