// Proof: inverts the stage's colour, so the probe reads post = 1 - pre (design 14, P0.2a items 2
// and 5).
//
// It has an `apply` for every stage's context, so it can be placed at any stage. At the
// Tonemap stages the colour is the picture, so the screen shows its negative; at Lighting it is
// the light, which can exceed 1, and the inverse of bright light is negative. Amount at 1 is the
// exact inverse the proof checks; lower values move the probe's post towards pre, which is what a
// slider proof watches.

uniform float Amount < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
    ui_label = "Amount"; ui_tooltip = "1 is the full inverse, 0 leaves the colour alone."; > = 1.0;

float3 invert(float3 c)
{
    return lerp(c, 1.0 - c, Amount);
}

float3 apply(float3 c, LightingCtx ctx)
{
    return invert(c);
}

float3 apply(float3 c, AlbedoCtx ctx)
{
    return invert(c);
}

float3 apply(float3 c, TonemapCtx ctx)
{
    return invert(c);
}
