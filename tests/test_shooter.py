"""Teste do shooter (sem tela e sem GUI):

  1. vision.find_sprites acha as POSIÇÕES das sprites reais (sprite.png) coladas sobre o mapa;
  2. locate_pokemon acha o nome real (pokemon.png), com e sem a busca rápida ao redor da última posição;
  3. FarSpriteShooter: só atira na sprite além da distância limite que PAROU; escolhe a mais distante entre as
     paradas; espera todas entrarem no limite; respeita o tempo máximo de espera; tolera oscilação e falha de detecção;
  4. ShooterController: converte a posição para coordenadas de tela e aciona mouse + tecla (mockados).

    python tests/test_shooter.py
"""
import math
import os
import sys
import tempfile

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import shooter as sh, vision                      # noqa: E402
from core.config import ConfigStore, MAP_PATH, POKEMON_PATH, SPRITE_PATH   # noqa: E402
from core.magic_cut import load_template                    # noqa: E402
from core.name_match import NameTemplate                    # noqa: E402

rng = np.random.default_rng(3)
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        fails.append(msg)


def background(w=900, h=500):
    bg = cv2.imread(MAP_PATH)
    bg = cv2.resize(bg, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    x0, y0 = int(rng.integers(0, bg.shape[1] - w)), int(rng.integers(0, bg.shape[0] - h))
    return bg[y0:y0 + h, x0:x0 + w].copy()


def paste(frame, bgr, mask, x, y):
    h, w = bgr.shape[:2]
    roi = frame[y:y + h, x:x + w]
    roi[mask > 0] = bgr[mask > 0]


# ---------------------------------------------------------------- 1. posições das sprites de parada
print("1. find_sprites devolve onde cada sprite está")
sb, sm = load_template(SPRITE_PATH)
tm = vision.SpriteTemplate(sb, sm)
frame = background()
places = [(100, 80), (700, 400), (420, 60), (150, 430), (800, 90)]
for x, y in places:
    paste(frame, sb, sm, x, y)
hits, best = vision.find_sprites(frame, tm, 0.9)
want = [(x + (tm.w - 1) / 2, y + (tm.h - 1) / 2) for x, y in places]
check(len(hits) == len(places), f"achou {len(hits)} de {len(places)} sprites")
check(all(any(math.hypot(hx - wx, hy - wy) <= 1.0 for hx, hy, _ in hits) for wx, wy in want),
      "cada centro encontrado bate (±1 px) com onde a sprite foi colada")
check(vision.detect_sprites(frame, tm, 0.9)[0] == len(hits), "detect_sprites segue devolvendo a mesma contagem")

# ---------------------------------------------------------------- 2. nome do pokémon
print("2. locate_pokemon")
nb, nm = load_template(POKEMON_PATH)
nt = NameTemplate(nb, nm)
frame = background()
px, py = 430, 250
paste(frame, nb, nm, px, py)
truth = (px + (nt.text_w - 1) / 2, py + (nt.text_h - 1) / 2)
full = sh.locate_pokemon(frame, nt, 0.7)
check(full is not None and math.hypot(full[0] - truth[0], full[1] - truth[1]) <= 1.5, f"busca na área toda: {full}")
near = sh.locate_pokemon(frame, nt, 0.7, near=(full[0] + 30, full[1] - 20))
check(near is not None and math.hypot(near[0] - full[0], near[1] - full[1]) <= 0.01, "busca rápida ao redor dá o mesmo ponto")
far_guess = sh.locate_pokemon(frame, nt, 0.7, near=(40, 40))
check(far_guess is not None and math.hypot(far_guess[0] - full[0], far_guess[1] - full[1]) <= 0.01,
      "palpite errado cai para a busca na área toda")
check(sh.locate_pokemon(background(), nt, 0.7) is None, "sem o nome na tela devolve None")

# ---------------------------------------------------------------- 3. lógica de decisão
print("3. FarSpriteShooter")
REF = (500.0, 300.0)
DT = 0.3


def on_circle(angle_deg, dist):
    a = math.radians(angle_deg)
    return REF[0] + dist * math.cos(a), REF[1] + dist * math.sin(a)


def run(lg, script, t_end=12.0):
    """script(t) -> lista de posições visíveis. Devolve [(t, Step)] dos passos com tiro/evento."""
    out, t = [], 0.0
    while t <= t_end:
        st = lg.update(REF, script(t), t)
        if st.shot or st.event:
            out.append((t, st))
        t = round(t + DT, 3)
    return out


# 3a. sprite que anda até parar longe: só atira depois de parar por still_s
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5, tol_px=6, wait_max_s=15)
stop_t = 2.0
dist_at = lambda t: 420 - 40 * min(t, stop_t)          # 40 px por segundo até t=2 -> para a 340 px
shots = run(lg, lambda t: [on_circle(30, dist_at(t))])
check(len(shots) == 1 and shots[0][1].shot is not None, "atirou uma vez")
t_shot = shots[0][0]
check(stop_t + 1.5 <= t_shot <= stop_t + 1.5 + 2 * DT + 1e-6, f"atirou ~{t_shot - stop_t:.1f}s depois de parar (janela 1.5s)")
check(abs(shots[0][1].shot.dist - 340) < 1, "alvo é a sprite parada a 340 px")

