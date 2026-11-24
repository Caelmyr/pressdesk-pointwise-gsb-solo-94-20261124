"""Read the cool-plate offset from a trial proof photo at half-pixel resolution.

The warm plate is fixed on the bed, so the photo is first reduced to cool ink coverage by
dividing out warm coverage and paper.  Cool edge points come from the cool separation, never
from warm texture, and are split into a main contour and finer adjacent lines.  A candidate
translation is only recommended when every structure it reaches stays within one pixel.
"""

import numpy as np
from scipy import ndimage

from image_lab.validation import Invalid

try:
    import cv2
except ImportError:  # pragma: no cover - pinned dependency, only a degraded seed
    cv2 = None

STEP = 0.25
RADIUS = 4.0
TAPS = np.arange(-RADIUS, RADIUS + 1e-9, STEP, dtype=np.float32)
WINDOW = np.hanning(len(TAPS)).astype(np.float32)
MAX_EDGE_POINTS = 2200
FINE_WIDTH = 4.0
HALF = 0.5
ADOPT_LIMIT = 1.0
AXIS_COMPONENT = 0.6
MIN_EDGE_GRADIENT = 0.025
MIN_PROFILE_PEAK = 0.25
MIN_PROFILE_ENERGY = 0.004
MIN_AXIS_POINTS = 8
MIN_AXIS_ENERGY = 0.25
MAX_STRUCTURE_OFFSET = 7.0


def color(hex_value):
    return np.array([int(hex_value[i:i + 2], 16) / 255.0 for i in (1, 3, 5)], dtype=np.float32)


def blur(image, sigma=0.9):
    return ndimage.gaussian_filter(np.asarray(image, dtype=np.float32), sigma, mode="nearest")


def sample(image, x, y):
    """Bilinear sample; outside the separation is bare paper."""
    image = np.asarray(image, dtype=np.float32)
    height, width = image.shape
    x0 = np.floor(x)
    y0 = np.floor(y)
    tx = (x - x0).astype(np.float32)
    ty = (y - y0).astype(np.float32)
    x0 = x0.astype(np.int64)
    y0 = y0.astype(np.int64)

    def gather(yy, xx):
        inside = (yy >= 0) & (yy < height) & (xx >= 0) & (xx < width)
        return np.where(inside, image[np.clip(yy, 0, height - 1), np.clip(xx, 0, width - 1)], 0.0)

    v00 = gather(y0, x0)
    v01 = gather(y0, x0 + 1)
    v10 = gather(y0 + 1, x0)
    v11 = gather(y0 + 1, x0 + 1)
    return (1 - tx) * (1 - ty) * v00 + tx * (1 - ty) * v01 + (1 - tx) * ty * v10 + tx * ty * v11


def recover_cool(photo, warm, settings):
    """Estimate cool ink coverage from the photo by dividing out fixed warm ink and paper."""
    paper = color(settings["paper"])
    warm_ink = color(settings["ink_a"])
    cool_ink = color(settings["ink_b"])
    warm_sheet = np.asarray(warm, dtype=np.float32) / 255.0
    base = np.maximum(paper[None, None, :] * (1 - warm_sheet[..., None] * (1 - warm_ink)[None, None, :]), 1e-3)
    observed = np.asarray(photo, dtype=np.float32) / 255.0
    estimated = np.clip((1 - observed / base) / (1 - cool_ink)[None, None, :], -0.3, 1.3)
    information = base * (1 - cool_ink)[None, None, :]
    weights = information ** 2
    total = np.maximum(weights.sum(axis=2), 1e-9)
    coverage = (estimated * weights).sum(axis=2) / total
    spread = estimated.max(axis=2) - estimated.min(axis=2)
    trust = (1 / (1 + (spread / 0.20) ** 2)) * np.clip(total / 0.05, 0, 1)
    return np.clip(coverage, 0, 1).astype(np.float32), trust.astype(np.float32)


