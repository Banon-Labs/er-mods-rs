// `zonly.hlsl` with `carriers/branchy.hlsl`'s two layers written in by hand on the stored position,
// so DXC computes the dependency table of a carrier with a branch, control dependence included.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

cbuffer cbObjMatrix : register(b5)
{
    float4 offset;
};

struct Out
{
    float4 pos : SV_Position;
    float z : TEXCOORD0;
};

float3 wave(float3 p, float t)
{
    p.x += er_p[305].x * sin(p.y * er_p[305].y + t * er_p[305].z);
    p.z += er_p[305].x * cos(p.x * er_p[305].y + t * er_p[305].z);
    return p;
}

float3 clamp_floor(float3 p, float t)
{
    if (p.y < er_p[321].x)
    {
        p.y = er_p[321].x + 0.1 * sin(t);
    }
    return p;
}

Out main(float3 stored : POSITION0)
{
    float t = er_p[3].x;
    float4 h3 = er_p[304];
    float3 p = lerp(stored, wave(stored, t), h3.x * h3.y);
    float4 h4 = er_p[320];
    p = lerp(p, clamp_floor(p, t), h4.x * h4.y);
    Out o;
    o.pos = float4(p + offset.xyz, 1.0);
    o.z = p.z;
    return o;
}
