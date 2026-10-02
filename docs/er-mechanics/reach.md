# Elden Ring reach, hitbox shape, tracking and animation play speed

Labels as in the other docs here. **VERIFIED** = a regulation value (installed 1.17.1
`regulation.bin` through `scripts/er-param-read.py`) or code read out of the executable: 1.16.2
VAs from the named Ghidra dump on :8765 (identical to `eldenring-deobf.bin`), each re-found by
byte pattern in `eldenring-deobf-1.17.1.bin` by the selftest. **TAE** = decoded from the player
TimeAct `c0000.anibnd -> tae/a<cat>.tae`. **MEASURED** = read out of an unpacked model (FLVER) or
animation clip (HKX). **INFERRED** = fits the data, consumer not traced. **COMMUNITY** = Smithbox
annotations/enums, the WitchyBND TimeAct template, SoulsFormats layouts. Nothing was launched.

Tools:

- `python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-reach.py <weapon> [--grip both] [--json]`
  prints every attack slot. `--table` prints the weapons below (over a minute; run it in the
  background). `--selftest` passes 49/49 with one skip (`c0000.flver` has no mesh to bound the
  hurtbox against). `--hurtbox` prints the defender hurtbox extents (section 3).
- `python3 /home/banon/projects/er-mods-rs/scripts/er-hkx-pose.py <cat> <anim> --bone R_Weapon --times 0,0.2`
  is the offline Havok clip decoder the world placement uses. `--selftest` passes 34/34.
- For the PvP ranking: `reach_profile(weapon, grip='both')` returns the per-slot rows, and
  `reach_summary(weapon, grip)` returns `{slot: {weapon_reach_m, world_reach_m,
  root_motion_to_hit_m, first_hit_frame_clip, first_hit_frame_real, target_centre_m,
  contact_centre_m, turn_budget_deg_locked, turn_budget_deg_unlocked, turn_live_deg_*,
  turn_late_deg_*, turn_lockin_frames_*, default_turn_frames_unlocked, sweep_area_m2, sweep_mean_width_m, sweep_arc_deg,
  sweep_row_arc_deg, swing_shape, hit_height_min_m, hit_height_max_m, coverage_factor,
  coverage_arc_eff_deg, coverage_turn_deg}}` (section 4b);
  `defender_hurtbox('idle' | 'bind')` returns the per-direction hurtbox radii. Load the module with `importlib`, the same way
  `er-mechanics-attacks.py` is loaded.

Inputs: `~/er-extract/LOOK_HERE_WITCHY_RECURSIVE_20260713/sharded/{parts,chr}`. That tree was
extracted on 2026-07-13, from the 1.16 game files. The 1.17 extraction (`~/er-extract/1170-20260830`)
holds only `msg`. So the models, TimeActs and clips are 1.16 data, while the regulation is 1.17.1.

## 1. Hit shapes

| field | meaning | label |
| --- | --- | --- |
| `AtkParam_Pc.hit<i>_DmyPoly1`, `_DmyPoly2`, `_Radius`, i = 0..15 | if DmyPoly2 is -1, a sphere at DmyPoly1; otherwise a capsule from DmyPoly1 to DmyPoly2 | VERIFIED values, COMMUNITY meaning (Smithbox annotation: "-1 makes it a sphere") |
| `hit<i>_hitType` | the **part** of the weapon (0 tip, 1 middle, 2 root, 3 map collision). It is not sphere vs capsule | COMMUNITY (Smithbox enum `ATK_PARAM_HIT_TYPE`) |
| `hti<i>_Priority` | priority between shapes of one attack | COMMUNITY, not used here |
| `hitSourceType` | 0 = weapon dummies, 1 = body dummies. 10,754 rows are weapon and 263 are body | VERIFIED count |
| dummy ids of 10000 and above | 10xxx means the right weapon and 11xxx the left weapon, with id % 1000 as the dummy; 21xxx occurs 101 times and is unresolved | INFERRED from the value ranges |

The task premise was "hitType = capsule vs sphere". That is wrong: the shape comes from
DmyPoly2 == -1, and hitType is the part.

Every weapon row seen uses the same pattern. Hit 0 is the damaging **tip** shape, a capsule from
dummy 100 (tip) to 120 (grip end) or 110 (mid), with radius 0.25-0.5. Hit 1 is a thin **root**
shape near the hilt (110-120 or a sphere at 120), with radius 0.05-0.2. Colossal weapons add a
middle shape (Giant-Crusher: 110-120 r0.2, part 0).

A TAE AttackBehavior event can be gated. Args+0xe is a SpEffect state-info id. When it is
nonzero, the handler skips the hitbox unless the attacker has that state (VERIFIED:
0x1404266d0 does `movzx edx, word [rbx+0xe]` and calls 0x1404f95a0 on the SpEffect module). The
Lance's extra hitboxes, judges 5000-5005 (a 2.0 m capsule to dummy 143, MV 200-410), carry state
187, which only SpEffect 1908 has. They are left out of reach, and `er-mechanics-attacks.py`
currently lists them as ordinary extra hits (`+j5000:200`). Zero-damage shapes are also left
out: the Greatsword's judge-1 sphere at dummy 130 has r1.15, MV 0 and poise 0.

## 2. Weapon dummies (FLVER)

`EquipParamWeapon.equipModelId` -> `parts/wp_a_<id:04d>.partsbnd.dcx -> WP_A_<id>.flver`
(MEASURED). FLVER2 layout: a 0x80 header, dummy count at +0x14, then 0x40-byte dummies (position
f32x3 at 0, ReferenceID s16 at +0x1c, parent bone s16 at +0x1e), then 0x20-byte materials and
0x80-byte bones (SoulsFormats layout, COMMUNITY). The selftest checks the dummies against the
file's own header bounding box: every dummy lies inside it, and dummy 100 is within 0.15 m of
the blade end.

- The origin is the grip. The blade runs along -Y. Every weapon bone read is identity, so
  dummy positions are model-space (MEASURED, checked per file).
- Ids 100 (tip), 110 (mid), 120 (grip end), 122 (pommel), 130, and 300+/400+ (trails and
  effects) are on every model read here. Greatsword (612): 100 at y -1.749. Giant-Crusher (870):
  100 at (-0.403, -1.153), so the tip shape sits on one face of the head.

