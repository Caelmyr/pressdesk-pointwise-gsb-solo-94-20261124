"use strict";

const el = id => document.getElementById(id);
const fieldNames = ["dx", "dy", "threshold", "ink_a", "ink_b", "paper"];
const classLabels = ["Paper", "Warm only", "Cool only", "Overlap"];
const classColors = ["#e6dcc7", "#e57d59", "#358191", "#2d3449"];
const viewCopy = {
  proof: "Warm and cool ink on paper", "plate-a": "Unshifted warm plate",
  "plate-b": "Unshifted cool plate", coverage: "Paper, warm, cool and overprint",
  drift: "Red = newly exposed paper · Blue = changed ink class",
  photo: "Reference photo in sheet coordinates", difference: "Brightness shows the largest RGB channel difference",
};
const precision = value => (Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1));
const S = {sheet: null, sheets: [], settings: null, point: null, zone: "", report: null, reports: [],
  view: "proof", tool: "", first: null, objectUrl: null, sequence: 0, pendingZone: null,
  reading: null, adopted: false};

async function request(path, method = "GET", data) {
  const options = {method};
  if (data instanceof FormData) options.body = data;
  else if (data !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(data);
  }
  const response = await fetch("/press" + path, options);
  if (!response.ok) {
    const message = await response.json().catch(() => ({}));
    throw new Error(message.error || "Request failed (" + response.status + ").");
  }
  const type = response.headers.get("Content-Type") || "";
  return type.includes("application/json") ? response.json() : response.blob();
}

function status(message, error = false) {
  el("status").hidden = !message;
  el("status").textContent = message;
  el("status").classList.toggle("error", error);
}

function action(task) {
  return async event => {
    try { await task(event); }
    catch (error) { status(error.message, true); }
  };
}

function route(suffix = "") {
  return "/sheets/" + S.sheet.id + suffix;
}

function reportQuery() {
  return S.report ? "?report=" + S.report.id : "";
}

function option(value, label) {
  return new Option(label, value);
}

function settingsToFields() {
  fieldNames.forEach(key => { el(key).value = key === "dx" || key === "dy" ? precision(S.settings[key]) : S.settings[key]; });
}

function readFields() {
  const changed = {...S.settings};
  fieldNames.forEach(key => {
    if (!el(key).checkValidity() || !el(key).value) throw new Error("Check " + el(key).labels[0].textContent.trim() + ".");
    changed[key] = el(key).type === "number" ? Number(el(key).value) : el(key).value;
  });
  const adopted = changed.adopted;
  if (adopted && (adopted.dx !== changed.dx || adopted.dy !== changed.dy)) delete changed.adopted;
  return changed;
}

function showViews() {
  for (const button of el("views").querySelectorAll("button")) {
    const view = button.dataset.view;
    button.hidden = !S.sheet.photo && ["photo", "difference"].includes(view);
    button.classList.toggle("active", view === S.view);
    button.setAttribute("aria-pressed", String(view === S.view));
  }
  el("viewHint").textContent = viewCopy[S.view];
}

function setTool(tool) {
  S.tool = tool;
  S.first = null;
  el("imageWrap").classList.toggle("interactive", Boolean(tool));
  el("probeTool").classList.toggle("active", tool === "probe");
  el("zoneTool").classList.toggle("active", tool === "zone");
}

function mark(tag, values) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(values)) element.setAttribute(key, value);
  return element;
}

function drawMarks() {
  const overlay = el("marks");
  overlay.replaceChildren();
  overlay.setAttribute("viewBox", "0 0 " + S.sheet.width + " " + S.sheet.height);
  const zone = S.settings.zones.find(item => item.id === S.zone);
  if (zone) {
    const [x, y, x1, y1] = zone.bounds;
    overlay.append(mark("rect", {x, y, width: x1 - x, height: y1 - y,
      fill: "none", stroke: "#fffdf7", "stroke-width": 2, "vector-effect": "non-scaling-stroke"}));
    overlay.append(mark("rect", {x, y, width: x1 - x, height: y1 - y,
      fill: "none", stroke: "#2a3a3b", "stroke-width": 1, "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke"}));
  }
  drawEdgeMarks(overlay);
  if (S.point || S.first) {
    const [x, y] = S.first || S.point;
    overlay.append(mark("circle", {cx: x + .5, cy: y + .5, r: 4, fill: "#273c38", stroke: "#fff", "stroke-width": 2, "vector-effect": "non-scaling-stroke"}));
  }
}

