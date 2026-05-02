"""
Motion-detection debug server.

Runs in-process inside main.py on port 5001 so it can share state with the
live MotionDetector. Provides:

  GET  /                       — HTML page with sliders, live MJPEG view, exclusion-zone draw UI
  GET  /stream.mjpg            — multipart MJPEG stream of the annotated frame
  GET  /settings.json          — current debug + motion settings + zones
  POST /settings               — update one or more settings (form-encoded); persists to overrides.json
  GET  /exclusion_zones.json   — list of current zones
  POST /exclusion_zones/add    — append a zone {zone: [x1,y1,x2,y2]}
  POST /exclusion_zones/remove — remove a zone by {index: N}

Sliders mutate `config.settings.settings` directly; MotionDetector reads
those values every frame, so changes take effect immediately with no restart.
All changes are persisted to config/overrides.json and reloaded on next startup.
"""

import logging
import threading
import time

from flask import Flask, Response, jsonify, request

from config.settings import settings, save_overrides

logger = logging.getLogger(__name__)


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Motion Debug</title>
<style>
  body { font-family: system-ui, sans-serif; background:#0f1117; color:#e8e8e8;
         margin:0; padding:18px; }
  h1 { font-size:1.1rem; margin:0 0 10px; }
  .layout { display:grid; grid-template-columns: 1fr 320px; gap:18px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .preview-wrap { position:relative; display:block; width:100%; }
  img.preview { width:100%; display:block; border-radius:8px; background:#000; }
  #zone-canvas { position:absolute; top:0; left:0; width:100%; height:100%;
                 border-radius:8px; }
  .panel { background:#1c1f26; border-radius:8px; padding:14px 16px; }
  label { display:block; margin:14px 0 4px; font-size:0.8rem; color:#aaa; }
  input[type=range] { width:100%; }
  .row { display:flex; justify-content:space-between; font-size:0.75rem;
         color:#7eb8f7; }
  .toggle { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
  .toggle input { transform:scale(1.3); }
  .stats { font-size:0.75rem; color:#888; margin-top:14px; line-height:1.6; }
  .stats b { color:#ddd; }
  button.reset { margin-top:14px; background:#2a2d36; color:#aaa; border:none;
                 padding:8px 12px; border-radius:6px; cursor:pointer; font-size:0.8rem; width:100%; }
  button.reset:hover { background:#3a3d46; color:#fff; }
  button.draw-toggle { margin-top:10px; background:#2a1f3a; color:#c87fff; border:1px solid #6a3fa0;
                       padding:8px 12px; border-radius:6px; cursor:pointer; font-size:0.8rem; width:100%; }
  button.draw-toggle.active { background:#4a2f6a; color:#fff; border-color:#9a6fd0; }
  button.draw-toggle:hover { background:#3a2a5a; }
  .zone-list { margin-top:10px; }
  .zone-item { display:flex; justify-content:space-between; align-items:center;
               background:#2a2d36; border-radius:6px; padding:5px 10px; margin-top:5px;
               font-size:0.75rem; color:#c0a0ff; }
  .zone-item button { background:none; border:none; color:#c66; cursor:pointer;
                      font-size:0.9rem; padding:0 4px; }
  .zone-item button:hover { color:#f88; }
  a { color:#7eb8f7; }
  .save-indicator { font-size:0.7rem; color:#5c9; margin-left:6px; display:none; }
</style></head><body>
<h1>🐦 Motion Detector — Debug View</h1>
<p style="font-size:0.8rem;color:#888;margin-bottom:14px;">
  Slider changes apply live and are saved automatically.
  Toggle debug off when done — JPEG encoding uses CPU.
  <a href="http://localhost:5000/">← Back to dashboard</a>
</p>

<div class="layout">
  <div>
    <div class="preview-wrap" id="preview-wrap">
      <img id="preview" class="preview" src="/stream.mjpg" alt="debug stream">
      <canvas id="zone-canvas"></canvas>
    </div>
    <p style="font-size:0.7rem;color:#555;margin-top:6px;">
      Enable "Draw exclusion zone" then drag a rectangle over areas to ignore.
      Zones are shown as magenta overlays on the live feed.
    </p>
  </div>
  <div class="panel">
    <div class="toggle">
      <input type="checkbox" id="debug_enabled">
      <label for="debug_enabled" style="margin:0;">
        Debug overlay enabled
        <span class="save-indicator" id="save-indicator">✓ saved</span>
      </label>
    </div>

    <label>Pixel diff threshold (motion_threshold)</label>
    <input type="range" id="motion_threshold" min="5" max="100" step="1">
    <div class="row"><span>5</span><span id="motion_threshold_v"></span><span>100</span></div>

    <label>Min area — entrance zone (motion_min_area)</label>
    <input type="range" id="motion_min_area" min="50" max="5000" step="50">
    <div class="row"><span>50</span><span id="motion_min_area_v"></span><span>5000</span></div>

    <label>Min area — nest zone (motion_min_area_nest)</label>
    <input type="range" id="motion_min_area_nest" min="500" max="50000" step="500">
    <div class="row"><span>500</span><span id="motion_min_area_nest_v"></span><span>50000</span></div>

    <label>Entrance zone height (entrance_zone_bottom)</label>
    <input type="range" id="entrance_zone_bottom" min="0.02" max="0.50" step="0.01">
    <div class="row"><span>0.02</span><span id="entrance_zone_bottom_v"></span><span>0.50</span></div>

    <button class="draw-toggle" id="draw-toggle">🔲 Draw exclusion zone</button>

    <div class="zone-list" id="zone-list"></div>

    <button class="reset" id="reset">Reset background model</button>

    <div class="stats" id="stats">loading…</div>
  </div>
</div>

<script>
  const fields = ["motion_threshold","motion_min_area","motion_min_area_nest","entrance_zone_bottom"];
  let drawMode = false;
  let dragStart = null;
  let zones = [];

  // ── Helpers ──────────────────────────────────────────────────────────────

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    return r.json();
  }

  async function postForm(body) {
    const r = await fetch("/settings", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams(body).toString(),
    });
    return r.json();
  }

  function flashSaved() {
    const el = document.getElementById("save-indicator");
    el.style.display = "inline";
    setTimeout(() => { el.style.display = "none"; }, 1500);
  }

  // ── Settings ─────────────────────────────────────────────────────────────

  async function load() {
    const r = await fetch("/settings.json");
    const s = await r.json();
    document.getElementById("debug_enabled").checked = s.debug_enabled;
    for (const f of fields) {
      document.getElementById(f).value = s[f];
      document.getElementById(f + "_v").textContent = s[f];
    }
    renderStats(s.stats);
    zones = s.zones || [];
    renderZoneList();
    drawZones();
  }

  function renderStats(st) {
    if (!st) { document.getElementById("stats").textContent = "no stats"; return; }
    document.getElementById("stats").innerHTML =
      "<b>Contours:</b> " + st.contours +
      " &nbsp; <b>Entrance max area:</b> " + st.max_area_entrance +
      " &nbsp; <b>Nest max area:</b> " + st.max_area_nest +
      "<br><b>Entrance triggered:</b> " + st.entrance_motion +
      " &nbsp; <b>Nest triggered:</b> " + st.nest_motion +
      "<br><b>AI cooldown remaining:</b> " + st.ai_cooldown_remaining + "s";
  }

  document.getElementById("debug_enabled").addEventListener("change", async (e) => {
    await postForm({debug_enabled: e.target.checked ? "1" : "0"});
  });

  for (const f of fields) {
    const el = document.getElementById(f);
    el.addEventListener("input", () => {
      document.getElementById(f + "_v").textContent = el.value;
    });
    el.addEventListener("change", async () => {
      await postForm({[f]: el.value});
      flashSaved();
    });
  }

  document.getElementById("reset").addEventListener("click", async () => {
    await postForm({reset: "1"});
  });

  // Poll stats + zones every 1.5s
  setInterval(async () => {
    const r = await fetch("/settings.json");
    const s = await r.json();
    renderStats(s.stats);
    zones = s.zones || [];
    renderZoneList();
    drawZones();
  }, 1500);

  load();

  // ── Exclusion zones list ──────────────────────────────────────────────────

  function renderZoneList() {
    const list = document.getElementById("zone-list");
    if (!zones.length) { list.innerHTML = ""; return; }
    list.innerHTML = zones.map((z, i) =>
      `<div class="zone-item">
        <span>Zone ${i+1} &nbsp; <span style="color:#888">(${z[0].toFixed(2)},${z[1].toFixed(2)})→(${z[2].toFixed(2)},${z[3].toFixed(2)})</span></span>
        <button onclick="removeZone(${i})" title="Delete zone">✕</button>
       </div>`
    ).join("");
  }

  async function removeZone(index) {
    await postJSON("/exclusion_zones/remove", {index});
    zones.splice(index, 1);
    renderZoneList();
    drawZones();
  }

  // ── Canvas draw UI ────────────────────────────────────────────────────────

  const canvas = document.getElementById("zone-canvas");
  const ctx = canvas.getContext("2d");
  const img = document.getElementById("preview");
  const drawBtn = document.getElementById("draw-toggle");

  function syncCanvasSize() {
    canvas.width = img.clientWidth;
    canvas.height = img.clientHeight;
  }

  img.addEventListener("load", syncCanvasSize);
  window.addEventListener("resize", () => { syncCanvasSize(); drawZones(); });
  syncCanvasSize();

  drawBtn.addEventListener("click", () => {
    drawMode = !drawMode;
    drawBtn.classList.toggle("active", drawMode);
    canvas.style.pointerEvents = drawMode ? "auto" : "none";
    canvas.style.cursor = drawMode ? "crosshair" : "default";
  });
  // Start with canvas non-interactive so normal page scroll works
  canvas.style.pointerEvents = "none";

  function clientToFraction(e) {
    const rect = canvas.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (e.clientY - rect.top)  / rect.height)),
    ];
  }

  canvas.addEventListener("mousedown", (e) => {
    if (!drawMode) return;
    dragStart = clientToFraction(e);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!drawMode || !dragStart) return;
    const [x2, y2] = clientToFraction(e);
    drawZones();
    // Live preview of the rect being drawn
    ctx.strokeStyle = "rgba(255,80,255,0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 3]);
    const w = canvas.width, h = canvas.height;
    ctx.strokeRect(
      dragStart[0] * w, dragStart[1] * h,
      (x2 - dragStart[0]) * w, (y2 - dragStart[1]) * h
    );
    ctx.setLineDash([]);
  });

  canvas.addEventListener("mouseup", async (e) => {
    if (!drawMode || !dragStart) return;
    const [x2, y2] = clientToFraction(e);
    const [x1, y1] = dragStart;
    dragStart = null;

    // Normalise so x1<x2 and y1<y2
    const zone = [
      Math.min(x1, x2), Math.min(y1, y2),
      Math.max(x1, x2), Math.max(y1, y2),
    ];
    // Ignore tiny accidental clicks
    if ((zone[2] - zone[0]) < 0.01 || (zone[3] - zone[1]) < 0.01) {
      drawZones(); return;
    }

    await postJSON("/exclusion_zones/add", {zone});
    zones.push(zone);
    renderZoneList();
    drawZones();
    flashSaved();
  });

  function drawZones() {
    syncCanvasSize();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const w = canvas.width, h = canvas.height;
    zones.forEach((z, i) => {
      const px = z[0] * w, py = z[1] * h;
      const pw = (z[2] - z[0]) * w, ph = (z[3] - z[1]) * h;
      ctx.fillStyle = "rgba(200,0,200,0.15)";
      ctx.fillRect(px, py, pw, ph);
      ctx.strokeStyle = "rgba(200,0,200,0.8)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(px, py, pw, ph);
      ctx.fillStyle = "rgba(200,0,200,0.9)";
      ctx.font = "11px system-ui";
      ctx.fillText("Zone " + (i+1), px + 4, py + 14);
    });
  }

  drawZones();
</script>
</body></html>
"""


def create_app(detector) -> Flask:
    app = Flask(__name__)

    # Silence Flask's per-request access logs — they spam the console at
    # MJPEG framerate.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.route("/")
    def index():
        return Response(_PAGE, mimetype="text/html")

    @app.route("/settings.json")
    def get_settings():
        return jsonify({
            "debug_enabled": detector.is_debug_enabled(),
            "motion_threshold": settings.motion_threshold,
            "motion_min_area": settings.motion_min_area,
            "motion_min_area_nest": settings.motion_min_area_nest,
            "entrance_zone_bottom": settings.entrance_zone_bottom,
            "zones": settings.exclusion_zones,
            "stats": detector.get_stats(),
        })

    @app.route("/settings", methods=["POST"])
    def post_settings():
        form = request.form

        if "debug_enabled" in form:
            detector.set_debug_enabled(form["debug_enabled"] == "1")

        if "motion_threshold" in form:
            settings.motion_threshold = max(1, min(255, int(form["motion_threshold"])))
        if "motion_min_area" in form:
            settings.motion_min_area = max(1, int(form["motion_min_area"]))
        if "motion_min_area_nest" in form:
            settings.motion_min_area_nest = max(1, int(form["motion_min_area_nest"]))
        if "entrance_zone_bottom" in form:
            v = float(form["entrance_zone_bottom"])
            settings.entrance_zone_bottom = max(0.01, min(0.95, v))

        if form.get("reset") == "1":
            detector.reset()

        save_overrides()
        logger.info(
            "Settings updated: thresh=%s min_e=%s min_n=%s zone=%.2f debug=%s",
            settings.motion_threshold, settings.motion_min_area,
            settings.motion_min_area_nest, settings.entrance_zone_bottom,
            detector.is_debug_enabled(),
        )
        return jsonify({"ok": True})

    @app.route("/exclusion_zones.json")
    def get_zones():
        return jsonify({"zones": settings.exclusion_zones})

    @app.route("/exclusion_zones/add", methods=["POST"])
    def add_zone():
        data = request.get_json(force=True)
        zone = data.get("zone", [])
        if len(zone) != 4:
            return jsonify({"error": "zone must be [x1,y1,x2,y2]"}), 400
        zone = [max(0.0, min(1.0, float(v))) for v in zone]
        settings.exclusion_zones.append(zone)
        save_overrides()
        logger.info("Exclusion zone added: %s (total: %d)", zone, len(settings.exclusion_zones))
        return jsonify({"ok": True, "zones": settings.exclusion_zones})

    @app.route("/exclusion_zones/remove", methods=["POST"])
    def remove_zone():
        data = request.get_json(force=True)
        idx = int(data.get("index", -1))
        if 0 <= idx < len(settings.exclusion_zones):
            removed = settings.exclusion_zones.pop(idx)
            save_overrides()
            logger.info("Exclusion zone %d removed: %s", idx, removed)
        return jsonify({"ok": True, "zones": settings.exclusion_zones})

    @app.route("/stream.mjpg")
    def stream():
        boundary = b"--frame"

        def gen():
            placeholder = (
                b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01"
                b"\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06"
            )  # tiny invalid JPEG — browser falls back to alt text
            while True:
                jpeg = detector.get_debug_jpeg()
                if jpeg is None:
                    payload = placeholder
                else:
                    payload = jpeg
                yield (
                    boundary + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
                    + payload + b"\r\n"
                )
                # ~15 fps cap is plenty for tuning
                time.sleep(0.066)

        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def start(detector, port: int = 5001) -> threading.Thread:
    app = create_app(detector)

    def run():
        # threaded=True so MJPEG stream doesn't block other requests
        app.run(host="0.0.0.0", port=port, debug=False,
                use_reloader=False, threaded=True)

    t = threading.Thread(target=run, name="DebugServer", daemon=True)
    t.start()
    logger.info("Motion debug server listening on http://localhost:%d/", port)
    return t