# 3b. parada, mas DENTRO do limite: nunca atira
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5)
check(run(lg, lambda t: [on_circle(30, 200)]) == [], "parada a 200 px (< limite 250): não atira")

# 3c. duas paradas além do limite: atira na mais distante
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5)
shots = run(lg, lambda t: [on_circle(30, 300), on_circle(150, 450), on_circle(250, 100)])
check(len(shots) == 1 and abs(shots[0][1].shot.dist - 450) < 1, "entre as paradas, atira na de 450 px (não na de 300 nem na de 100)")

# 3d. a mais distante ainda anda; uma mais perto (mas além do limite) está parada: atira na parada
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5)
shots = run(lg, lambda t: [on_circle(30, 300), on_circle(150, 600 - 25 * t)], t_end=6)
check(len(shots) == 1 and abs(shots[0][1].shot.dist - 300) < 1, "a mais distante ainda anda: atira na parada de 300 px")

# 3e. depois do tiro, espera todas entrarem no limite; só então volta a vigiar
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5, wait_max_s=0)
enter_t = 8.0
shots = run(lg, lambda t: [on_circle(30, 340 if t < enter_t else 200)], t_end=12)
evs = [(t, st.shot is not None, st.event) for t, st in shots]
check(len([e for e in evs if e[1]]) == 1, "um único tiro enquanto a sprite continua além do limite")
check(any(e[2] == "espera_ok" and e[0] >= enter_t for e in evs), "evento 'todas dentro do limite' só depois que ela entrou")
check(lg.state == sh.WATCH, "voltou a vigiar")

# 3f. tempo máximo de espera: não atira de novo na hora, só depois de parar outra vez
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5, wait_max_s=5)
shots = run(lg, lambda t: [on_circle(30, 340)], t_end=14)
kinds = [("tiro" if st.shot else st.event, t) for t, st in shots]
check([k for k, _ in kinds][:3] == ["tiro", "espera_timeout", "tiro"], f"tiro -> timeout -> novo tiro: {[k for k, _ in kinds]}")
t0, t1, t2 = (t for _, t in kinds[:3])
check(abs((t1 - t0) - 5) <= DT + 1e-6, "timeout após ~5s")
check(t2 - t1 >= 1.5, f"novo tiro só {t2 - t1:.1f}s depois do timeout (precisa parar de novo)")

# 3g. oscilação pequena conta como parada; oscilação grande, não
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5, tol_px=6)
jit = lambda t: [(on_circle(30, 340)[0] + (2 if int(t / DT) % 2 else -2), on_circle(30, 340)[1])]
check(len(run(lg, jit)) >= 1, "oscilando ±2 px (tolerância 6): atira")
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5, tol_px=6)
wig = lambda t: [(on_circle(30, 340)[0] + (12 if int(t / DT) % 2 else -12), on_circle(30, 340)[1])]
check(run(lg, wig) == [], "oscilando ±12 px (tolerância 6): não conta como parada")

