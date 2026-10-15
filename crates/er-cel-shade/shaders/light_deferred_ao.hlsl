// Port of Elden Ring's `GXLightDeferredAO.ppo` and, with `CEL_SSR` defined,
// `GXLightDeferredAOWithSsr.ppo` (both in gxrenderershader.shaderbnd.dcx): the pass that multiplies
// screen-space ambient occlusion into the finished diffuse and specular light buffers. The SSR
// variant differs only in blending the screen-space reflection texture into the specular output.
//
// Every register, the `cbLightAccParam` layout and the I/O signature are copied from the original
// container's reflection (`dxc -dumpbin`), and the body follows its decompiled SPIR-V line for
// line, so with every knob in `cel_look.hlsli` at its off value and no layers this is the vanilla
// pass. No root signature is declared here: the DLL creates this variant with the game pipeline's
// own, which carries the editor's space42 parameters (`editor_abi.hlsli`) appended.
//
// With `cel_light_band_stops` above 0, the diffuse output is posterized: its Rec. 709 luminance
// is rounded to whole steps of that many exposure stops and all three channels are scaled by one
// factor, so hue survives and the bands sit where the eye sees them at any exposure. The diffuse
// buffer at this point already holds the sun, the tiled point and spot lights and the ambient
// term, which is why this one pass reaches every light in the scene.
//
// The diffuse light after cel is the Lighting stage's colour: the user's Lighting layers run on it
// last, and the probe's AO region records it before and after them, with the raw depth.

#include "cel_look.hlsli"
#include "stage_ctx.hlsli"

cbuffer cbLightAccParam : register(b0)
{
    column_major float4x4 g_InvProjMatrix;
    column_major float4x3 g_ViewMatrix;
    float4 g_ScreenSize;
    column_major float4x4 g_CameraMatrix;
    float4 g_specPowerParam;
    column_major float4x4 g_ProjMatrix;
    uint g_lightCount;
    bool g_visualizeLightCount;
    uint g_spotLightCount;
    uint g_lightOrganizedBufferElements;
    float g_frustumNear;
    float g_frustumFar;
    float g_frustumSliceScale;
    float g_frustumSliceBias;
    uint4 g_lightTileSize;
    float g_ssrDepthThreshold;
    float g_ssrDepthFadeRangeRecp;
    float g_ssrFresnelThreshold;
    float g_ssrDisplayMipScale;
    float g_ssrDisplayMipOffset;
    float g_ssrReserved1;
    float2 g_ssrReserved2;
    bool g_isNeoOrScorpio;
    uint cbLightAccParam_pad22_y;
    uint2 g_emissiveCMaskInfo;
    float4 g_ViewportSize;
};

SamplerState SS_WrapLinear : register(s8);
SamplerState SS_ClampLinear : register(s9);
Texture2D<float4> g_ShadowTexture : register(t32);
Texture2D<float4> g_GBuffer0Texture : register(t0);
Texture2D<float4> g_AmbientBRDFTexture : register(t39);
#ifdef CEL_SSR
Texture2D<float4> g_RLRTexture : register(t44);
#endif
Texture2D<float4> g_DepthTexture : register(t1);
Texture2D<float4> g_GBuffer1Texture : register(t3);
Texture2D<float4> g_GBuffer2Texture : register(t4);
Texture2D<float4> g_IrradianceTexture : register(t6);
Texture2D<float4> g_SpecularAccTexture : register(t7);

struct PsIn
{
    noperspective float4 position : SV_Position;
    float2 uv : TEXCOORD0;
    float4 clip : TEXCOORD1;
};

struct PsOut
{
    float4 diffuse : SV_Target0;
    float4 specular : SV_Target1;
};

float3 posterize(float3 colour, float stops)
{
    float luma = max(dot(colour, float3(0.2126, 0.7152, 0.0722)), 1e-6);
    float banded = exp2(stops * floor(log2(luma) / stops + 0.5));
    return colour * (banded / luma);
}

// Inverse view depth. For any plane, 1/z is linear in screen space, so its second difference is
// zero across a flat floor or wall at any angle and large only where one surface ends in front of
// another -- which is the line a cartoon inks.
float inverse_depth(int2 texel, float2 clip)
{
    float depth = g_DepthTexture.Load(int3(texel, 0)).x;
    float4 view = mul(float4(clip, depth, 1.0), g_InvProjMatrix);
    return view.w / view.z;
}

float3 normal_at(int2 texel)
{
    return normalize(g_GBuffer0Texture.Load(int3(texel, 0)).xyz * 2.0 - 1.0);
}

