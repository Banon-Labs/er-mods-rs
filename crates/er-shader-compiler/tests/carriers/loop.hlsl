// A carrier with a loop whose trip count comes from the parameter block, at most 8: DXC cannot
// unroll it, so it stays a loop with a back edge and phis, which the splice must carry into the
// game's function intact. It also reads the frame, so `ER_FRAME` is part of its signature.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

struct VertexCtx
{
    float time;
    uint frame;
};

namespace L5
{
#define Octaves (asuint(er_p[256 + 5 * 16 + 1].x))
#define Amplitude (er_p[256 + 5 * 16 + 1].y)
    float3 apply(float3 p, VertexCtx c)
    {
        float3 q = p;
        uint n = min(Octaves, 8u);
        float phase = c.time + (float)(c.frame & 7u) * 0.01;
        for (uint i = 0; i < n; ++i)
        {
            float f = (float)(1u << i);
            q.y += Amplitude / f * sin(p.x * f + phase);
        }
        return q;
    }
#undef Octaves
#undef Amplitude
}

float3 er_vertex_stage(float3 p, VertexCtx c)
{
    float4 h5 = er_p[256 + 5 * 16];
    p = lerp(p, L5::apply(p, c), h5.x * h5.y);
    return p;
}

void main(float3 p : ER_POS, float t : ER_TIME, uint f : ER_FRAME, out float3 o : ER_OUT)
{
    VertexCtx c;
    c.time = t;
    c.frame = f;
    o = er_vertex_stage(p, c);
}
