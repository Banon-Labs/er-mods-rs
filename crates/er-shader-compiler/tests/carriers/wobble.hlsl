// The wobble template as a carrier, written out by hand with its parameters at slot 1 (header
// er_p[272], parameters er_p[273]), as `vertex/zonly_wobbled.hlsl` applies the same layer inline.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

struct VertexCtx
{
    float time;
    uint frame;
};

namespace L1
{
    float3 apply(float3 p, VertexCtx c)
    {
        float k = 6.2831853 / er_p[273].y;
        p.x += er_p[273].x * sin(p.y * k + c.time * er_p[273].z);
        p.z += er_p[273].x * sin(p.y * k * 1.31 + c.time * er_p[273].z * 0.77 + 1.7);
        return p;
    }
}

float3 er_vertex_stage(float3 p, VertexCtx c)
{
    float4 h1 = er_p[272];
    p = lerp(p, L1::apply(p, c), h1.x * h1.y);
    return p;
}

void main(float3 p : ER_POS, float t : ER_TIME, uint f : ER_FRAME, out float3 o : ER_OUT)
{
    VertexCtx c;
    c.time = t;
    c.frame = f;
    o = er_vertex_stage(p, c);
}