function drawEdgeMarks(overlay) {
  const reading = S.reading;
  const segments = reading?.segments || [];
  el("edgeLegend").hidden = !segments.length;
  for (const segment of segments) {
    const color = segment.structure === "fine" ? "#d9a126" : "#b8402d";
    overlay.append(mark("line", {x1: segment.x0, y1: segment.y0, x2: segment.x1, y2: segment.y1,
      stroke: "#fffdf7", "stroke-width": 4, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round"}));
    overlay.append(mark("line", {x1: segment.x0, y1: segment.y0, x2: segment.x1, y2: segment.y1,
      stroke: color, "stroke-width": 2, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round",
      "stroke-dasharray": segment.structure === "fine" ? "3 2" : "none"}));
  }
}

function showZones() {
  el("zoneSelect").replaceChildren(option("", "Whole sheet"),
    ...S.settings.zones.map(zone => option(zone.id, zone.name)));
  el("zoneSelect").value = S.zone;
}

function addLink(label, suffix) {
  const link = document.createElement("a");
  link.textContent = label;
  link.href = "/press" + route(suffix);
  link.download = "";
  el("downloads").append(link);
}

function showArchive() {
  el("reportSelect").replaceChildren(option("", "Live setup"),
    ...S.reports.map(report => option(report.id, report.title)));
  el("reportSelect").value = S.report?.id || "";
  el("downloads").replaceChildren();
  const note = S.report ? "report=" + S.report.id + "&" : "";
  addLink("Warm PNG", "/separation/a.png?" + note + "download");
  addLink("Cool PNG", "/separation/b.png?" + note + "download");
  if (S.sheet.photo) addLink("Photo PNG", "/photo.png?" + note + "download");
  if (S.report) {
    addLink("Proof PNG", "/reports/" + S.report.id + "/proof.png?download");
    addLink("Coverage CSV", "/reports/" + S.report.id + "/coverage.csv");
  }
  const frozen = Boolean(S.report);
  fieldNames.concat(["apply", "saveSetup", "saveProof", "zoneTool", "addPhoto",
    "readOffset", "adoptOffset"]).forEach(id => { el(id).disabled = frozen; });
}

function showPointSource(p) {
  const parts = [];
  if (p.outside) {
    parts.push("This point lies outside the original cool plate; its cool ink counts as paper white.");
  } else {
    const shares = (p.cool_sources || []).map(source =>
      "<b>(" + source.x + ", " + source.y + ")</b> " + Math.round(source.weight * 100) + "% = " + source.value);
    if (shares.length) parts.push("Cool ink here came from " + shares.join(" · "));
    if (p.cool_paper > 0) parts.push('<span class="paper">' + Math.round(p.cool_paper * 100) + "% paper white from outside the plate</span>");
  }
  el("pointSource").innerHTML = parts.join("<br>") || "No cool-ink source at this point.";
}

function readOffsetLabel(value) {
  return value === null || value === undefined ? "no evidence" : (value > 0 ? "+" : "") + precision(value);
}

function showReading(data) {
  S.reading = data.ok ? data : null;
  el("adoptOffset").disabled = !(data.ok && data.adoptable && data.mode !== "evaluate");
  el("offsetRead").classList.remove("warn", "ready");
  const detail = el("offsetDetail");
  detail.replaceChildren();
  if (!data.ok) {
    el("offsetRead").textContent = data.message || "No half-pixel reading from this zone.";
    el("offsetRead").classList.add("warn");
    el("edgeLegend").hidden = true;
    drawMarks();
    return;
  }
  const adopting = data.mode === "evaluate";
  const sx = data.shift.x, sy = data.shift.y;
  const lead = adopting
    ? "Actual print at X " + readOffsetLabel(data.current.x) + ", Y " + readOffsetLabel(data.current.y) + "."
    : "Suggested cool-plate shift X " + readOffsetLabel(sx) + ", Y " + readOffsetLabel(sy) + " (half-pixel grid).";
  el("offsetRead").innerHTML = "<strong>" + lead + "</strong>" +
    (data.message ? " " + data.message : "");
  if (data.adoptable && !adopting) el("offsetRead").classList.add("ready");
  else if (!data.adoptable) el("offsetRead").classList.add("warn");
  const rows = [];
  const axisRow = (axis, key) => {
    const item = data.axes[key];
    rows.push([axis + " axis", item.evidence ? (item.points + " cool-edge points") : "no evidence"]);
  };
  axisRow("X", "x");
  axisRow("Y", "y");
  for (const [key, label] of [["main", "Main contour"], ["fine", "Fine line"]]) {
    const item = data.structures[key];
    if (!item || item.max === null) continue;
    rows.push([label + " worst", item.max.toFixed(2) + " px over " + item.points + " points"]);
  }
  rows.push(["Adopt rule", "main and fine both ≤ " + data.limit.toFixed(1) + " px"]);
  for (const [label, value] of rows) {
    const row = document.createElement("span");
    const name = document.createElement("em");
    name.textContent = label;
    const output = document.createElement("b");
    output.textContent = value;
    row.append(name, output);
    detail.append(row);
  }
  detail.hidden = false;
  drawMarks();
}

function setAdopted(adopted) {
  S.adopted = adopted;
  el("readOffset").textContent = adopted ? "Check landed ink" : "Read cool offset";
}

function applyReading() {
  const data = S.reading;
  if (!data || !data.adoptable) return;
  const x = data.shift.x ?? Number(el("dx").value);
  const y = data.shift.y ?? Number(el("dy").value);
  S.settings.dx = x;
  S.settings.dy = y;
  S.settings.adopted = {dx: x, dy: y, photo: S.sheet.photo?.sha256 || null};
  settingsToFields();
  setAdopted(true);
  el("adoptOffset").disabled = true;
  el("offsetRead").classList.add("ready");
}

function showMeasurement(data) {
  el("coverageBar").replaceChildren();
  el("coverageGrid").replaceChildren();
  const values = [];
  data.classes.forEach((count, position) => {
    const percent = 100 * count / data.pixels;
    const segment = document.createElement("span");
    segment.style.width = percent + "%";
    segment.style.background = classColors[position];
    el("coverageBar").append(segment);
    const entry = document.createElement("div");
    const label = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.background = classColors[position];
    label.append(swatch, classLabels[position]);
    const output = document.createElement("b");
    output.textContent = percent.toFixed(1) + "%";
    entry.append(label, output);
    el("coverageGrid").append(entry);
    values.push(classLabels[position] + " " + output.textContent);
  });
  el("coverageBar").setAttribute("aria-label", values.join(", "));
  el("shiftSummary").textContent = data.new_paper.toLocaleString() + " newly exposed paper · " +
    data.changed.toLocaleString() + " changed pixels";
  el("profile").hidden = !data.probe;
  if (!data.probe) return;
  const p = data.probe;
  const cool = p.outside ? " · cool from paper white" : "";
  el("profileTitle").textContent = "Coverage along row " + p.y;
  el("pointRead").textContent = "X " + p.x + " · warm " + p.a + " · cool " + p.b + cool;
  showPointSource(p);
  const canvas = el("profileCanvas"), context = canvas.getContext("2d");
  const width = canvas.width, height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.strokeStyle = "#d9d4c7";
  context.lineWidth = 1;
  for (const level of [0, 128, 255]) {
    const y = height - 3 - level * (height - 6) / 255;
    context.beginPath();
    context.moveTo(0, y);
    context.lineTo(width, y);
    context.stroke();
  }
  for (const [key, ink] of [["a", "#d45436"], ["b", "#287f8d"]]) {
    const plot = data.profile;
    context.strokeStyle = ink;
    context.lineWidth = 2;
    context.beginPath();
    plot.x.forEach((pixel, index) => {
      const x = pixel * (width - 1) / (S.sheet.width - 1);
      const y = height - 3 - plot[key][index] * (height - 6) / 255;
      if (index) context.lineTo(x, y);
      else context.moveTo(x, y);
    });
    context.stroke();
  }
  const x = p.x * (width - 1) / (S.sheet.width - 1);
  context.beginPath();
  context.moveTo(x, 0);
  context.lineTo(x, height);
  context.strokeStyle = "#3d4843";
  context.stroke();
}

async function refresh(image = true) {
  const sequence = ++S.sequence;
  const payload = {settings: S.settings, zone: S.zone || null, point: S.point};
  const query = reportQuery();
  const measure = request(route("/measure") + query, "POST", payload);
  const view = image ? request(route("/view/" + S.view) + query, "POST", payload) : null;
  const [data, blob] = await Promise.all([measure, view]);
  if (sequence !== S.sequence) return;
  showMeasurement(data);
  if (blob) {
    if (S.objectUrl) URL.revokeObjectURL(S.objectUrl);
    S.objectUrl = URL.createObjectURL(blob);
    el("sheetView").src = S.objectUrl;
    el("sheetView").alt = viewCopy[S.view];
  }
  drawMarks();
}

function readingZone() {
  const zone = S.settings.zones.find(item => item.id === S.zone);
  if (zone) return zone.bounds;
  if (S.pendingZone) return S.pendingZone.bounds;
  return null;
}

async function readOffset(evaluate = false) {
  const bounds = readingZone();
  if (!bounds) {
    status("Mark a zone across the cool contour first, then read the offset.", true);
    return;
  }
  if (!S.sheet.photo) {
    status("Add the trial proof photo before reading the cool plate.", true);
    return;
  }
  evaluate = evaluate || S.adopted;
  if (evaluate) await refresh(false);
  const data = await request(route("/recommend") + reportQuery(), "POST",
    {settings: S.settings, bounds, evaluate});
  showReading(data);
  if (data.ok) status("");
  else status(data.message || "No half-pixel reading from this zone.", true);
}

async function getSheets() {
  S.sheets = await request("/sheets");
  el("sheetSelect").replaceChildren(...S.sheets.map(sheet => option(sheet.id, sheet.title)));
  if (S.sheet) el("sheetSelect").value = S.sheet.id;
}

async function openSheet(sheet, view = "proof") {
  S.sheet = sheet;
  S.settings = structuredClone(sheet.settings);
  S.view = view;
  S.report = null;
  S.point = null;
  S.zone = "";
  S.reading = null;
  setAdopted(Boolean(sheet.settings.adopted));
  setTool("");
  localStorage.setItem("pressdesk-sheet", sheet.id);
  el("dimensions").textContent = sheet.width + " × " + sheet.height + " px" + (sheet.synthetic ? " · SYNTHETIC" : "");
  el("workspace").hidden = false;
  el("empty").hidden = true;
  el("sheetSelect").value = sheet.id;
  S.reports = await request(route("/reports"));
  settingsToFields();
  showZones();
  showViews();
  showArchive();
  await refresh();
}

function openDialog(id) {
  const dialog = el(id);
  dialog.querySelector(".form-error").textContent = "";
  dialog.querySelector("form").reset();
  dialog.showModal();
}

function randomId() {
  return [...crypto.getRandomValues(new Uint8Array(8))].map(value => value.toString(16).padStart(2, "0")).join("");
}

function choosePoint(event) {
  const box = el("sheetView").getBoundingClientRect();
  const x = Math.max(0, Math.min(S.sheet.width - 1, Math.floor((event.clientX - box.left) * S.sheet.width / box.width)));
  const y = Math.max(0, Math.min(S.sheet.height - 1, Math.floor((event.clientY - box.top) * S.sheet.height / box.height)));
  return [x, y];
}

function submitForm(id, task) {
  el(id).addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("[type=submit]");
    submit.disabled = true;
    try {
      await task(new FormData(form));
      form.closest("dialog").close();
    } catch (error) {
      form.querySelector(".form-error").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
}

el("showImport").onclick = () => openDialog("plateDialog");
el("demo").onclick = action(async () => {
  const sheet = await request("/demo", "POST", {});
  await getSheets();
  await openSheet(sheet);
  status("Synthetic practice sheet opened.");
});
el("sheetSelect").onchange = action(async () => {
  await openSheet(await request("/sheets/" + el("sheetSelect").value));
  status("");
});
el("views").onclick = action(async event => {
  const view = event.target.dataset.view;
  if (!view) return;
  S.view = view;
  showViews();
  await refresh();
});
el("apply").onclick = action(async () => {
  S.settings = readFields();
  S.reading = null;
  setAdopted(Boolean(S.settings.adopted));
  el("adoptOffset").disabled = true;
  el("edgeLegend").hidden = true;
  await refresh();
  status("Proof recalculated.");
});
el("saveSetup").onclick = action(async () => {
  const settings = readFields();
  S.sheet = await request(route("/settings"), "PUT", {revision: S.sheet.revision, settings});
  S.settings = structuredClone(S.sheet.settings);
  setAdopted(Boolean(S.settings.adopted));
  await refresh();
  status("Setup saved.");
});
el("zoneSelect").onchange = action(async () => {
  S.zone = el("zoneSelect").value;
  S.reading = null;
  el("adoptOffset").disabled = true;
  el("edgeLegend").hidden = true;
  await refresh(false);
});
el("readOffset").onclick = action(() => readOffset(false));
el("adoptOffset").onclick = action(async () => {
  applyReading();
  await readOffset(true);
  status("Shift adopted at " + readOffsetLabel(S.settings.dx) + ", " + readOffsetLabel(S.settings.dy) +
    ". Below is where the ink actually landed; Save setup keeps the half-pixel value.");
});
el("zoneTool").onclick = () => {
  if (S.settings.zones.length >= 8) return status("This sheet already has eight zones.", true);
  setTool(S.tool === "zone" ? "" : "zone");
  status(S.tool ? "Click two opposite corners on the image." : "");
  drawMarks();
};
el("probeTool").onclick = () => {
  setTool(S.tool === "probe" ? "" : "probe");
  status(S.tool ? "Click a source pixel to see its row and ink values." : "");
  drawMarks();
};
el("sheetView").onclick = action(async event => {
  if (!S.tool) return;
  const point = choosePoint(event);
  if (S.tool === "probe") {
    S.point = point;
    await refresh(false);
    return;
  }
  if (!S.first) {
    S.first = point;
    drawMarks();
    status("Click the opposite corner.");
    return;
  }
  const [x, y] = point, [firstX, firstY] = S.first;
  const bounds = [Math.min(firstX, x), Math.min(firstY, y), Math.max(firstX, x) + 1, Math.max(firstY, y) + 1];
  S.pendingZone = {bounds, id: randomId()};
  setTool("");
  el("nameTitle").textContent = "Name inspection zone";
  openDialog("nameDialog");
});
el("saveProof").onclick = action(() => {
  S.pendingZone = null;
  el("nameTitle").textContent = "Name this proof";
  openDialog("nameDialog");
});
el("reportSelect").onchange = action(async () => {
  const key = el("reportSelect").value;
  if (!key) {
    await openSheet(await request(route()), S.view);
    status("Live setup opened.");
    return;
  }
  S.report = S.reports.find(report => report.id === key);
  S.sheet = S.report.sheet;
  S.settings = structuredClone(S.sheet.settings);
  S.point = S.report.point;
  S.zone = S.report.zone || "";
  S.reading = null;
  setAdopted(Boolean(S.sheet.settings.adopted));
  el("adoptOffset").disabled = true;
  el("edgeLegend").hidden = true;
  if (!S.sheet.photo && ["photo", "difference"].includes(S.view)) S.view = "proof";
  setTool("");
  settingsToFields();
  showZones();
  showViews();
  showArchive();
  await refresh();
  status("Saved proof opened.");
});
el("addPhoto").onclick = () => openDialog("photoDialog");
document.querySelectorAll("dialog .close").forEach(button => {
  button.onclick = () => button.closest("dialog").close();
});

submitForm("plateForm", async data => {
  const sheet = await request("/sheets", "POST", data);
  await getSheets();
  await openSheet(sheet);
  status("Both separations imported.");
});
submitForm("nameForm", async data => {
  const title = data.get("title").trim();
  if (S.pendingZone) {
    const zone = {...S.pendingZone, name: title || "Inspection zone"};
    S.settings.zones.push(zone);
    S.zone = zone.id;
    S.pendingZone = null;
    showZones();
    await refresh(false);
    status("Zone marked. Save the setup to keep it on the sheet.");
    return;
  }
  const report = await request(route("/reports"), "POST", {revision: S.sheet.revision,
    settings: S.settings, point: S.point, zone: S.zone || null, title});
  S.reports = await request(route("/reports"));
  showArchive();
  el("reportSelect").value = report.id;
  await el("reportSelect").onchange();
});
submitForm("photoForm", async data => {
  data.set("revision", S.sheet.revision);
  const sheet = await request(route("/photo"), "POST", data);
  await openSheet(sheet, "photo");
  status("Proof photo imported.");
});

action(async () => {
  await getSheets();
  if (!S.sheets.length) return;
  const last = localStorage.getItem("pressdesk-sheet");
  const next = S.sheets.find(sheet => sheet.id === last) || S.sheets[0];
  const showPhoto = next.photo && localStorage.getItem("pressdesk-open-photo") === next.id;
  localStorage.removeItem("pressdesk-open-photo");
  await openSheet(next, showPhoto ? "photo" : "proof");
})(null);
