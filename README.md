# NEV Teleop Client

NEV 차량 원격 조종 클라이언트. 영상 표시 + 텔레메트리 대시보드 + 조이스틱 조종을 PySide6 GUI에서 제공합니다.

## 구조

```
main.py                          # 진입점 (멀티스레드: GUI + 컨트롤러 + send_loop)
config.yaml                      # 설정 파일
nev_teleop_client/
├── state.py                     # 스레드 안전 공유 상태 (linear_x, steer_angle, estop)
├── client.py                    # Zenoh 퍼블리셔/구독자 + RTT 계산
├── send_loop.py                 # async 전송 루프 (teleop 20Hz, ping 1Hz)
├── config.py                    # YAML 설정 로더
├── controller/
│   ├── base.py                  # Controller ABC (폴링 + heartbeat 전송)
│   └── joystick.py              # JoystickController (pygame, deadzone, 스케일링)
└── gui/
    ├── main_window.py           # 메인 윈도우 (상태 배지 + 모드 버튼 + E-STOP)
    ├── video_widget.py          # GStreamer 영상 디코딩 + 표시
    └── telemetry_panel.py       # 텔레메트리 대시보드 (10개 카드)
```

## 실행

```bash
python3 main.py [--config config.yaml] [--server-locator tcp://IP:PORT]
```

## 설정

`config.yaml`:

```yaml
server_zenoh_locator: "tcp/127.0.0.1:7447"
vehicle_id: 0

video_codec: "h265"        # h264 | h265
hw_accel: false             # true: NVIDIA HW 디코딩, false: SW 디코딩

teleop_rate: 20.0           # Hz
ping_rate: 1.0              # Hz

controller_type: joystick
joystick:
  axis_speed: 1
  axis_steer: 3
  max_speed: 1.0            # m/s
  max_steer_deg: 27.0       # degrees
  deadzone: 0.05
  invert_speed: true
  btn_estop: 4
```

## 스레드 구조

| 스레드 | 역할 |
|--------|------|
| 메인 | Qt GUI (영상 표시, 텔레메트리 렌더링) |
| async | send_loop (teleop 20Hz, station_ping + bot_ping 1Hz) |
| controller | 조이스틱 폴링 20ms + controller_heartbeat 20Hz |

## Zenoh 토픽

### 발행 (Client → Server) `nev/station/{id}/...`

| 토픽 | 방식 | 내용 |
|------|------|------|
| `station_ping` | 고정 1Hz | `{ts}` — cli↔srv RTT |
| `bot_ping` | 고정 1Hz | `{ts}` — cli↔bot RTT (서버 릴레이) |
| `teleop` | 고정 20Hz | `{linear_x, steer_angle}` |
| `controller_heartbeat` | 고정 20Hz | `{connected}` — 조이스틱 상태 + station 생존 |
| `estop` | 즉시 (버튼) | `{active}` (RELIABLE) |
| `cmd_mode` | 즉시 (버튼) | `{mode}` (RELIABLE) |

### 구독 (Server → Client) `nev/gcs/{id}/...`

| 토픽 | 내용 |
|------|------|
| `telemetry` | 통합 텔레메트리 JSON |
| `camera` | 20B 릴레이 헤더 + H.264/H.265 NAL |
| `station_pong` | cli↔srv RTT 에코 `{ts}` |
| `bot_pong` | cli↔bot RTT 에코 `{ts}` (봇→서버 릴레이) |

## GUI 구성

```
+----------------------------------------------+
| NEV CLIENT          [SRV][VEH][STAS][JOY]    |  상태 배지
+----------------------------------------------+
| [IDLE] [CTRL] [NAV] [REMOTE]     [E-STOP]    |  모드/비상정지
+------------------------+---------------------+
|                        | VEHICLE  | MUX       |
|   영상 스트림           | NETWORK  | TWIST     |
|   (GStreamer 디코딩)    | E-STOP   | JOYSTICK  |  10개 카드
|                        | RESOURCES| NET IFACES|
|                        | DISK     | ALERTS    |
+------------------------+---------------------+
```

## 판단/계산 영역

- **조이스틱 입력**: deadzone 적용, max_speed/max_steer 스케일링, 축 반전
- **E-Stop 토글**: 버튼 → 현재 상태 반전 → 즉시 전송
- **cli↔srv RTT**: station_ping/station_pong (raw, 필터 없음, 3초 무응답 리셋)
- **cli↔bot RTT**: bot_ping/bot_pong (raw, 필터 없음, 3초 무응답 리셋)
- **영상 디코딩**: GStreamer 파이프라인, PTS 매핑으로 decode 지연 측정
- **연결 상태 표시**: 텔레메트리/영상 수신 경과 < 3초 → 정상, 배지 색상 결정
- **JOYSTICK 패널**: 서버 telemetry가 아닌 로컬 StationState에서 직접 표시

## 의존성

- [eclipse-zenoh](https://zenoh.io/) 1.8.0
- [PySide6](https://doc.qt.io/qtforpython-6/)
- [PyGObject](https://pygobject.gnome.org/) (GStreamer 바인딩)
- GStreamer 1.20
- [pygame](https://www.pygame.org/)
