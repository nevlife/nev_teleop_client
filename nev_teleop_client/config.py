import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_config(path: str, overrides: dict) -> dict:
    cfg = {}
    p = Path(path)
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}
    else:
        logger.warning(f'Config file not found: {path}')
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    return cfg
