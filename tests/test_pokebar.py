"""Testes puros da leitura de preenchimento e validação visual da Pokébar."""
import os
import sys
import tempfile
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from core.config import ConfigStore
from core.pokebar import BarReading, CommandValidator, bar_percent

ref = np.zeros((8, 100, 3), np.uint8); ref[:, :, :] = (20, 210, 30)
assert bar_percent(ref, ref) == 100.0
half = ref.copy(); half[:, 50:] = (10, 10, 10)
assert 49 <= bar_percent(half, ref) <= 51
empty = np.full_like(ref, 10)
assert bar_percent(empty, ref) == 0.0

cfg = ConfigStore(os.path.join(tempfile.mkdtemp(), "c.json")); cfg.set("pokebar_validar_shooter", True, save=False); cfg.set("pokebar_validar_timeout_s", .2, save=False); cfg.set("pokebar_validar_delta_pct", 8, save=False)
validator = CommandValidator(cfg)
values = iter([BarReading(100, True, False), BarReading(70, False, False)])
validator.monitor.snapshot = lambda: next(values, BarReading(70, False, False))
assert validator.validate("r", BarReading(100, True, False)) is True
values = iter([BarReading(70, False, False), BarReading(100, True, False)])
validator.monitor.snapshot = lambda: next(values, BarReading(100, True, False))
assert validator.validate("e", BarReading(70, False, False)) is True
print("TUDO OK")
