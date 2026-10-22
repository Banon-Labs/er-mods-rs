// Vignette: darkens, or tints, the edges of the screen.
//
// Made for the Tonemap post stage, so it lands on the finished picture after the game's own
// vignette. `ctx.uv` runs from 0 to 1 across the screen; the display size in `er_p[ER_P_FRAME].zw`
// keeps the round shape round on a wide screen.

uniform float Strength < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
    ui_label = "Strength"; ui_tooltip = "How far the edges go towards the colour below."; > = 0.4;
uniform float Radius < ui_type = "slider"; ui_min = 0.0; ui_max = 1.5; ui_step = 0.01;
    ui_category = "Shape"; ui_label = "Radius";
    ui_tooltip = "Where the darkening starts, from the centre: 1 is the middle of each edge."; >
    = 0.7;
uniform float Softness < ui_type = "slider"; ui_min = 0.01; ui_max = 1.0; ui_step = 0.01;
    ui_category = "Shape"; ui_label = "Softness";
    ui_tooltip = "How gradually it darkens past the radius."; > = 0.5;
uniform int Shape < ui_type = "combo"; ui_items = "Round\0Screen-shaped\0"; ui_category = "Shape";
    ui_label = "Shape"; ui_tooltip = "Round keeps a circle; screen-shaped follows the screen's sides."; >
    = 0;
uniform float3 Colour < ui_type = "color"; ui_label = "Colour";
    ui_tooltip = "The colour at the edges. Black darkens."; > = float3(0.0, 0.0, 0.0);

float3 apply(float3 c, TonemapCtx ctx)
{
    float2 from_centre = ctx.uv * 2.0 - 1.0;
    if (Shape == 0)
    {
        float2 display = max(er_p[ER_P_FRAME].zw, float2(1.0, 1.0));
        from_centre.x *= display.x / display.y;
    }
    float edge = smoothstep(Radius, Radius + Softness, length(from_centre));
    return lerp(c, Colour, edge * Strength);
}
