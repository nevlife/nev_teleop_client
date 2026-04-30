# NEV Teleop Client

NEV 차량 원격 조종 클라이언트.

컨트롤러(조이스틱 등) 입력을 읽어 Zenoh pub/sub으로 차량에 속도/조향 명령을 전송합니다.

## 구조

```
main.py                      # 진입점
config.yaml                  # 설정 파일
nev_teleop_client/
├── state.py                 # 공유 상태 (linear_x, steer_angle, ...)
├── client.py                # Zenoh 퍼블리셔
├── send_loop.py             # async 전송 루프 (client_heartbeat, teleop)
├── config.py                # YAML 설정 로더
└── controller/
    ├── base.py              # Controller ABC
    └── joystick.py          # JoystickController (pygame)
```

## 실행

```bash
python3 main.py --config config.yaml
```

Zenoh 서버 직접 지정:

```bash
python3 main.py --server-locator "tcp/192.168.1.100:7447"
```

## 설정

`config.yaml`:

```yaml
server_zenoh_locator: "tcp/127.0.0.1:7447"  # 빈 문자열이면 auto-discovery
heartbeat_rate: 5.0    # Hz
teleop_rate: 20.0      # Hz

rtp_mode: false        # true 시 RTP packetized H.265 + jitterbuffer (봇도 함께 활성화 필요)

vehicles:              # 다중 차량 표출. 미지정 시 vehicle_id (단일) 또는 "0" fallback
  - id: "0"
  - id: "1"

controller_type: joystick  # 컨트롤러 종류

joystick:
  axis_speed: 1
  axis_steer: 3
  max_speed: 1.0          # m/s
  max_steer_deg: 27.0     # degrees
  deadzone: 0.05
  invert_speed: true
  btn_estop: 4
```

### 비디오 모드

| `rtp_mode` | 클라 GStreamer 입력 | 봇 출력 | 비고 |
|------------|-------------------|--------|------|
| `false` (기본) | `appsrc → h265parse → nvh265dec` | NAL AU 통째 | 손실 시 GOP 깨짐 (legacy) |
| `true` | `appsrc → rtpjitterbuffer (latency=80ms) → rtph265depay → h265parse → nvh265dec` | RTP 패킷 | jitter 흡수 + `on-lost-packet` 자동 PLI 발사 (PR 4 예정) |

봇과 클라가 같은 모드여야 함. 봇 ROS2 파라미터 `rtp_mode` 와 동기.

### 다중 차량 표출

`vehicles` 리스트에 차량 N 대 지정 시 그리드 자동 사이징: 1→1x1, 2→1x2, 3-4→2x2, 5-6→2x3, 7-9→3x3. `vehicle_id` 단일 (구버전) 도 자동 fallback. 텔레메트리 패널은 첫 차량만 표시 (선택 UI 는 후속 작업).

## Zenoh 토픽

| 토픽 | 주기 | 내용 |
|------|------|------|
| `nev/station/client_heartbeat` | 5 Hz | `{ts}` |
| `nev/station/teleop` | 20 Hz | `{linear_x, steer_angle}` |
| `nev/station/controller_heartbeat` | 20 Hz | `{connected}` |
| `nev/station/estop` | 이벤트 | `{active}` |
| `nev/station/cmd_mode` | 이벤트 | `{mode}` |
| `nev/station/{vid}/video_ctl` | 손실 감지 시 (max 5Hz) | JSON `{"type":"pli"}` (PR 4 에서 자동화). `rtp_mode=true` 시 jitterbuffer `on-lost-packet` 콜백이 200 ms rate-limit 으로 발사 |

## 의존성

- [zenoh](https://zenoh.io/)
- [pygame](https://www.pygame.org/)
- PySide6 (Qt GUI)
- GStreamer 1.28 — `viewer_h265.py` 가 `/opt/gst128` 에서 강제 로드. `rtph265depay`, `rtpjitterbuffer`, `nvh265dec`, `cudadownload` 필요
