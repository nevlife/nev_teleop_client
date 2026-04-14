import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _validate_config(cfg: dict) -> None:
    for key in ("heartbeat_rate", "teleop_rate"):
        val = cfg.get(key)
        if val is not None:
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(f"{key} must be a positive number, got {val!r}")

    joy = cfg.get("joystick", {})
    if isinstance(joy, dict):
        for key in ("max_speed", "max_steer_deg", "deadzone"):
            val = joy.get(key)
            if val is not None:
                if not isinstance(val, (int, float)) or val < 0:
                    raise ValueError(
                        f"joystick.{key} must be a non-negative number, got {val!r}"
                    )

        for key in ("axis_speed", "axis_steer", "btn_estop"):
            val = joy.get(key)
            if val is not None:
                if not isinstance(val, int) or val < 0:
                    raise ValueError(
                        f"joystick.{key} must be a non-negative integer, got {val!r}"
                    )


def load_config(path: str, overrides: dict) -> dict:
    cfg = {}
    p = Path(path)
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    else:
        logger.warning(f"Config file not found: {path}")
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    _validate_config(cfg)
    return cfg
