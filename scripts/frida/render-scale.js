// Forces Elden Ring's own dynamic resolution to a fixed scene scale while the UI stays native.
//
// The chain is in bd `er-dynamic-resolution-chain-static-2026-10-15` (issue er-effects-rs-6tu5).
// Addresses are 2.7.2.0, carried from 1.17.1 with scripts/map-rvas-1171-to-1172.py: the update
// and its caller (the view setup, which loads the config from GXRenderingSystem+0x13a70 and reads
// +0x13a78) match the 1.17.1 functions once rip-relative and branch operands are masked, and every
// masked target carries to the new operand, the two globals below included.
//
// The per-frame update at 0x141b2e9b0 -> 0x141b300a0 takes the controller config in rcx: it
// returns the current scale unchanged when the enable byte (+0x00) is 0, and otherwise clamps it
// to [+0x1c, +0x20] before the setter writes GxDrawContext+0xedc. So pinning min and max to one
// value, with the enable byte set and the load-balancer coupling (+0x01) cleared, holds the scene
// at that scale. The update runs every frame, which makes it the clock for re-asserting the
// writes after a CSGraphics init or a render-mode change puts LoadBalancerParam's row back.
//
// Edit DIVISOR and save: the watcher reloads this file in place, and dispose() restores the values
// it found before the next copy writes its own.
//
// The scene is the output size divided by DIVISOR, and only a divisor that gives every scene pixel
// an exact DIVISOR x DIVISOR block of screen pixels is accepted. The view setup rounds the
// viewport to a multiple of 4 px (measured: 1/24 of 2160 came out 92, not 90), so both scene
// sides must also be multiples of 4. That makes the valid divisors exactly the divisors of
// gcd(width, height) / 4; the armed message lists them for the screen the game is drawing to.
const DIVISOR = 3;
// The game biases mips by -log2(DIVISOR) while +0x13a78 is set, which shimmers at low sizes.
const CLEAR_LOD_BIAS = true;
const VIEWPORT_SNAP_PX = 4;
const REPORT_EVERY_FRAMES = 120;

const IMAGE_BASE = 0x140000000;
const GXRS_GLOBAL = 0x1447f67c8; // 0x1447f37a8 -> 0x1447f67c8
const GX_DRAW_CONTEXT_GLOBAL = 0x1447f6400; // 0x1447f33e0 -> 0x1447f6400
const DRS_UPDATE = 0x141b300a0; // 0x141b2e9b0 -> 0x141b300a0
// From eldenring-deobf-1.17.2.bin; ends in `cmp byte ptr [rcx], 0`, the enable-byte read. Only the
// security-cookie load's displacement differs from 1.17.1 (`53 04 13 02` -> `73 1d 13 02`).
const DRS_UPDATE_PROLOGUE = '40 53 48 83 ec 40 48 8b 05 73 1d 13 02 48 33 c4 48 89 44 24 38 80 39 00';

const GXRS_DRS_CONFIG = 0x13a70;
const GXRS_LOD_BIAS_GATE = 0x13a78;
const DRS_ENABLE = 0x00;
const DRS_COUPLE_LOAD_BALANCER = 0x01;
const DRS_MIN = 0x1c;
const DRS_MAX = 0x20;
const DRS_CURRENT = 0x30;
// Two u32 width/height pairs that both read 3840x2160 in a 4K run (scanned 2026-10-15): the first
// output's size, beside its per-output scale at +0x3ec, and the size beside the primary ratio at
// +0xf04. The divisor is checked against both and the agent refuses when they disagree, because
// a clean block mapping needs the scene target and the screen to be the same size.
const CTX_OUTPUT_SIZE = 0x3c8;
const CTX_PRIMARY_SIZE = 0xefc;
const CTX_VIEWPORT_RATIO = 0xed0;
const CTX_PERCENTAGE = 0xedc;
const CTX_PRIMARY = 0xeec;
const CTX_PRIMARY_RATIO = 0xf04;
const CTX_GPU_TIMING = 0xf30;
const GPU_MS = 0xd344;

const MODULE = Process.getModuleByName('eldenring.exe');
function at (va) {
  return MODULE.base.add(va - IMAGE_BASE);
}

function hex (pointer, length) {
  return Array.prototype.map.call(new Uint8Array(pointer.readByteArray(length)), function (b) {
    return ('0' + b.toString(16)).slice(-2);
  }).join(' ');
}

function pair (pointer) {
  return [pointer.readFloat(), pointer.add(4).readFloat()];
}

function readings (drs, gxrs, ctx) {
  const timing = ctx.add(CTX_GPU_TIMING).readPointer();
  return {
    drsEnable: drs.add(DRS_ENABLE).readU8(),
    drsCoupleLoadBalancer: drs.add(DRS_COUPLE_LOAD_BALANCER).readU8(),
    drsMin: drs.add(DRS_MIN).readFloat(),
    drsMax: drs.add(DRS_MAX).readFloat(),
    drsCurrent: drs.add(DRS_CURRENT).readFloat(),
    gxrsGates13a78: hex(gxrs.add(GXRS_LOD_BIAS_GATE), 6),
    viewportRatio: pair(ctx.add(CTX_VIEWPORT_RATIO)),
    percentage: pair(ctx.add(CTX_PERCENTAGE)),
    primary: pair(ctx.add(CTX_PRIMARY)),
    primaryRatio: pair(ctx.add(CTX_PRIMARY_RATIO)),
    gpuMs: timing.isNull() ? null : timing.add(GPU_MS).readFloat()
  };
}

