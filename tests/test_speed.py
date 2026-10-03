"""Testes da velocidade por sprites, do envio contínuo de teclas e da previsão (sem tela, sem Windows).

    python tests/test_speed.py
"""
import math
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import mapping                      # noqa: E402
from core.config import DEFAULTS             # noqa: E402
from core.engine import MovementEngine       # noqa: E402
from core.mapmask import MapMask             # noqa: E402
from core.speed import KeyHolder, SpeedProfile, legacy_profile, profile_for  # noqa: E402


class Cfg(dict):
    def __init__(self, **kw):
        super().__init__(DEFAULTS)
        self.update(kw)


def test_faixas():
    c = Cfg()  # padrão: 0 = muito rápido, 1-2 = rápido, 3+ = devagar
    assert profile_for(0, c).nome == "muito rápido" and profile_for(0, c).continuo
    assert profile_for(1, c).nome == profile_for(2, c).nome == "rápido" and not profile_for(1, c).continuo
    assert profile_for(3, c).nome == profile_for(9, c).nome == "devagar"
    c = Cfg(vel_muito_rapido_ate=1, vel_rapido_ate=4)           # o usuário muda os limites na UI
    assert [profile_for(n, c).nome for n in (0, 1, 2, 4, 5)] == ["muito rápido", "muito rápido", "rápido", "rápido", "devagar"]
    assert profile_for(None, c).nome == "rápido"                # detecção de sprites desligada
    c = Cfg(vel_ativo=False, passo_ms=150)
    p = profile_for(0, c)
    assert p == legacy_profile(c) and p.passo_s == 0.15 and p.previsao_s == 0.0   # desligado = comportamento antigo
    c = Cfg(vel_devagar_passo_ms=90, vel_devagar_previsao_ms=30)
    assert profile_for(7, c).passo_s == 0.09 and profile_for(7, c).previsao_s == 0.03


def test_keyholder():
    ev = []
    h = KeyHolder(lambda k: ev.append(("down", k)), lambda k: ev.append(("up", k)))
    h.apply(["d"]); h.apply(["d"])
    assert ev == [("down", "d")], "tecla já pressionada não é reenviada"
    ev.clear(); h.apply(["d", "s"])
    assert ev == [("down", "s")], "D -> D+S só desce o S"
    ev.clear(); h.apply(["s"])
    assert ev == [("up", "d")]
    ev.clear(); h.apply(["w"])                                   # S -> W são opostas: solta antes de apertar
    assert ev == [("up", "s"), ("down", "w")]
    ev.clear(); h.apply(["a"])                                   # W -> A: a nova desce ANTES da antiga subir
    assert ev == [("down", "a"), ("up", "w")]
    ev.clear(); h.release()
    assert ev == [("up", "a")] and not h.held


def test_previsao():
    walk = np.ones((200, 200), bool)
    loop = np.array([(100 + 60 * math.cos(a), 100 + 60 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 180, endpoint=False)])
    e = MovementEngine(walk, loop)
    for i in range(12):                                          # seta andando a 20 px/s para a direita
        e.decide((40.0 + 20 * i * 0.05, 100.0), now=i * 0.05)
    vx, vy = e._vel
    assert abs(vx - 20) < 1.0 and abs(vy) < 0.5, e._vel
    p = e.predict((50.0, 100.0), 0.1)
    assert abs(p[0] - 52.0) < 0.2 and abs(p[1] - 100.0) < 0.2
    e._vel = (500.0, 0.0)
    assert math.hypot(*(np.array(e.predict((50.0, 100.0), 1.0)) - (50.0, 100.0))) <= 8.01   # limite da projeção
    e.notify_paused(now=10.0)
    assert e._vel == (0.0, 0.0) and e.predict((50.0, 100.0), 0.1) == (50.0, 100.0)
    # predict_s = 0 não muda nada em relação ao comportamento antigo
    e1, e2 = MovementEngine(walk, loop), MovementEngine(walk, loop)
    for i in range(30):
        pos = (100 + 55 * math.cos(i * 0.1), 100 + 55 * math.sin(i * 0.1))
        assert e1.decide(pos, now=i * 0.1) == e2.decide(pos, now=i * 0.1, predict_s=0.0)