# 3h. um quadro sem detectar a sprite não zera a contagem; sumir de vez descarta a sprite
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5)
blink = lambda t: [] if abs(t - 0.9) < 1e-6 else [on_circle(30, 340)]
check(len(run(lg, blink)) == 1, "falha de detecção em 1 quadro: ainda atira")
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5)
gone = lambda t: [on_circle(30, 340)] if t < 1.0 else []
check(run(lg, gone) == [] and lg.tracks == [], "sprite que sumiu (morreu) é esquecida e não recebe tiro")

# 3i. sprites que se cruzam não trocam de identidade
lg = sh.FarSpriteShooter(distance_px=250, still_s=1.5)
a = lambda t: (300 + 30 * t, 100)
b = lambda t: (700 - 30 * t, 110)
run(lg, lambda t: [a(t), b(t)], t_end=3)
ids = sorted(t.id for t in lg.tracks)
check(ids == [1, 2], f"duas sprites em movimento mantêm 2 rastros (ids {ids})")

# ---------------------------------------------------------------- 4. controlador (mouse + tecla mockados)
print("4. ShooterController")
cfg = ConfigStore(os.path.join(tempfile.mkdtemp(), "c.json"))
for k, v in {"shooter_ativo": True, "shooter_distancia_px": 250, "shooter_parada_s": 0.6, "shooter_tolerancia_px": 6,
             "shooter_tecla": "q", "shooter_espera_max_s": 15, "regiao_sprite": [1000, 200, 900, 500],
             "pokemon_limiar": 0.7}.items():
    cfg.set(k, v, save=False)
log = []
ctl = sh.ShooterController(cfg, lambda kind, msg: log.append((kind, msg)))
calls = []
sh.kb.move_mouse = lambda x, y: calls.append(("mouse", x, y))
sh.kb.tap = lambda key, hold_s=0.05: calls.append(("tecla", key))
sh.AIM_SETTLE_S = 0
ctl._tmpl_loaded, ctl._tmpl = True, object()                 # pula o arquivo do pokémon
sh.locate_pokemon = lambda frame, tmpl, limiar, near=None, margin=0: (450.0, 250.0)
far_sprite = (450.0 + 400, 250.0)                            # 400 px à direita do pokémon
t0 = sh.time.time()
for i in range(8):
    ctl.step(None, [(far_sprite[0], far_sprite[1], 1.0)])
    sh.time.sleep(0.12)
check(calls == [("mouse", 1000 + 850, 200 + 250), ("tecla", "q")],
      f"mouse na sprite (origem da área somada) e depois a tecla Q: {calls}")
check(ctl.shots == 1 and any(k == "TIRO" for k, _ in log), "um tiro registrado no log (tipo TIRO)")
check(ctl.status() == "shooter esperando as sprites chegarem", "status mostra que está esperando")
calls.clear()
for i in range(5):                                           # continua longe: não atira de novo
    ctl.step(None, [(far_sprite[0], far_sprite[1], 1.0)])
    sh.time.sleep(0.05)
check(calls == [], "enquanto espera, não atira de novo")
ctl.step(None, [(450.0 + 100, 250.0, 1.0)])                  # entrou no limite
check(ctl.status() == "shooter vigiando", "sprite entrou no limite: voltou a vigiar")

cfg.set("shooter_ativo", False, save=False)
ctl.begin()
ctl._tmpl_loaded, ctl._tmpl = True, object()
calls.clear()
for i in range(8):
    ctl.step(None, [(far_sprite[0], far_sprite[1], 1.0)])
    sh.time.sleep(0.12)
check(calls == [] and ctl.status() == "", "com o shooter desligado na interface, não faz nada")

cfg.set("shooter_ativo", True, save=False)
cfg.set("shooter_tecla", "tecla-invalida", save=False)
ctl.begin()
ctl._tmpl_loaded, ctl._tmpl = True, object()
calls.clear(); log.clear()
for i in range(8):
    ctl.step(None, [(far_sprite[0], far_sprite[1], 1.0)])
    sh.time.sleep(0.12)
