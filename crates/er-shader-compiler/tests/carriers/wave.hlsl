// Refused: a carrier that uses a wave intrinsic, so a vertex's position would depend on which
// other vertices share its wave -- a different answer in every pass.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

void main(float3 p : ER_POS, float t : ER_TIME, out float3 o : ER_OUT)
{
    o = p;
    o.y += WaveActiveSum(p.x) * er_p[257].x * t;
}