def _limpa(walk, max_area=12):
    n, lab, st, _ = cv2.connectedComponentsWithStats((~walk).astype(np.uint8), connectivity=8)
    out = walk.copy()
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] <= max_area:
            out[lab == i] = True
    return out


def _simula(walk, loop, prof, speed=10.0, ciclo=0.06, lag=0.03, noise=0.4, seed=0):
    """Mini-jogo: a seta anda enquanto há tecla pressionada; medida com atraso (lag) e ruído; o ciclo do bot
    (captura + localização) leva `ciclo`. Devolve (tempo da 1ª volta, fração do tempo bloqueado)."""
    rng = np.random.default_rng(seed)
    e = MovementEngine(walk, loop, lookahead=12, stuck_timeout_s=0.3)
    held = set()
    h = KeyHolder(held.add, held.discard)
    d = {"w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0)}
    dt, t, pos = 0.005, 0.0, np.array(loop[0], float)
    hist, n = [pos.copy()], len(loop)
    last = int(np.argmin(np.hypot(loop[:, 0] - pos[0], loop[:, 1] - pos[1])))
    prog, ticks, hits, lap = 0, 0, 0, None
    while t < 300 and lap is None:
        meas = hist[max(0, len(hist) - 1 - int(lag / dt))] + rng.uniform(-noise, noise, 2)
        h.apply(e.decide((float(meas[0]), float(meas[1])), now=t, predict_s=prof.previsao_s))
        total = ciclo if prof.continuo else prof.passo_s + ciclo
        end, release_at = t + total, t + (ciclo if prof.continuo else prof.passo_s)
        while t < end:
            if not prof.continuo and t >= release_at and held:
                h.release()
            if held:
                vx, vy = sum(d[k][0] for k in held), sum(d[k][1] for k in held)
                nv = math.hypot(vx, vy)
                if nv:
                    nxt = pos + np.array([vx, vy]) / nv * speed * dt
                    ix, iy = int(round(nxt[0])), int(round(nxt[1]))
                    if walk[iy, ix]:
                        pos = nxt
                    else:
                        hits += 1
            ticks += 1
            t += dt
            hist.append(pos.copy())
            near = int(np.argmin(np.hypot(loop[:, 0] - pos[0], loop[:, 1] - pos[1])))
            prog += (near - last + n // 2) % n - n // 2
            last = near
            if lap is None and prog >= n:
                lap = t
    return lap, hits / ticks


def test_volta_mais_rapida():
    mm = MapMask.load(os.path.join(ROOT, "mapa_completo.png"), os.path.join(ROOT, "mapa_mascara.png"))
    if mm is None:
        print("  (sem mapa no projeto: simulação pulada)")
        return
    walk = _limpa(mm.walkable())
    loop = mapping.build_loop(walk, "horario", abertura=0.75)
    antigo = _simula(walk, loop, SpeedProfile("antigo", 0.12, 0.0))
    rapido = _simula(walk, loop, SpeedProfile("rápido", 0.20, 0.05))
    muito = _simula(walk, loop, SpeedProfile("muito rápido", 0.0, 0.10))
    print(f"  volta: antigo {antigo[0]:.1f}s | rápido {rapido[0]:.1f}s | muito rápido {muito[0]:.1f}s "
          f"(bloqueado {muito[1] * 100:.1f}%)")
    assert rapido[0] < antigo[0] * 0.95, "faixa rápida deveria ganhar ao menos 5%"
    assert muito[0] < antigo[0] * 0.80, "tecla contínua deveria ganhar ao menos 20%"
    assert muito[1] < 0.05 and rapido[1] < 0.05, "não pode ficar empurrando parede"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