def cool_edges(cool, shape):
    """Peak cool-ink edges with unit normals and stroke width, one point per pixel run."""
    height, width = shape
    smooth = blur(cool)
    gy, gx = np.gradient(smooth)
    magnitude = np.hypot(gx, gy)
    peaks = (magnitude >= ndimage.maximum_filter(magnitude, 3)) & (magnitude >= MIN_EDGE_GRADIENT)
    peaks[:3] = peaks[-3:] = False
    peaks[:, :3] = peaks[:, -3:] = False
    ys, xs = np.nonzero(peaks)
    if len(xs) < 20:
        return None
    normals_x = gx[ys, xs] / magnitude[ys, xs]
    normals_y = gy[ys, xs] / magnitude[ys, xs]
    ink = cool > 0.12
    distance = ndimage.distance_transform_edt(ink)
    stroke = 2 * ndimage.maximum_filter(distance, size=15)[ys, xs]
    fine = stroke <= FINE_WIDTH
    return {"x": xs.astype(np.float32), "y": ys.astype(np.float32),
            "nx": normals_x.astype(np.float32), "ny": normals_y.astype(np.float32),
            "fine": fine, "magnitude": magnitude[ys, xs].astype(np.float32)}


def phase_seed(cool, photo_cool, shape):
    height, width = shape
    if min(height, width) < 8:
        return (0.0, 0.0), 0.0
    a = np.hypot(*np.gradient(blur(cool)))
    b = np.hypot(*np.gradient(blur(photo_cool)))
    if cv2 is not None:
        window = cv2.createHanningWindow((width, height), cv2.CV_32F)
        shift, response = cv2.phaseCorrelate(a.astype(np.float32), b.astype(np.float32), window)
        if np.isfinite(shift).all():
            return (float(shift[0]), float(shift[1])), float(response)
    # Fallback for a missing OpenCV build: cross-correlate heavily smoothed rasters.
    fa = np.fft.rfft2(blur(a, 3) - blur(a, 3).mean())
    fb = np.fft.rfft2(blur(b, 3) - blur(b, 3).mean())
    correlation = np.fft.irfft2(fb * np.conj(fa), s=(height, width))
    peak = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    dy = peak[0] if peak[0] <= height // 2 else peak[0] - height
    dx = peak[1] if peak[1] <= width // 2 else peak[1] - width
    return (float(dx), float(dy)), 0.0


def profiles(cool, photo_cool, edge, shift):
    """Warm-free normal profiles of cool separation and recovered cool ink at one shift."""
    qx = edge["x"] + shift[0]
    qy = edge["y"] + shift[1]
    nx = edge["nx"][:, None]
    ny = edge["ny"][:, None]
    offset_x = nx * TAPS[None, :]
    offset_y = ny * TAPS[None, :]
    source = sample(cool, edge["x"][:, None] + offset_x, edge["y"][:, None] + offset_y)
    moved_x = qx[:, None] + offset_x
    moved_y = qy[:, None] + offset_y
    observed = sample(photo_cool, moved_x, moved_y)
    observed = ndimage.gaussian_filter1d(observed, 0.6 / STEP, axis=1, mode="nearest")
    source = ndimage.gaussian_filter1d(source, 0.6 / STEP, axis=1, mode="nearest")
    return source, observed


