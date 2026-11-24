"""Integrated print-proof page and separation and reference-image routes."""

import csv
from io import BytesIO, StringIO
from pathlib import Path

import numpy as np
from flask import Blueprint, jsonify, render_template, request, send_file

from image_lab import create_app as create_preparation
from image_lab.validation import Invalid

from . import images, state, traps


def body():
    payload = request.get_json(silent=True)
    if payload is None and not request.data:
        return {}
    if not isinstance(payload, dict):
        raise Invalid("Send a JSON object for this operation.")
    return payload


def upload(field, plate=False):
    file = request.files.get(field)
    if file is None or not file.filename:
        raise Invalid("Choose both plate files." if plate else "Choose a proof photo.")
    return images.decode(file.stream.read(images.MAX_UPLOAD + 1), plate), Path(file.filename).name


def image_response(data, name, download=False):
    return send_file(data, mimetype="image/png", as_attachment=download, download_name=name, max_age=0)


def create_app(root):
    app = create_preparation(root / "prepare")
    library = state.Library(root)
    desk = Blueprint("press", __name__, url_prefix="/press")

    def context(sheet, report=None):
        report = report or request.args.get("report")
        if report:
            saved = library.report(sheet, report)
            return saved["sheet"], saved
        return library.sheet(sheet), None

    def preview_trap(record, preview):
        if not isinstance(preview, dict):
            raise Invalid("The trap preview must describe a plan and two zones.")
        plan_id = preview.get("plan")
        if plan_id not in traps.PLANS:
            raise Invalid("Choose one of the listed trap plans.")
        seam = images.locate_zone(record["settings"], preview.get("seam"))
        if seam is None:
            raise Invalid("Select the seam inspection zone to trap.")
        reserve_id = preview.get("reserve")
        reserve = images.locate_zone(record["settings"], reserve_id) if reserve_id else None
        threshold = state.integer(preview.get("threshold", record["settings"]["threshold"]),
                                  1, 255, "Ink threshold")
        a, b, _ = library.pixels(record)
        plans, _, protected = traps.build_plans(a, b, threshold, seam, reserve)
        chosen = next(plan for plan in plans if plan["plan"] == plan_id)
        return {"document": None, "add_a": chosen["mask_a"], "add_b": chosen["mask_b"],
                "protected": protected, "threshold": threshold}

    def resolve_trap(record, stored, payload):
        """Trap masks for the pinned saved proof, the live sheet, or a preview plan."""
        if stored is not None:
            return library.active_trap(record)
        active = library.active_trap(record)
        if active is not None:
            return active
        preview = payload.get("trap_preview")
        return preview_trap(record, preview) if preview else None

    def trap_overlay(record, trap):
        if trap is None:
            raise Invalid("Pick a trap plan to preview.")
        a, b, _ = library.pixels(record)
        threshold = trap.get("threshold")
        if threshold is None and trap["document"] is not None:
            threshold = trap["document"]["threshold"]
        threshold = threshold or record["settings"]["threshold"]
        am, bm = a >= threshold, b >= threshold
        protected = trap.get("protected")
        if protected is None:
            document = trap["document"]
            seam_m = traps.zone_mask(document["seam"], am.shape)
            reserve_m = traps.zone_mask(document["reserve"], am.shape)
            protected = traps.protected_paper(am, bm, seam_m, reserve_m)
        return traps.overlay(am, bm, trap["add_a"], trap["add_b"], protected)

    @app.get("/")
    def home():
        return render_template("pressdesk.html")

    @desk.get("/health")
    def health():
        return jsonify(status="ready", service="pressdesk")

    @desk.get("/sheets")
    def list_sheets():
        return jsonify(library.list())

    @desk.post("/demo")
    def sample():
        a, b = images.demo()
        return jsonify(library.create("Estuary · synthetic proof", a, b,
                                      ["Estuary warm.png", "Estuary cool.png"], synthetic=True)), 201

    @desk.post("/sheets")
    def new_sheet():
        a, a_name = upload("plate_a", plate=True)
        b, b_name = upload("plate_b", plate=True)
        return jsonify(library.create(request.form.get("title"), 255 - a, 255 - b, [a_name, b_name])), 201

    @desk.get("/sheets/<sheet>")
    def open_sheet(sheet):
        return jsonify(library.sheet(sheet))

    @desk.put("/sheets/<sheet>/settings")
    def save(sheet):
        payload = body()
        return jsonify(library.save(sheet, payload.get("revision"), payload.get("settings", {})))

    @desk.post("/sheets/<sheet>/photo")
    def photo(sheet):
        image, name = upload("photo")
        try:
            revision = int(request.form.get("revision", ""))
        except ValueError:
            raise Invalid("Supply the current sheet revision.") from None
        return jsonify(library.add_photo(sheet, revision, image, name))

    @desk.post("/sheets/<sheet>/prepared")
    def prepared(sheet):
        payload = body()
        project, result = payload.get("project"), payload.get("result")
        if not isinstance(project, str) or not isinstance(result, str):
            raise Invalid("Select a completed preparation result.")
        preparation = app.extensions["results"]
        meta = preparation.metadata(project, result)
        if meta["stale"]:
            raise Invalid("Recompute the preparation result before importing it as a proof photo.")
        source = preparation.image_path(project, "image", result)
        photo = images.decode(source.read_bytes())
        return jsonify(library.add_photo(sheet, payload.get("revision"), photo, "Prepared proof photo",
                                         origin={"project": project, "result": result}))

    @desk.post("/sheets/<sheet>/view/<view>")
    def get_view(sheet, view):
        if view not in images.MODES:
            raise Invalid("Choose a view in the proof toolbar.")
        record, stored = context(sheet)
        payload = body()
        options = record["settings"] if stored else state.settings(record, payload.get("settings", {}))
        trap = resolve_trap(record, stored, payload)
        a, b, photo = library.pixels(record, with_photo=view in {"photo", "difference"})
        if view in {"photo", "difference"} and photo is None:
            raise Invalid("Add a proof photo to use this view.")
        if view == "trap-overlay":
            content = trap_overlay(record, trap)
        else:
            masks = (trap["add_a"], trap["add_b"]) if trap else None
            content = images.extract_view(a, b, photo, options, view, masks)
        return image_response(BytesIO(images.png(content)), f"{view}.png")

    @desk.post("/sheets/<sheet>/measure")
    def measure(sheet):
        record, stored = context(sheet)
        payload = body()
        options = record["settings"] if stored else state.settings(record, payload.get("settings", {}))
        point = payload.get("point")
        if point is not None:
            point = state.coordinates(point, record["width"], record["height"])
        trap = resolve_trap(record, stored, payload)
        a, b, _ = library.pixels(record)
        masks = (trap["add_a"], trap["add_b"]) if trap else None
        return jsonify(images.measure(a, b, options, payload.get("zone"), point, masks))

    @desk.get("/sheets/<sheet>/separation/<plate>.png")
    def separation(sheet, plate):
        if plate not in {"a", "b"}:
            raise Invalid("Choose plate A or B.")
        record, stored = context(sheet)
        trap = library.active_trap(record)
        if trap is None:
            path = library.plate_paths(record)[plate == "b"]
            return image_response(path, f"separation-{plate}.png", "download" in request.args)
        a, b, _ = library.pixels(record)
        additions = trap["add_a"] if plate == "a" else trap["add_b"]
        coverage = np.where(additions, 255, a if plate == "a" else b)
        return image_response(BytesIO(images.png(255 - coverage)),
                              f"separation-{plate}.png", "download" in request.args)

    @desk.get("/sheets/<sheet>/photo.png")
    def get_photo(sheet):
        record, _ = context(sheet)
        path = library.photo_path(record)
        if not path:
            raise Invalid("This setup has no proof photo.")
        return image_response(path, "proof-photo.png", "download" in request.args)

    @desk.post("/sheets/<sheet>/traps/preview")
    def trap_preview(sheet):
        record, _ = context(sheet)
        payload = body()
        seam_id = payload.get("seam")
        seam = images.locate_zone(record["settings"], seam_id)
        if seam is None:
            raise Invalid("Select the seam inspection zone to trap.")
        reserve_id = payload.get("reserve")
        if reserve_id:
            reserve = images.locate_zone(record["settings"], reserve_id)
            if reserve is None:
                raise Invalid("The paper-hairline zone is not in the current setup.")
        else:
            reserve = None
        threshold = state.integer(payload.get("threshold", record["settings"]["threshold"]),
                                  1, 255, "Ink threshold")
        a, b, _ = library.pixels(record)
        plans, recommended, protected = traps.build_plans(a, b, threshold, seam, reserve)
        return jsonify({
            "recommended": recommended,
            "protected_paper": int(np.count_nonzero(protected)),
            "shifts": [list(item) for item in traps.SHIFT_GRID],
            "plans": [{k: v for k, v in plan.items() if not k.startswith("mask_")} for plan in plans],
        })

    @desk.get("/sheets/<sheet>/traps/<trap_id>")
    def get_trap(sheet, trap_id):
        record, _ = context(sheet)
        return jsonify(library.load_trap(record, trap_id)["document"])

    @desk.post("/sheets/<sheet>/traps")
    def adopt_trap(sheet):
        payload = body()
        record, document = library.create_trap(
            sheet, payload.get("revision"), payload.get("plan"),
            payload.get("seam"), payload.get("reserve"),
            state.integer(payload.get("threshold", 128), 1, 255, "Ink threshold"))
        return jsonify({"sheet": record, "trap": document}), 201

    @desk.delete("/sheets/<sheet>/traps")
    def remove_trap(sheet):
        payload = body()
        return jsonify(library.clear_trap(sheet, payload.get("revision")))

    @desk.get("/sheets/<sheet>/reports")
    def reports(sheet):
        return jsonify(library.reports(sheet))

    @desk.post("/sheets/<sheet>/reports")
    def save_report(sheet):
        payload = body()
        return jsonify(library.save_report(sheet, payload.get("revision"), payload.get("settings", {}),
                                           payload.get("zone"), payload.get("point"), payload.get("title"))), 201

    @desk.get("/sheets/<sheet>/reports/<report>/proof.png")
    def download_proof(sheet, report):
        record, _ = context(sheet, report)
        a, b, trap = library.trapped_pixels(record)
        options = record["settings"]
        content = images.proof(a, images.translate(b, options["dx"], options["dy"]), options)
        return image_response(BytesIO(images.png(content)), "registration-proof.png", "download" in request.args)

    @desk.get("/sheets/<sheet>/reports/<report>/coverage.csv")
    def download_coverage(sheet, report):
        record, saved = context(sheet, report)
        a, b, trap = library.trapped_pixels(record)
        masks = (trap["add_a"], trap["add_b"]) if trap else None
        data = images.measure(a, b, record["settings"], saved["zone"], saved["point"], masks)
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["paper", "warm", "cool", "overlap", "new_paper", "changed", "pixels"])
        writer.writerow([*data["classes"], data["new_paper"], data["changed"], data["pixels"]])
        return send_file(BytesIO(output.getvalue().encode()), mimetype="text/csv", as_attachment=True,
                         download_name="proof-coverage.csv", max_age=0)

    app.register_blueprint(desk)
    return app
