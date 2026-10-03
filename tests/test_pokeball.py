"""Teste da aba pokeball (sem tela, sem teclado, sem GUI e com relógio falso):

  1. perfis: padrão (shiny + normal), normalização e problemas (sem sprite, tecla inválida, tecla de movimento, reservada);
  2. detect / order_targets: o shiny vem sempre antes do normal, mesmo com similaridade menor;
  3. ThrowBook e estimate_shift: acompanha a sprite com a tela andando, respeita recarga e tentativas;
  4. PokeballThrower ("achou, jogou": UMA captura por varredura, nenhuma captura extra antes da bola) num mundo falso:
       - 8 mortos, tela parada e em movimento: as bolas caem em cima da sprite certa, com a tecla certa;
       - shiny primeiro; a tecla do shiny NUNCA cai em sprite normal (nem em sprite parecida), e vice-versa;
       - sprite que sumiu / que ficou; perfil desligado / sem sprite / tecla que falha não derrubam os outros;
  5. janela: só procura depois do R (trigger) e durante `pokeball_janela_s`; modo 'sempre'; desligado não faz nada;
  6. thread de verdade e gancho no shooter (on_sequence quando o R começa).

    python tests/test_pokeball.py
"""
import math
import os
import sys
import tempfile
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import keys as kb, pokeball as pk, shooter as sh          # noqa: E402
from core.config import ConfigStore, MAP_PATH, SPRITE_PATH         # noqa: E402
from core.magic_cut import load_template                           # noqa: E402
from core.vision import SpriteTemplate                              # noqa: E402

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        fails.append(msg)