def cross_correlation(source, observed):
    """Per-point shift of the observed profile against the separation profile, to sub-taps."""
    a = source - source.mean(axis=1, keepdims=True)
    b = observed - observed.mean(axis=1, keepdims=True)
    a = np.gradient(a, STEP, axis=1) * WINDOW
    b = np.gradient(b, STEP, axis=1) * WINDOW
    size = 64
    fa = np.fft.rfft(a, n=size, axis=1)
    fb = np.fft.rfft(b, n=size, axis=1)
    correlation = np.fft.irfft(fb * np.conj(fa), n=size, axis=1)
    correlation = np.concatenate([correlation[:, -size // 2:], correlation[:, :size // 2 + 1]], axis=1)
    normalizer = np.sqrt((a ** 2).sum(axis=1) * (b ** 2).sum(axis=1)) + 1e-9
    correlation = correlation / normalizer[:, None]
    lags = np.arange(-size // 2, size // 2 + 1)
    # A structure may sit up to a few pixels off its counterpart; only the reported
    # reading is limited to half-pixel steps.
    limitation = max(int(RADIUS / STEP), int(MAX_STRUCTURE_OFFSET / STEP))
    allowed = np.abs(lags) <= limitation
    usable = correlation[:, allowed]
    lag_values = lags[allowed]
    rows = np.arange(len(a))
    best = np.argmax(usable, axis=1)
    peak = usable[rows, best]
    left = usable[rows, np.clip(best - 1, 0, len(lag_values) - 1)]
    right = usable[rows, np.clip(best + 1, 0, len(lag_values) - 1)]
    curvature = left - 2 * peak + right
    safe = np.abs(curvature) > 1e-9
    offset = np.zeros_like(curvature)
    offset[safe] = 0.5 * (left[safe] - right[safe]) / curvature[safe]
    offset = np.clip(offset, -0.5, 0.5)
    residual = (lag_values[best] + offset) * STEP
    energy = np.abs(a).max(axis=1) * np.abs(b).max(axis=1)
    return residual.astype(np.float32), peak.astype(np.float32), energy.astype(np.float32)


def axis_evidence(cool, photo_cool, edge, shift):
    """Which axes the cool ink itself constrains around the zone."""
    qx = edge["x"] + shift[0]
    qy = edge["y"] + shift[1]
    nx, ny = edge["nx"], edge["ny"]
    probe = np.array([-0.5, 0.5], dtype=np.float32)
    warm_before = sample(cool, edge["x"] + nx * probe[0], edge["y"] + ny * probe[0])
    warm_after = sample(cool, edge["x"] + nx * probe[1], edge["y"] + ny * probe[1])
    cool_before = sample(photo_cool, qx + nx * probe[0], qy + ny * probe[0])
    cool_after = sample(photo_cool, qx + nx * probe[1], qy + ny * probe[1])
    warm_gradient = np.abs(warm_after - warm_before)
    cool_gradient = np.abs(cool_after - cool_before)
    usable = (warm_gradient > 0.012) & (cool_gradient > 0.012)
    response = {}
    for axis, component in (("x", nx), ("y", ny)):
        # A slanted edge still dates both axes, but a nearly level line says nothing about Y.
        chosen = usable & (np.abs(component) > AXIS_COMPONENT)
        count = int(chosen.sum())
        energy = float(cool_gradient[chosen].sum()) if count else 0.0
        response[axis] = {"evidence": bool(count >= MIN_AXIS_POINTS and energy > MIN_AXIS_ENERGY),
                          "points": count, "energy": round(energy, 3)}
    return response


def residuals(cool, photo_cool, edge, shift):
    source, observed = profiles(cool, photo_cool, edge, shift)
    values, peak, energy = cross_correlation(source, observed)
    confident = (peak > MIN_PROFILE_PEAK) & (energy > MIN_PROFILE_ENERGY)
    return values, confident


def refine(cool, photo_cool, edge, seed, free, iterations=8):
    """Projection least squares on normal residuals, holding axes without evidence fixed."""
    shift = [float(seed[0]) if free[0] else 0.0, float(seed[1]) if free[1] else 0.0]
    active = np.ones(len(edge["x"]), dtype=bool)
    for _ in range(iterations):
        values, confident = residuals(cool, photo_cool, edge, shift)
        active = confident
        if active.sum() < 10:
            return None
        weights = np.ones(int(active.sum()), dtype=np.float64)
        normals = np.stack([edge["nx"][active], edge["ny"][active]], axis=1).astype(np.float64)
        matrix = normals.T @ (weights[:, None] * normals)
        right = normals.T @ (weights * values[active].astype(np.float64))
        for index, is_free in enumerate(free):
            if not is_free:
                matrix[index, :] = 0.0
                matrix[:, index] = 0.0
                matrix[index, index] = 1.0
                right[index] = 0.0
        if abs(np.linalg.det(matrix)) < 1e-9:
            return None
        delta = np.linalg.solve(matrix, right)
        shift[0] += float(np.clip(delta[0], -2, 2))
        shift[1] += float(np.clip(delta[1], -2, 2))
        if np.max(np.abs(delta)) < 1e-3:
            break
    return shift


def snap(value, free):
    if not free:
        return value
    return round(float(value) * 2) / 2


def candidate_offsets(center, free):
    if not free:
        return [snap(center, free)]
    return sorted({snap(center + offset, free) for offset in (-0.5, -0.25, 0.0, 0.25, 0.5)})


def structure_metrics(edge, values, confident, shift):
    result = {}
    for name, mask in (("main", ~edge["fine"]), ("fine", edge["fine"])):
        chosen = mask & confident
        count = int(chosen.sum())
        magnitudes = np.abs(values[chosen])
        result[name] = {
            "points": count,
            "max": round(float(magnitudes.max()), 3) if count >= 4 else None,
            "p90": round(float(np.percentile(magnitudes, 90)), 3) if count >= 4 else None,
        }
    return result


def mismatch_segments(cool_shape, edge, values, confident, structure, shift, limit=1.0):
    """Where a structure still misses by more than a pixel, in sheet coordinates after the shift."""
    chosen = confident & (np.abs(values) > limit)
    if structure == "fine":
        chosen &= edge["fine"]
    else:
        chosen &= ~edge["fine"]
    indices = np.nonzero(chosen)[0]
    if len(indices) == 0:
        return []
    if len(indices) > 220:
        indices = indices[np.linspace(0, len(indices) - 1, 220).astype(int)]
    segments = []
    height, width = cool_shape
    for index in indices:
        x = float(edge["x"][index]) + shift[0]
        y = float(edge["y"][index]) + shift[1]
        end_x = x + float(edge["nx"][index]) * float(values[index])
        end_y = y + float(edge["ny"][index]) * float(values[index])
        segments.append({"x0": round(x, 2), "y0": round(y, 2),
                         "x1": round(end_x, 2), "y1": round(end_y, 2),
                         "error": round(abs(float(values[index])), 2), "structure": structure,
                         "inside": 0 <= end_x <= width - 1 and 0 <= end_y <= height - 1})
    return segments


def analyse(warm, cool, photo, settings, bounds, current, evaluate=False):
    """Return a half-pixel candidate or explicitly name the axis short of evidence.

    With ``evaluate`` the current shift is checked on the same photo instead of searching
    for a new candidate, so an adopted reading can be followed up on the actual print.
    """
    x0, y0, x1, y1 = bounds
    if x1 - x0 < 6 or y1 - y0 < 6:
        raise Invalid("Draw an inspection zone at least six pixels across over cool ink.")
    warm_zone = np.asarray(warm[y0:y1, x0:x1], dtype=np.float32)
    cool_zone = np.asarray(cool[y0:y1, x0:x1], dtype=np.float32) / 255.0
    photo_zone = photo[y0:y1, x0:x1]
    recovered, _ = recover_cool(photo_zone, warm_zone, settings)
    edge = cool_edges(cool_zone, cool_zone.shape)
    if edge is None:
        return {"ok": False, "analysed": True, "reason": "no_cool_edges",
                "message": "No cool-ink edge is visible inside the zone; move the zone across the cool contour."}
    total = len(edge["x"])
    if total > MAX_EDGE_POINTS:
        main = np.nonzero(~edge["fine"])[0]
        fine = np.nonzero(edge["fine"])[0]

        def thin(indices):
            if len(indices) <= MAX_EDGE_POINTS // 2:
                return indices
            return indices[np.linspace(0, len(indices) - 1, MAX_EDGE_POINTS // 2).astype(int)]

        keep = np.sort(np.concatenate([thin(main), thin(fine)]))
        edge = {key: value[keep] for key, value in edge.items()}
    seed, response = phase_seed(cool_zone, recovered, cool_zone.shape)
    evidence = axis_evidence(cool_zone, recovered, edge, seed)
    free = (evidence["x"]["evidence"], evidence["y"]["evidence"])
    if not any(free):
        return {"ok": False, "analysed": True, "reason": "no_axis_evidence", "axes": evidence,
                "missing": ["x", "y"], "phase": [round(seed[0], 2), round(seed[1], 2)],
                "message": "The cool ink in this zone shows no usable edge direction; X and Y both lack evidence."}
    if evaluate:
        refined = [float(current[0]), float(current[1])]
    else:
        refined = refine(cool_zone, recovered, edge, seed, free)
        if refined is None:
            refined = [seed[0] if free[0] else float(current[0]),
                       seed[1] if free[1] else float(current[1])]
    if evaluate:
        values, confident = residuals(cool_zone, recovered, edge, refined)
        metrics = structure_metrics(edge, values, confident, refined)
        best = ((0, 0, 0), (refined[0], refined[1]), metrics, values, confident)
    else:
        best = None
        for candidate_x in candidate_offsets(refined[0], free[0]):
            for candidate_y in candidate_offsets(refined[1], free[1]):
                candidate = (candidate_x, candidate_y)
                values, confident = residuals(cool_zone, recovered, edge, candidate)
                metrics = structure_metrics(edge, values, confident, candidate)
                present = [m["max"] for m in metrics.values() if m["max"] is not None]
                if not present:
                    continue
                score = (max(present), sum(present),
                         abs(candidate_x - refined[0]) + abs(candidate_y - refined[1]))
                if best is None or score < best[0]:
                    best = (score, candidate, metrics, values, confident)
    if best is None:
        return {"ok": False, "analysed": True, "reason": "no_confident_edges", "axes": evidence,
                "missing": [axis for axis, found in zip(("x", "y"), free) if not found],
                "phase": [round(seed[0], 2), round(seed[1], 2)],
                "message": "Too few confident cool edges in the zone for a half-pixel reading."}
    _, candidate, metrics, values, confident = best
    final_evidence = axis_evidence(cool_zone, recovered, edge, candidate)
    present = [m["max"] for m in metrics.values() if m["max"] is not None]
    adoptable = bool(present) and max(present) <= ADOPT_LIMIT
    missing = [axis for axis in ("x", "y") if not final_evidence[axis]["evidence"]]
    shift = {"x": candidate[0] if free[0] else None, "y": candidate[1] if free[1] else None}
    segments = []
    for structure in ("main", "fine"):
        segments.extend(mismatch_segments(cool_zone.shape, edge, values, confident,
                                          structure, candidate, ADOPT_LIMIT))
    for segment in segments:
        segment["x0"] += x0
        segment["x1"] += x0
        segment["y0"] += y0
        segment["y1"] += y0
    recommendation = {
        "ok": True,
        "analysed": True,
        "mode": "evaluate" if evaluate else "recommend",
        "shift": shift,
        "measured": {"x": round(float(refined[0]), 2), "y": round(float(refined[1]), 2)},
        "current": {"x": float(current[0]), "y": float(current[1])},
        "axes": final_evidence,
        "missing": missing,
        "free": {"x": bool(free[0]), "y": bool(free[1])},
        "phase": [round(seed[0], 2), round(seed[1], 2)],
        "phase_response": round(response, 3),
        "structures": metrics,
        "adoptable": adoptable,
        "limit": ADOPT_LIMIT,
        "resolution": HALF,
        "segments": segments,
        "zone": [x0, y0, x1, y1],
    }
    if missing:
        names = " and ".join(axis.upper() for axis in missing)
        recommendation["message"] = (f"{names} has no readable cool-edge evidence in this zone; "
                                     f"only {'X' if missing == ['y'] else 'Y'} is recommended."
                                     if len(missing) == 1 else "Neither axis is readable in this zone.")
    if not adoptable:
        blocked = [name for name, values in metrics.items()
                   if values["max"] is not None and values["max"] > ADOPT_LIMIT]
        labels = ["the main contour" if name == "main" else "the fine line" for name in blocked]
        label = " and ".join(labels) or "this contour"
        plural = len(labels) > 1
        both = metrics["main"]["max"] is not None and metrics["fine"]["max"] is not None
        tail = "one shift cannot close both." if both else "try a smaller readjustment or fewer fine details."
        recommendation["blocked"] = blocked
        recommendation["message"] = (f"{label[0].upper() + label[1:]} still "
                                     f"{'miss' if plural else 'misses'} by more than "
                                     f"{ADOPT_LIMIT:g} px after this shift (outlined on the image); {tail}")
    return recommendation
