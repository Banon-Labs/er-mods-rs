// `zonly.hlsl` with `carriers/wobble.hlsl`'s layer written in by hand, where the splice puts it:
// on the stored position, before anything else reads it. DXC computes this shader's dependency
// table itself, which is what the splice's rewritten table is compared with.
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

float3 wobble(float3 p, float t)
{
    float k = 6.2831853 / er_p[273].y;
    p.x += er_p[273].x * sin(p.y * k + t * er_p[273].z);
    p.z += er_p[273].x * sin(p.y * k * 1.31 + t * er_p[273].z * 0.77 + 1.7);
    return p;
}

Out main(float3 stored : POSITION0)
{
    float4 h = er_p[272];
    float3 p = lerp(stored, wobble(stored, er_p[3].x), h.x * h.y);
    Out o;
    o.pos = float4(p + offset.xyz, 1.0);
    o.z = p.z;
    return o;
}