**Weapon reach** = max over the shape's dummy points of |point| + radius, measured from the
grip. Because a capsule's farthest point from the origin is at one of its ends, taking the
maximum over the endpoints is exact.

## 3. Placing the weapon in the world

`scripts/er-hkx-pose.py` decodes the player skeleton and clips offline (Havok 2018 tagfiles with
a compendium type table, `hkaSplineCompressedAnimation`, THREECOMP40/48 rotations, 16-bit
vectors, `hkaDefaultAnimatedReferenceFrame` root motion). It checks itself against 34
references that are independent of the decoder: idle frame 0 against the bind pose within
0.09 mm, feet on the floor through four grounded clips, unit quaternions, constant bone lengths,
and a two-block seam.

- Model space: metres, +Y up, the character faces **-Z** at animation start (MEASURED three ways
  in the pose selftest; the clip's own `extractedMotion.forward` says +Z and is wrong).
- The right weapon bone is `R_Weapon` (index 119), a child of `R_Hand` (MEASURED from the
  hierarchy and the animation; the engine code that attaches the model was not traced).
- **Weapon frame on the bone** (INFERRED): weapon (x, y, z) maps to bone (-x, -y, z), with the
  grip at the bone origin. Two measurements back this. First, the Lance R1 thrust (a037_030000,
  frames 18-21) moves `R_Weapon` toward -Z while the bone's +Y points along -Z, so the blade
  (-Y) maps to bone +Y. Second, of the two half-turns that do that, only this one puts
  Giant-Crusher dummy 100 on the leading face of the head. The cosine between that face offset
  and the head's velocity is +0.98, +0.91 and +0.48 in a031_030000, 030010 and 032000; the other
  half-turn gives the negatives.
- **World reach** = the largest `-z + radius` over every damaging, ungated weapon shape, sampled
  every 1/60 s of its hit window, with root motion included. **Standing reach** is the same
  measured from where the root is at that instant. **Lunge** = forward root motion at the first
  hit frame.

### Defender hurtbox

Damage lands on the defender's ragdoll bodies, keyframed every frame onto its animated skeleton
(VERIFIED, bd `er-damage-hurtboxes-are-per-chr-hknp-ragdoll-not-param-2026-09-01`). The movement
capsule (radius 0.4, half-height 1.5, hard-coded in `InitForPlayer`) is a different system and is
not used here.

- `c0000.chrbnd -> c0000.HKX` (MEASURED, decoded by `er-hkx-pose.py load_ragdoll`): one
  `hknpRagdollData`, **18 bodies, all `hknpCapsuleShape`** (segment `a..b` in the body frame,
  radius `convexRadius` 0.045-0.117 m, segment 0.03-0.44 m): pelvis, three spine, neck, head, and
  per side thigh, calf, foot, upper arm, forearm, hand. No sphere or other convex shape.
- Each body follows one animation bone through the file's `hkaSkeletonMapper` (18 simple
  mappings, no chains, every `aFromBTransform` identity within 3e-4 m; `boneToBodyMap` is the
  identity). Body `Ragdoll_X001` follows bone `X`. Check: every body's authored rest
  `position`/`orientation` equals its bone's bind-pose model transform (0.00 mm, 0.17 deg worst).
- `c0000.hkxpwv` (MEASURED layout): 0x20-byte header, u16 at +0xa = **18 bodies**, u16 at +8 = 150
  bones; the counts add up to the file length exactly. Body count matches the ragdoll, so the
  game keeps the map. The part byte (+4 of each 16-byte body record) is 1 for bodies 0-8, 31 for
  the head and 0 for the rest; what those numbers mean was not traced.

Horizontal distance from the character origin to the farthest hurtbox surface (MEASURED,
`er-mechanics-reach.py --hurtbox`; the defender faces -Z, right hand on -X):

| pose | front | back | right | left | max any direction | vertical |
| --- | --- | --- | --- | --- | --- | --- |
| idle a000_000000 frame 0 | 0.316 (left foot) | 0.248 (right calf) | 0.422 (right foot) | 0.392 (left forearm) | 0.432 | -0.041 .. 1.696 |
| bind pose | 0.186 (right foot) | 0.147 (right calf) | 0.644 (right hand) | 0.640 (left hand) | 0.644 | -0.042 .. 1.736 |

The idle front figure comes from the forward foot. At chest height the body is much thinner, so
two numbers go on each reach row, both assuming the defender stands in idle **facing the
attacker**:

- **tgt** = world reach + idle front radius (0.316): the farthest the defender's centre can be if
  the reach point met the most forward body point, whatever its height. An upper bound.
- **cont** = the largest centre distance at which any sampled hit point (with its radius) still
  touches one of the 18 capsules, keeping the hit point's height and lateral offset (capsules
  sampled at 33 points each; hand-solved check in the selftest).

Giant-Crusher 2H R1 #1: world 4.222, tgt **4.538**, cont **4.353** (frame 24, hit point at height
1.40 m, where the body's front surface is 0.13 m ahead of its centre).

## 4. Tracking (turn speed)

| TAE event | handler (1.16.2 / 1.17.1) | effect | label |
| --- | --- | --- | --- |
| 224 `SetTurnSpeed` | 0x14042c480 / 0x14042c9d0 | Args: f32 deg/s; byte +4 `IsLockOnCheck` (1 = returns early unless `ChrIns::IsLockedOn`); byte +5 priority, read only when the event's +0x1a word is >= 0x19, else -1. Calls `CSChrActionFlagModule::SetTurnSpeed` 0x140406a60 / 0x140406f90, which stores the speed if the new priority is <= the current one (signed) | VERIFIED |
| 0 JumpTable 7 | case 0x140427853 / 0x140427da3 | `actionModifiersFlags` (+0x40) \|= 0x8000. The template names it "Disable Turning" | VERIFIED write, COMMUNITY name |
| (reader of JumpTable 7) | ChrCtrl update FUN_1403cbff0, load at 0x1403cc10f / FUN_1403cc000, 0x1403cc11f | `mov r15d, [rdx+0x40]; shr r15, 0xf; not r15b; and r15b, 1` keeps "may rotate" = not bit 15. The same byte is cleared by SpEffect state 435, `disableMove`, a ladder and a throw. When it ends up clear, the function stores a zero vector into `ChrCtrl.rotationUpdate` | VERIFIED (decompile and bytes, both builds) |
| HKS act `SetTurnSpeed` | `HksAct` 0x14040cbd0, case SetTurnSpeed | stores the script's number at behavior data +0x250, the `behaviorData->turnSpeed` that `CalculateTurnSpeed` falls back to | VERIFIED write |
| 703, 704, 705, 706 | 0x1404293e0, 0x140428f60, 0x14042b820, 0x140429c90 | 703 sets byte +0x1e3 of the module at +0xc0. 704 sets flag 0x10000 plus three floats at +0x94..+0x9c. 705 sets flag 0x1000000 and +0xa8. 706 is the lock-on turn speed | VERIFIED writes. Only one 703, and no 704-706, occurs in player attack clips (0x30000-0x3ffff) |

