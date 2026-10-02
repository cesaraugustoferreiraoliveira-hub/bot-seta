"""Teste da página revive (sem tela e sem GUI):

  1. LifeBarReader: % de vida lida da barra (com e sem a sprite), em qualquer cor, com a barra em qualquer lugar;
  2. pick_skill / normalize_skills: qual habilidade vale para cada % de vida;
  3. ChangeWatcher + ReviveController: segura a tecla até a foto mudar, solta, guarda a imagem nova e repete;
  4. LifeSkillMonitor: aperta a tecla da regra certa, respeita intervalo e o modo 'só com o bot parado';
  5. integração: o shooter chama o revive depois da sequência de teclas (todas as sprites dentro do limite).

    python tests/test_revive.py
"""
import os
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import revive as rv, shooter as sh                  # noqa: E402
from core.config import ConfigStore                           # noqa: E402
from core.lifebar import LifeBarReader, pick_skill, filled_columns   # noqa: E402
from core.vision import SpriteTemplate                        # noqa: E402

rng = np.random.default_rng(7)
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        fails.append(msg)


GREEN, YELLOW, RED = (40, 200, 40), (30, 215, 235), (30, 30, 220)      # BGR


def bar(pct, color=GREEN, w=100, h=14):
    """Barra de vida: moldura branca de 1 px, fundo cinza escuro, preenchimento colorido da esquerda."""
    img = np.full((h, w, 3), 60, np.uint8)
    inner = w - 2
    img[2:h - 2, 1:1 + int(round(inner * pct / 100))] = color
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), (255, 255, 255), 1)
    return img


def scene(pct, color, x=37, y=21, w=260, h=70):
    frame = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    b = bar(pct, color)
    frame[y:y + b.shape[0], x:x + b.shape[1]] = b
    return frame


# ---------------------------------------------------------------- 1. leitura da vida
print("1. LifeBarReader")
full = bar(100)
tmpl = SpriteTemplate(full, np.full(full.shape[:2], 255, np.uint8))
reader = LifeBarReader(tmpl, 0.6)
check(abs(filled_columns(full) - 98) <= 1, f"barra cheia: {filled_columns(full)} colunas cheias (esperado 98)")
for pct, col in ((100, GREEN), (75, GREEN), (50, YELLOW), (23, RED), (5, RED)):
    r = reader.read(scene(pct, col))
    ok = r.pct is not None and abs(r.pct - pct) <= 3 and r.box[:2] == (37, 21)
    check(ok, f"{pct}% em {['verde', 'amarelo', 'vermelho'][(GREEN, YELLOW, RED).index(col)]}: leu {r.pct if r.pct is None else round(r.pct, 1)}% "
              f"(sprite {r.score:.2f}, barra em {r.box[:2] if r.box else None})")
fresh = LifeBarReader(tmpl, 0.6)                    # primeira leitura já com o pokémon ferido (sprite capturada com vida cheia)
r = fresh.read(scene(40, RED, x=120, y=5))
check(r.pct is not None and abs(r.pct - 40) <= 3 and r.box[:2] == (120, 5), f"primeira leitura com 40% vermelho, barra em outro lugar: {r.pct}")
r = fresh.read(np.full((70, 260, 3), 30, np.uint8) + rng.integers(0, 20, (70, 260, 3), dtype=np.uint8))
check(r.pct is not None, "barra some por um quadro: usa a última posição conhecida em vez de desistir")
r = LifeBarReader(tmpl, 0.6).read(np.full((70, 260, 3), 25, np.uint8))
check(r.pct is None, "barra nunca vista e frame sem barra: devolve None")
nosprite = LifeBarReader(None)
for pct in (100, 62, 8):
    r = nosprite.read(bar(pct, GREEN if pct > 50 else RED))
    check(r.pct is not None and abs(r.pct - pct) <= 3, f"sem sprite (área = barra): {pct}% -> {r.pct if r.pct is None else round(r.pct, 1)}%")
r = nosprite.read(bar(0))
check(r.pct == 0.0, "barra vazia: 0%")
big = rng.integers(0, 40, (80, 200, 3), dtype=np.uint8)          # sem sprite: mesma LARGURA da barra, bastante margem em cima e embaixo
big[30:46, :] = bar(60, GREEN, w=200, h=16)
r = nosprite.read(big)
check(r.pct is not None and abs(r.pct - 60) <= 3, f"sem sprite, área alta (margem vertical): {r.pct if r.pct is None else round(r.pct, 1)}% (esperado ~60%)")

