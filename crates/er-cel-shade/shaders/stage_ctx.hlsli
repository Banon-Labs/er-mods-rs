// The port side of a stage (design 7.1 and 5.3): what a port hands its stage's layers, how a
// layer's result is mixed in, and what the port records in the probe.
//
// A port computes the game's colour and cel's change to it, fills its stage's context, calls the
// stage function in `editor_layers.hlsli` to get the colour after the user's layers, and records
// the two in the probe with `er_probe_record`. Only our variants contain this code, so a probe
// region that keeps advancing proves a variant of that port is bound.

#ifndef STAGE_CTX_HLSLI
#define STAGE_CTX_HLSLI

#include "editor_abi.hlsli"

// Which build of which port wrote a probe region: the port kind in the top byte, which is the
// port's index in `src/replace.rs` `REPLACEMENTS`, and below it the build number of a
// runtime-compiled variant, 0 for the embedded one. The compiler's arguments set it;
// `scripts/build-cel-shaders.py` passes `kind << 24`.
#ifndef ER_VARIANT_ID
#error "ER_VARIANT_ID comes from the compiler's arguments: -D ER_VARIANT_ID=<port kind << 24>"
#endif

// The Lighting stage, in the two AO passes. Its colour is the diffuse light after occlusion and
// cel, which the albedo pass's output is multiplied into later. The specular light is in the
// context and is not the stage's colour, so layers leave it as cel left it.
struct LightingCtx
{
    // The pixel, and the pass's screen coordinate from 0 to 1.
    uint2 px;
    float2 uv;
    // The depth buffer's raw value, and the view-space position it reconstructs.
    float depth;
    float3 view_pos;
    // World-space normal from G-buffer 0, and the same normal in view space.
    float3 normal;
    float3 view_normal;
    // The G-buffer as the pass reads it: `gbuffer1.x` is metalness and `gbuffer1.y` the ambient
    // BRDF table's second coordinate; `gbuffer2.rgb` is the base colour.
    float4 gbuffer1;
    float4 gbuffer2;
    // The pass's specular output after cel, and the occlusion and shadow term it multiplied in.
    float3 specular;
    float occlusion;
};

// The Albedo stage, in the albedo pass. Its colour is the diffuse albedo the pass writes,
// `(1 - metalness) * base colour`, after cel.
struct AlbedoCtx
{
    uint2 px;
    float2 uv;
    float metalness;
};

// The two tonemap stages, in both tonemap passes. TonemapPre's colour is the scene in HDR, bloom
// and the colour matrix applied, before the vignette and the tone curve; TonemapPost's is the
// finished picture after cel's saturation and levels.
struct TonemapCtx
{
    // The pixel, and the screen coordinate from 0 to 1 (the source texture is read at
    // `pp[0].xy * uv`).
    uint2 px;
    float2 uv;
    // The HDR source and the bloom, as read, and the vignette factor the pass applies.
    float3 source;
    float3 glare;
    float vignette;
};

// Blend modes a layer slot's header names.
#define ER_BLEND_NORMAL 0
#define ER_BLEND_ADD 1
#define ER_BLEND_MULTIPLY 2
#define ER_BLEND_SCREEN 3

// `x` with a layer's result `y` mixed in. `header` is the layer slot's header register
// (design 5.2) as plain floats: on (0 or 1), opacity (0 to 1), blend (one of `ER_BLEND_*`) and
// mask id. `mask` is how much of the layer's selector covers this pixel, 0 to 1: the generated
// code passes `er_mask(header.w, c)` (design 7.3). A layer that is off, transparent or masked out
// returns `x` exactly, even when `y` is not a number.
float3 er_mix(float3 x, float3 y, float4 header, float mask)
{
    float amount = saturate(header.x) * saturate(header.y) * saturate(mask);
    uint blend = (uint)max(header.z, 0.0);
    float3 blended = y;
    if (blend == ER_BLEND_ADD)
    {
        blended = x + y;
    }
    else if (blend == ER_BLEND_MULTIPLY)
    {
        blended = x * y;
    }
    else if (blend == ER_BLEND_SCREEN)
    {
        blended = 1.0 - (1.0 - x) * (1.0 - y);
    }
    return amount > 0.0 ? lerp(x, blended, amount) : x;
}