// 0 inside a surface, 1 on an outline. Silhouettes come from depth, creases sharper than
// `cel_outline_crease` from the normals; normal-map detail stays below that and draws nothing.
float outline(int2 texel, float2 clip, float3 normal)
{
    uint width, height;
    g_DepthTexture.GetDimensions(width, height);
    // The width is in 1080p pixels and scaled to the real resolution, so the line keeps its
    // weight at 4K.
    int radius = max(1, (int)round(cel_outline_width * width / 1920.0));
    int2 dx = int2(radius, 0);
    int2 dy = int2(0, radius);

    float centre = inverse_depth(texel, clip);
    float across = abs(inverse_depth(texel - dx, clip) + inverse_depth(texel + dx, clip) - 2.0 * centre);
    float down = abs(inverse_depth(texel - dy, clip) + inverse_depth(texel + dy, clip) - 2.0 * centre);
    float silhouette = saturate((max(across, down) / max(abs(centre), 1e-6) - cel_outline_depth) * 8.0);
    // A neighbour at the far plane makes the ratio infinite or undefined; either way it is an edge.
    if (!(silhouette == silhouette))
    {
        silhouette = 1.0;
    }

    float facing = min(min(dot(normal, normal_at(texel - dx)), dot(normal, normal_at(texel + dx))),
                       min(dot(normal, normal_at(texel - dy)), dot(normal, normal_at(texel + dy))));
    float crease = saturate((1.0 - facing - cel_outline_crease) * 4.0);
    return max(silhouette, crease);
}

#include "editor_layers.hlsli"

PsOut ps_main(PsIn input)
{
    int3 texel = int3(int2(input.position.xy), 0);
    float4 irradiance = g_IrradianceTexture.Load(texel);
    float4 specular = g_SpecularAccTexture.Load(texel);
    float4 shadow = g_ShadowTexture.Sample(SS_WrapLinear, input.uv);
    float occlusion = min(shadow.x, shadow.y);
    float4 gbuffer1 = g_GBuffer1Texture.Load(texel);
    float4 gbuffer2 = g_GBuffer2Texture.Load(texel);
    float3 normal = normalize(g_GBuffer0Texture.Load(texel).xyz * 2.0 - 1.0);
    float depth = g_DepthTexture.Load(texel).x;

    float4 view = mul(float4(input.clip.xy, depth, 1.0), g_InvProjMatrix);
    float3 eye = normalize(view.xyz / view.w);
    float3 view_normal = mul(normal, (float3x3)g_ViewMatrix);
    float n_dot_v = saturate(abs(dot(-eye, view_normal)));
    float2 brdf = g_AmbientBRDFTexture.Sample(SS_ClampLinear, float2(n_dot_v, gbuffer1.y)).xy;
    float3 f0 = (gbuffer2.rgb - 0.04) * gbuffer1.x + 0.04;
    float3 reflectance = brdf.x * f0 + brdf.y;

#ifdef CEL_SSR
    float depth_fade = saturate(g_ssrDepthThreshold - g_ssrDepthFadeRangeRecp * (view.xyz / view.w).z);
    float fresnel_fade = saturate(max(reflectance.x, max(reflectance.y, reflectance.z)) * 10.0
                                  - g_ssrFresnelThreshold);
    float fade = min(depth_fade, fresnel_fade);
    float mip = saturate(g_ssrDisplayMipScale * (1.0 - gbuffer1.y) + g_ssrDisplayMipOffset) * 5.0;
    float4 reflection = g_RLRTexture.SampleLevel(SS_ClampLinear, input.uv, mip);
    float3 specular_in = (1.0 - reflection.w * fade) * specular.rgb + reflection.rgb * fade;
#else
    float3 specular_in = specular.rgb;
#endif

    PsOut output;
    float3 diffuse = occlusion * irradiance.rgb;
    float3 specular_out = occlusion * reflectance * specular_in;
    // Branches on constants, so every pixel takes the same side and they cost nothing when off.
    if (cel_light_band_stops > 0.0)
    {
        diffuse = posterize(diffuse, cel_light_band_stops);
    }
    if (cel_outline_ink > 0.0)
    {
        // Both buffers are light, multiplied by albedo afterwards, so darkening them here inks the
        // line in the surface's own colour going to black rather than painting a flat grey over it.
        float ink = 1.0 - cel_outline_ink * outline(texel.xy, input.clip.xy, normal);
        diffuse *= ink;
        specular_out *= ink;
    }

    LightingCtx ctx;
    ctx.px = uint2(input.position.xy);
    ctx.uv = input.uv;
    ctx.depth = depth;
    ctx.view_pos = view.xyz / view.w;
    ctx.normal = normal;
    ctx.view_normal = view_normal;
    ctx.gbuffer1 = gbuffer1;
    ctx.gbuffer2 = gbuffer2;
    ctx.specular = specular_out;
    ctx.occlusion = occlusion;
    float3 lit = er_stage_lighting(diffuse, ctx);

    // The grid is laid over the depth buffer, the texture this pass reads at `SV_Position`.
    uint width, height;
    g_DepthTexture.GetDimensions(width, height);
    er_probe_record(ER_PROBE_AO_HEADER, ER_PROBE_AO_SAMPLES, ctx.px, uint2(width, height), diffuse,
                    lit, asuint(depth));

    output.diffuse = float4(lit, 1.0);
    output.specular = float4(specular_out, 1.0);
    return output;
}
