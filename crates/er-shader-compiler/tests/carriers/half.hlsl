// Refused: a carrier with a minimum-precision type. The driver may run it at any precision from 16
// bits up, so two pipelines could place one vertex differently.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

void main(float3 p : ER_POS, float t : ER_TIME, out float3 o : ER_OUT)
{
    min16float s = (min16float)p.x * (min16float)er_p[257].x;
    o = p;
    o.y += (float)(s * s + (min16float)t);
}
