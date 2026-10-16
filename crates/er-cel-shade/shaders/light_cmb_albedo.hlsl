// Port of Elden Ring's `GXLightCmbAlbedo.ppo` (gxrenderershader.shaderbnd.dcx): the diffuse
// albedo the lit buffer is multiplied by, `(1 - metalness) * base colour`, read from the G-buffer.
// Registers and signature are copied from the original container's reflection; with both knobs
// below off this is the vanilla pass.
//
// Elden Ring's surfaces carry their detail in the textures -- carved floors, embroidered cloth --
// and banding the light cannot remove that, because a flat-lit room has no light gradient to band.
// So the base colour itself is flattened here:
//
// * `cel_paint_radius` -- a four-quadrant Kuwahara filter. Each pixel takes the mean of whichever
//   neighbouring quadrant varies least, so texture detail merges into flat patches while edges
//   between patches stay sharp, which is what a painted cel looks like.
// * `cel_colour_levels` -- each channel then rounded to that many levels in gamma space, where
//   the steps are even to the eye, turning the patches into solid fills.

#include "cel_look.hlsli"

SamplerState SS_ClampPoint : register(s7);
Texture2D<float4> g_GBuffer1Texture : register(t3);
Texture2D<float4> g_GBuffer2Texture : register(t4);

struct PsIn
{
    noperspective float4 position : SV_Position;
    float2 uv : TEXCOORD0;
    float4 clip : TEXCOORD1;
};

// The largest radius the loop allows, matching `KNOBS` `paint_radius` max. A bound the compiler
// can see keeps the loop from being treated as unbounded.
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

float4 ps_main(PsIn input) : SV_Target0
{
    int radius = clamp((int)round(cel_paint_radius), 0, MAX_PAINT_RADIUS);
    float3 albedo = radius > 0
        ? kuwahara(input.uv, radius)
        : g_GBuffer2Texture.Sample(SS_ClampPoint, input.uv).rgb;
    float metalness = g_GBuffer1Texture.Sample(SS_ClampPoint, input.uv).x;
    // One level is a single flat colour, which is no picture at all; below two is off.
    if (cel_colour_levels >= 2.0)
    {
        float3 encoded = pow(saturate(albedo), 1.0 / 2.2);
        encoded = floor(encoded * cel_colour_levels + 0.5) / cel_colour_levels;
        albedo = pow(encoded, 2.2);
    }
    return float4((1.0 - metalness) * albedo, 1.0);
}