# ---------------------------------------------------------------- 2. regras de habilidade
print("2. regras por % de vida")
skills = rv.normalize_skills([{"pct": 70, "tecla": "1", "cooldown_s": 2}, {"pct": 40, "tecla": "2"}, {"pct": 15, "tecla": "3"},
                              {"pct": 50, "tecla": ""}, {"pct": "x", "tecla": "9"}, {"pct": 150, "tecla": "8"}])
check([s["tecla"] for s in skills] == ["1", "2", "3"], "normalize_skills ignora tecla vazia, % inválido e % fora de 0..100")
pk = lambda p: (pick_skill(skills, p) or {}).get("tecla")        # noqa: E731
check([pk(100), pk(70), pk(55), pk(40), pk(30), pk(15), pk(3)] == [None, "1", "1", "2", "2", "3", "3"],
      f"escolhe a regra de menor limite que ainda vale: {[pk(p) for p in (100, 70, 55, 40, 30, 15, 3)]}")

# ---------------------------------------------------------------- 3. revive: segura até a foto mudar
print("3. ReviveController")


class FakeScreen:
    """Foto que muda quando a tecla do revive está há `delay` segundos pressionada (simula o jogo)."""

    def __init__(self, delay):
        self.delay, self.t_down, self.state, self.events = delay, None, 0, []

    def frame(self):
        if self.t_down is not None and time.time() - self.t_down >= self.delay:
            self.state ^= 1                       # o jogo executou o revive: a foto troca
            self.t_down = None
        img = np.full((40, 60, 3), 50 if self.state == 0 else 200, np.uint8)
        return img

    def down(self, k):
        self.events.append(("down", k))
        if self.t_down is None:
            self.t_down = time.time()

    def up(self, k):
        self.events.append(("up", k))
        self.t_down = None


def mkcfg(**kw):
    c = ConfigStore(os.path.join(tempfile.mkdtemp(), "c.json"))
    for k, v in {"revive_ativo": True, "revive_tecla": "e", "regiao_revive_foto": [10, 10, 60, 40],
                 "revive_sens_pct": 2.0, "revive_timeout_s": 5.0, **kw}.items():
        c.set(k, v, save=False)
    return c


