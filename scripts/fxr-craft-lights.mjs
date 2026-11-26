#!/usr/bin/env bun
// Write an Elden Ring FXR that holds point lights and nothing else, for the bullet-flood GPU
// experiment (scripts/frida/bullet-flood.js, bd bullet-flood-artifacting-needs-point-lights-2720-2026-10-27).
//
//     bun scripts/fxr-craft-lights.mjs --id 442022 --lights 8 --radius 10 --spread 2 --duration 2 --out <file.fxr>
//
// Each light is its own child node at a fixed offset on a ring of `--spread` metres, so one
// instance puts `--lights` lights in view at once with no billboard, distortion or tracer beside
// them. `--duration` is the node lifetime in seconds (-1 = forever). The id is written into the
// header; the Frida side repoints an already-loaded definition with that id at these bytes.
//
// Built on EvenTorset's @cccode/fxr (the FXR Playground's library); bun installs the pinned version
// on first run.
import { FXR, BasicNode, NodeAttributes, StaticNodeTransform, PointLight, Game } from '@cccode/fxr@32.1.3'

function arg (name, fallback) {
  const i = process.argv.indexOf('--' + name)
  return i < 0 ? fallback : process.argv[i + 1]
}

const id = Number(arg('id', 442022))
const lights = Number(arg('lights', 8))
const radius = Number(arg('radius', 10))
const spread = Number(arg('spread', 2))
const duration = Number(arg('duration', 2))
const intensity = Number(arg('intensity', 4))
const out = arg('out', null)
if (!out) throw new Error('--out <file.fxr> is required')

const fxr = new FXR(id)
fxr.root.nodes = [...Array(lights).keys()].map(function (i) {
  const t = (2 * Math.PI * i) / lights
  return new BasicNode([
    new NodeAttributes({ duration }),
    new StaticNodeTransform({ offset: [spread * Math.cos(t), 1, spread * Math.sin(t)] }),
    new PointLight({ radius, diffuseMultiplier: intensity, diffuseColor: [1, 0.6, 0.2, 1] }),
  ])
})

await fxr.saveAs(out, Game.EldenRing)
console.log(JSON.stringify({ out, id, lights, radius, spread, duration, intensity }))