// How much of mask `id`, the one a layer chose, covers this pixel, 0 to 1 (design 7.4): the
// stage-local masks -- depth range, sky, facing, luminance, screen rectangle -- with their invert
// flag and feather. One overload per stage context, because each stage binds different inputs.
// Mask 0 is Everything. These are the identity until P0.2a evaluates the masks, so every id
// covers the whole screen; the generated code calls them already, so a layer compiles the same
// before and after.
float er_mask(float id, LightingCtx c)
{
    return 1.0;
}

float er_mask(float id, AlbedoCtx c)
{
    return 1.0;
}

float er_mask(float id, TonemapCtx c)
{
    return 1.0;
}

// How much of selector `id`'s family and instance terms (design 8) cover this pixel, 0 to 1: the
// mask records resolved into the mask plane (P3) and the capsules (P4). The identity until then.
float er_selected(float id, LightingCtx c)
{
    return 1.0;
}

float er_selected(float id, AlbedoCtx c)
{
    return 1.0;
}

float er_selected(float id, TonemapCtx c)
{
    return 1.0;
}

// The present count the DLL published with this parameter block. It starts at 1, so a probe
// header still holding 0 was never written.
uint er_frame()
{
    return asuint(er_p[ER_P_FRAME].y);
}

// Record this pixel in one probe region: `header_at` and `samples_at` are the region's
// `ER_PROBE_*_HEADER` and `ER_PROBE_*_SAMPLES`, `size` the target the grid is laid over, `pre` the
// colour after the game's maths and cel, `post` the colour after the layers, `aux` the region's
// last sample word.
//
// Every pixel raises the header's largest `SV_Position` seen with `InterlockedMax`, but only a
// pixel past the value already stored issues the atomic, so a frame costs one load per pixel and
// almost no atomics. The maximum is the largest any frame has drawn since the probe was created:
// it never falls, so a smaller target later is read from the header's width and height.
//
// Then the one pixel on each point of an 8x8 grid over `size` writes the header -- frame, variant
// and size -- and its sample, both stamped with the frame, which is how the decoder in
// `src/probe.rs` tells this frame's samples from older ones. A grid point the draw never covers
// keeps its older sample, and the decoder counts it as stale.
void er_probe_record(uint header_at, uint samples_at, uint2 px, uint2 size, float3 pre,
                     float3 post, uint aux)
{
    if (px.x > er_probe.Load(header_at + ER_PROBE_HEADER_MAX_X))
    {
        er_probe.InterlockedMax(header_at + ER_PROBE_HEADER_MAX_X, px.x);
    }
    if (px.y > er_probe.Load(header_at + ER_PROBE_HEADER_MAX_Y))
    {
        er_probe.InterlockedMax(header_at + ER_PROBE_HEADER_MAX_Y, px.y);
    }

    uint2 cell = px * ER_PROBE_GRID / max(size, uint2(1, 1));
    if (any(cell >= ER_PROBE_GRID))
    {
        return;
    }
    // The centre of the cell, in whole pixels of `size`.
    uint2 centre = (cell * 2 + 1) * size / (ER_PROBE_GRID * 2);
    if (any(px != centre))
    {
        return;
    }

    uint frame = er_frame();
    er_probe.Store(header_at + ER_PROBE_HEADER_FRAME, frame);
    er_probe.Store(header_at + ER_PROBE_HEADER_VARIANT, (uint)(ER_VARIANT_ID));
    er_probe.Store(header_at + ER_PROBE_HEADER_WIDTH, size.x);
    er_probe.Store(header_at + ER_PROBE_HEADER_HEIGHT, size.y);

    uint at = samples_at + (cell.y * ER_PROBE_GRID + cell.x) * ER_PROBE_SAMPLE_BYTES;
    er_probe.Store3(at + ER_PROBE_SAMPLE_PRE, asuint(pre));
    er_probe.Store(at + ER_PROBE_SAMPLE_FRAME, frame);
    er_probe.Store3(at + ER_PROBE_SAMPLE_POST, asuint(post));
    er_probe.Store(at + ER_PROBE_SAMPLE_AUX, aux);
}

#endif // STAGE_CTX_HLSLI
