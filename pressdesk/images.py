"""Immutable raster inputs, integer press registration and optical proofs."""

from io import BytesIO
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 2_400_000
MODES = {"proof", "plate-a", "plate-b", "coverage", "drift", "photo", "difference"}
PAPER_CLASS = [247, 243, 234]
CLASS_COLORS = [PAPER_CLASS, [229, 125, 89], [53, 129, 145], [45, 52, 73]]


def png(pixels):
    buffer = BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG", compress_level=3)
    return buffer.getvalue()


def decode(raw, plate=False):
    if len(raw) > MAX_UPLOAD:
        raise Invalid("Each image must be 12 MiB or smaller.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            formats = ("PNG",) if plate else ("PNG", "JPEG")
            with Image.open(BytesIO(raw), formats=formats) as source:
                width, height = source.size
                if min(width, height) < 8 or max(width, height) > 2400 or width * height > MAX_PIXELS:
                    raise Invalid("Use 8–2400 px per side and at most 2.4 million pixels.")
                if getattr(source, "n_frames", 1) != 1:
                    raise Invalid("Use a single-frame image.")
                if plate and (source.mode not in ("L", "1") or source.info.get("transparency") is not None):
                    raise Invalid("A plate must be an opaque 8-bit grayscale PNG. White means no ink.")
                oriented = ImageOps.exif_transpose(source)
                if not plate and ("A" in oriented.getbands() or "transparency" in oriented.info):
                    if oriented.convert("RGBA").getchannel("A").getextrema()[0] != 255:
                        raise Invalid("Flatten transparent areas before importing the proof photo.")
                return np.array(oriented.convert("L" if plate else "RGB"))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise Invalid("Image dimensions exceed the supported limit.") from None
    except (UnidentifiedImageError, ValueError, OSError, SyntaxError):
        raise Invalid("Choose an opaque grayscale PNG plate or an opaque PNG/JPEG proof photo.") from None


def bilinear(plate, x, y):
    """Sample coverage at fractional sheet coordinates; anything outside the plate is paper."""
    plate = np.asarray(plate, dtype=np.float32)
    height, width = plate.shape
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    tx = (x - x0).astype(np.float32)
    ty = (y - y0).astype(np.float32)

    def gather(yy, xx):
        inside = (yy >= 0) & (yy < height) & (xx >= 0) & (xx < width)
        values = np.where(inside, plate[np.clip(yy, 0, height - 1), np.clip(xx, 0, width - 1)], 0.0)
        return values.astype(np.float32)

    v00 = gather(y0, x0)
    v01 = gather(y0, x0 + 1)
    v10 = gather(y0 + 1, x0)
    v11 = gather(y0 + 1, x0 + 1)
    return (1 - tx) * (1 - ty) * v00 + tx * (1 - ty) * v01 + (1 - tx) * ty * v10 + tx * ty * v11


def translate(plate, dx, dy):
    """Move coverage in sheet coordinates, keeping half-pixel shifts and paper outside the plate."""
    height, width = plate.shape
    whole_x, whole_y = round(float(dx)), round(float(dy))
    if abs(float(dx) - whole_x) < 1e-9 and abs(float(dy) - whole_y) < 1e-9:
        moved = np.zeros_like(plate)
        if abs(whole_x) >= width or abs(whole_y) >= height:
            return moved
        x0, y0 = max(0, -whole_x), max(0, -whole_y)
        x1, y1 = min(width, width - whole_x), min(height, height - whole_y)
        moved[y0 + whole_y:y1 + whole_y, x0 + whole_x:x1 + whole_x] = plate[y0:y1, x0:x1]
        return moved
    xs = np.arange(width, dtype=np.float32)[None, :] - float(dx)
    ys = np.arange(height, dtype=np.float32)[:, None] - float(dy)
    return bilinear(plate, xs, ys).astype(np.float32)


def source_weights(plate, dx, dy, x, y):
    """Original cool-plate pixels and weights behind one sheet coordinate."""
    height, width = plate.shape
    source_x, source_y = x - float(dx), y - float(dy)
    x0, y0 = int(np.floor(source_x)), int(np.floor(source_y))
    tx, ty = source_x - x0, source_y - y0
    entries = []
    paper = 0.0
    for offset_y, weight_y in ((0, 1 - ty), (1, ty)):
        for offset_x, weight_x in ((0, 1 - tx), (1, tx)):
            weight = float(weight_x * weight_y)
            if weight <= 1e-9:
                continue
            sx, sy = x0 + offset_x, y0 + offset_y
            if 0 <= sx < width and 0 <= sy < height:
                entries.append({"x": sx, "y": sy, "weight": round(weight, 4),
                                "value": int(plate[sy, sx])})
            else:
                paper += weight
    return source_x, source_y, entries, round(paper, 4)


def rgb(hex_value):
    return np.array([int(hex_value[i:i + 2], 16) / 255.0 for i in (1, 3, 5)], dtype=np.float32)


def proof(a, moved_b, settings):
    """Approximate stacked ink transmission from linear coverage on neutral paper."""
    result = np.broadcast_to(rgb(settings["paper"]), (*a.shape, 3)).copy()
    for coverage, name in ((a, "ink_a"), (moved_b, "ink_b")):
        sheet = coverage[..., None].astype(np.float32) / 255.0
        result *= 1.0 - sheet * (1.0 - rgb(settings[name]))
    return np.rint(result * 255).astype(np.uint8)


def coverage_map(a, moved_b, threshold):
    return (a >= threshold).astype(np.uint8) + 2 * (moved_b >= threshold).astype(np.uint8)


def region_slices(zone, shape):
    if zone is None:
        return slice(0, shape[0]), slice(0, shape[1])
    x0, y0, x1, y1 = zone["bounds"]
    return slice(y0, y1), slice(x0, x1)


def locate_zone(settings, zone_id):
    return next((zone for zone in settings["zones"] if zone["id"] == zone_id), None)


def extract_view(a, b, photo, settings, view):
    moved_b = translate(b, settings["dx"], settings["dy"])
    if view == "plate-a":
        return 255 - a
    if view == "plate-b":
        return 255 - b
    if view == "coverage":
        return np.asarray(CLASS_COLORS, dtype=np.uint8)[coverage_map(a, moved_b, settings["threshold"])]
    if view == "drift":
        base = coverage_map(a, b, settings["threshold"])
        test = coverage_map(a, moved_b, settings["threshold"])
        same = np.where(base == 0, 243, 218).astype(np.uint8)
        result = np.repeat(same[..., None], 3, axis=-1)
        result[(base != test) & (test != 0)] = [54, 124, 158]
        result[(base != 0) & (test == 0)] = [221, 80, 61]
        return result
    if view == "photo" and photo is not None:
        return photo
    image = proof(a, moved_b, settings)
    if view == "difference" and photo is not None:
        delta = np.max(np.abs(image.astype(np.int16) - photo.astype(np.int16)), axis=2)
        display = np.empty_like(image)
        display[..., 0] = delta
        display[..., 1] = delta // 3
        display[..., 2] = delta // 4
        return display
    return image


def measure(a, b, settings, zone_id, point):
    moved_b = translate(b, settings["dx"], settings["dy"])
    threshold = settings["threshold"]
    zone = locate_zone(settings, zone_id)
    region = region_slices(zone, a.shape)
    before = coverage_map(a[region], b[region], threshold)
    after = coverage_map(a[region], moved_b[region], threshold)
    counts = np.bincount(after.ravel(), minlength=4)
    response = {
        "pixels": int(after.size),
        "classes": counts.tolist(),
        "mean_a": float(a[region].mean() * 100 / 255),
        "mean_b": float(moved_b[region].mean() * 100 / 255),
        "new_paper": int(np.count_nonzero((before != 0) & (after == 0))),
        "changed": int(np.count_nonzero(before != after)),
    }
    if point is not None:
        x, y = point
        width, height = a.shape[1], a.shape[0]
        source_x, source_y, entries, paper = source_weights(b, settings["dx"], settings["dy"], x, y)
        warm_cut = int(a[y, x]) >= threshold
        response["probe"] = {
            "x": x, "y": y, "a": int(a[y, x]), "b": int(round(float(moved_b[y, x]))),
            "b_source": [round(source_x, 2), round(source_y, 2)],
            "warm_class": warm_cut,
            "cool_class": int(moved_b[y, x]) >= threshold,
            "cool_sources": entries,
            "cool_paper": paper,
            "outside": paper >= 0.999,
        }
        xs = np.unique(np.append(np.linspace(0, width - 1, min(width, 320), dtype=int), x))
        response["profile"] = {"x": xs.tolist(), "a": a[y, xs].tolist(),
                               "b": moved_b[y, xs].round(1).tolist()}
    return response


def demo():
    """Generate a graphic sample with a close two-ink turn and narrow paper details."""
    height, width = 360, 600
    y, x = np.indices((height, width))
    panel = (48 <= x) & (x < 552) & (38 <= y) & (y < 270)
    split = 252 + 2 * (y - 38) // 5
    a = np.where(panel & (x < split), 255, 0).astype(np.uint8)
    b = np.where(panel & (x >= split), 255, 0).astype(np.uint8)
    for center, cy in ((155, 98), (174, 125), (187, 152), (199, 180)):
        hole = (x - center) ** 2 + (y - cy) ** 2 < 5 ** 2
        a[hole] = 0
    reserve = (x == split - 6) & (90 <= y) & (y < 192)
    a[reserve] = 0
    reserve_end = (split - 12 <= x) & (x < split - 7) & (188 <= y) & (y < 195)
    a[reserve_end] = 0
    for i, tone in enumerate((38, 76, 114, 152, 191, 229, 255)):
        left = 50 + i * 72
        a[303:325, left:left + 62] = tone
        b[323:344, left:left + 62] = tone
    return a, b
