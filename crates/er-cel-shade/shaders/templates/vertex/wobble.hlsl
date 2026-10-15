// Wobble: the mesh ripples sideways in waves that run up it.
//
// A vertex layer works on the vertex as the mesh stores it: model space, in the model's units
// (metres in FLVER files), before skinning. On a character that is the bind pose, so the ripple
// follows the animation. Every pass that draws the mesh -- depth, shadow, G-buffer, velocity --
// runs this same code, so the shadow and the motion vectors move with it.
//
// The time wraps every 8 pi seconds (`vertex_ctx.hlsli`); a whole-number Speed runs through the
// wrap without a jump, because both waves then turn a whole number of times in one period.

uniform float Amplitude < ui_type = "slider"; ui_min = 0.0; ui_max = 0.25; ui_step = 0.005;
    ui_units = "m"; ui_label = "Amplitude";
    ui_tooltip = "How far a vertex moves. The game culls with the unmoved bounds, so a large value can pop at the screen edge."; >
    = 0.03;
uniform float Wavelength < ui_type = "slider"; ui_min = 0.1; ui_max = 10.0; ui_step = 0.1;
    ui_units = "m"; ui_label = "Wavelength"; ui_tooltip = "The distance between two crests."; >
    = 1.5;
uniform float Speed < ui_type = "slider"; ui_min = 0.0; ui_max = 10.0; ui_step = 0.1;
    ui_label = "Speed (rad/s)"; ui_tooltip = "How fast the waves run."; > = 3.0;

float3 apply(float3 p, VertexCtx c)
{
    float k = 6.2831853 / Wavelength;
    p.x += Amplitude * sin(p.y * k + c.time * Speed);
    p.z += Amplitude * sin(p.y * k * 1.31 + c.time * Speed * 0.75 + 1.7);
    return p;
}
