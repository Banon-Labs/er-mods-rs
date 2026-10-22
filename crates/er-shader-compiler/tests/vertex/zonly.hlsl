// A stand-in game vertex shader, written for the splice tests, with an output that reads only the
// stored z: before a carrier is spliced in, its dependency table says TEXCOORD0 depends on input z
// alone, which a carrier that moves z by y has to change.
cbuffer cbObjMatrix : register(b5)
{
    float4 offset;
};

struct Out
{
    float4 pos : SV_Position;
    float z : TEXCOORD0;
};

Out main(float3 p : POSITION0)
{
    Out o;
    o.pos = float4(p + offset.xyz, 1.0);
    o.z = p.z;
    return o;
}
