// The user's layers, one function per stage (design 7.3). This file is the identity: every stage
// returns its colour unchanged. The embedded ports compile with it, so an embedded port draws the
// game's maths plus cel and nothing else.
//
// The runtime compiler (P0.2a) never compiles this file. At the line a port includes it, the
// generated source has the enabled layers in stack order and these same four functions, each
// mixing its stage's layers in with `er_mix`. A port includes it after its own resources and
// helper functions and before `ps_main`, because a layer may call the port's globals.

#include "stage_ctx.hlsli"

// AO and AO with SSR: the diffuse light.
float3 er_stage_lighting(float3 x, LightingCtx c)
{
    return x;
}

// The albedo pass: the diffuse albedo.
float3 er_stage_albedo(float3 x, AlbedoCtx c)
{
    return x;
}

// Both tonemaps, before the tone curve: the scene in HDR.
float3 er_stage_tonemap_pre(float3 x, TonemapCtx c)
{
    return x;
}

// Both tonemaps, last: the finished picture after cel.
float3 er_stage_tonemap_post(float3 x, TonemapCtx c)
{
    return x;
}
