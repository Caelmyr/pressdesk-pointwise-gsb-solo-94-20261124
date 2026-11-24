"""Immutable raster inputs, integer press registration and optical proofs."""

from io import BytesIO
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from image_lab.validation import Invalid

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 2_400_000
MODES = {"proof", "plate-a", "plate-b", "coverage", "drift", "trap-overlay",
         "photo", "difference"}
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


def translate(plate, dx, dy):
    """Move coverage in sheet coordinates, with unprinted paper outside the plate."""
    height, width = plate.shape
    moved = np.zeros_like(plate)
    if abs(dx) >= width or abs(dy) >= height:
        return moved
    x0, y0 = max(0, -dx), max(0, -dy)
    x1, y1 = min(width, width - dx), min(height, height - dy)
    moved[y0 + dy:y1 + dy, x0 + dx:x1 + dx] = plate[y0:y1, x0:x1]
    return moved


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


def extract_view(a, b, photo, settings, view, trap_masks=None, overlay=None):
    add_a, add_b = trap_masks or (None, None)
    if add_a is not None:
        a = np.where(add_a, 255, a)
        b = np.where(add_b, 255, b)
    moved_b = translate(b, settings["dx"], settings["dy"])
    if view == "plate-a":
        return 255 - a
    if view == "plate-b":
        return 255 - b
    if view == "trap-overlay":
        return overlay
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


def measure(a, b, settings, zone_id, point, trap_masks=None):
    add_a, add_b = trap_masks or (None, None)
    if add_a is not None:
        a = np.where(add_a, 255, a)
        b = np.where(add_b, 255, b)
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
        bx, by = x - settings["dx"], y - settings["dy"]
        width, height = a.shape[1], a.shape[0]
        response["probe"] = {"x": x, "y": y, "a": int(a[y, x]), "b": int(moved_b[y, x]),
                             "b_source": [bx, by] if 0 <= bx < width and 0 <= by < height else None}
        xs = np.unique(np.append(np.linspace(0, width - 1, min(width, 320), dtype=int), x))
        response["profile"] = {"x": xs.tolist(), "a": a[y, xs].tolist(), "b": moved_b[y, xs].tolist()}
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
