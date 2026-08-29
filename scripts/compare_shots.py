#!/usr/bin/env python
"""Compare two runs of the smoke-test screenshot matrix.

The Qt6 port has to reproduce what the Qt5 build drew, and a screenshot is
the only artefact that catches a lane that moved, a panel that collapsed or
a colour ramp that inverted.  But the two bindings do not draw *identically*
even when they are drawing the same thing: Qt6 rasterises text through a
different path, so glyph edges land on different subpixels and a strict
comparison reports every label in the window as a difference.

So this reports two numbers per pair and leaves the judgement to a reader:

    differing   fraction of pixels that differ at all, at a tolerance that
                ignores antialiasing noise
    structural  fraction of pixels whose difference survives a 3x3 box blur

Antialiasing is high-frequency: it shows up in `differing` and washes out of
`structural`.  A lane that moved is low-frequency and shows up in both.  A
pair with structural under ~0.5% is the same layout drawn by a different
rasteriser; a pair above a few percent has really changed and the written
diff image says where.

    scripts/compare_shots.py .devshots/baseline .devshots/qt6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Tolerance per 8-bit channel below which two pixels count as equal.  Qt5 and
# Qt6 disagree by a few levels on antialiased edges and by one level on some
# gradient fills; 12 is comfortably above that and far below any real colour
# change.
TOLERANCE = 12

# A pixel is "structurally" different when its neighbourhood is, not merely
# when it is.  Isolated edge pixels average away; a shifted lane does not.
STRUCTURAL_THRESHOLD = 0.25


def load(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def box_blur(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    """Mean of `mask` over a (2*radius+1)^2 window, via a summed-area table."""
    padded = np.pad(mask.astype(np.float32), radius + 1, mode="edge")
    integral = padded.cumsum(0).cumsum(1)
    size = 2 * radius + 1
    h, w = mask.shape
    # corners of the window for every output pixel
    total = (
        integral[size : size + h, size : size + w]
        - integral[0:h, size : size + w]
        - integral[size : size + h, 0:w]
        + integral[0:h, 0:w]
    )
    return total / (size * size)


def compare(a_path: Path, b_path: Path, diff_path: Path | None) -> dict:
    a, b = load(a_path), load(b_path)
    if a.shape != b.shape:
        return {"error": f"shape {a.shape} vs {b.shape}"}

    delta = np.abs(a - b).max(axis=2)
    differing = delta > TOLERANCE
    structural = box_blur(differing) > STRUCTURAL_THRESHOLD

    if diff_path is not None and differing.any():
        from PIL import Image

        # red where structural, dim yellow where merely antialiasing
        out = (a // 3).astype(np.uint8)
        out[differing] = [90, 90, 0]
        out[structural] = [255, 0, 0]
        Image.fromarray(out).save(diff_path)

    return {
        "differing": float(differing.mean()),
        "structural": float(structural.mean()),
        "max_delta": int(delta.max()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before", help="prefix of the baseline shots")
    ap.add_argument("after", help="prefix of the shots to compare")
    ap.add_argument(
        "--structural-budget",
        type=float,
        default=0.5,
        help="percent of structurally-different pixels tolerated (default 0.5)",
    )
    args = ap.parse_args(argv)

    before, after = Path(args.before), Path(args.after)
    shots = sorted(before.parent.glob(f"{before.name}-*.png"))
    if not shots:
        print(f"no shots matching {before}-*.png", file=sys.stderr)
        return 2

    worst = 0.0
    rows = []
    for shot in shots:
        suffix = shot.name[len(before.name) :]
        other = after.parent / f"{after.name}{suffix}"
        if not other.exists():
            rows.append((suffix.lstrip("-").removesuffix(".png"), None, "missing"))
            worst = 100.0
            continue
        diff_path = after.parent / f"{after.name}{suffix.removesuffix('.png')}-diff.png"
        result = compare(shot, other, diff_path)
        if "error" in result:
            rows.append((suffix.lstrip("-").removesuffix(".png"), None, result["error"]))
            worst = 100.0
            continue
        structural = result["structural"] * 100
        worst = max(worst, structural)
        rows.append(
            (
                suffix.lstrip("-").removesuffix(".png"),
                structural,
                f"differing {result['differing'] * 100:6.2f}%  "
                f"structural {structural:6.2f}%  max_delta {result['max_delta']:3d}",
            )
        )

    width = max(len(name) for name, _, _ in rows)
    for name, structural, note in rows:
        flag = " " if structural is not None and structural <= args.structural_budget else "!"
        print(f"{flag} {name:<{width}}  {note}")

    print(f"\nworst structural difference: {worst:.2f}% (budget {args.structural_budget}%)")
    return 0 if worst <= args.structural_budget else 1


if __name__ == "__main__":
    sys.exit(main())
