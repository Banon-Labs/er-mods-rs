// Refused: a carrier that writes a UAV, which is not a pure function of the vertex.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

RWByteAddressBuffer er_probe : register(u1, space42);

void main(float3 p : ER_POS, float t : ER_TIME, out float3 o : ER_OUT)
{
    er_probe.Store(0, asuint(p.x));
    o = p + er_p[257].xyz * t;
}
