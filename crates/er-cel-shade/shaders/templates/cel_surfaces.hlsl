// Cel surfaces: every surface's colour flattened into painted patches, the look's "Surfaces" knobs.
//
// Made for the Albedo stage, whose colour is the diffuse albedo the pass writes, `(1 - metalness)
// * base colour`. Elden Ring's surfaces carry their detail in the textures -- carved floors,
// embroidered cloth -- and banding the light cannot remove that, because a flat-lit room has no
// light gradient to band. So the base colour itself is flattened:
//
// * Paint radius -- a four-quadrant Kuwahara filter. Each pixel takes the mean of whichever
//   neighbouring quadrant varies least, so texture detail merges into flat patches while edges
//   between patches stay sharp, which is what a painted cel looks like.
// * Colour levels -- each channel then rounded to that many levels in gamma space, where the
//   steps are even to the eye, turning the patches into solid fills.
//
// Both work on the base colour before the metalness is multiplied in, which the stage's colour
// has already had, so the layer reads the base colour again from the pass's own G-buffer 2 and
// returns the whole product itself. This is the maths the albedo port ran before cel became
// layers, line for line; both knobs at 0 give back the stage's colour.

uniform int PaintRadius < ui_type = "slider"; ui_min = 0; ui_max = 6; ui_label = "Paint radius";
    ui_units = " samples";
    ui_tooltip = "Merges texture detail into flat patches with sharp edges. 0 keeps the textures. Costly above 4."; >
    = 3;
uniform int ColourLevels < ui_type = "slider"; ui_min = 0; ui_max = 16; ui_label = "Colour levels";
    ui_tooltip = "Levels per colour channel of every surface. 0 keeps every shade."; > = 6;

// The largest radius the loop allows, the slider's maximum. A bound the compiler can see keeps the
// loop from being treated as unbounded.
static const int MAX_PAINT_RADIUS = 6;

// Mean colour of the quadrant spanning `direction`, and the summed variance of its channels.
float4 quadrant(float2 uv, float2 step, float2 direction, int radius)
{
    float3 sum = 0.0;
    float3 square = 0.0;
    [loop] for (int y = 0; y <= radius; ++y)
    {
        [loop] for (int x = 0; x <= radius; ++x)
        {
            float3 colour = g_GBuffer2Texture.SampleLevel(SS_ClampPoint, uv + float2(x, y) * direction * step, 0).rgb;
            sum += colour;
            square += colour * colour;
        }
    }
    float count = (radius + 1) * (radius + 1);
    float3 mean = sum / count;
    float3 variance = abs(square / count - mean * mean);
    return float4(mean, variance.r + variance.g + variance.b);
}

float3 kuwahara(float2 uv, int radius)
{
    uint width, height;
    g_GBuffer2Texture.GetDimensions(width, height);
    // Sample spacing grows with resolution so the patch size is the same on screen at 1080p and 4K.
    float spacing = max(1.0, round(width / 1920.0));
    float2 step = spacing / float2(width, height);
    float4 a = quadrant(uv, step, float2(-1.0, -1.0), radius);
    float4 b = quadrant(uv, step, float2(1.0, -1.0), radius);
    float4 c = quadrant(uv, step, float2(-1.0, 1.0), radius);
    float4 d = quadrant(uv, step, float2(1.0, 1.0), radius);
    float4 best = a;
    best = b.w < best.w ? b : best;
    best = c.w < best.w ? c : best;
    best = d.w < best.w ? d : best;
    return best.rgb;
}

float3 apply(float3 c, AlbedoCtx ctx)
{
    int radius = clamp(PaintRadius, 0, MAX_PAINT_RADIUS);
    float levels = (float)ColourLevels;
    float3 albedo = radius > 0
        ? kuwahara(ctx.uv, radius)
        : g_GBuffer2Texture.Sample(SS_ClampPoint, ctx.uv).rgb;
    // One level is a single flat colour, which is no picture at all; below two is off.
    if (levels >= 2.0)
    {
        float3 encoded = pow(saturate(albedo), 1.0 / 2.2);
        encoded = floor(encoded * levels + 0.5) / levels;
        albedo = pow(encoded, 2.2);
    }
    return (1.0 - ctx.metalness) * albedo;
}
