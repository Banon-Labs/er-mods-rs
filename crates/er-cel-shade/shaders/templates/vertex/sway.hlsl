// Sway: the mesh bends to and fro, more the higher a vertex sits above the model's origin, like a
// plant in the wind. The base stays put.

uniform float Strength < ui_type = "slider"; ui_min = 0.0; ui_max = 0.5; ui_step = 0.01;
    ui_label = "Strength";
    ui_tooltip = "How far the top leans, as a fraction of the height above the origin."; > = 0.08;
uniform float Height < ui_type = "slider"; ui_min = 0.1; ui_max = 20.0; ui_step = 0.1;
    ui_units = "m"; ui_label = "Height";
    ui_tooltip = "The height that leans by the full strength; above it the lean keeps growing."; >
    = 2.0;
uniform float Speed < ui_type = "slider"; ui_min = 0.0; ui_max = 10.0; ui_step = 0.1;
    ui_label = "Speed (rad/s)"; ui_tooltip = "How fast it sways."; > = 1.5;
uniform float3 Direction < ui_type = "drag"; ui_min = -1.0; ui_max = 1.0; ui_step = 0.01;
    ui_label = "Direction"; ui_tooltip = "The way it leans, in model space; only x and z count."; >
    = float3(1.0, 0.0, 0.3);

float3 apply(float3 p, VertexCtx c)
{
    float h = max(p.y, 0.0) / Height;
    float lean = Strength * Height * h * h * sin(c.time * Speed);
    float2 d = Direction.xz;
    float len = length(d);
    d = len > 0.0001 ? d / len : float2(1.0, 0.0);
    p.xz += d * lean;
    return p;
}