const live = hex(at(DRS_UPDATE), 24);
if (live !== DRS_UPDATE_PROLOGUE) {
  send({ kind: 'refused', line: 'DRS update prologue differs from 2.7.2.0: ' + live });
  throw new Error('render-scale: not the 2.7.2.0 DRS update');
}

const gxrs = at(GXRS_GLOBAL).readPointer();
const ctx = at(GX_DRAW_CONTEXT_GLOBAL).readPointer();
const drs = gxrs.add(GXRS_DRS_CONFIG).readPointer();

function size (pointer) {
  return [pointer.readU32(), pointer.add(4).readU32()];
}

const output = size(ctx.add(CTX_OUTPUT_SIZE));
const primary = size(ctx.add(CTX_PRIMARY_SIZE));
if (output[0] !== primary[0] || output[1] !== primary[1]) {
  send({ kind: 'refused', line: 'output ' + output.join('x') + ' and scene target ' + primary.join('x') + ' differ, so no divisor maps cleanly' });
  throw new Error('render-scale: output and scene target sizes differ');
}

function clean (divisor) {
  if (divisor === 1) return true;
  return output[0] % divisor === 0 && output[1] % divisor === 0 &&
    (output[0] / divisor) % VIEWPORT_SNAP_PX === 0 && (output[1] / divisor) % VIEWPORT_SNAP_PX === 0;
}

const choices = [];
for (let divisor = 1; divisor <= Math.min(output[0], output[1]) / VIEWPORT_SNAP_PX; divisor += 1) {
  if (clean(divisor)) choices.push(divisor + ' (' + output[0] / divisor + 'x' + output[1] / divisor + ')');
}
if (!Number.isInteger(DIVISOR) || !clean(DIVISOR)) {
  send({ kind: 'refused', line: 'DIVISOR ' + DIVISOR + ' does not map onto ' + output.join('x') + ' cleanly; the clean ones are ' + choices.join(', ') });
  throw new Error('render-scale: DIVISOR is not a clean divisor of the output');
}
const SCALE = 1 / DIVISOR;
const scene = [output[0] / DIVISOR, output[1] / DIVISOR];

const original = {
  enable: drs.add(DRS_ENABLE).readU8(),
  couple: drs.add(DRS_COUPLE_LOAD_BALANCER).readU8(),
  min: drs.add(DRS_MIN).readFloat(),
  max: drs.add(DRS_MAX).readFloat(),
  lodBias: gxrs.add(GXRS_LOD_BIAS_GATE).readU8()
};
send({ kind: 'baseline', gxrs: gxrs.toString(), ctx: ctx.toString(), drs: drs.toString(), values: readings(drs, gxrs, ctx) });

let frames = 0;
let foreignConfig = 0;
const listener = Interceptor.attach(at(DRS_UPDATE), {
  onEnter: function (args) {
    // The update is only ever handed the controller config; count any other pointer instead of
    // writing through it.
    if (!args[0].equals(drs)) {
      foreignConfig += 1;
      return;
    }
    drs.add(DRS_ENABLE).writeU8(1);
    drs.add(DRS_COUPLE_LOAD_BALANCER).writeU8(0);
    drs.add(DRS_MIN).writeFloat(SCALE);
    drs.add(DRS_MAX).writeFloat(SCALE);
    if (CLEAR_LOD_BIAS) gxrs.add(GXRS_LOD_BIAS_GATE).writeU8(0);
    frames += 1;
    if (frames % REPORT_EVERY_FRAMES === 1) {
      // The size the view setup actually drew, back out of its own ratio, so a rounding the
      // divisor check did not foresee shows up as exact: false instead of passing silently.
      const values = readings(drs, gxrs, ctx);
      const drawn = [Math.round(values.viewportRatio[0] * output[0]), Math.round(values.viewportRatio[1] * output[1])];
      send({
        kind: 'frame',
        frames: frames,
        foreignConfig: foreignConfig,
        divisor: DIVISOR,
        drawn: drawn,
        exact: drawn[0] === scene[0] && drawn[1] === scene[1],
        values: values
      });
    }
  }
});

rpc.exports = {
  dispose: function () {
    listener.detach();
    drs.add(DRS_ENABLE).writeU8(original.enable);
    drs.add(DRS_COUPLE_LOAD_BALANCER).writeU8(original.couple);
    drs.add(DRS_MIN).writeFloat(original.min);
    drs.add(DRS_MAX).writeFloat(original.max);
    gxrs.add(GXRS_LOD_BIAS_GATE).writeU8(original.lodBias);
  }
};

send({
  kind: 'armed',
  line: 'pinning the scene to ' + scene.join('x') + ' (1/' + DIVISOR + ' of ' + output.join('x') + ') at the DRS update ' +
    at(DRS_UPDATE) + '; the clean divisors here are ' + choices.join(', ')
});
