"""Integrated print-proof page and separation and reference-image routes."""

import csv
from io import BytesIO, StringIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

from image_lab import create_app as create_preparation
from image_lab.validation import Invalid

from . import images, state


def body():
    payload = request.get_json(silent=True)
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
        options = record["settings"] if stored else state.settings(record, body().get("settings", {}))
        a, b, photo = library.pixels(record, with_photo=view in {"photo", "difference"})
        if view in {"photo", "difference"} and photo is None:
            raise Invalid("Add a proof photo to use this view.")
        return image_response(BytesIO(images.png(images.extract_view(a, b, photo, options, view))), f"{view}.png")

    @desk.post("/sheets/<sheet>/measure")
    def measure(sheet):
        record, stored = context(sheet)
        payload = body()
        options = record["settings"] if stored else state.settings(record, payload.get("settings", {}))
        point = payload.get("point")
        if point is not None:
            point = state.coordinates(point, record["width"], record["height"])
        a, b, _ = library.pixels(record)
        return jsonify(images.measure(a, b, options, payload.get("zone"), point))

    @desk.get("/sheets/<sheet>/separation/<plate>.png")
    def separation(sheet, plate):
        if plate not in {"a", "b"}:
            raise Invalid("Choose plate A or B.")
        record, _ = context(sheet)
        path = library.plate_paths(record)[plate == "b"]
        return image_response(path, f"separation-{plate}.png", "download" in request.args)

    @desk.get("/sheets/<sheet>/photo.png")
    def get_photo(sheet):
        record, _ = context(sheet)
        path = library.photo_path(record)
        if not path:
            raise Invalid("This setup has no proof photo.")
        return image_response(path, "proof-photo.png", "download" in request.args)

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
        a, b, _ = library.pixels(record)
        options = record["settings"]
        content = images.proof(a, images.translate(b, options["dx"], options["dy"]), options)
        return image_response(BytesIO(images.png(content)), "registration-proof.png", "download" in request.args)

    @desk.get("/sheets/<sheet>/reports/<report>/coverage.csv")
    def download_coverage(sheet, report):
        record, saved = context(sheet, report)
        a, b, _ = library.pixels(record)
        data = images.measure(a, b, record["settings"], saved["zone"], saved["point"])
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["paper", "warm", "cool", "overlap", "new_paper", "changed", "pixels"])
        writer.writerow([*data["classes"], data["new_paper"], data["changed"], data["pixels"]])
        return send_file(BytesIO(output.getvalue().encode()), mimetype="text/csv", as_attachment=True,
                         download_name="proof-coverage.csv", max_age=0)

    app.register_blueprint(desk)
    return app
