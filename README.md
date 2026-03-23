# NEV GCS

NEV 차량 원격 조종 클라이언트.

컨트롤러(조이스틱 등) 입력을 읽어 Zenoh pub/sub으로 차량에 속도/조향 명령을 전송합니다.

## 구조

```
main.py                      # 진입점
config.yaml                  # 설정 파일
nev_gcs/
├── state.py                 # 공유 상태 (linear_x, steer_angle, ...)
├── client.py                # Zenoh 퍼블리셔
├── send_loop.py             # async 전송 루프 (gcs_heartbeat, teleop)
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

## Zenoh 토픽

| 토픽 | 주기 | 내용 |
|------|------|------|
| `nev/station/gcs_heartbeat` | 5 Hz | `{ts}` |
| `nev/station/teleop` | 20 Hz | `{linear_x, steer_angle, raw_speed, raw_steer}` |
| `nev/station/controller_heartbeat` | 20 Hz | `{connected}` |
| `nev/station/estop` | 이벤트 | `{active}` |
| `nev/station/cmd_mode` | 이벤트 | `{mode}` |

## 컨트롤러 추가

1. `controller/` 안에 `Controller`를 상속하는 클래스 작성
2. `name()`과 `poll()` 구현
3. `controller/__init__.py`의 `CONTROLLERS`에 등록
4. `config.yaml`에 설정 섹션 추가

## 의존성

- Python 3
- [zenoh](https://zenoh.io/)
- [pygame](https://www.pygame.org/) (조이스틱 컨트롤러 사용 시)
- PyYAML
