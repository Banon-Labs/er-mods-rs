// A stand-in for a game velocity-pass vertex shader, written for the splice tests: a stored
// position, an instance offset, the current and the previous view-projection in a scene buffer at
// b8 (registers 24-27 and 29-32, as the game's), and a branch on the instance count so the entry
// block has a successor whose phi names it.
cbuffer cbSceneParam : register(b8)
{
    float4 pad0[24];
    float4x4 VC_MatrixViewProj;
    float4 pad1;
    float4x4 VC_PrevMatrixViewProj;
};

cbuffer cbObjMatrix : register(b5)
{
    float4 offset;
    uint count;
};

struct Out
{
    float4 pos : SV_Position;
    float4 prev : POSITION1;
    float3 normal : NORMAL0;
};

Out main(float3 p : POSITION0, float3 n : NORMAL0, uint instance : SV_InstanceID)
{
    Out o;
    float3 world = p + offset.xyz;
    if (instance < count)
    {
        world += float3(0.0, offset.w * (float)instance, 0.0);
    }
    o.pos = mul(float4(world, 1.0), VC_MatrixViewProj);
    o.prev = mul(float4(world, 1.0), VC_PrevMatrixViewProj);
    o.normal = n;
    return o;
}
