#!/usr/bin/env python3
"""Measure where the build planner's Cosmetics image editor puts an image, from the DOM.

The editor (`CharacterSlidersImageEditor` in the planner bundle) shows each `sliders.images[]`
entry in a `div.image-container` (33.3333vw x 50vh, `overflow: hidden`) as an `<img>` styled from
its `geometry`:

    transform: translate(-50%, -15%) scale(zoom); top: y%; right: x%;
    width: width ? width px : auto; height: height ? height px : auto

What those numbers do depends on CSS the source does not show in one place (whether anything
positions the `<img>`, what aligns it), so this reads the answer off a real layout instead: the
container's and the image's bounding boxes, the image's computed position, and how far a probe
change of each field moves it. No screenshot is taken -- every output is a number from
`getBoundingClientRect` or `getComputedStyle`.

    uv run --with playwright python3 scripts/planner-image-layout.py 51cd05be9f4d0f
    uv run --with playwright python3 scripts/planner-image-layout.py 51cd05be9f4d0f --viewport 2560x1440
"""

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

SITE = "https://er-build-planner.nyasu.business"
CHROMIUM = "/usr/sbin/chromium"

MEASURE = """
(probe) => {
  const box = document.querySelector('div.image-container');
  const img = box && box.querySelector('img');
  if (!img) return null;
  const rect = (el) => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; };
  const cs = getComputedStyle(img);
  const out = {
    box: rect(box), img: rect(img), natural: [img.naturalWidth, img.naturalHeight],
    position: cs.position, inline_style: img.getAttribute('style'),
    parent_text_align: getComputedStyle(box).textAlign, box_display: getComputedStyle(box).display,
  };
  // Move one field at a time and record how far the image's box moves, then restore it.
  const base = rect(img);
  const saved = img.getAttribute('style');
  const effects = {};
  for (const [name, css] of Object.entries(probe)) {
    img.setAttribute('style', saved + ';' + css);
    const r = rect(img);
    effects[name] = {dx: r.x - base.x, dy: r.y - base.y, dw: r.w - base.w, dh: r.h - base.h};
    img.setAttribute('style', saved);
  }
  out.effects = effects;
  return out;
}
"""

PROBE = {
    "y+10%": "top: 10%",
    "x+10%": "right: 10%",
    "height=400px": "height: 400px",
    "width=300px": "width: 300px",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("build_id")
    parser.add_argument("--viewport", default="1920x1080")
    parser.add_argument("--images", type=int, default=4, help="how many images to step through")
    args = parser.parse_args()
    width, height = (int(v) for v in args.viewport.split("x"))
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM, headless=True)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(f"{SITE}/cosmetics?b={args.build_id}", wait_until="networkidle", timeout=25000)
        page.wait_for_selector("div.image-container img", timeout=15000)
        results = []
        for index in range(args.images):
            page.wait_for_function("() => { const i = document.querySelector('div.image-container img'); return i && i.complete && i.naturalWidth > 0; }", timeout=15000)
            results.append({"index": index, **page.evaluate(MEASURE, PROBE)})
            nxt = page.locator("button:has(svg[data-icon='chevron-right'])").first
            if index + 1 < args.images and nxt.count() and nxt.is_enabled():
                nxt.click()
                page.wait_for_timeout(300)
        browser.close()
    print(json.dumps({"viewport": [width, height], "images": results}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
