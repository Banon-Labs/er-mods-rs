// The editor's binding ABI: what the DLL binds in register space 42, and where each value sits.
// `src/params.rs`, `src/probe.rs` and `src/look_binding.rs` declare the same numbers, and host
// tests in those files read the declarations and `#define`s below and fail if the two drift.
//
// The DLL appends these parameters, visible to pixel shaders, to every root signature with room
// for them, and binds A, B and C after every `SetGraphicsRootSignature` (design 5.1):
//
//   A  b0, space42  root CBV        the parameter block published this present, `er_p`
//   B  u1, space42  root UAV        the probe, `er_probe`
//   C  u0, space42  root UAV        the mask plane (P2), a 4 KB dummy until then, `er_mask_plane`
//   D  b1, space42  two constants   set only around duplicated draws (P3), declared where used
//
// A root signature without room for all four carries fewer (A and B, or A alone, or none), and a
// port pass is redirected only on one that carries all four.
//
// Included by `cel_look.hlsli` and `stage_ctx.hlsli`, so it is guarded.

#ifndef EDITOR_ABI_HLSLI
#define EDITOR_ABI_HLSLI

// The parameter block (design 5.2). The DLL rewrites all 1024 registers every present into the
// next block of an 8-block ring, so any register may be read. A root CBV has no bounds check, which
// is why every block is the full 16 KB declared here.
cbuffer EditorParams : register(b0, space42)
{
    float4 er_p[1024];
};

#define ER_P_REGISTERS 1024

// er_p[0..2]: the cel knobs, `Look::gpu_block` in `src/settings.rs`.
#define ER_P_CEL 0
#define ER_P_CEL_REGISTERS 3

// One name per knob, in `KNOBS` order: knob i is register i / 4, component i % 4, which is how
// `Look::gpu_block` packs them. Every knob at its `off` value is the game's own picture. A host
// test in `src/settings.rs` reads these lines and fails if a knob moves on one side only.
#define cel_light_band_stops (er_p[0].x)
#define cel_outline_ink (er_p[0].y)
#define cel_outline_width (er_p[0].z)
#define cel_outline_depth (er_p[0].w)
#define cel_outline_crease (er_p[1].x)
#define cel_paint_radius (er_p[1].y)
#define cel_colour_levels (er_p[1].z)
#define cel_screen_levels (er_p[1].w)
#define cel_saturation (er_p[2].x)

// er_p[3]: (time in seconds, asfloat(frame), display width, display height). The frame is the
// present count, starting at 1; probe headers and samples carry it.
#define ER_P_FRAME 3

// er_p[4]: asfloat of (mask pitch, mask rows, reverse Z, flags). Zero until the mask exists (P2).
#define ER_P_MASK 4

// er_p[5..31]: selector configuration (P2, P3).
#define ER_P_SELECTOR 5
#define ER_P_SELECTOR_REGISTERS 27

// er_p[64..191]: capsule anchors, two registers each (P4).
#define ER_P_CAPSULES 64
#define ER_P_CAPSULE_REGISTERS 128

// er_p[256..1023]: 48 layer slots of 16 registers. Each slot is a header register
// (on, opacity, blend, mask id) and up to 15 parameter registers (P0.2a).
#define ER_P_LAYERS 256
#define ER_P_LAYER_SLOTS 48
#define ER_P_LAYER_REGISTERS 16

// The probe (design 5.3): 8 KB that only our port variants write, copied into a readback ring at
// the `Close` of every command list that bound one. A probe that keeps advancing proves a variant
// is bound; a stalled one proves the game's own pass is.
RWByteAddressBuffer er_probe : register(u1, space42);

// The mask plane (P2). Declared now so the register is fixed; nothing reads it yet. Not named
// `er_mask`: that is the mask function in `stage_ctx.hlsli` (design 7.4), and HLSL refuses a
// global and a function of one name.
RWByteAddressBuffer er_mask_plane : register(u0, space42);

#define ER_PROBE_BYTES 0x2000

// One header per region, at a stride of 0x20 bytes: frame, variant id, the target's width and
// height, then the largest `SV_Position` x and y seen, taken with `InterlockedMax`.
#define ER_PROBE_HEADER_STRIDE 0x20
#define ER_PROBE_AO_HEADER 0x0000
#define ER_PROBE_ALBEDO_HEADER 0x0020
#define ER_PROBE_TONEMAP_HEADER 0x0040
#define ER_PROBE_HEADER_FRAME 0
#define ER_PROBE_HEADER_VARIANT 4
#define ER_PROBE_HEADER_WIDTH 8
#define ER_PROBE_HEADER_HEIGHT 12
#define ER_PROBE_HEADER_MAX_X 16
#define ER_PROBE_HEADER_MAX_Y 20

// Counters: sampled coverage (P3), and marked and changed pixels per selector bit.
#define ER_PROBE_COUNTERS 0x0060

// 64 samples per region on an 8x8 grid, 32 bytes each: float3 pre, uint frame, float3 post, and
// one more word -- the depth bits at AO and albedo, the mask word at tonemap. "pre" is the colour
// after the game's maths and cel, "post" after the layers. A sample counts only when its frame
// equals its region header's.
#define ER_PROBE_AO_SAMPLES 0x0100
#define ER_PROBE_ALBEDO_SAMPLES 0x0900
#define ER_PROBE_TONEMAP_SAMPLES 0x1100
#define ER_PROBE_GRID 8
#define ER_PROBE_SAMPLES 64
#define ER_PROBE_SAMPLE_BYTES 32
#define ER_PROBE_SAMPLE_PRE 0
#define ER_PROBE_SAMPLE_FRAME 12
#define ER_PROBE_SAMPLE_POST 16
#define ER_PROBE_SAMPLE_AUX 28

// The pick record (P4).
#define ER_PROBE_PICK 0x1900

#endif // EDITOR_ABI_HLSLI
