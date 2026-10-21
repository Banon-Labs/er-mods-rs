// Refused: a carrier with a system-value input. A carrier sees the stored position, the time and
// the frame, and nothing else, so the splice has no game value to feed `SV_VertexID` from.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

void main(float3 p : ER_POS, float t : ER_TIME, uint id : SV_VertexID, out float3 o : ER_OUT)
{
    o = p;
    o.y += (float)(id & 1u) * er_p[257].x * t;
}
