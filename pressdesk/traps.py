"""Integer-spread traps built from two thresholded separation masks.

A trap only ever adds ink; it never removes coverage and never moves plate
coordinates. The candidate spreads are checked against every integer
registration shift in the required grid so that the chosen join cannot open a
paper gap, while the deliberately reserved paper hairline stays uncovered.
"""

from io import BytesIO

import numpy as np

from . import images

DX_LIMIT = 2
DY_LIMIT = 1
SHIFT_GRID = tuple((dx, dy) for dy in range(-DY_LIMIT, DY_LIMIT + 1)
                   for dx in range(-DX_LIMIT, DX_LIMIT + 1))
INNER_GRID = tuple((dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
PLANS = ("warm", "cool", "balanced")
PLAN_NAMES = {"warm": "Warm spread", "cool": "Cool spread", "balanced": "Balanced split"}

PAPER_TINT = np.array([244, 240, 231], dtype=np.uint8)
WARM_TINT = np.array([241, 206, 190], dtype=np.uint8)
COOL_TINT = np.array([197, 223, 227], dtype=np.uint8)
OVERLAP_TINT = np.array([214, 205, 220], dtype=np.uint8)
PROTECTED_TINT = np.array([151, 141, 122], dtype=np.uint8)
WARM_ADDED = np.array([212, 84, 54], dtype=np.uint8)
COOL_ADDED = np.array([40, 127, 141], dtype=np.uint8)
BOTH_ADDED = np.array([45, 52, 73], dtype=np.uint8)


def shift(mask, dx, dy):
    """Move a boolean mask in sheet coordinates, padding with False."""
    return images.translate(mask.astype(np.uint8), dx, dy).astype(bool)


def zone_mask(zone, shape):
    mask = np.zeros(shape, dtype=bool)
    if zone is None:
        return mask
    x0, y0, x1, y1 = zone["bounds"]
    mask[y0:y1, x0:x1] = True
    return mask


def masks(a, b, threshold):
    return a >= threshold, b >= threshold


def protected_paper(a_mask, b_mask, seam_m, reserve_m):
    """Paper at zero offset inside either chosen box; never receive trap ink."""
    return (~a_mask & ~b_mask) & (seam_m | reserve_m)


def _warm_candidates(a_mask, b_mask, grid, seam_m, protected):
    """Warm pixels needed so some warm ink covers every cool drift target."""
    needed = np.zeros_like(a_mask)
    for dx, dy in grid:
        needed |= ~shift(b_mask, dx, dy)
    return (~a_mask) & needed & seam_m & ~protected


def _cool_candidates(a_mask, b_mask, grid, cool_allowed):
    """Cool source pixels needed so drifting cool still covers the warm edge."""
    needed = np.zeros_like(a_mask)
    for dx, dy in grid:
        needed |= ~shift(a_mask, -dx, -dy)
    return (~b_mask) & needed & cool_allowed


def _repair_with_warm(a_mask, b_mask, add_a, add_b, seam_m, protected, grid):
    """Close residual gaps by adding warm ink; return whether the plan is sound."""
    add_a = add_a.copy()
    for _ in range(16):
        covered = a_mask | add_a
        gap = np.zeros_like(a_mask)
        for dx, dy in grid:
            gap |= seam_m & ~(covered | shift(b_mask | add_b, dx, dy)) & (a_mask | b_mask)
        if not gap.any():
            return add_a, True
        extra = gap & ~a_mask & seam_m & ~protected
        if not extra.any():
            return add_a, False
        add_a |= extra
    return add_a, False


def evaluate(a_mask, b_mask, add_a, add_b, seam_m, reserve_m, protected, grid):
    """Check every grid shift for join gaps and hairline fills."""
    warm = a_mask | add_a
    cool = b_mask | add_b
    failures = []
    for dx, dy in grid:
        covered = warm | shift(cool, dx, dy)
        gaps = int(np.count_nonzero(seam_m & ~covered & (a_mask | b_mask)))
        fills = int(np.count_nonzero(reserve_m & protected & covered))
        if gaps or fills:
            failures.append({"dx": int(dx), "dy": int(dy), "gaps": gaps,
                             "hairline_fills": fills})
    new_overprint = warm & cool & ~(a_mask & b_mask)
    cool_side = int(np.count_nonzero(new_overprint & ~a_mask))
    warm_side = int(np.count_nonzero(new_overprint & ~b_mask))
    return {
        "feasible": bool(not failures),
        "failures": failures,
        "add_warm": int(np.count_nonzero(add_a)),
        "add_cool": int(np.count_nonzero(add_b)),
        "overprint": int(np.count_nonzero(new_overprint)),
        "overprint_cool_side": cool_side,
        "overprint_warm_side": warm_side,
        "imbalance": abs(cool_side - warm_side),
    }


def build_plans(a, b, threshold, seam, reserve):
    """Return the three trap plans with masks, grid metrics and a recommendation."""
    a_mask, b_mask = masks(a, b, threshold)
    seam_m = zone_mask(seam, a_mask.shape)
    reserve_m = zone_mask(reserve, a_mask.shape)
    protected = protected_paper(a_mask, b_mask, seam_m, reserve_m)

    # A cool source pixel is unusable when any allowed drift would park it on
    # top of the reserved paper hairline.
    cool_blocked = np.zeros_like(protected)
    for dx, dy in SHIFT_GRID:
        cool_blocked |= shift(protected, -dx, -dy)
    cool_allowed = seam_m & ~protected & ~cool_blocked

    warm_full = _warm_candidates(a_mask, b_mask, SHIFT_GRID, seam_m, protected)
    cool_full = _cool_candidates(a_mask, b_mask, SHIFT_GRID, cool_allowed)
    warm_inner = _warm_candidates(a_mask, b_mask, INNER_GRID, seam_m, protected)
    cool_inner = _cool_candidates(a_mask, b_mask, INNER_GRID, cool_allowed)
    warm_inner, _ = _repair_with_warm(
        a_mask, b_mask, warm_inner, cool_inner, seam_m, protected, SHIFT_GRID)

    raw = {
        "warm": (warm_full, np.zeros_like(a_mask)),
        "cool": (np.zeros_like(a_mask), cool_full),
        "balanced": (warm_inner, cool_inner),
    }
    plans = []
    for plan_id in PLANS:
        add_a, add_b = raw[plan_id]
        metrics = evaluate(a_mask, b_mask, add_a, add_b, seam_m, reserve_m,
                           protected, SHIFT_GRID)
        plans.append({
            "plan": plan_id,
            "name": PLAN_NAMES[plan_id],
            "shifts": [list(item) for item in SHIFT_GRID],
            "dx_limit": DX_LIMIT,
            "dy_limit": DY_LIMIT,
            "threshold": int(threshold),
            "mask_a": add_a,
            "mask_b": add_b,
            **metrics,
        })
    feasible = [plan for plan in plans if plan["feasible"]]
    recommended = None
    if feasible:
        recommended = min(feasible, key=lambda p: (p["add_cool"], p["imbalance"],
                                                   p["add_warm"], PLANS.index(p["plan"])))["plan"]
    return plans, recommended, protected


def trapped_plates(a, b, add_a, add_b):
    """Coverage arrays with trap additions burned in at full ink."""
    warm = np.where(add_a, 255, a).astype(np.uint8)
    cool = np.where(add_b, 255, b).astype(np.uint8)
    return warm, cool


def overlay(a_mask, b_mask, add_a, add_b, protected):
    """RGB map of original coverage, preserved paper and the ink each plate gains."""
    image = np.broadcast_to(PAPER_TINT, (*a_mask.shape, 3)).copy()
    image[a_mask & ~b_mask] = WARM_TINT
    image[b_mask & ~a_mask] = COOL_TINT
    image[a_mask & b_mask] = OVERLAP_TINT
    image[protected] = PROTECTED_TINT
    image[add_a] = WARM_ADDED
    image[add_b] = COOL_ADDED
    image[add_a & add_b] = BOTH_ADDED
    return image


def mask_png(add_a, add_b):
    return (BytesIO(images.png((add_a * 255).astype(np.uint8))),
            BytesIO(images.png((add_b * 255).astype(np.uint8))))
