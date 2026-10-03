"""Linha de comando do bot (sem GUI).

    python run.py rodar      # inicia o bot  (F7 pausa/retoma, F8 para)
    python run.py previsao   # salva previsao.png com a rota calculada
"""
import sys
import time

import cv2

from core import mapping
from core.config import ConfigStore, MAP_PATH, MAPMASK_PATH, PROJECT_DIR
from core.mapmask import MapMask
from core.runner import BotRunner


def cmd_rodar():
    import keyboard  # só necessário aqui (atalhos globais)
    cfg = ConfigStore()
    last = {"msg": None}

    def status(msg):
        if msg != last["msg"]:
            last["msg"] = msg
            print(msg)

    runner = BotRunner(cfg, on_status=status)
    keyboard.add_hotkey("f7", lambda: print("Pausado" if runner.toggle_pause() else "Retomado"))
    keyboard.add_hotkey("f8", runner.stop)
    print("Iniciando em 3s... F7 pausa/retoma, F8 para.")
    time.sleep(3)
    runner.start()
    while runner.running:
        time.sleep(0.2)


def cmd_previsao():
    cfg = ConfigStore()
    mm = MapMask.load(MAP_PATH, MAPMASK_PATH)
    if mm is None or not mm.walkable().any():
        sys.exit("Capture o mapa e marque o corredor na interface (python main.py > sprites/capture).")
    loop = mapping.build_loop(mm.walkable(), cfg["sentido"], abertura=float(cfg.get("abertura_curva", 0.75)))
    out = PROJECT_DIR / "previsao.png"
    cv2.imwrite(str(out), mm.overlay(loop, scale=3))
    print(f"{out} salvo. Verde = corredor, vermelho = obstáculo, linha amarela = rota (seta branca = sentido).")


if __name__ == "__main__":
    cmds = {"rodar": cmd_rodar, "previsao": cmd_previsao}
    if len(sys.argv) > 1 and sys.argv[1] in cmds:
        cmds[sys.argv[1]]()
    else:
        print(__doc__)
