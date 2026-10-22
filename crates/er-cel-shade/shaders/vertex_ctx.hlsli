// The vertex side of the editor (`docs/plans/er-shader-editor-vertex.md`, sections 3 and 7.3):
// what a vertex layer's `apply` is handed, and the parameter block a vertex carrier reads.
//
// A vertex carrier is the layer stack compiled once as a `vs_6_0` shader whose only inputs are the
// stored position (`ER_POS`), the time (`ER_TIME`) and the frame (`ER_FRAME`), and whose only
// output is the moved position (`ER_OUT`). `er-shader-compiler`'s `vsplice` copies its code into
// each game vertex shader right after the shader loads `POSITION0`, feeding the time and the frame
// from `er_p[3]`, or from `er_p[ER_P_PREVIOUS]` for a velocity pass's previous position.
//
// A carrier may read the parameter block and nothing else. `editor_abi.hlsli` also declares the
// probe and the mask plane; no vertex layer names them, and DXC drops a resource that no code
// reads, so neither reaches a carrier. One that did would be refused by `vsplice::check_carrier`.

#ifndef VERTEX_CTX_HLSLI
#define VERTEX_CTX_HLSLI

// This is a vertex carrier: the library leaves out what the helper cannot splice (`er/noise.hlsli`
// drops `[loop]` from its octave loops).
#define ER_VERTEX_CARRIER

#include "editor_abi.hlsli"
#include "er/noise.hlsli"

// The period the time a vertex layer sees wraps at, in seconds: 8 pi, about 25 s. The DLL's clock
// runs from the moment it loaded and never wraps, and `sin` and `cos` are accurate only for
// arguments within 100 pi of 0 (the D3D functional spec's bound), so a layer that multiplies time
// by up to 10 rad/s stays inside it. A rate in rad/s that is a multiple of 0.25 loops through the
// wrap without a jump, and so does any period that divides 8 pi, which is what the proof layers
// use; any other rate jumps once each wrap. The wrap is applied inside the carrier, so the current
// and the previous time of a velocity pass go through the same arithmetic.
#define ER_TIME_WRAP 25.132741228718345

// The Vertex stage, in every pass that draws a selected mesh. What a vertex layer's `apply` is
// handed besides the stored position: when the position is being moved for.
struct VertexCtx
{
    // Seconds, wrapped to [0, ER_TIME_WRAP): er_p[3].x, or er_p[ER_P_PREVIOUS].x in a velocity
    // pass's previous-position call.
    float time;
    // The present count that time belongs to.
    uint frame;
};

// `t` wrapped to [0, ER_TIME_WRAP).
float er_wrap_time(float t)
{
    return t - ER_TIME_WRAP * floor(t / ER_TIME_WRAP);
}

#endif // VERTEX_CTX_HLSLI
