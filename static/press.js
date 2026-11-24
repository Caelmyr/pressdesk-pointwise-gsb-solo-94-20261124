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
const S = {sheet: null, sheets: [], settings: null, point: null, zone: "", report: null, reports: [],
  view: "proof", tool: "", first: null, objectUrl: null, sequence: 0, pendingZone: null};

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
  fieldNames.forEach(key => { el(key).value = S.settings[key]; });
}

function readFields() {
  const changed = {...S.settings};
  fieldNames.forEach(key => {
    if (!el(key).checkValidity() || !el(key).value) throw new Error("Check " + el(key).labels[0].textContent.trim() + ".");
    changed[key] = el(key).type === "number" ? Number(el(key).value) : el(key).value;
  });
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
  if (S.point || S.first) {
    const [x, y] = S.first || S.point;
    overlay.append(mark("circle", {cx: x + .5, cy: y + .5, r: 4, fill: "#273c38", stroke: "#fff", "stroke-width": 2, "vector-effect": "non-scaling-stroke"}));
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
  fieldNames.concat(["apply", "saveSetup", "saveProof", "zoneTool", "addPhoto"]).forEach(id => { el(id).disabled = frozen; });
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
  const cool = p.b_source ? " from " + p.b_source.join(", ") : " outside source";
  el("profileTitle").textContent = "Coverage along row " + p.y;
  el("pointRead").textContent = "X " + p.x + " · warm " + p.a + " · cool " + p.b + cool;
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
  await refresh();
  status("Proof recalculated.");
});
el("saveSetup").onclick = action(async () => {
  const settings = readFields();
  S.sheet = await request(route("/settings"), "PUT", {revision: S.sheet.revision, settings});
  S.settings = structuredClone(S.sheet.settings);
  await refresh();
  status("Setup saved.");
});
el("zoneSelect").onchange = action(async () => {
  S.zone = el("zoneSelect").value;
  await refresh(false);
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
