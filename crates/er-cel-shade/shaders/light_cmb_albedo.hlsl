// Port of Elden Ring's `GXLightCmbAlbedo.ppo` (gxrenderershader.shaderbnd.dcx): the diffuse
// albedo the lit buffer is multiplied by, `(1 - metalness) * base colour`, read from the G-buffer.
// Registers and signature are copied from the original container's reflection, and the body is
// the original's maths alone: with no layers this is the vanilla pass.
//
// The cel look's flattened surfaces -- the Kuwahara paint and the colour levels this file used to
// run -- are the stock layer `templates/cel_surfaces.hlsl` at the Albedo stage now. A layer can do
// it because this file declares G-buffer 2 and its point sampler before `editor_layers.hlsli`, so
// the layer reads the base colour's neighbourhood itself.
//
// The albedo the pass writes is the Albedo stage's colour: the user's Albedo layers run on it, and
// the probe's albedo region records it before and after them. This pass binds no depth, so the
// region's last sample word is 0 here.

#include "stage_ctx.hlsli"

// Point sampling, clamped at the edges: the sampler the pass reads the G-buffer with.
SamplerState SS_ClampPoint : register(s7);
// G-buffer 1: `.x` is the metalness.
Texture2D<float4> g_GBuffer1Texture : register(t3);
// G-buffer 2: the base colour in `.rgb`, read at `uv`.
Texture2D<float4> g_GBuffer2Texture : register(t4);

struct PsIn
{
    noperspective float4 position : SV_Position;
    float2 uv : TEXCOORD0;
    float4 clip : TEXCOORD1;
};

#include "editor_layers.hlsli"

float4 ps_main(PsIn input) : SV_Target0
{
    float3 albedo = g_GBuffer2Texture.Sample(SS_ClampPoint, input.uv).rgb;
    float metalness = g_GBuffer1Texture.Sample(SS_ClampPoint, input.uv).x;
    float3 colour = (1.0 - metalness) * albedo;

    AlbedoCtx ctx;
    ctx.px = uint2(input.position.xy);
    ctx.uv = input.uv;
    ctx.metalness = metalness;
    float3 layered = er_stage_albedo(colour, ctx);

    // The grid is laid over G-buffer 2, the base colour this pass reads; it binds no depth.
    uint width, height;
    g_GBuffer2Texture.GetDimensions(width, height);
    er_probe_record(ER_PROBE_ALBEDO_HEADER, ER_PROBE_ALBEDO_SAMPLES, ctx.px, uint2(width, height),
                    colour, layered, 0);

    return float4(layered, 1.0);
}
