"""Checks for half-pixel cool-plate readings, adoption and probe provenance."""

import io
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pressdesk import fit, images, state, web  # noqa: E402


SETTINGS = {"dx": 0, "dy": 0, "threshold": 128, "ink_a": "#d45436",
            "ink_b": "#287f8d", "paper": "#f1e8d6", "zones": []}


def scene(main=(2.0, -1.0), fine=(3.5, 0.0), fine_line=True):
    height, width = 160, 260
    warm = np.zeros((height, width), np.float32)
    warm[35:70, 40:140] = 0.9
    cool = np.zeros((height, width), np.float32)
    cool[20:125, 25:120] = 1.0
    if fine_line:
        cool[70:72, 135:230] = 0.35
    moved = np.zeros_like(cool)
    for mask, shift in (((slice(20, 125), slice(25, 120)), main),
                        ((slice(70, 72), slice(135, 230)), fine)):
        part = np.zeros_like(cool)
        part[mask] = cool[mask]
        if part.any():
            moved += ndimage.shift(part, (shift[1], shift[0]), order=1, mode="constant")
    paper = np.array([0.945, 0.910, 0.839], np.float32)
    warm_ink = np.array([0.831, 0.329, 0.212], np.float32)
    cool_ink = np.array([0.157, 0.498, 0.553], np.float32)
    base = paper * (1 - warm[..., None] * (1 - warm_ink))
    photo = base * (1 - moved[..., None] * (1 - cool_ink))
    return warm * 255, cool * 255, np.rint(photo * 255).clip(0, 255).astype(np.uint8)


def test_half_pixel_reading():
    warm, cool, photo = scene()
    result = fit.analyse(warm, cool, photo, SETTINGS, [0, 0, 260, 160], (0, 0))
    assert result["ok"]
    assert result["shift"] == {"x": 2.0, "y": -0.5}
    assert result["adoptable"]
    for structure in ("main", "fine"):
        assert result["structures"][structure]["max"] <= 1.0
    assert result["segments"] == []


def test_axes_without_evidence_are_named():
    height, width = 160, 260
    warm = np.zeros((height, width), np.float32)
    warm[35:70, 40:140] = 0.9
    cool = np.zeros((height, width), np.float32)
    cool_only_line = np.zeros((height, width), np.float32)
    cool_only_line[70:72, 135:230] = 0.35
    shifted = ndimage.shift(cool_only_line, (0.5, 2.0), order=1, mode="constant")
    paper = np.array([0.945, 0.910, 0.839], np.float32)
    warm_ink = np.array([0.831, 0.329, 0.212], np.float32)
    cool_ink = np.array([0.157, 0.498, 0.553], np.float32)
    base = paper * (1 - (warm / 255)[..., None] * (1 - warm_ink))
    photo = np.rint(base * (1 - shifted[..., None] * (1 - cool_ink)) * 255).clip(0, 255).astype(np.uint8)
    result = fit.analyse(warm * 255, cool_only_line * 255, photo, SETTINGS, [0, 0, 260, 160], (0, 0))
    assert result["ok"]
    assert result["free"] == {"x": False, "y": True}
    assert result["missing"] == ["x"]
    assert result["shift"]["x"] is None
    assert result["shift"]["y"] == 0.5


def test_structures_beyond_a_pixel_are_not_adoptable():
    warm, cool, photo = scene(main=(1.0, 0.0), fine=(1.8, 2.8))
    result = fit.analyse(warm, cool, photo, SETTINGS, [0, 0, 260, 160], (0, 0))
    assert result["ok"]
    assert not result["adoptable"]
    assert result["segments"]
    assert {segment["structure"] for segment in result["segments"]} <= {"main", "fine"}


def test_evaluate_keeps_actual_half_pixel_shift():
    warm, cool, photo = scene(main=(2.5, 1.5), fine=(2.5, 1.5))
    result = fit.analyse(warm, cool, photo, SETTINGS, [0, 0, 260, 160], (2.5, 1.5), evaluate=True)
    assert result["mode"] == "evaluate"
    assert result["shift"] == {"x": 2.5, "y": 1.5}
    assert result["adoptable"]


def test_translate_and_probe_provenance():
    plate = np.zeros((6, 6), np.uint8)
    plate[2, 2] = 255
    moved = images.translate(plate, 0.5, 0.0)
    assert moved[2, 2] == pytest_approx(127.5)
    source_x, source_y, entries, paper = images.source_weights(plate, 0.5, 0.0, 2, 2)
    assert (source_x, source_y) == (1.5, 2.0)
    assert paper == 0.0
    assert sorted((entry["x"], entry["weight"]) for entry in entries) == [(1, 0.5), (2, 0.5)]
    _, _, outside, outside_paper = images.source_weights(plate, 8.0, 0.0, 1, 1)
    assert outside == []
    assert outside_paper == 1.0


def pytest_approx(value):
    return round(value, 3)


def test_settings_accept_half_steps_and_reject_others():
    record = {"settings": dict(SETTINGS), "width": 20, "height": 20, "photo": None}
    accepted = state.settings(record, {"dx": 1.5, "dy": -2.5})
    assert (accepted["dx"], accepted["dy"]) == (1.5, -2.5)
    for bad in (1.3, "2", True):
        try:
            state.settings(record, {"dx": bad})
        except Exception as error:
            assert "half-pixel" in str(error)
        else:
            raise AssertionError("non-half-pixel offset accepted")


def test_http_flow():
    with TemporaryDirectory() as root:
        app = web.create_app(Path(root))
        client = app.test_client()
        record = client.post("/press/demo").get_json()
        sheet = record["id"]
        warm, cool = images.demo()
        moved = ndimage.shift(cool.astype(np.float32), (2.5, 3.5), order=1, mode="constant")
        photo = images.proof(warm, moved, record["settings"])
        buffer = io.BytesIO()
        Image.fromarray(photo).save(buffer, "PNG")
        buffer.seek(0)
        record = client.post(f"/press/sheets/{sheet}/photo",
                             data={"revision": record["revision"], "photo": (buffer, "proof.png")},
                             content_type="multipart/form-data").get_json()
        reply = client.post(f"/press/sheets/{sheet}/recommend",
                            json={"settings": record["settings"], "bounds": [40, 30, 560, 360]})
        assert reply.status_code == 200
        reading = reply.get_json()
        assert reading["shift"] == {"x": 3.5, "y": 2.5}
        assert reading["adoptable"]
        saved = client.put(f"/press/sheets/{sheet}/settings",
                           json={"revision": record["revision"],
                                 "settings": {**record["settings"], "dx": 3.5, "dy": 2.5,
                                              "adopted": {"dx": 3.5, "dy": 2.5,
                                                          "photo": record["photo"]["sha256"]}}})
        assert saved.status_code == 200
        assert saved.get_json()["settings"]["adopted"]["dx"] == 3.5
        probe = client.post(f"/press/sheets/{sheet}/measure",
                            json={"settings": record["settings"], "point": [2, 2]}).get_json()["probe"]
        assert probe["cool_sources"] and probe["cool_paper"] == 0.0
        outside = client.post(f"/press/sheets/{sheet}/measure",
                              json={"settings": {**record["settings"], "dx": 32.0},
                                    "point": [2, 2]}).get_json()["probe"]
        assert outside["outside"] and outside["cool_paper"] == 1.0
        view = client.post(f"/press/sheets/{sheet}/view/proof", json={"settings": record["settings"]})
        assert view.status_code == 200 and view.content_type == "image/png"


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_"):
            value()
            print("ok", name)
