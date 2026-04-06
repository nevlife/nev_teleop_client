# NEV Teleop Client

NEV 차량 원격 조종 오퍼레이터 클라이언트. 영상 표시 + 텔레메트리 대시보드 + 조이스틱 조종을 단일 PySide6 GUI에서 제공합니다.

## 구조

```
main.py                      # 진입점 (GUI + 컨트롤러)
viewer_h265.py               # 영상 전용 뷰어 (컨트롤러 없음)
controller_main.py           # 헤드리스 컨트롤러 (GUI 없음)
config.yaml                  # 설정 파일
nev_teleop_client/
├── state.py                 # 공유 상태 (linear_x, steer_angle, ...)
├── client.py                # Zenoh 퍼블리셔/구독자
├── send_loop.py             # async 전송 루프 (heartbeat, teleop, ping)
├── config.py                # YAML 설정 로더
├── controller/
│   ├── base.py              # Controller ABC
│   └── joystick.py          # JoystickController (pygame)
└── gui/
    ├── main_window.py       # 메인 윈도우 (상태바 + 모드 버튼 + E-STOP)
    ├── video_widget.py      # GStreamer 영상 디코딩 + 표시
    └── telemetry_panel.py   # 텔레메트리 대시보드 (10개 카드)
```

## 실행

```bash
python3 main.py
```

## 설정

`config.yaml`:

```yaml
server_zenoh_locator: "tcp/127.0.0.1:7447"

video_codec: "h264"    # h264 | h265
hw_accel: true         # true: NVIDIA HW 디코딩, false: SW 디코딩

heartbeat_rate: 5.0    # Hz
teleop_rate: 20.0      # Hz
ping_rate: 1.0         # Hz

controller_type: joystick

joystick:
  axis_speed: 1
  axis_steer: 3
  max_speed: 1.0          # m/s
  max_steer_deg: 27.0     # degrees
  deadzone: 0.05
  invert_speed: true
  btn_estop: 4
```

## GUI 구성

- **상단바**: VEH / STAS / JOY / REM 연결 상태 뱃지 + 시계
- **명령바**: IDLE / CTRL / NAV / REMOTE 모드 버튼 + E-STOP
- **좌측**: 영상 (GStreamer H.264/H.265 디코딩)
- **우측**: 텔레메트리 패널 (HUNTER, MUX, NETWORK, TWIST, E-STOP, JOYSTICK, RESOURCES, NET, DISK, ALERTS)

## Zenoh 토픽

### 구독

| 토픽 | 내용 |
|------|------|
| `nev/gcs/camera` | 영상 스트림 (서버 경유) |
| `nev/gcs/telemetry` | 텔레메트리 JSON (서버 집계) |
| `nev/gcs/station_pong` | RTT 측정 응답 |

### 발행

| 토픽 | 주기 | 내용 |
|------|------|------|
| `nev/station/client_heartbeat` | 5 Hz | `{ts}` |
| `nev/station/teleop` | 20 Hz | `{linear_x, steer_angle}` |
| `nev/station/controller_heartbeat` | 50 Hz | `{connected}` |
| `nev/station/ping` | 1 Hz | `{ts}` |
| `nev/station/estop` | 이벤트 | `{active}` |
| `nev/station/cmd_mode` | 이벤트 | `{mode}` |

## 의존성

- [eclipse-zenoh](https://zenoh.io/) 1.8.0
- [PySide6](https://doc.qt.io/qtforpython-6/)
- [PyGObject](https://pygobject.gnome.org/) (GStreamer 바인딩)
- GStreamer 1.20
- [pygame](https://www.pygame.org/)
