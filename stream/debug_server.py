"""
Motion-detection debug server.

Runs in-process inside main.py on port 5001 so it can share state with the
live MotionDetector. Provides:

  GET  /                — HTML page with sliders and live MJPEG view
  GET  /stream.mjpg     — multipart MJPEG stream of the annotated frame
  GET  /settings.json   — current debug + motion settings
  POST /settings        — update one or more settings (form-encoded)

Sliders mutate `config.settings.settings` directly; MotionDetector reads
those values every frame, so changes take effect immediately with no restart.
"""

import logging
import threading
import time

from flask import Flask, Response, jsonify, request

from config.settings import settings

logger = logging.getLogger(__name__)


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Motion Debug</title>
<style>
  body { font-family: system-ui, sans-serif; background:#0f1117; color:#e8e8e8;
         margin:0; padding:18px; }
  h1 { font-size:1.1rem; margin:0 0 10px; }
  .layout { display:grid; grid-template-columns: 1fr 320px; gap:18px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  img.preview { width:100%; max-height:80vh; object-fit:contain;
                background:#000; border-radius:8px; }
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
                 padding:8px 12px; border-radius:6px; cursor:pointer; font-size:0.8rem; }
  button.reset:hover { background:#3a3d46; color:#fff; }
  a { color:#7eb8f7; }
</style></head><body>
<h1>🐦 Motion Detector — Debug View</h1>
<p style="font-size:0.8rem;color:#888;margin-bottom:14px;">
  Slider changes apply live. Toggle off when done — JPEG encoding adds CPU.
  <a href="http://localhost:5000/">← Back to dashboard</a>
</p>

<div class="layout">
  <div>
    <img id="preview" class="preview" src="/stream.mjpg" alt="debug stream">
  </div>
  <div class="panel">
    <div class="toggle">
      <input type="checkbox" id="debug_enabled">
      <label for="debug_enabled" style="margin:0;">Debug overlay enabled</label>
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

    <button class="reset" id="reset">Reset background model</button>

    <div class="stats" id="stats">loading…</div>
  </div>
</div>

<script>
  const fields = ["motion_threshold","motion_min_area","motion_min_area_nest","entrance_zone_bottom"];

  async function load() {
    const r = await fetch("/settings.json");
    const s = await r.json();
    document.getElementById("debug_enabled").checked = s.debug_enabled;
    for (const f of fields) {
      document.getElementById(f).value = s[f];
      document.getElementById(f + "_v").textContent = s[f];
    }
    renderStats(s.stats);
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

  async function post(body) {
    const r = await fetch("/settings", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams(body).toString(),
    });
    return r.json();
  }

  document.getElementById("debug_enabled").addEventListener("change", async (e) => {
    await post({debug_enabled: e.target.checked ? "1" : "0"});
  });

  for (const f of fields) {
    const el = document.getElementById(f);
    el.addEventListener("input", () => {
      document.getElementById(f + "_v").textContent = el.value;
    });
    el.addEventListener("change", async () => {
      await post({[f]: el.value});
    });
  }

  document.getElementById("reset").addEventListener("click", async () => {
    await post({reset: "1"});
  });

  // Poll stats every 1s
  setInterval(async () => {
    const r = await fetch("/settings.json");
    const s = await r.json();
    renderStats(s.stats);
  }, 1000);

  load();
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

        logger.info(
            "Debug settings updated: thresh=%s min_e=%s min_n=%s zone=%.2f debug=%s",
            settings.motion_threshold, settings.motion_min_area,
            settings.motion_min_area_nest, settings.entrance_zone_bottom,
            detector.is_debug_enabled(),
        )
        return jsonify({"ok": True})

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
                    # Debug off or no frames yet — send a blank-ish placeholder
                    # so the browser keeps the connection alive.
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
