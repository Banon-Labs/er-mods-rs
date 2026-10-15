// Refused: a carrier that reads a texture. The game binds nothing of ours to a vertex shader but
// the parameter block, so this read would see whatever the game's own table holds there.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

Texture2D<float> er_height : register(t0, space42);

void main(float3 p : ER_POS, float t : ER_TIME, out float3 o : ER_OUT)
{
    o = p;
    o.y += er_height.Load(int3(int(p.x), int(p.z), 0)) * er_p[257].x;
}
