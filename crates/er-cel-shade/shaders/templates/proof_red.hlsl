// Proof: keeps only the red channel, so the probe reads post = (pre.r, 0, 0) (design 14, P0.2a
// item 3). Rewriting a staged `proof_invert` layer into this one is the edit whose new variant
// the proof times from file to pixels.
//
// It has an `apply` for every stage's context, so it can be placed at any stage.

uniform float Amount < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
    ui_label = "Amount"; ui_tooltip = "1 keeps only red, 0 leaves the colour alone."; > = 1.0;

float3 red(float3 c)
{
    return lerp(c, c * float3(1.0, 0.0, 0.0), Amount);
}

float3 apply(float3 c, LightingCtx ctx)
{
    return red(c);
}

float3 apply(float3 c, AlbedoCtx ctx)
{
    return red(c);
}

float3 apply(float3 c, TonemapCtx ctx)
{
    return red(c);
}