scr = FakeScreen(delay=0.4)
log = []
ctl = rv.ReviveController(mkcfg(), lambda k, m: log.append((k, m)), grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
ctl.arm()
t0 = time.time()
ok = ctl.execute()
dt = time.time() - t0
check(ok and 0.35 <= dt <= 1.0, f"segura a tecla e confirma quando a foto muda ({dt:.2f}s)")
check(scr.events[0] == ("down", "e") and scr.events[-1] == ("up", "e") and sum(1 for e in scr.events if e[0] == "up") == 1,
      "a tecla E é pressionada, continua pressionada durante a espera e é solta UMA vez no fim")
check(sum(1 for e in scr.events if e[0] == "down") >= 5, f"reenvia a tecla enquanto segura ({sum(1 for e in scr.events if e[0] == 'down')}x)")
check(ctl.watcher.changed_pct(scr.frame()) == 0.0, "a imagem nova ficou guardada na memória como referência")
scr.events.clear()
ok2 = ctl.execute()                                 # segundo comando: o jogo troca a foto de volta
check(ok2 and ctl.executions == 2, "segundo comando também confirma (a referência foi atualizada)")

scr = FakeScreen(delay=999)                         # a foto NUNCA muda
log = []
ctl = rv.ReviveController(mkcfg(revive_timeout_s=0.5), lambda k, m: log.append((k, m)), grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
t0 = time.time()
ok = ctl.execute()
check(not ok and 0.45 <= time.time() - t0 <= 1.0 and scr.events[-1] == ("up", "e"), "foto não muda: segura até o tempo máximo e solta a tecla")
check(any(k == "ALERTA" and "NÃO mudou" in m for k, m in log), "registra ALERTA explicando que a foto não mudou")

scr = FakeScreen(delay=999)
stop = threading.Event()
ctl = rv.ReviveController(mkcfg(revive_timeout_s=0), None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
threading.Timer(0.3, stop.set).start()
t0 = time.time()
ok = ctl.execute(stop)
check(not ok and time.time() - t0 < 1.0 and scr.events[-1] == ("up", "e"), "sem tempo máximo, mas o bot voltou a andar (stop): solta a tecla")

scr = FakeScreen(delay=0.2)
ctl = rv.ReviveController(mkcfg(), None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
ctl.watcher.remember(np.full((40, 60, 3), 123, np.uint8))     # memória velha: já difere da foto atual ANTES de apertar
t0 = time.time()
ok = ctl.execute()
check(ok and time.time() - t0 >= 0.15, "memória velha (já diferente): usa a foto de agora como base e ainda espera a mudança de verdade")
log = []
ctl = rv.ReviveController(mkcfg(regiao_revive_foto=None), lambda k, m: log.append((k, m)), grab=lambda r: None, key_down=scr.down, key_up=scr.up)
check(ctl.execute() is False and any(k == "ALERTA" for k, _ in log), "sem área da foto: não aperta nada e avisa")
ctl = rv.ReviveController(mkcfg(revive_tecla="tecla-invalida"), lambda k, m: log.append((k, m)), grab=lambda r: scr.frame(), key_down=scr.down, key_up=scr.up)
scr.events.clear()
check(ctl.execute() is False and not scr.events, "tecla inválida: não aperta nada")
w = rv.ChangeWatcher()
a = np.full((20, 20, 3), 100, np.uint8)
w.remember(a)
b = a.copy(); b[:1, :] = 255                          # 5% dos pixels
check(abs(w.changed_pct(b) - 5.0) < 0.01 and w.changed_pct(a) == 0.0, "changed_pct mede a % de pixels que mudaram")
n = a.astype(np.int16) + rng.integers(-6, 7, a.shape); n = n.clip(0, 255).astype(np.uint8)
check(w.changed_pct(n) == 0.0, "ruído leve de cor não conta como mudança")

# ---------------------------------------------------------------- 4. habilidades por vida
print("4. LifeSkillMonitor")
pressed = []
now = [1000.0]
frames = {"pct": 100}
lst = [{"pct": 70, "tecla": "1", "cooldown_s": 5}, {"pct": 30, "tecla": "2", "cooldown_s": 5}]
cfgm = mkcfg(vida_hab_ativo=True, vida_hab_lista=lst, regiao_vida=[0, 0, 100, 14], vida_intervalo_s=0.5, vida_somente_parado=True)
log = []
mon = rv.LifeSkillMonitor(cfgm, lambda k, m: log.append((k, m)), grab=lambda reg: bar(frames["pct"], GREEN if frames["pct"] > 50 else RED),
                          tap=lambda k: pressed.append(k), clock=lambda: now[0])


def tick(dt=1.0, halted=True):
    now[0] += dt
    mon.step(halted)


frames["pct"] = 90
tick()
check(pressed == [], "vida 90%: nenhuma habilidade")
frames["pct"] = 60
tick()
check(pressed == ["1"], f"vida 60% (≤70): tecla 1 -> {pressed}")
tick(1.0)
check(pressed == ["1"], "dentro do intervalo da regra: não repete")
tick(5.0)
check(pressed == ["1", "1"], "passado o intervalo e a vida ainda baixa: repete")
frames["pct"] = 20
tick()
check(pressed[-1] == "2", f"vida 20% (≤30): vale a regra mais específica, tecla 2 -> {pressed[-1]}")
pressed.clear()
tick(0.1)
check(pressed == [], "leituras mais rápidas que o intervalo de leitura são ignoradas")
tick(10.0, halted=False)
check(pressed == [], "bot andando e 'só parado' ligado: não aperta")
cfgm.set("vida_somente_parado", False, save=False)
tick(10.0, halted=False)
check(pressed == ["2"], "desligando 'só com o bot parado': aperta mesmo andando")
cfgm.set("vida_hab_ativo", False, save=False)
pressed.clear(); tick(10.0)
check(pressed == [], "habilidades desligadas na interface: não faz nada")
check(any(k == "DETECÇÃO" and "Vida do pokémon" in m for k, m in log), "log registra a vida lida")

# ---------------------------------------------------------------- 5. shooter -> sequência -> revive
print("5. shooter chama o revive depois da sequência")
order = []
scr = FakeScreen(delay=0.3)
cfg5 = mkcfg(shooter_ativo=True, shooter_distancia_px=250, shooter_parada_s=0.6, shooter_tolerancia_px=6, shooter_tecla="q",
             regiao_sprite=[1000, 200, 900, 500], pokemon_limiar=0.7, shooter_seq_ativo=True,
             shooter_seq_teclas=[{"tecla": "r", "espera_ms": 100}, {"tecla": "f", "espera_ms": 0}])
log5 = []
rc = rv.ReviveController(cfg5, lambda k, m: log5.append((k, m)), grab=lambda reg: scr.frame(),
                         key_down=lambda k: (order.append(("down", k)), scr.down(k)), key_up=lambda k: (order.append(("up", k)), scr.up(k)))
rc.arm()
ctl5 = sh.ShooterController(cfg5, lambda k, m: log5.append((k, m)), revive=rc)
ctl5._tmpl_loaded, ctl5._tmpl = True, object()
sh.locate_pokemon = lambda frame, tmpl, limiar, near=None, margin=0: (450.0, 250.0)
sh.kb.tap = lambda key, hold_s=0.05: order.append(("tap", key))
sh.kb.move_mouse = lambda x, y: None
sh.AIM_SETTLE_S = 0
ctl5.step(None, [(550.0, 250.0, 1.0)])                # tudo dentro do limite
time.sleep(1.2)
seq = [e for e in order if e[0] == "tap"]
check(seq == [("tap", "r"), ("tap", "f")], f"sequência R, F apertada: {seq}")
i_f = order.index(("tap", "f"))
check(("down", "e") in order and order.index(("down", "e")) > i_f, "o revive (E) só começa DEPOIS da sequência")
check(order[-1] == ("up", "e") and rc.executions == 1, "revive segurou E, a foto mudou e a tecla foi solta")
n = len(order)
ctl5.step(None, [(550.0, 250.0, 1.0)]); time.sleep(0.4)
check(len(order) == n, "continua tudo dentro do limite: não repete")

order.clear(); scr = FakeScreen(delay=0.3)
cfg6 = mkcfg(shooter_ativo=True, shooter_seq_ativo=False, regiao_sprite=[1000, 200, 900, 500], shooter_distancia_px=250)
rc6 = rv.ReviveController(cfg6, None, grab=lambda reg: scr.frame(), key_down=lambda k: (order.append(("down", k)), scr.down(k)),
                          key_up=lambda k: (order.append(("up", k)), scr.up(k)))
rc6.arm()
c6 = sh.ShooterController(cfg6, None, revive=rc6)
c6._tmpl_loaded, c6._tmpl = True, object()
c6.step(None, [(550.0, 250.0, 1.0)]); time.sleep(1.0)
check(rc6.executions == 1 and not [e for e in order if e[0] == "tap"], "só o revive ligado (sequência desligada): revive roda sozinho")
order.clear(); scr = FakeScreen(delay=999)
cfg7 = mkcfg(shooter_ativo=True, shooter_seq_ativo=False, revive_ativo=False, regiao_sprite=[1000, 200, 900, 500], shooter_distancia_px=250)
rc7 = rv.ReviveController(cfg7, None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
c7 = sh.ShooterController(cfg7, None, revive=rc7)
c7._tmpl_loaded, c7._tmpl = True, object()
c7.step(None, [(550.0, 250.0, 1.0)]); time.sleep(0.3)
check(not scr.events, "revive desligado na interface: não aperta nada")

# interrupção: o bot volta a andar (shooter.end) no meio do revive
scr = FakeScreen(delay=999)
cfg8 = mkcfg(shooter_ativo=True, shooter_seq_ativo=False, revive_timeout_s=0, regiao_sprite=[1000, 200, 900, 500], shooter_distancia_px=250)
rc8 = rv.ReviveController(cfg8, None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
rc8.arm()
c8 = sh.ShooterController(cfg8, None, revive=rc8)
c8._tmpl_loaded, c8._tmpl = True, object()
c8.step(None, [(550.0, 250.0, 1.0)]); time.sleep(0.3)
check(scr.events and scr.events[-1][0] == "down", "revive segurando a tecla enquanto a foto não muda")
c8.end()                                              # o bot voltou a andar
time.sleep(0.3)
check(scr.events[-1] == ("up", "e"), "bot voltou a andar: a tecla do revive é solta")

print()
print("TUDO OK" if not fails else f"{len(fails)} FALHA(S): " + "; ".join(fails))
sys.exit(1 if fails else 0)
