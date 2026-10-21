// A vertex-layer carrier, as codegen would emit it: one vertex layer (a wobble) and a
// branchy one (a clamp with an if), composed in stack order.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

struct VertexCtx
{
    float time;
};

namespace L3
{
#define Amplitude (er_p[256 + 3 * 16 + 1].x)
#define Frequency (er_p[256 + 3 * 16 + 1].y)
#define Speed (er_p[256 + 3 * 16 + 1].z)
    float3 apply(float3 p, VertexCtx c)
    {
        p.x += Amplitude * sin(p.y * Frequency + c.time * Speed);
        p.z += Amplitude * cos(p.x * Frequency + c.time * Speed);
        return p;
    }
#undef Amplitude
#undef Frequency
#undef Speed
}

namespace L4
{
#define Floor (er_p[256 + 4 * 16 + 1].x)
    float3 apply(float3 p, VertexCtx c)
    {
        if (p.y < Floor)
        {
            p.y = Floor + 0.1 * sin(c.time);
        }
        return p;
    }
#undef Floor
}

float3 er_vertex_stage(float3 p, VertexCtx c)
{
    float4 h3 = er_p[256 + 3 * 16];
    p = lerp(p, L3::apply(p, c), h3.x * h3.y);
    float4 h4 = er_p[256 + 4 * 16];
    p = lerp(p, L4::apply(p, c), h4.x * h4.y);
    return p;
}

void main(float3 p : ER_POS, float t : ER_TIME, out float3 o : ER_OUT)
{
    VertexCtx c;
    c.time = t;
    o = er_vertex_stage(p, c);
}
