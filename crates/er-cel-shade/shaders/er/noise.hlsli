// Procedural noise for layers (design 3.1), so most layers that want a noise texture need no
// texture at all: a layer cannot declare a resource, and the game binds none of ours. A layer
// writes `#include "er/noise.hlsli"`; the editor puts this file once before every layer of the
// pass, so each of them sees the same functions.
//
// Everything here is a pure function of its arguments: no globals, no resources, and every loop
// bounded by a literal. In a vertex carrier, whose `vertex_ctx.hlsli` defines `ER_VERTEX_CARRIER`,
// the octave loops go without `[loop]`: the compiler records that attribute on the loop as
// metadata, and the helper splices no code that carries any into a game vertex shader.
//
// - `er_hash`, `er_hash01`: integer hashes, and the same as a float in [0, 1).
// - `er_value_noise`: smooth value noise in [0, 1], in two and three dimensions, one lattice cell
//   per unit of `p`.
// - `er_fbm`: value noise summed over octaves, each twice the frequency and half the amplitude of
//   the last, normalised back to [0, 1].
// - `er_worley`: cellular noise, the distance from `p` to the nearest feature point (`.x`, F1)
//   and to the second nearest (`.y`, F2), one feature point per unit cell.

#ifndef ER_NOISE_HLSLI
#define ER_NOISE_HLSLI

// The most octaves `er_fbm` sums.
#define ER_NOISE_MAX_OCTAVES 8

// The PCG hash of one word (Jarzynski and Olano, "Hash Functions for GPU Rendering", 2020).
uint er_hash(uint v)
{
    uint state = v * 747796405u + 2891336453u;
    uint word = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u;
    return (word >> 22u) ^ word;
}

uint er_hash(uint2 v)
{
    return er_hash(v.x ^ er_hash(v.y));
}

uint er_hash(uint3 v)
{
    return er_hash(v.x ^ er_hash(v.y ^ er_hash(v.z)));
}

// The top 24 bits of a hash as a float in [0, 1).
float er_hash01(uint v)
{
    return (float)(er_hash(v) >> 8) * (1.0 / 16777216.0);
}

float er_hash01(uint2 v)
{
    return (float)(er_hash(v) >> 8) * (1.0 / 16777216.0);
}

float er_hash01(uint3 v)
{
    return (float)(er_hash(v) >> 8) * (1.0 / 16777216.0);
}

// The lattice corner `cell + offset` as hash input. Negative cells wrap, which a hash does not mind.
uint2 er_noise_key(float2 cell, int2 offset)
{
    return uint2(int2(cell) + offset);
}

uint3 er_noise_key(float3 cell, int3 offset)
{
    return uint3(int3(cell) + offset);
}

float er_value_noise(float2 p)
{
    float2 cell = floor(p);
    float2 f = p - cell;
    float2 u = f * f * (3.0 - 2.0 * f);
    float a = er_hash01(er_noise_key(cell, int2(0, 0)));
    float b = er_hash01(er_noise_key(cell, int2(1, 0)));
    float c = er_hash01(er_noise_key(cell, int2(0, 1)));
    float d = er_hash01(er_noise_key(cell, int2(1, 1)));
    return lerp(lerp(a, b, u.x), lerp(c, d, u.x), u.y);
}

float er_value_noise(float3 p)
{
    float3 cell = floor(p);
    float3 f = p - cell;
    float3 u = f * f * (3.0 - 2.0 * f);
    float a = er_hash01(er_noise_key(cell, int3(0, 0, 0)));
    float b = er_hash01(er_noise_key(cell, int3(1, 0, 0)));
    float c = er_hash01(er_noise_key(cell, int3(0, 1, 0)));
    float d = er_hash01(er_noise_key(cell, int3(1, 1, 0)));
    float e = er_hash01(er_noise_key(cell, int3(0, 0, 1)));
    float g = er_hash01(er_noise_key(cell, int3(1, 0, 1)));
    float h = er_hash01(er_noise_key(cell, int3(0, 1, 1)));
    float k = er_hash01(er_noise_key(cell, int3(1, 1, 1)));
    float front = lerp(lerp(a, b, u.x), lerp(c, d, u.x), u.y);
    float back = lerp(lerp(e, g, u.x), lerp(h, k, u.x), u.y);
    return lerp(front, back, u.z);
}

// `octaves` is clamped to 1..`ER_NOISE_MAX_OCTAVES`.
float er_fbm(float2 p, int octaves)
{
    int count = clamp(octaves, 1, ER_NOISE_MAX_OCTAVES);
    float sum = 0.0;
    float total = 0.0;
    float amplitude = 0.5;
#ifndef ER_VERTEX_CARRIER
    [loop]
#endif
    for (int octave = 0; octave < ER_NOISE_MAX_OCTAVES; ++octave)
    {
        if (octave >= count)
        {
            break;
        }
        sum += amplitude * er_value_noise(p);
        total += amplitude;
        p = p * 2.0 + 17.0;
        amplitude *= 0.5;
    }
    return sum / total;
}

float er_fbm(float3 p, int octaves)
{
    int count = clamp(octaves, 1, ER_NOISE_MAX_OCTAVES);
    float sum = 0.0;
    float total = 0.0;
    float amplitude = 0.5;
#ifndef ER_VERTEX_CARRIER
    [loop]
#endif
    for (int octave = 0; octave < ER_NOISE_MAX_OCTAVES; ++octave)
    {
        if (octave >= count)
        {
            break;
        }
        sum += amplitude * er_value_noise(p);
        total += amplitude;
        p = p * 2.0 + 17.0;
        amplitude *= 0.5;
    }
    return sum / total;
}

// The feature point of the cell at `cell`, somewhere inside it.
float2 er_worley_feature(float2 cell)
{
    uint2 key = er_noise_key(cell, int2(0, 0));
    return cell + float2(er_hash01(key), er_hash01(key ^ uint2(0x9e3779b9u, 0x7f4a7c15u)));
}

float3 er_worley_feature(float3 cell)
{
    uint3 key = er_noise_key(cell, int3(0, 0, 0));
    return cell + float3(er_hash01(key), er_hash01(key ^ uint3(0x9e3779b9u, 0x7f4a7c15u, 0u)),
                         er_hash01(key ^ uint3(0u, 0x9e3779b9u, 0x7f4a7c15u)));
}

float2 er_worley(float2 p)
{
    float2 cell = floor(p);
    float first = 8.0;
    float second = 8.0;
    [unroll] for (int y = -1; y <= 1; ++y)
    {
        [unroll] for (int x = -1; x <= 1; ++x)
        {
            float d = length(er_worley_feature(cell + float2(x, y)) - p);
            second = d < first ? first : min(second, d);
            first = min(first, d);
        }
    }
    return float2(first, second);
}

float2 er_worley(float3 p)
{
    float3 cell = floor(p);
    float first = 8.0;
    float second = 8.0;
    [unroll] for (int z = -1; z <= 1; ++z)
    {
        [unroll] for (int y = -1; y <= 1; ++y)
        {
            [unroll] for (int x = -1; x <= 1; ++x)
            {
                float d = length(er_worley_feature(cell + float3(x, y, z)) - p);
                second = d < first ? first : min(second, d);
                first = min(first, d);
            }
        }
    }
    return float2(first, second);
}

#endif // ER_NOISE_HLSLI