check(calls == [] and any(k == "ERRO" for k, _ in log), "tecla inválida: não mexe o mouse, registra ERRO e para de insistir")

# ---------------------------------------------------------------- 5. ajustes em px e sequência de teclas
print("5. ajuste do centro, ajuste do clique e sequência de teclas")
cfg2 = ConfigStore(os.path.join(tempfile.mkdtemp(), "c2.json"))
for k, v in {"shooter_ativo": True, "shooter_distancia_px": 250, "shooter_parada_s": 0.6, "shooter_tolerancia_px": 6,
             "shooter_tecla": "q", "shooter_espera_max_s": 15, "regiao_sprite": [1000, 200, 900, 500],
             "pokemon_limiar": 0.7, "shooter_ref_dy_px": 60, "shooter_mira_dy_px": 35,
             "shooter_seq_ativo": True,
             "shooter_seq_teclas": [{"tecla": "r", "espera_ms": 150}, {"tecla": "e", "espera_ms": 0}]}.items():
    cfg2.set(k, v, save=False)
log2 = []
ctl2 = sh.ShooterController(cfg2, lambda kind, msg: log2.append((kind, msg)))
ctl2._tmpl_loaded, ctl2._tmpl = True, object()
calls.clear()
t_taps = []
sh.kb.tap = lambda key, hold_s=0.05: (calls.append(("tecla", key)), t_taps.append(sh.time.time()))
# nome em (450, 250) + ajuste 60 => centro do círculo em (450, 310). Sprite a 400 px à direita desse centro.
far2 = (450.0 + 400, 310.0)
for i in range(8):
    ctl2.step(None, [(far2[0], far2[1], 1.0)])
    sh.time.sleep(0.12)
check(calls == [("mouse", 1000 + 850, 200 + 310 + 35), ("tecla", "q")],
      f"clique {35} px ABAIXO da sprite: {calls}")
check(abs(ctl2.logic.tracks[0].dist - 400) < 0.01, f"distância medida do centro ajustado: {ctl2.logic.tracks[0].dist:.1f}")
check(not any(c_[1] in ("r", "e") for c_ in calls), "sprite ainda longe: sequência R/E NÃO dispara")
calls.clear(); t_taps.clear()
ctl2.step(None, [(450.0 + 100, 310.0, 1.0)])                  # entrou no limite
sh.time.sleep(0.5)
check([c_ for c_ in calls if c_[0] == "tecla"] == [("tecla", "r"), ("tecla", "e")], f"todas dentro: R depois E: {calls}")
check(len(t_taps) == 2 and 0.13 <= t_taps[1] - t_taps[0] <= 0.35, f"intervalo ~150 ms entre R e E: {t_taps[1] - t_taps[0]:.3f}s")
n = len(calls)
ctl2.step(None, [(450.0 + 100, 310.0, 1.0)])
sh.time.sleep(0.3)
check(len(calls) == n, "continua tudo dentro: não repete a sequência")
ctl2.step(None, [(450.0 + 400, 310.0, 1.0)])                  # uma sprite voltou a ficar longe -> rearma
ctl2.step(None, [(450.0 + 100, 310.0, 1.0)])
sh.time.sleep(0.5)
check(len(calls) == n + 2, "depois que alguma sprite sai e todas voltam a entrar: dispara de novo")
cfg2.set("shooter_seq_ativo", False, save=False)
ctl2.begin(); ctl2._tmpl_loaded, ctl2._tmpl = True, object()
calls.clear()
ctl2.step(None, [(450.0 + 100, 310.0, 1.0)])
sh.time.sleep(0.3)
check(calls == [], "sequência desligada na interface: não aperta nada")
check(sh.parse_sequence([{"tecla": " ", "espera_ms": 5}, {"tecla": "x", "espera_ms": "abc"}, {"tecla": "y"}]) == [("x", 0), ("y", 0)],
      "parse_sequence ignora linha sem tecla e espera inválida")

print()
print("TUDO OK" if not fails else f"{len(fails)} FALHA(S): " + "; ".join(fails))
sys.exit(1 if fails else 0)
