// Port of Elden Ring's `ToneMap_TabledToneMapPS` and, with `CEL_LUT` defined,
// `ToneMap_TabledToneMapLUTPS` (both in gxposteffect.shaderbnd.dcx): the pass that turns the
// finished HDR frame into the picture on screen -- bloom mixed in, a colour matrix, a vignette, a
// tonemapping table, the colour-grading LUT, and an HDR-output branch.
//
// This is the one pass every pixel goes through after all lighting. Forward-lit materials (hair,
// fur, translucent cloth) and emissive light sources compute their colour themselves and never
// touch the deferred passes the rest of this crate replaces, so posterizing here is what reaches
// them. Measured 2026-10-14: the player's hair is `P[ChrCustomize][Hair]_FwdSdw` and Rennala's
// robe `CS[VA_Frame][Fur]_FurBlur`, both forward, and both stayed smooth until this pass.
//
// The body is transcribed from the decompiled originals, and the constant buffers are declared as
// the raw `float4` arrays the originals index, at the original registers and sizes (64 and 416
// bytes), so no field layout can drift. With both knobs below at their off values it is the
// vanilla pass.
//
// `cel_saturation` scales the finished colour's distance from its own grey. `cel_screen_levels`
// then rounds its luminance to that many steps between black and white, in display space where
// steps are even to the eye, and scales the channels by one factor so hue survives. The darkest
// step is left as it was, so shadow detail is not crushed to black.
//
// Two stages run here. TonemapPre takes the scene in HDR, after the bloom and the colour matrix
// and before the vignette and the tone curve; TonemapPost takes the finished picture after cel.
// The probe's tonemap region records the picture before and after the TonemapPost layers, so a
// TonemapPre layer shows in both.

#include "cel_look.hlsli"
#include "stage_ctx.hlsli"

cbuffer cbToneMap : register(b1)
{
    float4 tm[26];
};

cbuffer cbPostProcessCommon : register(b4)
{
    float4 pp[4];
};

SamplerState SS_ClampLinear : register(s1);
Texture2D<float4> g_SourceTexture : register(t0);
Texture2D<float4> g_ToneMapTableTexture : register(t1);
#ifdef CEL_LUT
Texture3D<float4> g_ColorGradingLUTTexture : register(t2);
#endif
Texture2D<float4> g_GlareAccTexture : register(t3);

struct PsIn
{
    float4 position : SV_Position;
    float3 uv : TEXCOORD0;
};

static const float3 LUMA_601 = float3(0.2989099919795990, 0.5866100192070007, 0.1144800037145615);

float table(float x)
{
    return g_ToneMapTableTexture.SampleLevel(SS_ClampLinear, float2(x / (x + 0.2), 0.0), 0.0).x;
}

// The HDR-output branch, shared by both variants.
float3 hdr_output(float3 x)
{
    float inverse = 1.0 / tm[1].x;
    float3 expanded = exp2(log2(x / max(1.0 - x, 0.01)) * inverse);
    float y = dot(expanded, LUMA_601);
    float low = exp2(log2(y) * tm[1].x);
    float high = exp2(log2((y - 1.0) * 0.052631579339504242 + 1.0) * tm[1].x);
    float curve = y > 1.0 ? ((high / (high + 1.0)) - 0.5) * 19.0 + 0.5 : low / (low + 1.0);
    float luma = dot(x, LUMA_601) + 9.9999997473787516e-05;
    return exp2(log2(tm[19].y * ((curve * x) / luma)) * 0.30303031206130981) * 0.49770236015319824;
}

static const float3 LUMA_709 = float3(0.2126, 0.7152, 0.0722);

float3 posterize_screen(float3 colour, float levels)
{
    float luma = dot(colour, LUMA_709);
    float banded = floor(luma * levels + 0.5) / levels;
    return banded > 0.0 && luma > 1e-4 ? colour * (banded / luma) : colour;
}

#include "editor_layers.hlsli"

float4 ps_main(PsIn input) : SV_Target0
{
    float2 uv = pp[0].xy * input.uv.xy;
    float4 glare = g_GlareAccTexture.SampleLevel(SS_ClampLinear, uv, 0.0);
    float4 source = g_SourceTexture.SampleLevel(SS_ClampLinear, uv, 0.0);
    float3 colour = tm[3].xyz * source.xyz + tm[7].x * glare.xyz;
    float3 mixed = float3(
        max(0.0, dot(colour, tm[11].xyz) + tm[11].w),
        max(0.0, dot(colour, tm[12].xyz) + tm[12].w),
        max(0.0, dot(colour, tm[13].xyz) + tm[13].w));

    float2 vignette_at = tm[18].xy * (input.uv.xy * 2.0 - 1.0);
    float vignette = saturate(1.0 - saturate(length(vignette_at) * tm[18].z + tm[18].w));
    vignette = vignette * vignette * (3.0 - vignette * 2.0);
    vignette = vignette * vignette;
    vignette = vignette * vignette;

    TonemapCtx ctx;
    ctx.px = uint2(input.position.xy);
    ctx.uv = input.uv.xy;
    ctx.source = source.xyz;
    ctx.glare = glare.xyz;
    ctx.vignette = vignette;
    mixed = er_stage_tonemap_pre(mixed, ctx);

    float3 toned = vignette * float3(table(mixed.x), table(mixed.y), table(mixed.z));

    bool hdr = asuint(tm[16].z) != 0;
    float3 result;
#ifdef CEL_LUT
    float3 lut_at = exp2(log2(max(toned, 0.0)) * tm[2].z) * 0.9375 + 0.03125;
    float3 graded = g_ColorGradingLUTTexture.Sample(SS_ClampLinear, lut_at).xyz;
    result = hdr ? hdr_output(exp2(log2(max(graded, 0.0)) * (1.0 / tm[2].z))) : graded;
#else
    result = hdr ? hdr_output(toned) : exp2(log2(max(toned, 0.0)) * tm[2].z);
#endif

    if (cel_saturation != 1.0)
    {
        float grey = dot(result, LUMA_709);
        result = max(lerp(grey.xxx, result, cel_saturation), 0.0);
    }
    // One step is a flat picture; below two is off.
    if (cel_screen_levels >= 2.0)
    {
        result = posterize_screen(result, cel_screen_levels);
    }
    float3 layered = er_stage_tonemap_post(result, ctx);

    // The grid is laid over the HDR source's size; no mask exists yet, so the last word is 0.
    uint width, height;
    g_SourceTexture.GetDimensions(width, height);
    er_probe_record(ER_PROBE_TONEMAP_HEADER, ER_PROBE_TONEMAP_SAMPLES, ctx.px, uint2(width, height),
                    result, layered, 0);

    return float4(layered, 1.0);
}