`ChrCtrlJointModifier::CalculateTurnSpeed` (0x1403c4090 / 0x1403c40a0) takes the first value
that is >= 0 from this list: the joint turn speed (704 path), the TAE 224 turn speed, the
behavior-data turn speed (module +0xc0, field +0x250), and the modifier's own `turnVelocity`. It
multiplies the result by 0.017453292 (deg -> rad, 0x14329e62c) (VERIFIED). So 224 values are
degrees per second.

Across all player attack clips there are 8,746 SetTurnSpeed events, 2,307 of them lock-on-only.
The most common values are 720 (4,296), 60, 450, 180, 600 and 360 deg/s (TAE). A typical attack
turns at 720 deg/s for a few frames while locked on, then blocks turning with JumpTable 7
through the swing (Greatsword R1: JT7 f0-13 and f21-90; 224 at 720 lock-on only for f11-14,
360 for f14-15, 720 for f15-16).

**Turn budget** = the integral of the active 224 speed over real time from animation start to
the first damaging hit, computed both locked on and not locked on. Frames under JumpTable 7 add
nothing, even where a 224 event overlaps them: the ChrCtrl update zeroes the rotation request
there (VERIFIED write; that the joint modifier then has nothing to turn toward is INFERRED, its
read of `rotationUpdate` was not traced). Before 2026-09-29 the overlap frames were counted,
so the budget counted 224 speed under JumpTable 7. When several events are active at once, the
last one run is taken, since priority -1 always overwrites (INFERRED from SetTurnSpeed).
`default_turn_frames_*` counts the real frames before the hit that have neither a usable 224
event nor JumpTable 7. Those frames turn at the default rate, 720 deg/s:

| step | evidence | label |
| --- | --- | --- |
| no attack state or attack helper in `c0000.hks` reaches the `SetTurnSpeed` act (id 2004); only move, roll and step states do, and the move values (240/180/90) only for AI-controlled players | `python3 /home/banon/projects/er-mods-rs/scripts/er-hks-disasm.py --reaches SetTurnSpeed` (a HavokScript bytecode reader; `--selftest` 21/21) | VERIFIED from the bytecode (1.16 extraction) |
| behavior data +0x250 is reset to -1.0 every frame by FUN_1404146b0, called from `ChrIns::PreBehaviorSafe`; only it and the module constructor store -1.0 there | 1.16.2 decompile and byte scan | VERIFIED store; running before the HKS update each frame is INFERRED from the name |
| so `CalculateTurnSpeed` falls through to the modifier's `turnVelocity`, which the `ChrCtrlJointModifier` constructor (0x1403c3c50 / 0x1403c3c60) sets to 720.0 (`mov dword [rcx+8], 0x44340000` at 0x1403c3c6a / 0x1403c3c7a, the only such store in either image) | selftest `TURN_VELOCITY_STORE` | VERIFIED store; that the player's modifier keeps it is INFERRED (the only `SetTurnVelocity` caller found is the enemy path) |

With 720 filling the free frames, the unlocked budget equals the locked one for every R1 and R2
tabled here, since the lock-on-only 224 events are themselves mostly 720.

**Late turn** (`turn_late_deg_*`): the same integral over only the last 10 real frames before the
hit (`TURN_LATE_FRAMES`, INFERRED as the window in which a dodge reacts to a swing already under
way). **Lock-in** (`turn_lockin_frames_*`): real frames from the last instant the attacker can
still turn to the first hit. Almost every slot tabled can turn until 0-3 frames before the hit;
the outlier is the Giant-Crusher `a197` R2, whose late turn is 43 degrees (58 two-handed) against
150-220 for the rest: JumpTable 7 covers clip frames 21-29 of its last 12, and 29-33 turn at 360.

**Turn while live** (`turn_live_deg_*`): the same integral from the first hit frame to the end
of that hit window. It is 0 for 379 of the 384 slots of the section 6 weapons (both grips, every
slot the script prints): JumpTable 7 covers their active frames, so they do not track while the
hitbox is live (TAE). The five exceptions: Erdsteel Dagger charged R2 12 deg, Lance running R2
(1H and 2H) 5 deg, Cleanrot Knight's Sword jump R1 (1H and 2H) 40 deg locked on only.

## 4b. Spatial coverage: swing shape, width, footprint

Inputs are the same as world reach: every damaging, ungated weapon shape, posed every 1/60 s of
its hit window with root motion (MEASURED pose and dummies). The metrics are modelling choices
(INFERRED); the game's own hit test cadence and swept-capsule rule were not traced (open item 6).

- **Spheres.** A capsule is sampled as spheres of its AtkParam radius every 0.1 m along its axis
  between the two dummies (`CAPSULE_POINT_SPACING_M`); a sphere shape is one sphere.
- **Defender profile.** For each 5 cm height slice, the widest horizontal distance from the idle
  defender's axis to its hurtbox surface, over all 18 capsules and all directions
  (`defender_profile`). Using every direction makes it independent of which way the defender
  faces, so it is an upper bound for a defender facing the attacker.
- **Contact disc.** A sphere at height y with radius r touches a defender whose axis is within
  `max over slices |dy| <= r of profile(slice) + sqrt(r^2 - dy^2)` horizontally
  (`contact_disc_radius`). This is where the AtkParam radius enters: Giant-Crusher's 0.5 against
  the Greatsword's 0.4 widens every disc by 0.1 m.
