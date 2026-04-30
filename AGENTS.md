# nev_teleop_client — agent guide

PySide6 Qt GUI + Zenoh viewer/joystick controller. Uses GStreamer 1.28 via `/opt/gst128` LD path injection in `viewer_h265.py`.

## Code map

- `main.py`, `viewer_h265.py`, `controller_main.py` — entry points (joystick controller, H.265 viewer, combined viewer).
- `nev_teleop_client/state.py`, `client.py`, `send_loop.py`, `config.py` — controller stack.
- `nev_teleop_client/gui/main_window.py` — Qt main window. Owns `VideoGrid` + `TelemetryPanel`. `_resolve_vehicle_ids(cfg)` resolves the vehicle list.
- `nev_teleop_client/gui/video_widget.py` — single-vehicle GStreamer pipeline + RTP opt-in + PLI publisher.
- `nev_teleop_client/gui/video_grid.py` — N-vehicle grid container (auto-sized 1→1x1, 2→1x2, 3-4→2x2, 5-6→2x3, 7-9→3x3).
- `nev_teleop_client/gui/telemetry_panel.py` — per-vehicle telemetry display (currently single-vehicle).

## Video pipeline (branch `feat/video-rtp-multivehicle`)

Opt-in via `rtp_mode: false` in `config.yaml`. Must match the bot's `rtp_mode` ROS 2 parameter.

`rtp_mode=false` (legacy): `appsrc do-timestamp=false caps="video/x-h265,..." ! h265parse ! nvh265dec ! cudadownload ! videoconvert ! ... ! appsink`. Manual PTS via `_decode_pts_seq`/`_decode_pts_map`.

`rtp_mode=true`: `appsrc do-timestamp=true caps="application/x-rtp,media=video,encoding-name=H265,clock-rate=90000,payload=96" ! rtpjitterbuffer latency=80 mode=slave do-lost=true ! rtph265depay ! h265parse ! video/x-h265,stream-format=byte-stream,alignment=au ! nvh265dec max-display-delay=0 ! cudadownload ! videoconvert ! video/x-raw,format=RGB ! appsink drop=true max-buffers=1 sync=false emit-signals=true`. Software fallback swaps `nvh265dec ! cudadownload` for `avdec_h265`. Jitterbuffer's `on-lost-packet` calls `_on_rtp_lost` → `_send_pli()`.

## Multi-vehicle

`config.yaml` accepts a `vehicles:` list. Resolution order in `_resolve_vehicle_ids`:
1. `vehicles` non-empty → list of ids. Dict entries' `id` key wins; raw str/int entries are accepted directly.
2. Else single `vehicle_id` → `[str(vehicle_id)]`.
3. Else `["0"]`.

Each vehicle id templates Zenoh keys: `nev/gcs/{id}/camera`, `nev/gcs/{id}/telemetry`, and the publisher `nev/station/{id}/video_ctl`. `TelemetryPanel` only mirrors the first vehicle; selection UI is a follow-up.

`MainWindow.__init__` pulls `rtp_mode = bool(cfg.get('rtp_mode', False))` and forwards through `VideoGrid` to each `VideoWidget`.

## Signaling publisher

`video_widget._send_pli()`:
- Zenoh key `nev/station/{vehicle_id}/video_ctl`, reliable, INTERACTIVE_HIGH, JSON `{"type":"pli"}`.
- 200 ms rate-limit via `_last_pli_send`.
- Publisher is declared in `start()`, undeclared in `stop()`.
- The publisher exists in both modes (legacy + rtp); only the rtp mode wires `_on_rtp_lost` to call it.

## Pre-existing issue

`client.py` uses 3-part station keys (`nev/station/{topic}`) for teleop/estop/controller_heartbeat/cmd_mode. The server's `station_bridge._on_station` requires 4-part keys (`nev/station/{vid}/{topic}`). End-to-end teleop/estop is likely broken today. Out of scope for this branch. The new `_send_pli()` publisher uses the correct 4-part form.

## Pending PRs

- PR 4 — `_on_rtp_lost` is already wired to `_send_pli()`. Add a second hook for decoder PTS-gap detection (covers losses jitterbuffer didn't flag).
- PR 5 — receive-side measurement loop (`recv_kbps`, `loss_ratio`) → 1 Hz `bitrate` JSON over signaling. Hysteresis 5 %, range [500, 4000] kbps. Loss-only simplified GCC; delay-based variant deferred.

## Dependencies

- eclipse-zenoh
- PySide6
- GStreamer 1.28 at `/opt/gst128` (rtph265depay, rtpjitterbuffer, nvh265dec, cudadownload).
- pygame
