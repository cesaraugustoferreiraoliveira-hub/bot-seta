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
from core.combo import Cycle                                 # noqa: E402
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
    """Simula o jogo: a foto troca `delay` s depois de um TOQUE no E. Os `ignore` primeiros toques são ignorados
    (o jogo não usou o revive). Guarda a hora de cada toque."""

    def __init__(self, delay, ignore=0):
        self.delay, self.ignore, self.t_effect, self.state, self.events, self.downs, self.down_t = delay, ignore, None, 0, [], 0, []

    def frame(self):
        if self.t_effect is not None and time.time() >= self.t_effect:
            self.state ^= 1                       # o jogo executou o revive: a foto troca
            self.t_effect = None
        return np.full((40, 60, 3), 50 if self.state == 0 else 200, np.uint8)

    def down(self, k):
        self.events.append(("down", k))
        self.downs += 1
        self.down_t.append(time.time())
        if self.downs > self.ignore and self.t_effect is None:
            self.t_effect = time.time() + self.delay

    def up(self, k):
        self.events.append(("up", k))


def rdy(key="e", gap=0.0):
    """Ciclo com o R já executado e o intervalo já cumprido (para testar só o revive)."""
    cy = Cycle(key, gap)
    cy.mark_r_done()
    return cy


def mkcfg(**kw):
    c = ConfigStore(os.path.join(tempfile.mkdtemp(), "c.json"))
    for k, v in {"revive_ativo": True, "revive_tecla": "e", "regiao_revive_foto": [10, 10, 60, 40],
                 "revive_sens_pct": 2.0, "revive_verifica_s": 0.5, "revive_toque_ms": 10, "revive_tentativas": 3, **kw}.items():
        c.set(k, v, save=False)
    return c


