// Cel saturation: the colour strength of the finished picture, the look's "Saturation" knob.
//
// Made for the Tonemap post stage, the picture as it goes to the screen, which forward-lit hair,
// fur and glowing things reach too. Each colour moves away from or towards its own grey by the
// same factor. This is the maths the tonemap port ran before cel became layers; 1 gives back the
// stage's colour.

uniform float Saturation < ui_type = "slider"; ui_min = 0.0; ui_max = 2.0; ui_step = 0.01;
    ui_label = "Saturation"; ui_tooltip = "Colour strength of the finished picture. 1 is the game's own."; >
    = 1.35;

static const float3 LUMA = float3(0.2126, 0.7152, 0.0722);

float3 apply(float3 c, TonemapCtx ctx)
{
    if (Saturation == 1.0)
    {
        return c;
    }
    float grey = dot(c, LUMA);
    return max(lerp(grey.xxx, c, Saturation), 0.0);
}
