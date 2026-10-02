"""Testes sem tela para o cálculo de barras e a validação R/E.

    python tests/test_pokebar.py
"""
import os
import sys
import tempfile
import threading

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import pokebar as pb  # noqa: E402
from core.config import ConfigStore  # noqa: E402


def bar(percent):
    img = np.full((20, 200, 3), 40, dtype=np.uint8)
    img[4:16, 16:184] = (20, 20, 20)
    img[4:16, 16:16 + round(168 * percent / 100)] = (20, 210, 20)
    return img


def check(value, text):
    print(("  ok   " if value else "  FALHA ") + text)
    if not value:
        raise SystemExit(1)


print("1. percentual e barra cheia")
check(pb.fill_percent(bar(0)) == 0, "barra vazia é 0%")
check(48 <= pb.fill_percent(bar(50)) <= 52, "barra pela metade é ~50%")
check(pb.is_full(pb.fill_percent(bar(100))), "barra completa é reconhecida como cheia")
check(not pb.is_full(pb.fill_percent(bar(95))), "barra incompleta não é reconhecida como cheia")

print("2. validação da sequência R/E")
cfg = ConfigStore(os.path.join(tempfile.mkdtemp(), "config.json"))
cfg.set("regiao_pokebar", [1, 1, 1, 1], save=False)
cfg.set("pokebar_validar_sequencia", True, save=False)
cfg.set("pokebar_tolerancia_validacao_s", .1, save=False)
controller = pb.PokeBarController(cfg)
states = iter([(80, 100), (80, 60), (80, 100)])
controller.read = lambda: next(states)
tapped = []
pb.kb.tap = lambda key: tapped.append(key)
check(controller.run_sequence([("r", 0), ("e", 0)], threading.Event()), "R reduz habilidades e E enche: sequência validada")
check(tapped == ["r", "e"], "as duas teclas foram enviadas")

print("3. regra de vida não repete até recuperar")
cfg.set("pokebar_ativo", True, save=False)
cfg.set("pokebar_regras_vida", [{"percentual": 30, "tecla": "f"}], save=False)
controller.read = lambda: (25, 100)
tapped.clear(); controller.step(); controller.step()
check(tapped == ["f"], "em 25% a tecla de cura é apertada uma vez")
controller.read = lambda: (80, 100); controller.step()
controller.read = lambda: (20, 100); controller.step()
check(tapped == ["f", "f"], "ao recuperar e cair de novo, a regra é rearmada")
