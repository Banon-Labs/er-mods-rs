// Proof: lifts the mesh by a fixed distance (`docs/plans/er-shader-editor-vertex.md`, section 9,
// oracle D). With Hold up on, the default, the mesh stays lifted: nothing in it changes from frame
// to frame, so every pass of every frame agrees whatever parameter block each command list bound,
// and `check-shader-editor-probe.py --expect vertex-d` makes the steps itself by switching the
// vertex stage off and on. With Hold up off, the mesh rises for half a period and comes down for
// half a period. The period is a twelfth of the time wrap, about 2.09 s, so the steps run on
// through the wrap without a jump.
//
// Skinning rotates the lift with the bones, but every pass of one frame rotates it alike, so each
// probed depth takes one of two values in step with the layer. Collapsing the mesh to its origin
// would prove nothing on a skinned mesh: each vertex would land on its blended bone origin.

uniform float Lift < ui_type = "slider"; ui_min = 0.0; ui_max = 100.0; ui_step = 0.1;
    ui_units = "m"; ui_label = "Lift"; ui_tooltip = "How far the mesh rises while it is up."; >
    = 0.5;
uniform bool Hold < ui_label = "Hold up";
    ui_tooltip = "Keep the mesh lifted. Off, it steps up and down about once a second."; > = true;

static const float Period = ER_TIME_WRAP / 12.0;

float3 apply(float3 p, VertexCtx c)
{
    if (Hold || frac(c.time / Period) < 0.5)
    {
        p.y += Lift;
    }
    return p;
}