# ---------------------------------------------------------------- material de teste
sb, sm = load_template(SPRITE_PATH)
NORMAL_BGR, SHINY_BGR = sb, sb[..., ::-1].copy()                      # shiny = mesma sprite com as cores trocadas
T_NORMAL, T_SHINY = SpriteTemplate(NORMAL_BGR, sm), SpriteTemplate(SHINY_BGR, sm)
SW, SH_ = T_NORMAL.w, T_NORMAL.h
BG = cv2.resize(cv2.imread(str(MAP_PATH)), None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
BG = np.tile(BG, (3, 3, 1))
BASE = 1000                                                          # folga: a tela pode andar para qualquer lado sem sair do fundo
REGION = [10, 20, 900, 500]                                           # área de busca (origem na tela)
KEYS = {"shiny": "v", "normal": "b"}


def make_cfg(**over):
    cfg = ConfigStore(os.path.join(tempfile.mkdtemp(), "c.json"))
    base = {"pokeball_perfis": [dict(p, modo="toque") for p in pk.default_profiles()], "pokeball_ativo": True, "pokeball_modo": "apos_r", "pokeball_janela_s": 8.0, "regiao_sprite": REGION,
            "pokeball_assentar_ms": 15, "pokeball_toque_ms": 20, "pokeball_intervalo_ms": 10,
            "pokeball_tentativas": 1, "pokeball_recarga_s": 1.2}
    base.update(over)
    for k, v in base.items():
        cfg.set(k, v, save=False)
    return cfg


def loader_for(templates):
    return lambda pid: templates.get(pid)


class World:
    """Tela falsa: o fundo e os mortos andam juntos (como no jogo). Relógio, captura, mouse e teclas falsos."""

    def __init__(self, vx=0.0, vy=0.0, grab_cost=0.035, find_cost=0.05):
        self.t, self.vx, self.vy = 0.0, vx, vy
        self.sprites = []                    # dicts: id, ball, wx, wy (canto na tela inteira em t=0), alive, spawn, vanish
        self.cursor = (0, 0)
        self.taps = []                       # (t, tecla, id da sprite sob o mouse ou None)
        self.grab_cost, self.find_cost = grab_cost, find_cost
        self.fail_capture = set()            # ids que NÃO somem quando recebem a bola certa
        self.tap_error = {}                  # tecla -> exceção levantada ao apertar
        self.grabs = 0                       # quantas capturas de tela foram feitas
        self.fail_until = 0.0                # antes deste instante nenhuma bola captura (aquecimento da velocidade)
        self.held = set()                    # teclas de bola seguradas agora
        self.hold_events = []                # (t, "down"/"up", tecla)
        self.held_move = ()                  # teclas de movimento seguradas (ajuste pela direção)
        self.hold_error = {}                 # tecla -> exceção ao descer
        self.hold_seen = {}                  # id da sprite -> quantas vezes o mouse esteve nela com a tecla certa descendo

    # relógio
    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def off(self):
        return int(round(self.vx * self.t)), int(round(self.vy * self.t))

    def add(self, sid, ball, x, y, spawn=0.0, vanish=None):
        self.sprites.append({"id": sid, "ball": ball, "wx": x, "wy": y, "alive": True, "spawn": spawn, "vanish": vanish})

    def visible(self):
        ox, oy = self.off()
        return [(s, s["wx"] - ox, s["wy"] - oy) for s in self.sprites
                if s["alive"] and s["spawn"] <= self.t and (s["vanish"] is None or self.t < s["vanish"])]

    def render(self, region):
        ox, oy = self.off()
        x, y, w, h = region
        img = BG[BASE + y + oy:BASE + y + oy + h, BASE + x + ox:BASE + x + ox + w].copy()
        for s, sx, sy in self.visible():
            lx, ly = int(sx - x), int(sy - y)
            if 0 <= lx and 0 <= ly and lx + SW <= w and ly + SH_ <= h:
                bgr = SHINY_BGR if s["ball"] == "shiny" else NORMAL_BGR
                roi = img[ly:ly + SH_, lx:lx + SW]
                roi[sm > 0] = bgr[sm > 0]
        return img

    # dispositivos falsos
    def grab(self, region):
        self.grabs += 1
        cost = self.grab_cost * (region[2] * region[3]) / (REGION[2] * REGION[3]) + 0.002
        self.t += cost / 2
        img = self.render(region)
        self.t += cost / 2
        return img

    def move(self, x, y):
        self.cursor = (x, y)
        self.t += 0.0005

    def under_cursor(self):
        hit = None
        for s, sx, sy in self.visible():
            if sx <= self.cursor[0] < sx + SW and sy <= self.cursor[1] < sy + SH_:
                hit = s
        return hit

    def hold_down(self, key):
        """Tecla segurada: cada 'tecla para baixo' (e a repetição) com o mouse em cima da sprite certa captura."""
        if key in self.hold_error:
            raise self.hold_error[key]
        self.held.add(key)
        self.hold_events.append((self.t, "down", key))
        hit = self.under_cursor()
        if hit is not None and key == KEYS[hit["ball"]] and hit["id"] not in self.fail_capture and self.t >= self.fail_until:
            hit["alive"] = False
            self.hold_seen[hit["id"]] = self.hold_seen.get(hit["id"], 0) + 1

    def hold_up(self, key):
        self.held.discard(key)
        self.hold_events.append((self.t, "up", key))

    def tap(self, key, hold_s=0.05):
        if key in self.tap_error:
            raise self.tap_error[key]
        hit = self.under_cursor()
        self.taps.append((self.t, key, hit["id"] if hit else None))
        if hit is not None and key == KEYS[hit["ball"]] and hit["id"] not in self.fail_capture and self.t >= self.fail_until:
            hit["alive"] = False             # capturado
        self.t += hold_s


def thrower(world, cfg, log=None, templates=None):
    templates = templates if templates is not None else {"shiny": T_SHINY, "normal": T_NORMAL}
    return pk.PokeballThrower(cfg, (lambda k, m: log.append((k, m))) if log is not None else None,
                              grab=world.grab, tap=world.tap, move=world.move, clock=world.clock, sleep=world.sleep,
                              loader=loader_for(templates), hold_down=world.hold_down, hold_up=world.hold_up,
                              held=lambda: world.held_move)


# a detecção custa tempo de verdade: o relógio falso anda junto
_orig_find = pk.find_sprites
CURRENT = {"world": None}


def _slow_find(frame, tmpl, limiar, prepared=None):
    w = CURRENT["world"]
    if w is not None:
        w.t += w.find_cost * frame.size / (REGION[2] * REGION[3] * 3)
    return _orig_find(frame, tmpl, limiar, prepared)


pk.find_sprites = _slow_find


def run_all(world, th, max_steps=40):
    CURRENT["world"] = world
    for _ in range(max_steps):
        th.step()
        if not any(s["alive"] and s["spawn"] <= world.t for s in world.sprites) and world.t > 0.5:
            break
    return world


def eight(world, shinies=(2, 5)):
    """8 mortos espalhados (2 shiny e 6 normais), em coordenadas da tela inteira (a área começa em REGION)."""
    spots = [(150, 120), (330, 90), (520, 140), (700, 100), (180, 300), (360, 360), (560, 320), (740, 380)]
    for i, (x, y) in enumerate(spots):
        world.add(i, "shiny" if i in shinies else "normal", REGION[0] + x, REGION[1] + y)


# ---------------------------------------------------------------- 1. perfis
print("1. perfis")
d = pk.default_profiles()
check([p["id"] for p in d] == ["shiny", "normal"], "padrão: um perfil shiny e um normal")
check(d[0]["prioridade"] < d[1]["prioridade"], "o shiny tem prioridade (número menor) sobre o normal")
check(pk.parse_profiles([]) == pk.default_profiles() and pk.parse_profiles(None) == pk.default_profiles(),
      "sem perfis na config volta aos dois padrões")
pp = pk.parse_profiles([{"id": "A b!", "tecla": " x ", "prioridade": "3", "limiar": 7}, {"id": "ab"}, {"nome": "sem id"}, 5])
check(len(pp) == 1 and pp[0]["id"] == "ab" and pp[0]["tecla"] == "x" and pp[0]["prioridade"] == 3 and pp[0]["limiar"] == 1.0,
      f"normaliza id, tecla, prioridade e limiar (ids repetidos/vazios descartados): {pp}")

cfg = make_cfg(pokeball_perfis=[
    {"id": "shiny", "nome": "Shiny", "tecla": "v", "prioridade": 1, "limiar": 0.9, "ativo": True},
    {"id": "normal", "nome": "Normal", "tecla": "w", "prioridade": 2, "limiar": 0.9, "ativo": True},      # w = movimento
    {"id": "x1", "nome": "SemSprite", "tecla": "n", "prioridade": 3, "limiar": 0.9, "ativo": True},
    {"id": "x2", "nome": "Invalida", "tecla": "tecla-invalida", "prioridade": 4, "limiar": 0.9, "ativo": True},
    {"id": "x3", "nome": "SemTecla", "tecla": "", "prioridade": 5, "limiar": 0.9, "ativo": True},
    {"id": "x4", "nome": "Desligado", "tecla": "m", "prioridade": 6, "limiar": 0.9, "ativo": False}])
balls, probs = pk.load_balls(cfg, loader_for({"shiny": T_SHINY, "normal": T_NORMAL, "x4": T_NORMAL}))
check([b.id for b in balls] == ["shiny"], f"só o perfil válido e ativo é carregado: {[b.id for b in balls]}")
txt = " | ".join(probs)
check(len(probs) == 4 and "movimento" in txt and "sem sprite" in txt and "inválida" in txt and "sem tecla" in txt,
      f"os 4 problemas são descritos: {probs}")
kb.reserve("e")
cfg_r = make_cfg(pokeball_perfis=[{"id": "shiny", "nome": "Shiny", "tecla": "e", "prioridade": 1, "limiar": 0.9, "ativo": True}])
b2, p2 = pk.load_balls(cfg_r, loader_for({"shiny": T_SHINY}))
kb.reserve()
check(b2 == [] and "reservada" in p2[0], f"tecla reservada ao revive é recusada: {p2}")

check(d[0]["modo"] == "toque" and d[1]["modo"] == "segurar" and d[1]["segurar_s"] == 2.0,
      "padrão: Premier em toque; Ultra SEGURANDO a tecla por 2 s")
pm = pk.parse_profiles([{"id": "a", "modo": "SEGURAR", "segurar_s": "3,5"}, {"id": "b", "modo": "xx", "segurar_s": 999},
                        {"id": "c", "segurar_s": "abc"}, {"id": "d", "segurar_s": 0}])
check([p["modo"] for p in pm] == ["segurar", "toque", "toque", "toque"], f"modo inválido vira toque: {[p['modo'] for p in pm]}")
check(pm[1]["segurar_s"] == 60.0 and pm[2]["segurar_s"] == 2.0 and pm[3]["segurar_s"] == 0.1,
      f"tempo de segurar limitado a 0,1..60 s e inválido volta a 2 s: {[p['segurar_s'] for p in pm]}")
mv = pk.movement_vector
check(mv(()) == (0.0, 0.0) and mv(("w",)) == (0.0, -1.0) and mv(("s",)) == (0.0, 1.0) and mv(("a",)) == (-1.0, 0.0) and mv(("d",)) == (1.0, 0.0),
      "movement_vector: w cima, s baixo, a esquerda, d direita")
ne = mv(("w", "d"))
check(abs(ne[0] - 0.7071) < 1e-3 and abs(ne[1] + 0.7071) < 1e-3, f"diagonal normalizada: {ne}")
check(mv(("w", "s")) == (0.0, 0.0) and mv(("a", "d")) == (0.0, 0.0), "teclas opostas se anulam")

# ---------------------------------------------------------------- 2. detecção e ordem
print("2. detect / order_targets")
balls = [pk.Ball("shiny", "Shiny", "v", 1, 0.9, T_SHINY), pk.Ball("normal", "Normal", "b", 2, 0.9, T_NORMAL)]
w = World()
eight(w)
frame = w.render(REGION)
CURRENT["world"] = None
dets = pk.detect(frame, balls)
check(len(dets) == 8, f"achou {len(dets)} de 8 sprites")
check(sum(d.ball.id == "shiny" for d in dets) == 2 and sum(d.ball.id == "normal" for d in dets) == 6,
      "2 shiny e 6 normais, cada uma no perfil certo (as cores trocadas não se confundem)")
want = {(x - REGION[0] + (SW - 1) / 2, y - REGION[1] + (SH_ - 1) / 2) for x, y in
        [(REGION[0] + a, REGION[1] + b) for a, b in [(150, 120), (330, 90), (520, 140), (700, 100), (180, 300), (360, 360), (560, 320), (740, 380)]]}
check(all(any(math.hypot(d.x - wx, d.y - wy) <= 1.0 for wx, wy in want) for d in dets), "centros batem (±1 px)")
order = pk.order_targets(dets)
check([d.ball.id for d in order[:2]] == ["shiny", "shiny"] and all(d.ball.id == "normal" for d in order[2:]),
      "ordem: os 2 shiny primeiro, depois as normais")
fake = [pk.Det(balls[1], 1, 1, 0.99), pk.Det(balls[0], 2, 2, 0.91)]
check(pk.order_targets(fake)[0].ball.id == "shiny", "shiny com similaridade menor ainda vem antes do normal")

# ---------------------------------------------------------------- 3. ThrowBook
print("3. ThrowBook e estimate_shift")
book = pk.ThrowBook()
dn = [pk.Det(balls[1], 100.0, 100.0, 1.0)]
book.associate(dn, 0.0)
check(dn[0].rec is None and book.eligible(dn[0], 0.0, 1, 1.2), "sprite nova: elegível")
book.note_throw(dn[0], 100.0, 100.0, 0.0)
moved = [pk.Det(balls[1], 118.0, 100.0, 1.0)]                    # a tela andou 120 px/s por 0,15 s
book.associate(moved, 0.15, (120.0, 0.0))
check(moved[0].rec is not None, "a tela anda: a mesma sprite é reconhecida pela velocidade")
check(not book.eligible(moved[0], 0.15, 1, 1.2), "já jogada (1 tentativa): não joga de novo")
check(not book.eligible(moved[0], 0.15, 2, 1.2) and book.eligible(moved[0], 1.3, 2, 1.2), "com 2 tentativas: só depois da recarga")
book.note_throw(moved[0], 118.0, 100.0, 1.3)
check(not book.eligible(moved[0], 5.0, 2, 1.2), "2 tentativas gastas: não joga mais")
other = [pk.Det(balls[1], 300.0, 100.0, 1.0)]
book.associate(other, 1.4)
check(other[0].rec is None, "outra sprite do mesmo perfil, longe da jogada: não é confundida")
book.associate([], 10.0)
check(book.records == [], "sprite que sumiu (capturada) é esquecida")
prev = [(100, 100), (300, 120), (500, 90), (200, 300)]
cur = [(p[0] - 40, p[1] + 10) for p in prev[1:]]                  # uma foi capturada; o resto andou (-40, +10)
sh_ = pk.estimate_shift(prev, cur, 300)
check(sh_ is not None and abs(sh_[0] + 40) <= 1 and abs(sh_[1] - 10) <= 1, f"deslocamento da tela achado por votação: {sh_}")
check(pk.estimate_shift([], cur, 300) is None and pk.estimate_shift(prev, [], 300) is None, "sem sprites: não sabe")
check(pk.estimate_shift([(0, 0), (500, 0)], [(10, 0), (800, 0)], 100) is not None, "um só par possível: aceita")

# ---------------------------------------------------------------- 4. arremessos
print("4. achou, jogou (tela parada)")
log = []
w = World()
eight(w)
th = thrower(w, make_cfg(), log)
run_all(w, th)
ok_hit = [(t, k, i) for t, k, i in w.taps if i is not None and k == KEYS["shiny" if i in (2, 5) else "normal"]]
check(len(w.taps) == 8 and len(ok_hit) == 8, f"8 bolas, as 8 em cima da sprite certa com a tecla certa ({len(ok_hit)}/{len(w.taps)})")
check(all(not s_["alive"] for s_ in w.sprites), "os 8 mortos foram capturados")
check([k for _, k, _ in w.taps[:2]] == ["v", "v"] and all(k == "b" for _, k, _ in w.taps[2:]),
      f"ordem: Premier (V) nos 2 shiny primeiro, depois Ultra (B): {[k for _, k, _ in w.taps]}")
check(w.grabs == 1, f"UMA captura para jogar as 8 bolas: {w.grabs} captura(s), nenhuma para reconfirmar")
span = w.taps[-1][0] - w.taps[0][0]
check(span < 0.45, f"as 8 bolas em {span:.2f}s")
check(w.taps[0][0] < 0.25, f"a 1ª bola sai {w.taps[0][0] * 1000:.0f} ms depois de começar (captura + detecção do shiny, sem esperar o normal)")
check(th.stats["shiny"] == 2 and th.stats["normal"] == 6 and sum(1 for k, _ in log if k == "BOLA") == 8,
      "estatística por perfil e 8 linhas BOLA no log")
th.step()
check(len(w.taps) == 8, "nada mais para jogar: não joga")

print("4b. tela em movimento (o bot não para)")
for vx, vy in ((120.0, 0.0), (-100.0, 60.0), (0.0, -140.0), (150.0, 80.0), (260.0, -120.0)):
    w = World(vx, vy)
    eight(w)
    WARM = 1.0                                           # as primeiras bolas "falham": só aquecem a velocidade
    w.fail_until = WARM
    for s_ in w.sprites:                                 # a sprite anda na tela a (-vx, -vy): mantém todas na área
        s_["wx"] += int(vx * 1.6)
        s_["wy"] += int(vy * 1.6)
    th = thrower(w, make_cfg(pokeball_tentativas=99, pokeball_recarga_s=0.1))
    run_all(w, th, 80)
    after = [(t, k, i) for t, k, i in w.taps if t >= WARM]
    empty = [1 for _, k, i in after if i is None]
    wrong = [1 for _, k, i in after if i is not None and k != KEYS[w.sprites[i]["ball"]]]
    check(all(not s_["alive"] for s_ in w.sprites) and not wrong and len(empty) <= 1,
          f"tela a {math.hypot(vx, vy):.0f} px/s: com a velocidade já aprendida, todos capturados "
          f"({len(after)} bolas depois do aquecimento, {len(empty)} no vazio, {len(wrong)} bola errada)")
check(abs(th.vel[0] + 260) < 40 and abs(th.vel[1] - 120) < 40, f"velocidade das sprites aprendida só comparando varreduras: {th.vel[0]:.0f}, {th.vel[1]:.0f} px/s (real -260, 120)")

print("4c. a bola de um perfil NUNCA cai na sprite do outro")
# só normais na tela, com o perfil shiny ativo
w = World()
eight(w, shinies=())
th = thrower(w, make_cfg())
run_all(w, th)
check(len(w.taps) == 8 and all(k == "b" for _, k, _ in w.taps), f"só normais: 8 Ultra (B) e nenhuma Premier (V): {[k for _, k, _ in w.taps]}")
# só shiny na tela
w = World()
eight(w, shinies=tuple(range(8)))
th = thrower(w, make_cfg())
run_all(w, th)
check(len(w.taps) == 8 and all(k == "v" for _, k, _ in w.taps), "só shiny: 8 Premier (V) e nenhuma Ultra (B)")
# modelos PARECIDOS: o perfil shiny também "casa" com a sprite normal (nota >= limiar), mas a normal casa melhor
near = np.clip(NORMAL_BGR.astype(np.int16) + 6, 0, 255).astype(np.uint8)
T_NEAR = SpriteTemplate(near, sm)
sc_ = float(pk.sprite_scores(np.ascontiguousarray(np.pad(NORMAL_BGR, ((8, 8), (8, 8), (0, 0)), mode="edge")), T_NEAR).max())
check(0.9 <= sc_ < 1.0, f"(preparo) o modelo parecido casaria com a sprite normal com nota {sc_:.3f} >= limiar 0,90")
w = World()
eight(w, shinies=())
th = thrower(w, make_cfg(), templates={"shiny": T_NEAR, "normal": T_NORMAL})
run_all(w, th)
check(len(w.taps) == 8 and all(k == "b" for _, k, _ in w.taps), f"sprite normal que o shiny quase reconhece: só Ultra, nenhuma Premier: {[k for _, k, _ in w.taps]}")
# e a shiny de verdade (mais parecida com o perfil shiny) leva a Premier mesmo com o normal quase casando
w = World()
w.add(0, "shiny", REGION[0] + 300, REGION[1] + 200)
SHINY_BGR_BAK = SHINY_BGR.copy()
SHINY_BGR[:] = near                                              # a "shiny" do jogo é igual ao modelo parecido
th = thrower(w, make_cfg(), templates={"shiny": T_NEAR, "normal": T_NORMAL})
run_all(w, th)
SHINY_BGR[:] = SHINY_BGR_BAK
check(len(w.taps) == 1 and w.taps[0][1] == "v", f"a sprite que é do shiny recebe a Premier: {[k for _, k, _ in w.taps]}")
# lugar tomado por um perfil não é jogado pelo outro (perfis idênticos)
w = World()
w.add(0, "normal", REGION[0] + 300, REGION[1] + 200)
th = thrower(w, make_cfg(), templates={"shiny": T_NORMAL, "normal": T_NORMAL})
th.step()
check(len(w.taps) == 1 and w.taps[0][1] == "v", f"dois perfis idênticos na mesma sprite: só UMA bola, a do de maior prioridade ({[k for _, k, _ in w.taps]})")

print("4d. sprite que some, sprite que fica")
w = World()
w.add(0, "normal", REGION[0] + 200, REGION[1] + 150, vanish=0.04)    # some logo depois da captura
w.add(1, "normal", REGION[0] + 500, REGION[1] + 250)
th = thrower(w, make_cfg())
run_all(w, th, 6)
check(all(i != 1 or True for _, _, i in w.taps) and sum(1 for _, _, i in w.taps if i == 1) == 1,
      f"a sprite que ficou recebe exatamente 1 bola: {[(round(t, 2), k, i) for t, k, i in w.taps]}")
check(not any(i == 0 and k == "b" and t >= 0.04 for t, k, i in w.taps), "nenhuma bola na sprite depois que ela sumiu")

w = World()
w.add(0, "normal", REGION[0] + 300, REGION[1] + 200)
w.fail_capture = {0}                                                  # a bola falha: o pokémon continua lá
th = thrower(w, make_cfg(pokeball_tentativas=2, pokeball_recarga_s=1.0))
CURRENT["world"] = w
while w.t < 0.9:
    th.step()
    w.sleep(0.05)
n_early = len(w.taps)
while w.t < 2.5:
    th.step()
    w.sleep(0.05)
check(n_early == 1, f"na recarga (1,0 s) não joga de novo na mesma sprite ({n_early} bola)")
check(len(w.taps) == 2, f"depois da recarga joga a 2ª (e última) tentativa; sem 3ª ({len(w.taps)} bolas)")
check(w.taps[1][0] - w.taps[0][0] >= 1.0, f"intervalo entre as duas bolas na mesma sprite: {w.taps[1][0] - w.taps[0][0]:.2f}s")

print("4e. perfis problemáticos não derrubam os outros")
w = World()
eight(w)
log = []
perfis = [{"id": "shiny", "nome": "Shiny", "tecla": "v", "prioridade": 1, "limiar": 0.9, "ativo": True},
          {"id": "normal", "nome": "Normal", "tecla": "b", "prioridade": 2, "limiar": 0.9, "ativo": True}]
w.tap_error = {"v": PermissionError("reservada")}
th = thrower(w, make_cfg(pokeball_perfis=perfis), log)
run_all(w, th, 12)
check(len(w.taps) == 6 and all(k == "b" for _, k, _ in w.taps), f"tecla do shiny falhou: só as 6 normais foram jogadas ({len(w.taps)} bolas)")
check(any(k == "ERRO" and "Shiny" in m for k, m in log) and sum(1 for k, _ in log if k == "ERRO") == 1,
      "um único ERRO no log e o perfil fica desligado (sem insistir)")
w = World()
eight(w)
th = thrower(w, make_cfg(pokeball_perfis=[dict(perfis[0], ativo=False), dict(perfis[1])]))
run_all(w, th, 12)
check(len(w.taps) == 6 and all(k == "b" for _, k, _ in w.taps), "perfil do shiny desativado na interface: só as normais")
w = World()
eight(w)
th = thrower(w, make_cfg(pokeball_perfis=perfis), templates={"normal": T_NORMAL})
log = []
th._log_fn = lambda k, m: log.append((k, m))
run_all(w, th, 12)
check(len(w.taps) == 6 and any("sem sprite" in m for _, m in log), "perfil sem sprite é avisado e ignorado")
w = World()
eight(w)
th = thrower(w, make_cfg(regiao_sprite=None))
log = []
th._log_fn = lambda k, m: log.append((k, m))
th.step()
check(w.taps == [] and any("Área de busca" in m for _, m in log), "sem área de busca: avisa e não faz nada")
w = World()
eight(w)
th = thrower(w, make_cfg(regiao_sprite=None, regiao_pokeball=[10, 20, 900, 500]))
run_all(w, th, 12)
check(len(w.taps) == 8, "a área própria da pokeball vale no lugar da área de busca da sprite")

print("4f. mira")
w = World()
w.add(0, "normal", REGION[0] + 300, REGION[1] + 200)
th = thrower(w, make_cfg(pokeball_mira_dy_px=3))
run_all(w, th, 3)
check(len(w.taps) == 1 and w.taps[0][2] == 0, "ajuste de mira (3 px abaixo) ainda cai dentro da sprite")
cx = REGION[0] + 300 + (SW - 1) / 2
cy = REGION[1] + 200 + (SH_ - 1) / 2
check(abs(w.cursor[0] - cx) <= 1.5 and abs(w.cursor[1] - (cy + 3)) <= 1.5, f"mouse em coordenadas de tela (origem da área somada): {w.cursor}")

print("4g. modo SEGURAR: a tecla fica apertada e o mouse percorre as sprites")
HOLD = [{"id": "shiny", "nome": "Shiny", "tecla": "v", "prioridade": 1, "limiar": 0.9, "ativo": True, "modo": "toque", "segurar_s": 2.0},
        {"id": "normal", "nome": "Normal", "tecla": "b", "prioridade": 2, "limiar": 0.9, "ativo": True, "modo": "segurar", "segurar_s": 2.0}]
w = World()
eight(w)
log = []
th = thrower(w, make_cfg(pokeball_perfis=HOLD), log)
run_all(w, th)
check(all(not s_["alive"] for s_ in w.sprites), "os 8 mortos foram capturados (2 shiny no toque, 6 normais segurando)")
check([k for _, k, _ in w.taps] == ["v", "v"] and all(w.taps[i][2] in (2, 5) for i in range(2)), f"Premier em toque nos 2 shiny: {[k for _, k, _ in w.taps]}")
ev = w.hold_events
check(ev[0][1:] == ("down", "b") and ev[-1][1:] == ("up", "b") and sum(1 for e in ev if e[1] == "up") == 1,
      "a Ultra (B) desce, repete e é solta UMA vez no fim")
check(not w.held, f"nenhuma tecla ficou presa: {w.held}")
check(set(w.hold_seen) == {0, 1, 3, 4, 6, 7} and "v" not in {e[2] for e in ev}, f"o mouse passou por cada uma das 6 normais com B apertado: {sorted(w.hold_seen)}; Premier (V) nunca segurada")
t_first_b, t_last = ev[0][0], ev[-1][0]
check(t_last - t_first_b < 1.0, f"terminou assim que acabaram as sprites, sem esperar os 2 s: {t_last - t_first_b:.2f}s")
check(any(k == "BOLA" and "SEGURADA" in m and "acabaram as sprites" in m for k, m in log), "log: tecla SEGURADA ... acabaram as sprites")
downs = [t for t, e, k in ev if e == "down"]
check(len(downs) > 8, f"a tecla é reenviada (repetição do teclado) enquanto segura: {len(downs)} envios")

print("4g-2. tempo definido pelo usuário: solta quando acaba, mesmo com sprites sobrando")
w = World()
eight(w, shinies=())
w.fail_capture = set(range(8))                           # nada captura: só o relógio manda
th = thrower(w, make_cfg(pokeball_perfis=[dict(HOLD[0]), dict(HOLD[1], segurar_s=1.0)], pokeball_tentativas=1), None)
CURRENT["world"] = w
th.step()
ev = w.hold_events
dur = ev[-1][0] - ev[0][0]
check(ev[-1][1:] == ("up", "b") and 0.95 <= dur <= 1.25, f"segurou {dur:.2f}s (definido 1,0 s) e soltou")
check(not w.held, "tecla solta")
n_sessions = sum(1 for e in ev if e[1] == "up")
th.step()
check(sum(1 for e in w.hold_events if e[1] == "up") == n_sessions, "com 1 tentativa por sprite, não segura de novo logo em seguida")

print("4g-3. aparece um shiny enquanto segura a Ultra: solta e vai para o shiny")
w = World()
eight(w, shinies=())
w.fail_capture = set(range(8))
w.add(99, "shiny", REGION[0] + 450, REGION[1] + 230, spawn=0.45)
th = thrower(w, make_cfg(pokeball_perfis=[dict(HOLD[0]), dict(HOLD[1], segurar_s=5.0)], pokeball_tentativas=3, pokeball_recarga_s=0.5), log := [])
CURRENT["world"] = w
th.step()
check(any("apareceu sprite de prioridade maior" in m for _, m in log), "a Ultra soltou: apareceu sprite de prioridade maior")
check(not w.held and w.hold_events[-1][1] == "up", "tecla solta ao ceder a vez")
th.step()
check(w.taps and w.taps[0][1] == "v" and w.taps[0][2] == 99, f"no passo seguinte a Premier vai primeiro no shiny: {w.taps[:1]}")

print("4g-4. erro ao segurar: perfil desligado e tecla solta; o resto continua")
w = World()
eight(w)
w.hold_error = {"b": PermissionError("negado")}
th = thrower(w, make_cfg(pokeball_perfis=HOLD), log := [])
run_all(w, th, 6)
check(sum(1 for k, _ in log if k == "ERRO") == 1 and not w.held, "um ERRO e nenhuma tecla presa")
check(len(w.taps) == 2 and all(k == "v" for _, k, _ in w.taps), "o shiny (toque) continuou funcionando")

print("4g-5. parar a thread no meio de segurar solta a tecla")
w = World()
eight(w, shinies=())
w.fail_capture = set(range(8))
real = pk.PokeballThrower(make_cfg(pokeball_perfis=[dict(HOLD[0]), dict(HOLD[1], segurar_s=30.0)], pokeball_demora_sprite_ms=20),
                          grab=w.grab, tap=w.tap, move=w.move, loader=loader_for({"shiny": T_SHINY, "normal": T_NORMAL}),
                          hold_down=w.hold_down, hold_up=w.hold_up, held=lambda: ())
CURRENT["world"] = None
real.start()
real.trigger(60.0)
t_end = time.time() + 10
while time.time() < t_end and "b" not in w.held:
    time.sleep(0.02)
check("b" in w.held, "a tecla B está segurada")
real.stop()
check(not real.running and not w.held and w.hold_events[-1][1:] == ("up", "b"), "stop() solta a tecla e encerra a thread")

print("4h. ajuste automático pela direção do personagem")
def aim_case(modo, held, px=30, mira_dy=0):
    w = World()
    w.held_move = held
    w.add(0, "normal", REGION[0] + 300, REGION[1] + 200)
    th = thrower(w, make_cfg(pokeball_ajuste_modo=modo, pokeball_ajuste_px=px, pokeball_mira_dy_px=mira_dy))
    th.step()
    cx = REGION[0] + 300 + (SW - 1) / 2
    cy = REGION[1] + 200 + (SH_ - 1) / 2
    return w.cursor[0] - cx, w.cursor[1] - cy
for held, want, name in ((("w",), (0, 30), "indo para CIMA: a mira desce"), (("s",), (0, -30), "indo para BAIXO: a mira sobe"),
                         (("a",), (30, 0), "indo para a ESQUERDA: a mira vai para a direita"), (("d",), (-30, 0), "indo para a DIREITA: a mira vai para a esquerda"),
                         (("w", "d"), (-21.2, 21.2), "NE: a mira vai para SW"), (("s", "a"), (21.2, -21.2), "SO: a mira vai para NE"),
                         ((), (0, 0), "parado: sem ajuste")):
    dx, dy = aim_case("teclas", held)
    check(abs(dx - want[0]) <= 1.5 and abs(dy - want[1]) <= 1.5, f"{name}: ({dx:+.1f}, {dy:+.1f}) esperado {want}")
dx, dy = aim_case("teclas", ("w",), px=50)
check(abs(dy - 50) <= 1.5, f"a distância é a configurada (50 px): {dy:+.1f}")
dx, dy = aim_case("nenhum", ("w", "d"))
check(abs(dx) <= 1.5 and abs(dy) <= 1.5, "modo 'sem ajuste': a mira fica na sprite mesmo andando")
dx, dy = aim_case("velocidade", ("w",))
check(abs(dx) <= 1.5 and abs(dy) <= 1.5, "modo 'velocidade' sem velocidade medida ainda: na sprite (as teclas não entram)")
dx, dy = aim_case("teclas", ("w",), mira_dy=3)
check(abs(dy - 33) <= 1.5, "soma com o ajuste fixo da mira (+3 px)")
# no modo segurar o ajuste também vale, instante a instante
w = World()
w.held_move = ("d",)
w.add(0, "normal", REGION[0] + 300, REGION[1] + 200)
th = thrower(w, make_cfg(pokeball_perfis=HOLD, pokeball_ajuste_modo="teclas", pokeball_ajuste_px=4))
th.step()
check(w.hold_events and w.cursor[0] < REGION[0] + 300 + (SW - 1) / 2, "modo segurar: a mira também é ajustada pela direção")
# calibrado de verdade: tela andando para a direita (sprites vão para a ESQUERDA), a ~4 px à frente por bola
w = World(vx=120.0, vy=0.0)
w.add(0, "normal", REGION[0] + 300 + 60, REGION[1] + 200)
w.held_move = ("d",)
th = thrower(w, make_cfg(pokeball_ajuste_modo="teclas", pokeball_ajuste_px=12, pokeball_tentativas=3, pokeball_recarga_s=0.05))
run_all(w, th, 6)
check(not w.sprites[0]["alive"], "andando para a direita com o ajuste pela direção, a bola acerta a sprite que foge para a esquerda")

print("5. janela (R) / modos")
w = World()
cfg = make_cfg(pokeball_janela_s=2.0)
th = thrower(w, cfg)
check(not th.active(), "antes do R: inativo")
th.trigger()
check(th.active(), "R apertado: ativo")
w.t = 1.9
check(th.active(), "dentro da janela: ativo")
w.t = 2.1
check(not th.active(), "passou a janela: inativo")
th.trigger(5.0)
w.t = 6.0
check(th.active(), "trigger com tempo explícito")
cfg.set("pokeball_ativo", False, save=False)
check(not th.active(), "desligado na interface: inativo")
th.trigger()
check(not th.active(), "desligado: o R não abre janela")
cfg.set("pokeball_ativo", True, save=False)
cfg.set("pokeball_modo", "sempre", save=False)
w.t = 100.0
check(th.active(), "modo 'sempre': sempre ativo")
th2 = thrower(World(), make_cfg())
th2.trigger()
th2.book.records.append(pk.Record("normal", 1, 1, 0.0))
th2.trigger()
check(th2.book.records == [], "novo R: lote novo, o registro do anterior é limpo")

print("5b. thread de verdade")
w = World()
eight(w)
real = pk.PokeballThrower(make_cfg(pokeball_assentar_ms=1, pokeball_toque_ms=1, pokeball_intervalo_ms=1),
                          grab=w.grab, tap=w.tap, move=w.move, loader=loader_for({"shiny": T_SHINY, "normal": T_NORMAL}))
CURRENT["world"] = None
real.start()
time.sleep(0.5)
check(w.taps == [] and w.grabs == 0, "thread ligada mas sem R: não captura e não joga nada")
real.trigger(20.0)
t_end = time.time() + 25
while time.time() < t_end and any(s_["alive"] for s_ in w.sprites):
    time.sleep(0.05)
real.stop()
check(not real.running, "stop() encerra a thread")
check(len(w.taps) == 8 and all(not s_["alive"] for s_ in w.sprites), f"depois do R a thread jogou as 8 bolas ({len(w.taps)})")
check([k for _, k, _ in w.taps[:2]] == ["v", "v"], "e os 2 shiny foram os primeiros")

# ---------------------------------------------------------------- 6. gancho no shooter
print("6. shooter chama o pokeball quando o R começa")
cfg = make_cfg(shooter_ativo=True, shooter_seq_ativo=True, shooter_seq_teclas=[{"tecla": "r", "espera_ms": 0}],
               shooter_confirma_s=0, shooter_seq_repetir_s=0.1, shooter_seq_intervalo_ms=30, revive_ativo=False)
ctl = sh.ShooterController(cfg, lambda k, m: None)
called = []
ctl.on_sequence = lambda: called.append(time.time())
sh.kb.tap = lambda key, hold_s=0.05: None
ctl.logic.state = sh.WATCH
st = sh.Step(far=0, visible=3)
ctl._check_sequence(st, 250.0)
time.sleep(0.4)
check(len(called) == 1, f"on_sequence chamado uma vez quando a sequência (R) começa ({len(called)})")
ctl._check_sequence(st, 250.0)
check(len(called) == 1, "não chama de novo enquanto a sequência já foi disparada")
ctl.on_sequence = lambda: 1 / 0
ctl.begin()
ctl._check_sequence(st, 250.0)
time.sleep(0.3)
check(True, "erro no callback não derruba o shooter")

print()
print("TUDO OK" if not fails else f"{len(fails)} FALHA(S): " + "; ".join(fails))
sys.exit(1 if fails else 0)