- **Footprint** = the union of the discs in the ground plane of the attacker's start position
  (-Z forward): every place a defender can stand at the press and be hit, if neither moves
  sideways and the attacker does not turn (`footprint`, rasterised in 5 cm rows, exact along x).
  `sweep_area_m2` is its area, `sweep_mean_width_m` its area over its depth.
- **Arc.** Azimuth is measured about the start position, 0 straight ahead, positive to the
  attacker's right, only for footprint beyond 1 m (`SWEEP_MIN_RANGE_M`, so the hilt passing the
  body does not count). `sweep_arc_deg` is the union of covered azimuths; it is dominated by the
  nearest rows, where a small disc subtends a large angle. `sweep_row_arc_deg` is the azimuth
  covered at one forward distance, averaged over the rows the footprint occupies: the angular
  tolerance at a typical range, and what the coverage factor uses.
- **Swing shape.** The far end of each shape (the dummy farthest from the grip) is followed in
  the attacker's body frame at each instant (root position and yaw removed). Its path splits into
  horizontal arc length, vertical travel and radial travel; the largest names it `sweep`, `slam`
  or `thrust` (`swing_shape`). `hit_height_min_m` / `max` are the lowest and highest sphere
  surface more than 0.8 m from the body. Negative minima are the head going under the floor
  plane in the clip; the floor is not modelled.
- Selftests: one disc against pi r^2 and its arc against asin(r / d); a 90 degree level arc of
  radius 2 m is a sweep of length pi; the contact-disc sum; the a026_030000 turn budget with the
  Disable Turning frames removed, solved by hand.

Measured (the R1 and R2 #1 rows; `h / v / r` = far end's horizontal / vertical / radial path in
m, `arcR` = `sweep_row_arc_deg`, `arcU` = `sweep_arc_deg`, `low` = lowest hit surface, `late
turn` = `turn_late_deg_locked` (equal to unlocked for every row here), `cov` = the coverage
factor below):

| weapon | slot | anim | swing | h / v / r | arcR | arcU | width | area | low | late turn | cov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Greatsword | R1 | a026_030000 | sweep | 9.7 / 2.8 / 2.0 | 55 | 160 | 2.96 | 14.2 | -0.24 | 159 | 1.03 |
| Greatsword | R2 | a136_030505 | sweep | 11.7 / 2.0 / 2.7 | 69 | 173 | 4.10 | 20.7 | -0.31 | 192 | 1.07 |
| Greatsword | 2H R1 | a026_032000 | sweep | 10.5 / 3.4 / 2.3 | 48 | 199 | 2.72 | 15.1 | -0.18 | 162 | 1.01 |
| Greatsword | 2H R2 | a136_032505 | sweep | 11.9 / 2.1 / 2.5 | 68 | 178 | 4.06 | 20.7 | -0.31 | 192 | 1.07 |
| Giant-Crusher | R1 | a031_030000 | slam | 2.6 / 5.0 / 4.1 | 31 | 51 | 1.53 | 6.1 | -0.66 | 150 | 0.96 |
| Giant-Crusher | R2 | a197_030505 | slam | 2.6 / 6.1 / 4.5 | 13 | 18 | 1.26 | 5.2 | -0.85 | 43 | 0.78 |
| Giant-Crusher | 2H R1 | a031_032000 | sweep | 8.4 / 4.7 / 3.2 | 47 | 111 | 2.41 | 10.0 | -0.38 | 156 | 1.01 |
| Giant-Crusher | 2H R2 | a197_032505 | slam | 2.2 / 5.7 / 4.2 | 13 | 18 | 1.31 | 5.2 | -0.86 | 58 | 0.89 |
| Erdsteel Dagger | R1 | a020_030000 | sweep | 4.1 / 0.4 / 0.8 | 45 | 124 | 2.01 | 6.4 | 0.67 | 192 | 1.00 |
| Erdsteel Dagger | 2H R1 | a020_032000 | sweep | 2.9 / 1.6 / 0.8 | 41 | 84 | 1.62 | 4.4 | 0.34 | 192 | 0.99 |
| Uchigatana | R1 | a029_030000 | sweep | 5.8 / 3.7 / 2.1 | 45 | 100 | 1.86 | 6.9 | -0.18 | 192 | 1.00 |
| Uchigatana | R2 | a029_030505 | thrust | 1.8 / 0.3 / 2.4 | 32 | 60 | 1.41 | 5.6 | 0.59 | 168 | 0.96 |
| Uchigatana | 2H R1 | a029_032000 | sweep | 5.0 / 4.0 / 2.7 | 36 | 53 | 1.44 | 4.4 | -0.13 | 192 | 0.97 |
| Lance | R1 | a037_030000 | thrust | 0.4 / 0.5 / 1.0 | 30 | 62 | 1.30 | 5.4 | 0.61 | 192 | 0.95 |
| Lance | 2H R1 | a037_032000 | thrust | 0.5 / 0.5 / 1.4 | 30 | 79 | 1.29 | 5.7 | 0.67 | 192 | 0.95 |
| Cleanrot Knight's Sword | R1 | a027_030000 | thrust | 0.7 / 1.3 / 1.3 | 32 | 85 | 1.35 | 5.2 | 0.56 | 192 | 0.96 |
| Shamshir | R1 | a156_030000 | sweep | 6.0 / 1.7 / 2.1 | 58 | 110 | 2.59 | 9.3 | 0.35 | 192 | 1.04 |
| Devonia's Hammer | R1 | a031_030000 | slam | 3.0 / 5.4 / 4.9 | 30 | 51 | 1.52 | 6.2 | -0.78 | 150 | 0.95 |
| Devonia's Hammer | 2H R2 | a031_032505 | sweep | 9.1 / 4.4 / 2.9 | 40 | 82 | 2.39 | 9.3 | -0.28 | 168 | 0.99 |
| Fire Knight's Greatsword | 2H R1 | a137_032000 | sweep | 10.6 / 3.9 / 1.3 | 47 | 228 | 2.45 | 13.1 | -0.33 | 180 | 1.01 |
| Claymore | R1 | a025_030000 | sweep | 8.6 / 1.3 / 1.0 | 58 | 152 | 2.86 | 11.1 | 0.26 | 179 | 1.04 |
| Claymore | 2H R1 | a025_032000 | sweep | 4.3 / 4.3 / 2.9 | 38 | 70 | 1.67 | 5.5 | -0.39 | 202 | 0.98 |
| Ruins Greatsword | 2H R2 | a138_032505 | slam | 0.6 / 3.4 / 1.6 | 50 | 69 | 3.13 | 13.1 | -1.57 | 197 | 1.01 |
| Zweihander | 2H R1 | a026_032000 | sweep | 10.3 / 3.3 / 2.3 | 48 | 199 | 2.70 | 14.8 | -0.17 | 162 | 1.01 |

