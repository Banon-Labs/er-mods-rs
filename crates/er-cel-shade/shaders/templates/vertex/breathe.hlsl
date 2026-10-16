// Breathe: the mesh swells and shrinks about the model's origin.

uniform float Amount < ui_type = "slider"; ui_min = 0.0; ui_max = 0.5; ui_step = 0.01;
    ui_label = "Amount"; ui_tooltip = "The largest change in size: 0.1 is ten percent."; > = 0.05;
uniform float Speed < ui_type = "slider"; ui_min = 0.0; ui_max = 10.0; ui_step = 0.1;
    ui_label = "Speed (rad/s)"; ui_tooltip = "How fast it breathes."; > = 2.0;

float3 apply(float3 p, VertexCtx c)
{
    return p * (1.0 + Amount * sin(c.time * Speed));
}
