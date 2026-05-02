"""
Bird Box Dashboard
==================
Simple web UI to view AI activity log.

Usage:
  venv\\Scripts\\activate
  python dashboard.py

Then open http://localhost:5000
"""

import json
from pathlib import Path
from flask import Flask, render_template_string, send_from_directory

from config.settings import settings

app = Flask(__name__)

HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Great Tit Nest — Activity</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f1117; color: #e8e8e8; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: #888; font-size: 0.85rem; margin-bottom: 24px; }

  /* Desktop: two-column layout */
  .main-grid { display: block; }
  @media (min-width: 1100px) {
    .main-grid {
      display: grid;
      grid-template-columns: 1fr 440px;
      gap: 28px;
      align-items: start;
    }
    .right-panel {
      max-height: calc(100vh - 80px);
      overflow-y: auto;
      position: sticky;
      top: 24px;
    }
    .right-panel::-webkit-scrollbar { width: 6px; }
    .right-panel::-webkit-scrollbar-track { background: transparent; }
    .right-panel::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
  }

  .live-wrap { margin-bottom: 28px; }
  .live-label { font-size: 0.75rem; color: #e55; font-weight: 600; letter-spacing: 0.08em; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
  .live-dot { width: 8px; height: 8px; border-radius: 50%; background: #e55; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  video { width: 100%; max-height: 65vh; object-fit: contain; border-radius: 10px; background: #000; display: block; margin: 0 auto; }
  .offline-msg { background: #1c1f26; border-radius: 10px; padding: 32px; text-align: center; color: #555; font-size: 0.9rem; }
  .stats { display: flex; gap: 12px; margin-bottom: 28px; flex-wrap: wrap; }
  .stat { background: #1c1f26; border-radius: 10px; padding: 12px 16px; min-width: 100px; flex: 1; }
  .stat-value { font-size: 1.6rem; font-weight: 700; color: #7eb8f7; }
  .stat-label { font-size: 0.7rem; color: #888; margin-top: 2px; }

  /* Activity heatmap */
  .heatmap-wrap { margin-bottom: 28px; }
  .heatmap-title { font-size: 0.85rem; font-weight: 600; color: #aaa; margin-bottom: 10px; }
  .heatmap { display: flex; gap: 3px; align-items: flex-end; height: 60px; }
  .heatmap-bar { flex: 1; border-radius: 3px 3px 0 0; min-width: 8px; transition: height 0.3s; position: relative; }
  .heatmap-bar:hover::after {
    content: attr(data-label);
    position: absolute; bottom: calc(100% + 4px); left: 50%; transform: translateX(-50%);
    font-size: 0.65rem; color: #ccc; white-space: nowrap; background: #222; padding: 2px 6px; border-radius: 4px;
  }
  .heatmap-labels { display: flex; gap: 3px; margin-top: 4px; }
  .heatmap-labels span { flex: 1; text-align: center; font-size: 0.55rem; color: #555; min-width: 8px; }

  .panel-title { font-size: 1rem; font-weight: 600; margin-bottom: 14px; color: #aaa; }
  .events { display: flex; flex-direction: column; gap: 10px; }
  .event { background: #1c1f26; border-radius: 10px; padding: 14px 18px; border-left: 3px solid #3a6; }
  .event.no-desc { border-left-color: #555; }
  .event.key-moment { border-left-color: #fca311; background: #2b2512; }
  .event.intruder { border-left-color: #e53e3e; background: #2a1215; }
  .event-time { font-size: 0.8rem; color: #888; margin-bottom: 6px; }
  .event-desc { font-size: 0.95rem; line-height: 1.5; }
  .event-meta { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .badge { font-size: 0.65rem; padding: 2px 8px; border-radius: 99px; background: #2a2d36; color: #aaa; }
  .badge.uploaded { background: #1a3a2a; color: #5c9; }
  .badge.pending  { background: #2a2a1a; color: #ba6; }
  .badge.failed   { background: #2a1a1a; color: #c66; }
  .badge.entrance { background: #1a2a3a; color: #7bb8e8; }
  .badge.nest     { background: #2a2a1a; color: #b8a86e; }
  .badge.entering { background: #1a3a2a; color: #5c9; }
  .badge.leaving  { background: #2a1a3a; color: #b88be8; }
  .badge.intruder-badge { background: #3a1a1a; color: #e55; font-weight: 600; }
  .drive-link { font-size: 0.75rem; color: #7eb8f7; text-decoration: none; }
  .drive-link:hover { text-decoration: underline; }
  .snapshot-thumb { width: 100%; max-width: 200px; border-radius: 6px; margin-top: 8px; cursor: pointer; border: 1px solid #333; }
  .snapshot-thumb:hover { border-color: #7eb8f7; }
  .empty { color: #555; text-align: center; padding: 60px; }
  .refresh { color: #555; font-size: 0.75rem; margin-top: 24px; }
</style>
</head>
<body>
<h1>🐦 Great Tit Nest</h1>
<p class="subtitle">
  Auto-refreshes every 30 seconds &mdash; {{ total }} events recorded
  &nbsp;·&nbsp;
  <a href="http://localhost:5001/" target="_blank" style="color:#7eb8f7;text-decoration:none;">🐛 Motion Debug</a>
</p>

<div class="main-grid">
  <div class="left-panel">
    <div class="live-wrap">
      <div class="live-label"><span class="live-dot"></span> LIVE</div>
      <video id="video" controls autoplay muted playsinline></video>
      <div class="offline-msg" id="offline-msg" style="display:none">Stream offline — waiting for camera</div>
    </div>

    <div class="stats">
      <div class="stat">
        <div class="stat-value">{{ total }}</div>
        <div class="stat-label">Total events</div>
      </div>
      <div class="stat">
        <div class="stat-value">{{ with_desc }}</div>
        <div class="stat-label">AI descriptions</div>
      </div>
      <div class="stat">
        <div class="stat-value">{{ uploaded }}</div>
        <div class="stat-label">Clips on Drive</div>
      </div>
      <div class="stat">
        <div class="stat-value">{{ today }}</div>
        <div class="stat-label">Events today</div>
      </div>
      <div class="stat">
        <div class="stat-value" style="color:#fca311;">{{ key_moments }}</div>
        <div class="stat-label">Key Moments</div>
      </div>
    </div>

    {% if heatmap %}
    <div class="heatmap-wrap">
      <div class="heatmap-title">📊 Today's Activity</div>
      <div class="heatmap">
        {% for h in heatmap %}
        <div class="heatmap-bar" style="height: {{ h.pct }}%; background: {{ h.color }};" data-label="{{ h.label }}"></div>
        {% endfor %}
      </div>
      <div class="heatmap-labels">
        {% for h in heatmap %}
        <span>{{ h.hour_label }}</span>
        {% endfor %}
      </div>
    </div>
    {% endif %}
  </div>

  <div class="right-panel">
    <div class="panel-title">📋 Activity Log</div>
    {% if events %}
    <div class="events">
      {% for e in events %}
      <div class="event {% if not e.ai_description %}no-desc{% endif %} {% if e.is_intruder %}intruder{% elif e.is_key_moment %}key-moment{% endif %}">
        <div class="event-time">
          {% if e.is_intruder %}🚨 INTRUDER &mdash; {% elif e.is_key_moment %}⭐ KEY MOMENT &mdash; {% endif %}
          {{ e.event_start_fmt }}{% if e.duration %} &mdash; {{ e.duration }}{% endif %}
        </div>
        <div class="event-desc">{{ e.ai_description or "No AI description (Gemini Vision unavailable)" }}</div>
        <div class="event-meta">
          {% if e.motion_zone %}
            <span class="badge {{ e.motion_zone }}">{{ e.zone_icon }} {{ e.motion_zone }}</span>
          {% endif %}
          {% if e.motion_direction and e.motion_direction != 'unknown' %}
            <span class="badge {{ e.motion_direction }}">{{ e.dir_icon }} {{ e.motion_direction }}</span>
          {% endif %}
          {% if e.clip_filename %}
            <span class="badge">📹 {{ e.clip_filename }}</span>
          {% endif %}
          <span class="badge {{ e.drive_upload_status }}">{{ e.drive_upload_status }}</span>
          {% if e.drive_clip_url %}
            <a class="drive-link" href="{{ e.drive_clip_url }}" target="_blank">View on Drive ↗</a>
          {% endif %}
        </div>
        {% if e.snapshot_filename %}
        <a href="/snapshots/{{ e.snapshot_filename }}" target="_blank">
          <img class="snapshot-thumb" src="/snapshots/{{ e.snapshot_filename }}" alt="Snapshot" loading="lazy">
        </a>
        {% endif %}
      </div>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">No events recorded yet. Motion will appear here.</div>
    {% endif %}
  </div>
</div>

<p class="refresh">Page auto-refreshes every 30s</p>

<script>
  var video = document.getElementById('video');
  var offlineMsg = document.getElementById('offline-msg');
  var src = 'http://localhost:8888/analysis/index.m3u8';

  function startPlayer() {
    if (Hls.isSupported()) {
      var hls = new Hls({ liveSyncDurationCount: 1, liveMaxLatencyDurationCount: 3 });
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, function(e, data) {
        if (data.fatal) {
          video.style.display = 'none';
          offlineMsg.style.display = '';
          setTimeout(function() {
            video.style.display = '';
            offlineMsg.style.display = 'none';
            startPlayer();
          }, 5000);
        }
      });
      hls.on(Hls.Events.MANIFEST_PARSED, function() {
        video.play().catch(function(){});
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src;
      video.play().catch(function(){});
    }
  }

  startPlayer();
</script>
</body>
</html>
"""

ZONE_ICONS = {"entrance": "🚪", "nest": "🪹", "unknown": "❓"}
DIR_ICONS = {"entering": "⬇️", "leaving": "⬆️", "unknown": ""}


def _fmt_duration(start_iso, end_iso) -> str | None:
    if not end_iso:
        return None
    from datetime import datetime
    try:
        s = datetime.fromisoformat(start_iso)
        e = datetime.fromisoformat(end_iso)
        secs = int((e - s).total_seconds())
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m {secs % 60}s"
    except Exception:
        return None


def _fmt_time(iso: str) -> str:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso)
        # Convert to local time if UTC
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%d %b %Y, %H:%M:%S")
    except Exception:
        return iso


def _build_heatmap(entries: list[dict]) -> list[dict]:
    """Build hourly activity heatmap data for today."""
    from datetime import date, datetime
    today_str = date.today().isoformat()
    hour_counts = [0] * 24

    for e in entries:
        start = e.get("event_start", "")
        if start.startswith(today_str):
            try:
                dt = datetime.fromisoformat(start)
                hour_counts[dt.hour] += 1
            except Exception:
                pass

    max_count = max(hour_counts) if any(hour_counts) else 1
    current_hour = datetime.now().hour

    heatmap = []
    for h in range(24):
        count = hour_counts[h]
        pct = max(4, int((count / max_count) * 100)) if count > 0 else 4

        if h > current_hour:
            color = "#1a1a1a"  # future hours
        elif count == 0:
            color = "#1c1f26"
        elif count <= 2:
            color = "#2a4a3a"
        elif count <= 5:
            color = "#3a6a4a"
        else:
            color = "#5c9a6a"

        heatmap.append({
            "hour": h,
            "hour_label": str(h) if h % 3 == 0 else "",
            "count": count,
            "pct": pct,
            "color": color,
            "label": f"{h:02d}:00 — {count} event{'s' if count != 1 else ''}",
        })

    return heatmap


@app.route("/snapshots/<path:filename>")
def serve_snapshot(filename):
    return send_from_directory(settings.snapshots_dir, filename)


@app.route("/")
def index():
    log_path = settings.activity_log_path
    try:
        entries = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    except Exception:
        entries = []

    from datetime import date
    today_str = date.today().isoformat()

    events = []
    for e in reversed(entries):
        zone = e.get("motion_zone", "unknown")
        direction = e.get("motion_direction", "unknown")
        events.append({
            **e,
            "event_start_fmt": _fmt_time(e.get("event_start", "")),
            "duration": _fmt_duration(e.get("event_start", ""), e.get("event_end")),
            "zone_icon": ZONE_ICONS.get(zone, ""),
            "dir_icon": DIR_ICONS.get(direction, ""),
        })

    total = len(entries)
    with_desc = sum(1 for e in entries if e.get("ai_description"))
    uploaded = sum(1 for e in entries if e.get("drive_upload_status") == "uploaded")
    today = sum(1 for e in entries if e.get("event_start", "").startswith(today_str))
    key_moments = sum(1 for e in entries if e.get("is_key_moment", False))
    heatmap = _build_heatmap(entries)

    return render_template_string(
        HTML,
        events=events,
        total=total,
        with_desc=with_desc,
        uploaded=uploaded,
        today=today,
        key_moments=key_moments,
        heatmap=heatmap,
    )


if __name__ == "__main__":
    print("Dashboard running at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