Across all 384 slots of these weapons: 195 sweeps, 125 slams, 64 thrusts. What the table shows:

- The Giant-Crusher / Greatsword gap is spatial as well as temporal. One-handed, the Greatsword
  R1 sweeps 55 degrees per range row over a 14.2 m2 footprint; the Giant-Crusher R1 slams, 31
  degrees over 6.1 m2. The R2 gap is the largest in the table: the Greatsword R2 sweeps 69
  degrees, the Giant-Crusher's unique `a197` R2 slams through 13 and also tracks worst late (43
  degrees in its last 10 frames), giving the lowest factor tabled (0.78). Two-handed R1 the two
  are level on arc (47 vs 48) and on late turn (156 vs 162).
- Colossal weapon slams (Giant-Crusher, Devonia's 1H) reach lowest (-0.66 to -0.86), the thrusts
  never go under 0.5 m. Whether a low floor matters against a crouching or rolling defender is
  not modelled: the defender is idle.
- With the 720 default, tracking separates few slots: every row here but the `a197` R2 has 150
  degrees or more of late turn, so the saturated turn term adds the same 120 degrees and the
  factor ranks the arc. Factors run 0.78 to 1.07; thrusts sit at 0.95, wide sweeps at 1.03-1.07.

### Coverage factor (INFERRED weights)

```
turn     = 0.5 * turn_late_deg_locked + 0.5 * turn_late_deg_unlocked
arc_eff  = min(360, sweep_row_arc_deg + 2 * min(turn, 60))
coverage = clamp(sqrt(arc_eff / 165), 0.7, 1.4)
```

The late turn counts only the last 10 real frames, because turning before the defender commits
to a dodge does not follow the dodge. It widens the arc on both sides because the attacker can
rotate either way toward the target. It saturates at 60 degrees because a dodge only displaces
the defender so far in azimuth (a 2.5 m roll at 3 m range is about 40 degrees). 165 is near the
median `arc_eff` of these weapons' R1 slots (166), so a typical R1 scores 1.0. The square root
keeps the factor in the same range as the reach factor in `er-builds-pvp.py`. None of this is in the game:
constants `COVER_*` in the script, `coverage_factor(row)` computes it, and every reach row
carries `coverage_factor`, `coverage_arc_eff_deg` and `coverage_turn_deg`.

## 5. Animation play speed

| step | address (1.16.2 / 1.17.1) | label |
| --- | --- | --- |
| TAE 608 `AnimSpeedGradient`: speed = SpeedAtStart + (SpeedAtEnd - SpeedAtStart) * min(progress, 1), tail-jumps into a setter that writes `CSChrBehaviorModule+0x15c4` | 0x140426420 -> 0x14041bda0 / 0x140426970 -> 0x14041c2e0 | VERIFIED |
| each behavior update copies +0x15c4 into +0x15c0 (`animSpeedGradientMultiplier`) and resets +0x15c4 to 1.0 | 0x14041d6ac / 0x14041dbec | VERIFIED |
| `CSChrBehaviorModule::Update`: graph dt = dt * behaviorDataFactor * debugAnimSpeed * +0x15c0 | 0x14041d760 | VERIFIED multiply. behaviorDataFactor (thunk 0x140416270 -> 0x144cb547a; 1.17.1 0x1404167a0) is a debug override: it returns `CSChrBehaviorDataModule+0x310` only when `GlobalDebugFlags` +0x2e (or +0x32 for the player) is set, else 1.0. Both flags are zero-filled BSS with only debug-setting writers, so it is 1.0 in retail (VERIFIED by the timing agent, `attacks.md` section 4, bd `behaviordatafactor-is-debug-gated-1-in-retail-2026-09-29`) |

So attacks do play at speeds other than 1.0, often. Of the 3,790 player attack clips (0x30000-0x35fff)
with a hit event, 622 carry a 608 event and 595 of those speed up the startup before the first
hit. Values run from 0.6 to 2.0; 11 events slow the clip. The most common are 1.2, 1.25, 1.3,
1.5 and 1.08 (TAE). Examples: Greatsword/Zweihander/Ruins Greatsword R1 (a026_030000) plays at
1.34 for clip frames 0-17, so the hit at clip frame 22 lands at real frame 17.7.
Giant-Crusher/Devonia's R1 (a031_030000) plays at 1.28 for frames 0-23, so the hit moves from
clip frame 23 to real frame 18.0. Colossal charged R2s run at 1.8 during the charge.

**Every frame count in `attacks.md` is clip time.** Real startup is shorter wherever a 608
window precedes the hit. This tool reports both (`first_hit_frame_clip`,
`first_hit_frame_real`), and so does the table below.

There is no play-speed column in `EquipParamWeapon` or `SpEffectParam` (VERIFIED: no such field
in either paramdef), and no weapon, talisman or buff play-speed field was found. The last
candidate, behaviorDataFactor, is ruled out: it is debug-gated and 1.0 in retail (table above).
So TAE 608 is the only play-speed input for these clips. `SpEffectParam.dexterityCancelSystemOnlyAddDexterity`
("Cast Speed +") changes when a cancel flag ends. It does not change play speed (COMMUNITY
annotation).

Event 603, which the template calls "[ExePatch]DebugAnimSpeed", appears 175 times in player
clips, mostly jump attacks, with small int args (4-28). Its handler (0x140428c90, 1.17.1
0x1404291e0) writes `CSChrBehaviorDataModule+0x310` = (end - start) * 30 / Args[0] when Args[0]
> 0, else 1.0. That field is read only through the debug-gated behaviorDataFactor, so 603 has no
retail effect (VERIFIED, same source as above).

## 6. Table

`reach` = weapon reach from the grip. `lunge` = root motion to the first hit. `stand` = reach
from the body at the reach instant. `world` = forward reach from the start position (m).
`hit c/r` = first damaging hit in clip / real 30 fps frames. `speed` = 608 windows in clip
frames. `turn L/U` = degrees of turn before the hit, locked on / not (TAE 224, else 720
deg/s; none under JumpTable 7). Shapes list the tip
shape (the R1 row); every weapon also has a thin root shape. The R2 rows are the release clips
(`...0505`/`...0500`), measured from their own start, not from the button press.

| weapon | slot | anim | tip shape | reach | lunge | stand | world | hit c/r | speed | turn L/U |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Greatsword | R1 | a026_030000 | c100-120 r0.4 | 2.149 | 1.24 | 2.64 | 4.06 | 22 / 17.7 | x1.34 f0-17 | 159 / 159 |
| Greatsword | R2 | a136_030505 | c100-120 r0.4 | 2.149 | 1.42 | 3.05 | 4.64 | 17 / 17 | - | 192 / 192 |
| Greatsword | running R1 | a026_030200 | c100-120 r0.4 | 2.149 | 3.30 | 2.91 | 6.43 | 23 / 18.9 | x1.22 f0-23 | 158 / 158 |
| Greatsword | 2H R1 | a026_032000 | c100-120 r0.4 | 2.149 | 1.01 | 2.67 | 4.06 | 21 / 16.7 | x1.34 f0-17 | 162 / 162 |
| Greatsword | 2H R2 | a136_032505 | c100-120 r0.4 | 2.149 | 1.42 | 3.01 | 4.60 | 17 / 17 | - | 192 / 192 |
| Giant-Crusher | R1 | a031_030000 | c100-130 r0.5 | 1.721 | 1.93 | 2.54 | 4.63 | 23 / 18.0 | x1.28 f0-23 | 150 / 150 |
| Giant-Crusher | R2 | a197_030505 | c100-130 r0.5 | 1.721 | 4.65 | 2.40 | 7.23 | 34 / 29.2 | x1.25 f7-31 | 138 / 138 |
| Giant-Crusher | running R1 | a031_030200 | c100-130 r0.5 | 1.721 | 4.30 | 2.47 | 7.03 | 23 / 20.8 | x1.15 f0-17 | 195 / 195 |
| Giant-Crusher | 2H R1 | a031_032000 | c100-130 r0.5 | 1.721 | 1.49 | 2.50 | 4.22 | 22 / 17.9 | x1.23 f0-22 | 156 / 156 |
| Giant-Crusher | 2H R2 | a197_032505 | c100-130 r0.5 | 1.721 | 4.67 | 2.39 | 7.21 | 32 / 27.2 | x1.25 f7-31 | 143 / 143 |
| Erdsteel Dagger | R1 | a020_030000 | c100-110 r0.4 | 0.857 | 0.91 | 1.65 | 2.88 | 10 / 10 | - | 192 / 192 |
| Erdsteel Dagger | R2 | a103_030505 | c100-110 r0.3 | 0.757 | 1.09 | 1.60 | 3.52 | 12 / 9.8 | x1.25 f0-10 | 158 / 158 |
| Erdsteel Dagger | running R1 | a020_030200 | c100-110 r0.4 | 0.857 | 2.70 | 1.67 | 4.59 | 15 / 12.9 | x1.12 f0-10, x1.25 f10-15 | 123 / 123 |
| Erdsteel Dagger | 2H R1 | a020_032000 | c100-110 r0.3 | 0.757 | 1.13 | 1.36 | 2.88 | 10 / 10 | - | 192 / 192 |
| Uchigatana | R1 | a029_030000 | c100-110 r0.35 | 1.459 | 1.25 | 2.03 | 3.48 | 13 / 13 | - | 192 / 192 |
| Uchigatana | R2 | a029_030505 | c110-100 r0.3 | 1.409 | 1.20 | 2.44 | 4.20 | 12 / 12 | - | 192 / 192 |
| Uchigatana | running R1 | a029_030200 | c100-110 r0.35 | 1.459 | 3.15 | 2.05 | 5.63 | 15 / 15 | - | 166 / 166 |
| Uchigatana | 2H R1 | a029_032000 | c100-110 r0.35 | 1.459 | 1.12 | 2.10 | 3.49 | 14 / 14 | - | 192 / 192 |
| Uchigatana | 2H R2 | a029_032505 | c100-110 r0.35 | 1.459 | 1.49 | 1.94 | 3.54 | 13 / 13 | - | 192 / 192 |
| Lance | R1 | a037_030000 | c120-100 r0.3 | 2.187 | 1.19 | 2.83 | 4.49 | 18 / 18 | - | 192 / 192 |
| Lance | R2 | a037_030505 | c120-100 r0.3 | 2.187 | 1.32 | 3.05 | 4.87 | 15 / 15 | - | 192 / 192 |
| Lance | running R1 | a037_030200 | c120-100 r0.3 | 2.187 | 2.67 | 2.67 | 6.54 | 17 / 17 | - | 174 / 174 |
| Lance | 2H R1 | a037_032000 | c120-100 r0.3 | 2.187 | 1.09 | 2.82 | 4.45 | 18 / 18 | - | 192 / 192 |
| Lance | 2H R2 | a037_032505 | c120-100 r0.3 | 2.187 | 1.25 | 3.09 | 4.85 | 15 / 15 | - | 192 / 192 |
| Cleanrot Knight's Sword | R1 | a027_030000 | c120-100 r0.35 | 1.494 | 0.56 | 2.33 | 3.60 | 15 / 15 | - | 192 / 192 |
| Cleanrot Knight's Sword | R2 | a027_030505 | c120-100 r0.3 | 1.444 | 0.71 | 2.28 | 3.51 | 10 / 10 | - | 192 / 192 |
| Cleanrot Knight's Sword | 2H R1 | a027_032000 | c120-100 r0.35 | 1.494 | 0.57 | 2.35 | 3.67 | 13 / 13 | - | 168 / 168 |
| Shamshir | R1 | a156_030000 | c100-110 r0.4 | 1.319 | 1.32 | 2.24 | 3.62 | 14 / 14 | - | 192 / 192 |
| Shamshir | R2 | a028_030505 | c100-110 r0.4 | 1.319 | 1.11 | 2.08 | 3.24 | 10 / 10 | - | 192 / 192 |
| Shamshir | 2H R1 | a156_032000 | c100-110 r0.4 | 1.319 | 1.32 | 2.21 | 3.58 | 14 / 13.0 | x1.08 f0-14 | 178 / 178 |
| Devonia's Hammer | R1 | a031_030000 | c100-130 r0.5 | 1.701 | 1.93 | 2.59 | 4.67 | 23 / 18.0 | x1.28 f0-23 | 150 / 150 |
| Devonia's Hammer | R2 | a031_030505 | c100-130 r0.5 | 1.701 | 1.68 | 2.55 | 4.53 | 16 / 16 | - | 192 / 192 |
| Devonia's Hammer | 2H R1 | a031_032000 | c100-130 r0.5 | 1.701 | 1.49 | 2.52 | 4.24 | 22 / 17.9 | x1.23 f0-22 | 156 / 156 |
| Fire Knight's Greatsword | R1 | a137_030000 (a263 imports it) | c100-120 r0.4 | 2.103 | 1.06 | 2.46 | 3.77 | 19 / 16.8 | x1.2 f0-13 | 180 / 180 |
| Fire Knight's Greatsword | R2 | a135_030505 | c120-100 r0.4 | 2.103 | 0.91 | 2.87 | 4.03 | 11 / 11 | - | 224 / 224 |
| Fire Knight's Greatsword | 2H R1 | a137_032000 | c100-120 r0.4 | 2.103 | 1.06 | 2.40 | 3.70 | 19 / 16.8 | x1.2 f0-13 | 180 / 180 |
| Claymore | R1 | a025_030000 | c100-110 r0.4 | 1.587 | 0.90 | 2.11 | 3.40 | 16 / 15.0 | x1.07 f0-16 | 179 / 179 |
| Claymore | R2 | a125_030505 | c100-110 r0.4 | 1.587 | 1.36 | 2.41 | 4.61 | 12 / 12 | - | 192 / 192 |
| Claymore | 2H R1 | a025_032000 | c100-110 r0.4 | 1.587 | 1.27 | 2.10 | 3.81 | 16 / 15.0 | x1.07 f0-16 | 202 / 202 |
| Ruins Greatsword | R1 | a026_030000 | c100-120 r0.4 | 1.789 | 1.24 | 2.28 | 3.70 | 22 / 17.7 | x1.34 f0-17 | 159 / 159 |
| Ruins Greatsword | R2 | a138_030505 | c100-120 r0.4 + sphere 100 r1.6 | 2.989 | 1.46 | 3.84 | 5.67 | 15 / 15 | - | 192 / 192 |
| Ruins Greatsword | 2H R2 | a138_032505 | c100-120 r0.4 + sphere 100 r1.6 | 2.989 | 1.30 | 3.77 | 5.22 | 16 / 16 | - | 197 / 197 |
| Zweihander | R1 | a026_030000 | c100-120 r0.4 | 2.103 | 1.24 | 2.59 | 4.01 | 22 / 17.7 | x1.34 f0-17 | 159 / 159 |
| Zweihander | R2 | a135_030505 | c120-100 r0.4 | 2.103 | 0.91 | 2.87 | 4.03 | 11 / 11 | - | 224 / 224 |
| Zweihander | 2H R1 | a026_032000 | c100-120 r0.4 | 2.103 | 1.01 | 2.63 | 4.01 | 21 / 16.7 | x1.34 f0-17 | 162 / 162 |

The script prints the full slot list (charged R2s, rolling, backstep, jump and guard-counter
attacks). Things the table shows:

- Weapon length is not reach. The Giant-Crusher's weapon reach is 0.43 m shorter than the
  Greatsword's, yet its R1 standing reach is 0.10 m shorter and its world reach 0.57 m longer,
  because it lunges 1.93 m where the Greatsword lunges 1.24 m.
- Before the 720 default was traced, Uchigatana 2H R1 showed 24 deg of turn and Cleanrot Knight's
  Sword R1 0 deg unlocked; both came from counting only TAE 224 events. Both are now 192.
- Ruins Greatsword R2 adds a sphere at the tip with r1.6 (a138 R2 clips). The community
  describes these heavies as releasing a shockwave (COMMUNITY). This sphere gives the weapon
  the largest standing reach in the list (3.84 m).

## 7. Clips borrowed from other entries

Two TAE mini-header forms matter (MEASURED from the headers; layouts from SoulsFormats
`TAE.Animation.MiniHeader`, COMMUNITY):

- Type 1, import other animation. The entry has no events and borrows the events of id
  `cat*1000000 + anim` (int at +0x18). Every a263 entry (Fire Knight's Greatsword) is one:
  a263_030000 -> a137_030000. `er-tae-event-scan.py` does not follow these, so
  `er-mechanics-attacks.py` prints no R1 row for that weapon. This tool follows them.
- Type 0 with byte +0x19 set plays another clip's HKX (id at +0x1c) under its own events. The
  crouch R1 a026_030310 plays a026_030300 (the rolling R1 clip) at speed 0.86.

## 7b. Skills (`skill_reach`)

`skill_reach(weapon, category, anim, judge)` measures one weapon-skill animation the way a slot is
measured. It is `attack_reach` with `clip=(category, anim)`: the TimeAct is the skill's own,
`a<600 + SwordArtsParam.swordArtsTypeNew>` (`er-mechanics-ashes.skill_tae`), not the weapon's
motion category. Hit shapes, weapon dummies, pose, footprint, turn budgets and `coverage_factor`
are all read unchanged, so a skill's world reach and coverage are the same quantities as a slot's.

- **Judges.** The judges are those of TAE 1 and, in skill clips only, TAE 307 with flag 8 (Args+4
  u32 flags, Args+8 judge). Flag 8 resolves the judge like event 1 (VERIFIED 0x14042a580, as
  `er-mechanics-ashes.anim_actions` reads it). Slot clips are read exactly as before.
- **Which animation.** The caller (`er-mechanics-ashes.skill_reach_factors`) passes the animation
  of the skill's first melee hit and that hit's judge. When that animation is a follow-up (Stamp's
  040010 after the stance, Ground Slam's landing 040004 after the leap), the clip is measured from
  its own first frame. The lead-in's movement is not added (INFERRED hand-over), so such a skill
  reads short.
- **Cost.** Results are cached per (weapon, category, anim). The first call pays the pose decoder
  start, about 2.5 s, and each later clip about 0.04 s.
- **Per window.** A skill row also carries `window_contacts`: for each hit window (start clip
  frame, judge), its pose samples in real seconds with each sphere's contact disc against the idle
  defender (`window_points`). `window_contact_time(entries, distance_at)` returns the first sample
  that touches a defender `distance_at(t)` metres ahead, held back as in `front_contact_times`.
  `er-mechanics-ashes.skill_landing` uses it to decide hit by hit (ashes-of-war.md section 15a).
- **Every slot has them too.** `attack_reach` now keeps `window_contacts` for attack slots as well.
  They stay out of `reach_summary` (a whole ranking's samples would be gigabytes);
  `slot_contacts(weapon, grip)` returns them for the most recent weapon only, and measures a
  weapon again (about 0.4 s) when a class median has loaded another one since. The reaction dodge
  of ashes-of-war.md section 16 reads them.

Claymore, measured 2026-09-29 (`skill_reach`):

| skill | anim | world reach | coverage | swing |
|---|---|---|---|---|
| Lion's Claw | a600 040000 | 6.13 m | 0.917 | slam |
| Stamp (Upward Cut), attack | a606 040010 | 3.39 m | 0.989 | slam |
| Spinning Slash | a603 040000 | 4.40 m | 1.054 | sweep |
| Carian Grandeur | a666 040000 | 7.02 m | 0.949 | slam |
| Wild Strikes, first loop | a610 040051 | 2.30 m | 1.012 | sweep |

On a Lance: Impaling Thrust (a601) 6.42 m (thrust, 0.922), Giant Hunt (a616) 5.50 m (slam,
0.928). On a Greatsword, Ground Slam's landing a715 040004: 2.61 m.

Projectile skills are not measured here. Their reach comes from the Bullet model in
`er-mechanics-ashes.bullet_reach`, which is INFERRED (ashes-of-war.md section 15).

## 8. Not established

1. Resolved 2026-09-29: behaviorDataFactor is a debug override, 1.0 in retail (section 5).
2. Resolved 2026-09-29: the ChrCtrl update reads `actionModifiersFlags & 0x8000` and zeroes the
   rotation request (section 4). Still open: the joint modifier's read of `rotationUpdate`.
3. Mostly resolved 2026-09-29: the default turn rate is the joint modifier's `turnVelocity`,
   720 deg/s (section 4). Still open: whether anything other than the enemy-only
   `SetTurnVelocity` path rewrites the player's modifier +0x8, and the "Pre" ordering of the
   +0x250 reset against the HKS update.
4. The weapon model's frame on `R_Weapon` is INFERRED from two measured swings (section 3), not
   from the attaching code. Left-hand (11xxx) shapes use `L_Weapon` with the same frame, which is
   unchecked. Body-source shapes (`hitSourceType` 1) are not placed, because the c0000 body
   dummies are not read.
5. Dummy prefix 21xxx (101 uses) is unresolved.
6. World reach and the footprint assume the attacker does not turn. Tracking rotates root
   motion and swing in the game; the coverage factor adds it as a rotation of the whole
   footprint, which is exact only for turning done before the first hit (the live-frame turn is
   0 for almost every slot, section 4). The hitbox is sampled at 60 Hz, and the game's own
   hit-test cadence and swept-capsule rule were not traced. Engine blending and IK are not
   modelled. The footprint's defender is idle and uses the all-direction profile (an upper bound).
7. R2 rows are the release clip only. The time spent in the `...0500` start clip before release
   is not added, and neither is its root motion.
8. Resolved 2026-09-29: TAE 603 writes the debug play-speed field and has no retail effect
   (section 5).
9. The models, TimeActs and clips are 1.16 extractions (2026-07-13). No 1.17 extraction of
   `parts` or `chr` exists on this machine, so any 1.17 animation change is invisible here. The
   executable facts were re-checked in 1.17.1.
10. 30 fps as the frame unit is the attacks-doc convention (INFERRED).
11. The defender is posed in idle only. The ragdoll follows whatever the defender plays, so a
    defender mid-attack, rolling, crouching or guarding has other extents; whether the game drops
    any bodies from hit tests during a roll's invincibility frames, and whether body scaling
    (`ChrCtrl` scaleSize) resizes the capsules, is not traced. Armour is assumed not to change the
    hurtbox (INFERRED: the ragdoll loads from `c0000.chrbnd`; armour parts files were not
    checked for physics data).
12. Skills (section 7b): which animation of a skill TimeAct the behavior script plays per grip
    and lock-on is not traced, and a follow-up animation is measured without its lead-in's
    movement. Which TAE 307 judges without flag 8 (the literal BehaviorParam rows) can hit is
    not read here; `er-mechanics-ashes` classes those rows as body hitboxes.

## 9. Exact commands

```
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-reach.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-reach.py Greatsword
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-reach.py Giant-Crusher --grip both --json
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-reach.py --table
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-reach.py --hurtbox
python3 /home/banon/projects/er-mods-rs/scripts/er-mechanics-ashes.py term --weapon Claymore --aff Heavy --two   # every skill's reach and coverage
python3 /home/banon/projects/er-mods-rs/scripts/er-hkx-pose.py --selftest
python3 /home/banon/projects/er-mods-rs/scripts/er-hkx-pose.py 37 30000 --bone R_Weapon --times 0.6,0.7 --rotation
uv run --with capstone python3 /home/banon/projects/er-mods-rs/scripts/er-tae-dispatch-decode.py
uv run --with capstone python3 /home/banon/projects/er-mods-rs/scripts/map-rvas-1162-to-1170.py 0x14042c480 0x140426420 0x1403c4090
python3 /home/banon/projects/er-mods-rs/scripts/ghidra/mcp_query.py getDecompiledCode --params '{"address":"14041d760"}'
```
