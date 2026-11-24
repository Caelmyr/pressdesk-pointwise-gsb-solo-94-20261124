"""Settings validation and on-disk sheet and proof records."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from threading import RLock
from uuid import uuid4

import numpy as np
from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid

from . import images, traps

HEX = re.compile(r"#[0-9A-Fa-f]{6}")
KEY = re.compile(r"[0-9a-f]{16}")
LIMIT = 10 ** 9
DEFAULTS = {"dx": 0, "dy": 0, "threshold": 128, "ink_a": "#d45436",
            "ink_b": "#287f8d", "paper": "#f1e8d6", "zones": []}


def name(value, default=""):
    return (str(value).strip()[:90] if value is not None else "") or default


def integer(value, low, high, title):
    if type(value) is not int or not low <= value <= high:
        raise Invalid(f"{title} must be a whole number from {low} to {high}.")
    return value


def coordinates(point, width, height):
    if not isinstance(point, list) or len(point) != 2:
        raise Invalid("A probe needs an image x and y.")
    return [integer(point[0], 0, width - 1, "X"), integer(point[1], 0, height - 1, "Y")]


def color(value, title):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise Invalid(f"{title} must use a six-digit RGB color.")
    return value.lower()


def settings(record, changes):
    if not isinstance(changes, dict):
        raise Invalid("The proof setup must be a JSON object.")
    data = {**record["settings"], **changes}
    width, height = record["width"], record["height"]
    zones = data["zones"]
    if not isinstance(zones, list) or len(zones) > 8:
        raise Invalid("You can keep up to eight inspection zones.")
    normalized = []
    for zone in zones:
        if not isinstance(zone, dict):
            raise Invalid("An inspection zone needs a name and rectangle.")
        key = zone.get("id")
        if not isinstance(key, str) or KEY.fullmatch(key) is None or any(p["id"] == key for p in normalized):
            raise Invalid("Each inspection zone needs a unique identifier.")
        bounds = zone.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise Invalid("Click opposite corners of the inspection zone.")
        x0 = integer(bounds[0], 0, width - 1, "Left")
        y0 = integer(bounds[1], 0, height - 1, "Top")
        x1 = integer(bounds[2], x0 + 1, width, "Right")
        y1 = integer(bounds[3], y0 + 1, height, "Bottom")
        normalized.append({"id": key, "name": name(zone.get("name"), "Inspection zone"),
                           "bounds": [x0, y0, x1, y1]})
    return {
        "dx": integer(data["dx"], -32, 32, "X shift"),
        "dy": integer(data["dy"], -32, 32, "Y shift"),
        "threshold": integer(data["threshold"], 1, 255, "Ink threshold"),
        "ink_a": color(data["ink_a"], "Warm ink"),
        "ink_b": color(data["ink_b"], "Cool ink"),
        "paper": color(data["paper"], "Paper"),
        "zones": normalized,
    }


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read(path):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        raise NotFound("That sheet or saved proof could not be found.") from None


def write(path, data):
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_bytes(data)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def put(path, document):
    write(path, json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())


def available_key(value):
    if not isinstance(value, str) or KEY.fullmatch(value) is None:
        raise NotFound("That sheet or saved proof could not be found.")
    return value


class Library:
    def __init__(self, root):
        self.root = Path(root) / "sheets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def directory(self, sheet):
        return self.root / available_key(sheet)

    def list(self):
        rows = [read(p) for p in self.root.glob("*/sheet.json")]
        return sorted(rows, key=lambda row: (row["updated_at"], row["id"]), reverse=True)

    def create(self, title, plate_a, plate_b, labels, synthetic=False):
        if plate_a.shape != plate_b.shape:
            raise Invalid("Both plates must have the same pixel dimensions.")
        height, width = plate_a.shape
        sheet = uuid4().hex[:16]
        directory = self.directory(sheet)
        record = {"id": sheet, "title": name(title, "Two-ink sheet"), "width": width, "height": height,
                  "synthetic": synthetic, "labels": labels, "updated_at": timestamp(), "revision": 0,
                  "settings": DEFAULTS, "photo": None}
        if synthetic:
            record["settings"] = {**DEFAULTS, "zones": [
                {"id": "0000000000000001", "name": "Feather seam", "bounds": [263, 75, 325, 201]},
                {"id": "0000000000000002", "name": "Paper hairline", "bounds": [268, 90, 310, 193]},
            ]}
        record["trap"] = None
        directory.mkdir()
        try:
            write(directory / "plate-a.png", images.png(255 - plate_a))
            write(directory / "plate-b.png", images.png(255 - plate_b))
            put(directory / "sheet.json", record)
        except OSError:
            shutil.rmtree(directory)
            raise
        return record

    def sheet(self, sheet):
        return read(self.directory(sheet) / "sheet.json")

    @contextmanager
    def edit(self, sheet, revision):
        expected = integer(revision, 0, LIMIT, "Sheet revision")
        with self.lock:
            record = self.sheet(sheet)
            if expected != record["revision"]:
                raise Conflict("The sheet changed. Reopen it before saving again.")
            yield record
            record["revision"] += 1
            record["updated_at"] = timestamp()
            put(self.directory(sheet) / "sheet.json", record)

    def save(self, sheet, revision, changes):
        with self.edit(sheet, revision) as record:
            record["settings"] = settings(record, changes)
        return record

    def add_photo(self, sheet, revision, photo, label, origin=None):
        with self.edit(sheet, revision) as record:
            if photo.shape[:2] != (record["height"], record["width"]):
                raise Invalid("The proof photo must have the same displayed dimensions as the plates.")
            data = images.png(photo)
            hash_value = sha256(data).hexdigest()
            path = self.directory(sheet) / f"photo-{hash_value}.png"
            if not path.exists():
                write(path, data)
            record["photo"] = {"sha256": hash_value, "name": name(label, "Proof photo"), "origin": origin}
        return record

    def plate_paths(self, record):
        directory = self.directory(record["id"])
        return directory / "plate-a.png", directory / "plate-b.png"

    def trap_path(self, record, trap_id):
        available_key(trap_id)
        return self.directory(record["id"]) / f"trap-{trap_id}.json"

    def load_trap(self, record, trap_id):
        if trap_id is None:
            return None
        document = read(self.trap_path(record, trap_id))
        directory = self.directory(record["id"])
        add_a = np.asarray(images.decode((directory / document["mask_a"]).read_bytes(), plate=True)) >= 128
        add_b = np.asarray(images.decode((directory / document["mask_b"]).read_bytes(), plate=True)) >= 128
        return {"document": document, "add_a": add_a, "add_b": add_b}

    def active_trap(self, record):
        """Trap attached to this record (live sheet or saved proof), or None."""
        trap_id = record.get("trap")
        return self.load_trap(record, trap_id) if trap_id else None

    def create_trap(self, sheet, revision, plan_id, seam_id, reserve_id, threshold):
        if plan_id not in traps.PLANS:
            raise Invalid("Choose one of the listed trap plans.")
        with self.edit(sheet, revision) as record:
            settings = record["settings"]
            seam = images.locate_zone(settings, seam_id)
            if seam is None:
                raise Invalid("Select the seam inspection zone to trap.")
            reserve = images.locate_zone(settings, reserve_id) if reserve_id else None
            a, b, _ = self.pixels(record)
            plans, recommended, protected = traps.build_plans(a, b, threshold, seam, reserve)
            chosen = next(plan for plan in plans if plan["plan"] == plan_id)
            if not chosen["feasible"]:
                raise Invalid("That plan cannot seal the seam across the shift grid; "
                              "choose a plan marked usable.")
            trap_id = uuid4().hex[:16]
            directory = self.directory(sheet)
            mask_a, mask_b = traps.mask_png(chosen["mask_a"], chosen["mask_b"])
            mask_a_name, mask_b_name = f"trap-{trap_id}-a.png", f"trap-{trap_id}-b.png"
            document = {
                "id": trap_id,
                "created_at": timestamp(),
                "plan": plan_id,
                "name": traps.PLAN_NAMES[plan_id],
                "threshold": int(threshold),
                "dx_limit": traps.DX_LIMIT,
                "dy_limit": traps.DY_LIMIT,
                "shifts": [list(item) for item in traps.SHIFT_GRID],
                "seam": {"id": seam_id, "name": seam["name"], "bounds": seam["bounds"]},
                "reserve": None if reserve is None else {"id": reserve_id, "name": reserve["name"],
                                                          "bounds": reserve["bounds"]},
                "recommended": recommended,
                "plans": [{k: v for k, v in plan.items() if not k.startswith("mask_")} for plan in plans],
                "mask_a": mask_a_name,
                "mask_b": mask_b_name,
                "protected_paper": int(np.count_nonzero(protected)),
            }
            write(directory / mask_a_name, mask_a.getvalue())
            write(directory / mask_b_name, mask_b.getvalue())
            put(self.trap_path(record, trap_id), document)
            # Older trap files are retained: saved proofs pin their trap id and
            # must keep rendering the exact plates they were archived with.
            record["trap"] = trap_id
        return self.sheet(sheet), document

    def clear_trap(self, sheet, revision):
        with self.edit(sheet, revision) as record:
            record["trap"] = None
        return self.sheet(sheet)

    def trapped_pixels(self, record):
        """Original-coordinate coverage arrays with the active trap burned in."""
        a, b, _ = self.pixels(record)
        trap = self.active_trap(record)
        if trap is not None:
            a, b = traps.trapped_plates(a, b, trap["add_a"], trap["add_b"])
        return a, b, trap

    def photo_path(self, record):
        photo = record.get("photo")
        return self.directory(record["id"]) / f'photo-{photo["sha256"]}.png' if photo else None

    def pixels(self, record, with_photo=False):
        a, b = [255 - images.decode(path.read_bytes(), plate=True) for path in self.plate_paths(record)]
        photo = self.photo_path(record) if with_photo else None
        return a, b, images.decode(photo.read_bytes()) if photo else None

    def reports(self, sheet):
        paths = self.directory(sheet).glob("proof-*.json")
        rows = [read(path) for path in paths]
        return sorted(rows, key=lambda row: (row["created_at"], row["id"]), reverse=True)

    def report(self, sheet, report):
        return read(self.directory(sheet) / f"proof-{available_key(report)}.json")

    def save_report(self, sheet, revision, changes, zone, point, title):
        with self.lock:
            record = self.sheet(sheet)
            if integer(revision, 0, LIMIT, "Sheet revision") != record["revision"]:
                raise Conflict("The sheet changed. Open it again before saving this proof.")
            record["settings"] = settings(record, changes)
            if point is not None:
                point = coordinates(point, record["width"], record["height"])
            if zone is not None and images.locate_zone(record["settings"], zone) is None:
                raise Invalid("That inspection zone is not in the current setup.")
            key = uuid4().hex[:16]
            proof = {"id": key, "title": name(title, "Registration proof"), "created_at": timestamp(),
                     "sheet": record, "zone": zone, "point": point, "trap": record.get("trap")}
            put(self.directory(sheet) / f"proof-{key}.json", proof)
            return proof
