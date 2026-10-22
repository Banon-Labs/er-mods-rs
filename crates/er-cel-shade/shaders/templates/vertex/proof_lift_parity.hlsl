// Proof: lifts the mesh on odd frames only (`docs/plans/er-shader-editor-vertex.md`, section 9,
// oracle F). Every pass of one frame must agree on the frame's parity; a frame whose depth
// prepass and G-buffer draw read different parameter blocks shows up as a mixed frame and as holes.

uniform float Lift < ui_type = "slider"; ui_min = 0.0; ui_max = 100.0; ui_step = 0.1;
    ui_units = "m"; ui_label = "Lift"; ui_tooltip = "How far the mesh rises on an odd frame."; >
    = 0.5;

float3 apply(float3 p, VertexCtx c)
{
    if ((c.frame & 1u) != 0u)
    {
        p.y += Lift;
    }
    return p;
}