scr = FakeScreen(delay=0.3)
log = []
ctl = rv.ReviveController(mkcfg(), lambda k, m: log.append((k, m)), grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
ctl.arm()
t0 = time.time()
ok = ctl.execute(rdy())
dt = time.time() - t0
check(ok and 0.25 <= dt <= 0.9, f"um toque, a foto muda e o revive é confirmado ({dt:.2f}s)")
check(scr.events == [("down", "e"), ("up", "e")], f"o E é UM toque rápido (desce e solta uma vez): {scr.events}")
check(ctl.watcher.changed_pct(scr.frame()) == 0.0, "a imagem nova ficou guardada na memória como referência")
scr.events.clear()
ok2 = ctl.execute(rdy())                            # segundo comando: o jogo troca a foto de volta
check(ok2 and ctl.executions == 2 and scr.downs == 2, "segundo comando também confirma com um toque")

scr = FakeScreen(delay=999)                         # a foto NUNCA muda
log = []
ctl = rv.ReviveController(mkcfg(revive_verifica_s=0.4, revive_tentativas=2), lambda k, m: log.append((k, m)), grab=lambda reg: scr.frame(),
                          key_down=scr.down, key_up=scr.up)
ok = ctl.execute(rdy())
check(not ok and scr.downs == 2, f"foto não muda: exatamente 2 toques (o limite), não mais: {scr.downs}")
check(scr.down_t[1] - scr.down_t[0] >= 0.4, f"o 2º toque só veio depois da janela de verificação: {scr.down_t[1] - scr.down_t[0]:.2f}s")
check(scr.events[-1] == ("up", "e") and len(scr.events) == 4, "cada toque foi solto")
check(any(k == "ALERTA" and "NÃO mudou" in m for k, m in log), "registra ALERTA explicando que a foto não mudou")

scr = FakeScreen(delay=0.2, ignore=1)               # o jogo ignora o 1º toque
ctl = rv.ReviveController(mkcfg(revive_verifica_s=0.5), None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
ok = ctl.execute(rdy())
check(ok and scr.downs == 2 and scr.down_t[1] - scr.down_t[0] >= 0.5, "1º toque não usado: o 2º vem só depois da janela e confirma")

scr = FakeScreen(delay=0.6)                         # o efeito demora, mas acontece dentro da janela
ctl = rv.ReviveController(mkcfg(revive_verifica_s=1.0), None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
ok = ctl.execute(rdy())
check(ok and scr.downs == 1, f"efeito lento (0,6 s) dentro da janela de 1 s: UM toque só ({scr.downs})")

scr = FakeScreen(delay=999)
cy_stop = rdy()
ctl = rv.ReviveController(mkcfg(revive_verifica_s=5.0, revive_tentativas=0), None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
threading.Timer(0.3, cy_stop.cancel).start()
t0 = time.time()
ok = ctl.execute(cy_stop)
check(not ok and time.time() - t0 < 1.0 and scr.downs == 1, "bot desligado durante a verificação: para na hora e não toca de novo")

scr = FakeScreen(delay=0.2)
ctl = rv.ReviveController(mkcfg(), None, grab=lambda reg: scr.frame(), key_down=scr.down, key_up=scr.up)
ctl.watcher.remember(np.full((40, 60, 3), 123, np.uint8))     # memória velha: já difere da foto atual ANTES de apertar
ok = ctl.execute(rdy())
check(ok and scr.downs == 1, "memória velha: a referência é refeita logo antes do toque e só a mudança de verdade confirma")
log = []
ctl = rv.ReviveController(mkcfg(regiao_revive_foto=None), lambda k, m: log.append((k, m)), grab=lambda r: None, key_down=scr.down, key_up=scr.up)
check(ctl.execute(rdy()) is False and any(k == "ALERTA" for k, _ in log), "sem área da foto: não aperta nada e avisa")
ctl = rv.ReviveController(mkcfg(revive_tecla="tecla-invalida"), lambda k, m: log.append((k, m)), grab=lambda r: scr.frame(), key_down=scr.down, key_up=scr.up)
scr.events.clear()
check(ctl.execute(rdy()) is False and not scr.events, "tecla inválida: não aperta nada")
w = rv.ChangeWatcher()
a = np.full((20, 20, 3), 100, np.uint8)
w.remember(a)
b = a.copy(); b[:1, :] = 255                          # 5% dos pixels
check(abs(w.changed_pct(b) - 5.0) < 0.01 and w.changed_pct(a) == 0.0, "changed_pct mede a % de pixels que mudaram")
n = a.astype(np.int16) + rng.integers(-6, 7, a.shape); n = n.clip(0, 255).astype(np.uint8)
check(w.changed_pct(n) == 0.0, "ruído leve de cor não conta como mudança")

print("4. LifeSkillMonitor")
rv.LIFEBAR_PATH = os.path.join(tempfile.mkdtemp(), "sem_sprite.png")   # o teste não pode depender do lifebar.png real do projeto
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

# ---------------------------------------------------------------- 5. shooter -> R -> (intervalo) -> E
print("5. shooter: R, depois de 0,8 s o revive (E)")
from core import keys as kb                                  # noqa: E402


def build(seq=True, revive=True, delay=0.3, verify=0.5, seq_keys=None, gap_cfg=None, confirm=0.0, ignore=0, **more):
    """Monta shooter + revive com teclas/tela falsas; devolve (controller, ordem_dos_eventos, tela, revive)."""
    order = []
    scr_ = FakeScreen(delay=delay, ignore=ignore)
    extra = {"revive_intervalo_s": gap_cfg} if gap_cfg is not None else {}
    cfg = mkcfg(shooter_ativo=True, shooter_distancia_px=250, shooter_parada_s=0.6, shooter_tolerancia_px=6, shooter_tecla="q",
                regiao_sprite=[1000, 200, 900, 500], pokemon_limiar=0.7, shooter_seq_ativo=seq, revive_ativo=revive,
                revive_verifica_s=verify, shooter_confirma_s=confirm,
                shooter_seq_teclas=seq_keys or [{"tecla": "r", "espera_ms": 0}], **{"shooter_seq_intervalo_ms": 50, "shooter_seq_repetir_s": 0.3, **extra, **more})
    lg = []
    rc_ = rv.ReviveController(cfg, lambda k, m: lg.append((k, m)), grab=lambda reg: scr_.frame(),
                              key_down=lambda k: (order.append((time.monotonic(), "down", k)), scr_.down(k)),
                              key_up=lambda k: (order.append((time.monotonic(), "up", k)), scr_.up(k)))
    rc_.arm()
    c_ = sh.ShooterController(cfg, lambda k, m: lg.append((k, m)), revive=rc_)
    c_._tmpl_loaded, c_._tmpl = True, object()
    c_.lg = lg
    return c_, order, scr_, rc_


REAL_TAP = kb.tap                                    # guardada: o teste 5 troca kb.tap por um falso
sh.locate_pokemon = lambda frame, tmpl, limiar, near=None, margin=0: (450.0, 250.0)
taps = []
sh.kb.tap = lambda key, hold_s=0.05: taps.append((time.monotonic(), key))      # não envia tecla de verdade
sh.kb.move_mouse = lambda x, y: None
sh.AIM_SETTLE_S = 0
INSIDE, OUTSIDE = [(550.0, 250.0, 1.0)], [(1300.0, 250.0, 1.0)]

c5, order, scr, rc = build()
c5.step(None, INSIDE)                                 # tudo dentro do limite
time.sleep(3.0)
t_r = [t for t, k in taps if k == "r"]
downs_e = [t for t, a_, k in order if a_ == "down" and k == "e"]
check(len(t_r) == 6 and not [k for _, k in taps if k != "r"], f"o R é apertado VÁRIAS vezes (0,3 s / 50 ms = 6): {len(t_r)}x")
check(t_r[-1] - t_r[0] >= 0.2, f"repetiu o R por {t_r[-1] - t_r[0]:.2f}s")
check(bool(downs_e), "o E (revive) foi apertado")
gap = downs_e[0] - t_r[-1] if downs_e and t_r else -1
check(gap >= 0.8, f"o primeiro E saiu {gap:.2f}s depois do ÚLTIMO R (mínimo 0,80 s)")
check(c5.combo_done, "terminou R + E: combo_done=True (o runner volta a andar)")
check(order[-1][1:] == ("up", "e") and rc.executions == 1, "revive segurou E, a foto mudou e a tecla foi solta")
check(any("depois do R" in m for k, m in c5.lg if k == "INFO"), "o log informa quanto tempo o E esperou depois do R")
n = len(order)
c5.step(None, INSIDE); time.sleep(0.3)
check(len(order) == n, "continua tudo dentro do limite: não repete o ciclo")
n_r = len(t_r)
c5.step(None, OUTSIDE); c5.step(None, INSIDE); time.sleep(3.0)
r_all = [t for t, k in taps if k == "r"]
check(len(r_all) > n_r and rc.executions == 2, "sprite saiu e voltou: novo ciclo, novo R, novo E")
e_second = [t for t, a_, k in order if a_ == "down" and k == "e" and t > r_all[n_r]]
check(bool(e_second) and e_second[0] - r_all[-1] >= 0.8, "no segundo ciclo o E também espera 0,8 s depois do último R")

print("   revive ligado, sequência (R) DESLIGADA: sem R não há E")
taps.clear()
c6, order, scr, rc6 = build(seq=False)
c6.step(None, INSIDE); time.sleep(1.2)
check(not order and not scr.events and not taps, "nenhuma tecla enviada (nem R, nem E)")
check(any("só pode ser apertado depois do R" in m for k, m in c6.lg), "avisa por que o E não saiu")

print("   revive DESLIGADO: nada de E")
c7, order, scr, rc7 = build(revive=False)
taps.clear()
c7.step(None, INSIDE); time.sleep(1.2)
time.sleep(1.0)
check(not order and not scr.events and taps and {k for _, k in taps} == {"r"}, "só o R sai; o E não")

print("   o R falha ao enviar: o E NÃO sai")
c8, order, scr, rc8 = build()
taps.clear()
def boom(key, hold_s=0.05):
    raise OSError("SendInput recusado")
sh.kb.tap = boom
c8.step(None, INSIDE); time.sleep(1.0)
check(not order and not scr.events, "R falhou: nenhuma tecla E foi enviada")
check(any(k == "ERRO" and "NÃO será apertado sem o R" in m for k, m in c8.lg), "log explica que o E não sai sem o R")
sh.kb.tap = lambda key, hold_s=0.05: taps.append((time.monotonic(), key))

print("   o bot volta a andar durante o intervalo de 0,8 s: o E NÃO sai")
c9, order, scr, rc9 = build()
taps.clear()
c9.step(None, INSIDE); time.sleep(0.5)                # R já foi repetido, E esperando os 0,8 s
check(taps and {k for _, k in taps} == {"r"} and not order, "R apertado; E ainda esperando o intervalo")
c9.end()                                              # o bot voltou a andar
time.sleep(1.2)
check(not order and not scr.events, "ciclo cancelado: nenhum E enviado depois disso")

print("   bot desligado durante a verificação do E: nenhum toque novo")
c10, order, scr, rc10 = build(delay=999, verify=5.0)
taps.clear()
c10.step(None, INSIDE); time.sleep(2.0)
check(scr.downs == 1 and c10.busy, "um toque no E e o bot continua parado verificando")
c10.end()
time.sleep(0.5)
check(scr.downs == 1 and not c10.busy and scr.events[-1] == ("up", "e"), "cancelado: nada mais é enviado e a tecla está solta")

print("   sequência com o E dentro dela é recusada")
c11, order, scr, rc11 = build(seq_keys=[{"tecla": "r", "espera_ms": 0}, {"tecla": "e", "espera_ms": 0}])
taps.clear()
c11.step(None, INSIDE); time.sleep(0.5)
check(not taps and not order, "sequência contém a tecla do revive: nada é enviado")
check(any(k == "ERRO" and "é a do revive" in m for k, m in c11.lg), "log explica o motivo")

print("   intervalo R -> E configurado pelo usuário é respeitado")
c12, order, scr, rc12 = build(gap_cfg=0.1)
taps.clear()
c12.step(None, INSIDE); time.sleep(2.0)
t_r = [t for t, k in taps if k == "r"]
downs_e = [t for t, a_, k in order if a_ == "down" and k == "e"]
d_ = downs_e[0] - t_r[-1]
check(downs_e and 0.1 <= d_ < 0.6, f"gap 0,1 s: E saiu {d_:.2f}s depois do último R")

print("   confirmação: espera 1 s e confere sprite nova fora do limite antes do R")
taps.clear()
c13, order, scr, rc13 = build(confirm=0.6)
c13.step(None, INSIDE); time.sleep(0.3); c13.step(None, INSIDE)
check(not taps, "antes de completar a confirmação nenhum R sai")
c13.step(None, INSIDE + OUTSIDE); time.sleep(0.5)          # chegou sprite nova LONGE durante a confirmação
check(not taps and any("R NÃO foi dado" in m for k, m in c13.lg), "sprite nova fora do limite: confirmação cancelada, sem R")
c13.step(None, INSIDE); time.sleep(0.7); c13.step(None, INSIDE); time.sleep(0.4)
check(taps, "tudo dentro de novo e confirmado por 0,6 s: o R é dado")
c13.end()

print("   padrão: R ~10x em 1,5 s (150 ms); tempo maior = mais apertos")
taps.clear()
c14, order, scr, rc14 = build(shooter_seq_repetir_s=1.5, shooter_seq_intervalo_ms=150)
c14.step(None, INSIDE); time.sleep(0.2)
check(c14.busy, "durante o R repetido o procedimento está em andamento (bot não anda)")
time.sleep(3.2)
t_r = [t for t, k in taps if k == "r"]
check(len(t_r) == 10 and 1.2 <= t_r[-1] - t_r[0] <= 1.6, f"R apertado {len(t_r)}x ao longo de {t_r[-1] - t_r[0]:.2f}s")
check(rc14.executions == 1 and c14.combo_done and not c14.busy, "E confirmado, procedimento concluído, bot liberado para andar")
taps.clear()
c14b, order, scr, _ = build(shooter_seq_repetir_s=3.0, shooter_seq_intervalo_ms=150)
c14b.step(None, INSIDE); time.sleep(3.4)
check(len([1 for _, k in taps if k == "r"]) == 20, f"3,0 s a cada 150 ms: {len([1 for _, k in taps if k == 'r'])}x (20)")
c14b.end()

print("   o jogo só usa o revive no 3º toque: um toque por vez, espaçados; o bot só anda depois")
taps.clear()
c15, order, scr, rc15 = build(delay=0.1, verify=0.4, ignore=2, revive_tentativas=0)
c15.step(None, INSIDE); time.sleep(2.2)
check(scr.downs == 3 and rc15.executions == 1 and c15.combo_done and not c15.busy, f"{scr.downs} toques até a foto mudar; só então o bot é liberado")
gaps = [b_ - a_ for a_, b_ in zip(scr.down_t, scr.down_t[1:])]
check(all(g >= 0.4 for g in gaps), f"intervalos entre toques ≥ janela de verificação (0,4 s): {[round(g, 2) for g in gaps]}")

print("   foto nunca muda: 3 toques no máximo e o bot é liberado com ALERTA")
taps.clear()
c16, order, scr, rc16 = build(delay=999, verify=0.3, revive_tentativas=3)
c16.step(None, INSIDE); time.sleep(2.2)
check(scr.downs == 3 and c16.combo_done and not c16.busy, f"{scr.downs} toques (limite 3) e depois o bot anda")

# ---------------------------------------------------------------- 6. as travas em si (Cycle + teclas reservadas)
print("6. Cycle e teclas reservadas")
from core.combo import ComboBlocked, effective_gap             # noqa: E402

check(effective_gap(0.1) == 0.1 and effective_gap(0) == 0 and effective_gap(1.5) == 1.5 and effective_gap("x") == 0.8,
      "effective_gap: valor do usuário; inválido volta a 0,8 s")
clock = [100.0]
cy = Cycle("e", 0.8, clock=lambda: clock[0])
sent = []
def press(c_):
    with c_.revive_press():
        sent.append(clock[0])
try:
    press(cy); blocked = False
except ComboBlocked:
    blocked = True
check(blocked and not sent, "E antes do R: bloqueado")
cy.mark_r_done()
for dt, expect in ((0.0, False), (0.5, False), (0.79, False), (0.8, True)):
    clock[0] = 100.0 + dt
    try:
        press(cy); ok_ = True
    except ComboBlocked:
        ok_ = False
    check(ok_ == expect, f"{dt:.2f}s depois do R: {'liberado' if expect else 'bloqueado'}")
    if ok_:
        break
cy.finish()
try:
    press(cy); blocked = False
except ComboBlocked:
    blocked = True
check(blocked, "ciclo já usado: E bloqueado de novo")
cyr = Cycle("e", 0.0, clock=lambda: clock[0], retry_gap_s=2.0); cyr.mark_r_done()
sent.clear(); press(cyr)
try:
    press(cyr); blocked = False
except ComboBlocked:
    blocked = True
check(blocked and len(sent) == 1, "segundo toque no E antes do fim da janela de verificação: bloqueado")
clock[0] += 2.0; press(cyr)
check(len(sent) == 2 and cyr.presses == 2, "passada a janela, o segundo toque é liberado")
cy2 = Cycle("e", 0.8, clock=lambda: clock[0]); cy2.mark_r_done(); clock[0] += 5; cy2.cancel()
try:
    press(cy2); blocked = False
except ComboBlocked:
    blocked = True
check(blocked, "ciclo cancelado (mesmo com o R feito e o intervalo cumprido): E bloqueado")
cy3 = Cycle("e", 0.8, clock=lambda: clock[0]); cy3.cancel(); cy3.mark_r_done()
check(not cy3.r_done, "R marcado depois do cancelamento não vale")

raw = []
kb.tap = REAL_TAP
real_send = kb._send_raw
kb._send_raw = lambda scan, ext, pyname, vk, up: raw.append((pyname, up))      # não toca no sistema
try:
    kb.reserve("e")
    for fn in (REAL_TAP, kb.hold_down):
        try:
            fn("e"); blocked = False
        except PermissionError:
            blocked = True
        check(blocked and not raw, f"keys.{getattr(fn, "__name__", "?")}('e') reservada e sem autorização: PermissionError, nada enviado")
    REAL_TAP("r", 0)
    check(raw == [("r", False), ("r", True)], "tecla NÃO reservada (R) continua funcionando")
    raw.clear()
    kb.hold_up("e")
    check(raw == [("e", True)], "soltar a tecla reservada nunca é bloqueado")
    raw.clear()
    with kb.authorized("e"):
        kb.hold_down("e")
    check(raw == [("e", False)], "dentro de `authorized` a tecla sai")
    raw.clear()
    res = []
    def other():
        try:
            REAL_TAP("e", 0); res.append("saiu")
        except PermissionError:
            res.append("bloqueado")
    with kb.authorized("e"):
        th = threading.Thread(target=other); th.start(); th.join()
    check(res == ["bloqueado"] and not raw, "a autorização vale só para a thread que a pediu")
    try:
        REAL_TAP("e", 0); blocked = False
    except PermissionError:
        blocked = True
    check(blocked, "fora do bloco a trava volta")
finally:
    kb._send_raw = real_send
    kb.reserve()

# o revive direto, sem ciclo / sem R: não aperta
scr = FakeScreen(delay=0.1); lg = []
ctl = rv.ReviveController(mkcfg(), lambda k, m: lg.append((k, m)), grab=lambda r: scr.frame(), key_down=scr.down, key_up=scr.up)
check(ctl.execute(None) is False and not scr.events, "execute() sem ciclo: recusado, nada enviado")
no_r = Cycle("e", 0.0)
check(ctl.execute(no_r) is False and not scr.events, "execute() com ciclo SEM o R executado: recusado, nada enviado")
wrong = rdy("x")
check(ctl.execute(wrong) is False and not scr.events, "execute() com ciclo de outra tecla: recusado, nada enviado")

print()
print("TUDO OK" if not fails else f"{len(fails)} FALHA(S): " + "; ".join(fails))
sys.exit(1 if fails else 0)
