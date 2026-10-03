"""Pokeball: joga a bola na sprite de cada pokémon morto (mouse em cima da sprite + tecla da bola).

Lógica "achou, jogou" (sem reconfirmar, sem segunda captura):
  * O R do shooter mata vários pokémon de uma vez (geralmente 8). Cada morto aparece na tela com uma sprite própria.
  * O usuário define PERFIS: sprite (recorte mágico), tecla da bola, prioridade e similaridade mínima. Por padrão há
    dois: "shiny" (Premier Ball, prioridade 1) e "normal" (Ultra Ball, prioridade 2).
  * Uma thread própria (o bot continua andando) repete: captura a área de busca UMA vez e, perfil por perfil na ordem de
    prioridade, procura a sprite e JOGA NA HORA em cada uma que achou, com a tecla DAQUELE perfil. O shiny é procurado e
    jogado antes de o normal ser sequer procurado. Cada bola só sai em sprite do próprio perfil: a Premier nunca vai
    numa sprite normal (veja `_beaten_by_other`).
  * Cada perfil tem um MODO: "toque" (mouse na sprite + um toque na tecla) ou "segurar" (a tecla fica SEGURADA, repetida
    como o teclado faz, por um tempo definido pelo usuário, enquanto o mouse percorre as sprites achadas, passada após
    passada; a cada passada o bot confere de novo o que sobrou e solta a tecla na hora se não sobrou nada ou se apareceu
    uma sprite de prioridade maior, como o shiny). Segurar é bem mais garantido que tocar.
  * Como a tela anda, o ponto de clique é deslocado de um de dois jeitos (aba Ajustes): pela VELOCIDADE medida (compara as
    sprites de uma varredura com as da anterior: custo zero) ou pela DIREÇÃO do personagem naquele instante (teclas de
    movimento seguradas): indo para cima a mira desce, para a direita vai para a esquerda, e as diagonais compõem.
  * Cada sprite jogada vira um registro (`ThrowBook`): não joga de novo antes da recarga e respeita o máximo de tentativas.

`ThrowBook`, `detect` e `order_targets` são lógica pura. `PokeballThrower` liga isso à config, ao log, à tela, ao mouse
e ao teclado; tudo é injetável (os testes usam versões falsas e um relógio falso).
"""
from __future__ import annotations
import copy
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass

import numpy as np

from . import keys as kb
from .botlog import ALERTA, BOLA, ERRO, INFO
from .config import ConfigStore, POKEBALL_DIR
from .vision import SpriteTemplate, find_sprites, prepare_frame, sprite_scores

MAX_SPEED = 3000.0      # px/s: acima disso a medida da velocidade da tela é descartada como erro
SPEED_ALPHA = 0.6       # peso da medida nova na velocidade suavizada
SPEED_STALE_S = 0.8     # velocidade medida há mais que isso não vale mais (o personagem pode ter parado)
MIN_DT = 0.03           # intervalo mínimo entre a varredura e o recorte para medir a velocidade (abaixo disso é ruído de 1 px)
FORGET_S = 2.0          # registro de sprite não vista por este tempo é esquecido (a bola capturou o pokémon)
MIN_ASSOC_PX = 40.0

MODES = ("toque", "segurar")
DEFAULT_PROFILES = [
    {"id": "shiny", "nome": "Shiny (Premier Ball)", "tecla": "v", "prioridade": 1, "limiar": 0.90, "ativo": True,
     "modo": "toque", "segurar_s": 2.0},
    {"id": "normal", "nome": "Normal (Ultra Ball)", "tecla": "b", "prioridade": 2, "limiar": 0.90, "ativo": True,
     "modo": "segurar", "segurar_s": 2.0},
]
FIXED_IDS = ("shiny", "normal")     # os dois perfis padrão: podem ser desativados, mas não removidos


def default_profiles() -> list[dict]:
    return copy.deepcopy(DEFAULT_PROFILES)


def sprite_path(profile_id: str):
    return POKEBALL_DIR / f"{profile_id}.png"


def parse_profiles(raw) -> list[dict]:
    """Normaliza a lista da config. Sem nenhum perfil válido, volta aos dois padrões (shiny e normal)."""
    out, seen = [], set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        pid = "".join(ch for ch in str(item.get("id") or "").strip().lower() if ch.isalnum() or ch in "_-")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        try:
            prio = int(float(item.get("prioridade", 99)))
        except (TypeError, ValueError):
            prio = 99
        try:
            lim = min(1.0, max(0.3, float(item.get("limiar", 0.9))))
        except (TypeError, ValueError):
            lim = 0.9
        modo = str(item.get("modo") or "toque").strip().lower()
        try:
            hold_s = min(60.0, max(0.1, float(item.get("segurar_s", 2.0))))
        except (TypeError, ValueError):
            hold_s = 2.0
        out.append({"id": pid, "nome": str(item.get("nome") or pid), "tecla": str(item.get("tecla") or "").strip(),
                    "prioridade": prio, "limiar": lim, "ativo": bool(item.get("ativo", True)),
                    "modo": modo if modo in MODES else "toque", "segurar_s": hold_s})
    return out or default_profiles()


# ====================================================================== perfis carregados
@dataclass
class Ball:
    id: str
    nome: str
    tecla: str
    prioridade: int
    limiar: float
    tmpl: SpriteTemplate
    modo: str = "toque"
    segurar_s: float = 2.0


def load_balls(cfg: ConfigStore, loader=None) -> tuple[list[Ball], list[str]]:
    """Perfis ATIVOS prontos para uso e a lista de problemas dos que ficaram de fora (sem sprite, tecla inválida...)."""
    loader = loader or (lambda pid: SpriteTemplate.from_file(sprite_path(pid)))
    layout = kb.LAYOUTS.get(cfg.get("teclas_movimento", "wasd"), kb.LAYOUTS["wasd"])
    moving = {v[2] for v in layout.values()}                    # w/a/s/d ou up/left/down/right
    balls, problems = [], []
    for p in parse_profiles(cfg.get("pokeball_perfis")):
        if not p["ativo"]:
            continue
        key = p["tecla"]
        if not key:
            problems.append(f"{p['nome']}: sem tecla da bola")
            continue
        try:
            kb.check_key(key)
        except ValueError:
            problems.append(f"{p['nome']}: tecla inválida ({key!r})")
            continue
        if key.lower() in moving:
            problems.append(f"{p['nome']}: a tecla {key.upper()} é de movimento, escolha outra")
            continue
        if kb.is_reserved(key):
            problems.append(f"{p['nome']}: a tecla {key.upper()} é reservada ao revive")
            continue
        tmpl = loader(p["id"])
        if tmpl is None:
            problems.append(f"{p['nome']}: sem sprite (selecione-a na aba pokeball)")
            continue
        balls.append(Ball(p["id"], p["nome"], key, p["prioridade"], p["limiar"], tmpl, p["modo"], p["segurar_s"]))
    return balls, problems


# ====================================================================== detecção e ordem
@dataclass
class Det:
    ball: Ball
    x: float                # centro da sprite (pixels da área de busca)
    y: float
    score: float
    rec: "Record | None" = None


def detect(frame: np.ndarray, balls: list[Ball]) -> list[Det]:
    """Todas as sprites de todos os perfis num quadro. Se duas sprites de perfis diferentes caem no mesmo lugar
    (modelos parecidos), fica a de melhor similaridade."""
    found: list[Det] = []
    prepared = prepare_frame(frame) if balls else None      # o quadro é preparado uma vez só para todos os perfis
    for b in balls:
        hits, _ = find_sprites(frame, b.tmpl, b.limiar, prepared)
        found += [Det(b, x, y, s) for x, y, s in hits]
    found.sort(key=lambda d: -d.score)
    kept: list[Det] = []
    for d in found:
        if all(k.ball.id == d.ball.id          # mesmo perfil: o find_sprites já tirou os acertos sobrepostos
               or math.hypot(k.x - d.x, k.y - d.y) > 0.5 * max(d.ball.tmpl.w, d.ball.tmpl.h, k.ball.tmpl.w, k.ball.tmpl.h)
               for k in kept):
            kept.append(d)
    return kept


def order_targets(dets: list[Det]) -> list[Det]:
    """Ordem dos arremessos: prioridade do perfil (1 primeiro: o shiny) e, dentro dela, a melhor similaridade."""
    return sorted(dets, key=lambda d: (d.ball.prioridade, -d.score))


# ====================================================================== sprites já jogadas
@dataclass
class Record:
    ball_id: str
    x: float
    y: float
    t: float                # quando (x, y) valia
    throws: int = 0
    last_throw: float = 0.0
    last_seen: float = 0.0


class ThrowBook:
    """Acompanha as sprites em que já foi jogada bola, de varredura em varredura (a tela anda, então a posição é
    corrigida pela velocidade da tela antes de comparar)."""

    def __init__(self, forget_s: float = FORGET_S):
        self.forget_s = forget_s
        self.records: list[Record] = []

    def clear(self) -> None:
        self.records = []

    def associate(self, dets: list[Det], now: float, vel=(0.0, 0.0)) -> None:
        for d in dets:
            d.rec = None
        pairs = []
        for ri, r in enumerate(self.records):
            px, py = r.x + vel[0] * (now - r.t), r.y + vel[1] * (now - r.t)
            for di, d in enumerate(dets):
                if d.ball.id != r.ball_id:
                    continue
                lim = max(MIN_ASSOC_PX, 0.9 * max(d.ball.tmpl.w, d.ball.tmpl.h))
                dist = math.hypot(d.x - px, d.y - py)
                if dist <= lim:
                    pairs.append((dist, ri, di))
        used_r: set[int] = set()
        used_d: set[int] = set()
        for _, ri, di in sorted(pairs):                          # os pares mais próximos primeiro
            if ri in used_r or di in used_d:
                continue
            used_r.add(ri)
            used_d.add(di)
            r, d = self.records[ri], dets[di]
            r.x, r.y, r.t, r.last_seen = d.x, d.y, now, now
            d.rec = r
        self.records = [r for r in self.records if now - r.last_seen <= self.forget_s]

    @staticmethod
    def eligible(d: Det, now: float, attempts: int, cooldown_s: float) -> bool:
        r = d.rec
        if r is None:
            return True
        return r.throws < attempts and now - r.last_throw >= cooldown_s

    def note_throw(self, d: Det, x: float, y: float, now: float) -> None:
        r = d.rec
        if r is None:
            r = Record(d.ball.id, x, y, now, last_seen=now)
            self.records.append(r)
            d.rec = r
        r.x, r.y, r.t, r.last_seen, r.last_throw = x, y, now, now, now
        r.throws += 1


# ====================================================================== a thread que acha e joga
def _default_grab(region):
    from . import capture      # import tardio: o pyautogui precisa de tela
    return capture.grab(region)


def _beaten_by_other(frame: np.ndarray, hit: Det, balls: list[Ball]) -> bool:
    """A sprite achada para `hit.ball` é, na verdade, de outro perfil (nota maior ali)? Confere só num recorte
    pequeno ao redor do acerto (barato). É o que impede a Premier de ir numa sprite normal parecida com a shiny."""
    h, w = frame.shape[:2]
    for b in balls:
        if b.id == hit.ball.id:
            continue
        pad = max(b.tmpl.w, b.tmpl.h, hit.ball.tmpl.w, hit.ball.tmpl.h)
        x0, y0 = max(0, int(hit.x - pad)), max(0, int(hit.y - pad))
        x1, y1 = min(w, int(hit.x + pad) + 1), min(h, int(hit.y + pad) + 1)
        sc = sprite_scores(np.ascontiguousarray(frame[y0:y1, x0:x1]), b.tmpl)
        if sc is not None and sc.size and float(sc.max()) > hit.score + 1e-6:
            return True
    return False


def estimate_shift(prev: list[tuple[float, float]], cur: list[tuple[float, float]], max_shift: float):
    """Quanto as sprites andaram na tela entre duas varreduras: o deslocamento que mais pares (anterior -> atual)
    repetem (todas andam juntas). Devolve (dx, dy) ou None se não houver como saber."""
    cands = [(c[0] - p[0], c[1] - p[1]) for p in prev for c in cur
             if math.hypot(c[0] - p[0], c[1] - p[1]) <= max_shift]
    if not cands:
        return None
    best, votes = None, 0
    for cx, cy in cands:
        v = sum(1 for ox, oy in cands if abs(ox - cx) <= 3.0 and abs(oy - cy) <= 3.0)
        if v > votes:
            best, votes = (cx, cy), v
    if votes < 2 and len(cands) > 1:
        return None                       # vários pares possíveis e nenhum se repete: ambíguo
    return best


def movement_vector(held) -> tuple[float, float]:
    """Direção do personagem (ux, uy; y para baixo) pelas teclas de movimento seguradas, com tamanho 1 (0 se parado).
    w = para cima, s = para baixo, a = esquerda, d = direita; diagonal é normalizada."""
    h = {str(k).lower() for k in held}
    ux = (1 if "d" in h else 0) - (1 if "a" in h else 0)
    uy = (1 if "s" in h else 0) - (1 if "w" in h else 0)
    n = math.hypot(ux, uy)
    return (ux / n, uy / n) if n else (0.0, 0.0)


class PokeballThrower:
    """Uma thread de verdade: captura -> (para cada perfil, do de menor número de prioridade ao maior) acha e joga.
    `trigger()` abre a janela (o shooter chama quando aperta o R); no modo "sempre" a janela está sempre aberta.
    Os valores da config são relidos o tempo todo."""

    def __init__(self, cfg: ConfigStore, log=None, paused=None, grab=None, tap=None, move=None,
                 clock=time.monotonic, sleep=time.sleep, loader=None, hold_down=None, hold_up=None, held=None):
        self.cfg = cfg
        self._log_fn = log
        self._paused = paused or (lambda: False)
        self._grab = grab or _default_grab
        self._tap = tap or kb.tap
        self._move = move or kb.move_mouse
        self._hold_down = hold_down or kb.hold_down
        self._hold_up = hold_up or kb.hold_up
        self._held = held or kb.held_keys           # teclas de movimento seguradas agora (ajuste pela direção do personagem)
        self.clock, self._sleep = clock, sleep
        self._loader = loader
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._until = 0.0
        self._test_until = 0.0                # janela do teste da interface: vale mesmo com a pokeball desligada
        self.book = ThrowBook()
        self.vel = (0.0, 0.0)                 # velocidade das sprites na tela (px/s)
        self._vel_t = -1e9
        self._prev: dict[str, tuple[float, list]] = {}   # perfil -> (instante, posições) da varredura anterior
        self._balls: list[Ball] = []
        self._sig = None
        self._disabled: set[str] = set()      # perfis cuja tecla falhou: ficam fora até a config mudar
        self._warned: set[str] = set()
        self._last_err = 0.0
        self.last_scan_s = 0.0                # quanto a última varredura (captura + detecção) levou
        self.stats: Counter[str] = Counter()  # bolas jogadas por perfil

    # ---------------------------------------------------------------- log
    def _log(self, kind: str, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(kind, msg)

    def _warn_once(self, key: str, kind: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self._log(kind, msg)

    # ---------------------------------------------------------------- controle
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("pokeball_ativo"))

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pokeball", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)

    def _reset_batch(self) -> None:
        self.book.clear()
        self._prev = {}

    def trigger(self, seconds: float | None = None) -> None:
        """O R começou: procura e joga bolas pelos próximos `seconds` (padrão: 'janela' da config)."""
        if not self.enabled or self.cfg.get("pokeball_modo", "apos_r") == "sempre":
            return
        s = float(seconds if seconds is not None else self.cfg.get("pokeball_janela_s", 8.0) or 0)
        with self._lock:
            self._until = self.clock() + max(0.0, s)
            self._reset_batch()               # lote novo de mortos
        self._log(INFO, f"Pokeball: R apertado, procurando pokémon mortos por {s:.1f}s")

    def test_window(self, seconds: float) -> None:
        """Teste da interface: procura e joga bolas por `seconds`, mesmo com a pokeball desligada na config."""
        with self._lock:
            self._test_until = self.clock() + max(0.0, float(seconds))
            self._reset_batch()

    def active(self) -> bool:
        with self._lock:
            if self.clock() < self._test_until:
                return True
        if not self.enabled:
            return False
        if self.cfg.get("pokeball_modo", "apos_r") == "sempre":
            return True
        with self._lock:
            return self.clock() < self._until

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.active() or self._paused():
                self._stop.wait(0.03)
                continue
            try:
                n = self.hunt_once()
            except Exception as exc:  # noqa: BLE001
                now = time.monotonic()
                if now - self._last_err > 2.0:
                    self._last_err = now
                    self._log(ERRO, f"Pokeball: falha na varredura ({type(exc).__name__}: {exc})")
                self._stop.wait(0.3)
                continue
            if n == 0:
                self._stop.wait(max(0.0, float(self.cfg.get("pokeball_varredura_pausa_ms", 20) or 0) / 1000.0))

    # ---------------------------------------------------------------- perfis (recarrega se a config ou as sprites mudaram)
    def _refresh_balls(self) -> list[Ball]:
        c = self.cfg
        sig = [repr(c.get("pokeball_perfis")), repr(c.get("teclas_movimento"))]
        for p in parse_profiles(c.get("pokeball_perfis")):
            try:
                sig.append(sprite_path(p["id"]).stat().st_mtime_ns)
            except OSError:
                sig.append(None)
        sig = tuple(sig)
        if sig != self._sig:
            self._sig = sig
            self._disabled.clear()
            self._warned.clear()
            self._balls, problems = load_balls(c, self._loader)
            for msg in problems:
                self._warn_once(msg, ALERTA, f"Pokeball: perfil ignorado — {msg}")
            if self._balls:
                txt = "; ".join(f"{b.nome} → {b.tecla.upper()} (prioridade {b.prioridade})"
                                for b in sorted(self._balls, key=lambda b: b.prioridade))
                self._log(INFO, f"Pokeball: perfis ativos: {txt}")
        return sorted((b for b in self._balls if b.id not in self._disabled), key=lambda b: b.prioridade)

    def _region(self):
        r = self.cfg.get("regiao_pokeball") or self.cfg.get("regiao_sprite")
        return [int(v) for v in r] if r else None

    # ---------------------------------------------------------------- velocidade da tela (de graça: só compara posições)
    def _velocity(self, now: float) -> tuple[float, float]:
        return self.vel if now - self._vel_t <= SPEED_STALE_S else (0.0, 0.0)

    def _learn_velocity(self, ball: Ball, dets: list[Det], t_scan: float) -> None:
        pts = [(d.x, d.y) for d in dets]
        prev = self._prev.get(ball.id)
        self._prev[ball.id] = (t_scan, pts)
        if not prev or not pts:
            return
        dt = t_scan - prev[0]
        if dt < MIN_DT or dt > 1.2:
            return
        sh = estimate_shift(prev[1], pts, MAX_SPEED * dt)
        if sh is None:
            return
        vx, vy = sh[0] / dt, sh[1] / dt
        ox, oy = self._velocity(t_scan)
        self.vel = (SPEED_ALPHA * vx + (1 - SPEED_ALPHA) * ox, SPEED_ALPHA * vy + (1 - SPEED_ALPHA) * oy)
        self._vel_t = t_scan

    # ---------------------------------------------------------------- achar as sprites de um perfil
    def _find(self, ball: Ball, balls: list[Ball], frame, prepared, claimed) -> list[Det]:
        hits, _ = find_sprites(frame, ball.tmpl, ball.limiar, prepared)
        dets = []
        for x, y, sc in hits:
            d = Det(ball, x, y, sc)
            if any(math.hypot(x - cx, y - cy) <= r for cx, cy, r in claimed):
                continue                      # esse lugar já é de outro perfil
            if len(balls) > 1 and _beaten_by_other(frame, d, balls):
                continue                      # é a sprite de outro perfil: esta bola NÃO vai aqui
            dets.append(d)
        return dets

    # ---------------------------------------------------------------- uma varredura: acha e joga
    def hunt_once(self) -> int:
        """Captura a área UMA vez. Para cada perfil (prioridade 1 primeiro) acha as sprites e joga a bola na hora em
        cada uma (toque) ou segura a tecla enquanto o mouse as percorre (segurar). Devolve quantas sprites foram atendidas."""
        c = self.cfg
        balls = self._refresh_balls()
        region = self._region()
        if not balls:
            return 0
        if region is None:
            self._warn_once("sem_area", ALERTA, "Pokeball: defina a 'Área de busca da sprite' (aba sprites/capture) "
                                                "ou a área da pokeball (aba pokeball).")
            return 0
        t_a = self.clock()
        frame = self._grab(region)
        t_b = self.clock()
        t_scan = (t_a + t_b) / 2              # o quadro vale para o meio da captura
        prepared = prepare_frame(frame)
        attempts = max(1, int(c.get("pokeball_tentativas", 2) or 1))
        cooldown = max(0.0, float(c.get("pokeball_recarga_s", 0.8) or 0))
        gap = max(0.0, float(c.get("pokeball_intervalo_ms", 10) or 0) / 1000.0)
        claimed: list[tuple[float, float, float]] = []   # lugares já tomados por um perfil de prioridade maior
        done = 0
        for ball in balls:
            if self._stop.is_set() or self._paused():
                break
            if ball.id in self._disabled:
                continue
            dets = self._find(ball, balls, frame, prepared, claimed)
            claimed += [(d.x, d.y, 0.5 * max(ball.tmpl.w, ball.tmpl.h)) for d in dets]
            self.book.associate(dets, t_scan, self._velocity(t_scan))
            self._learn_velocity(ball, dets, t_scan)
            self.last_scan_s = self.clock() - t_a
            dets = [d for d in dets if self.book.eligible(d, self.clock(), attempts, cooldown)]
            if not dets:
                continue
            if ball.modo == "segurar":
                done += self._hold_sweep(ball, dets, t_scan, region, balls)
                continue
            for d in dets:                    # achou -> joga, na ordem da melhor similaridade
                if self._stop.is_set() or self._paused():
                    break
                if ball.id in self._disabled:
                    break
                if self._throw(d, t_scan, region):
                    done += 1
                    if gap:
                        self._sleep(gap)
        return done

    def step(self) -> int:
        """Uma varredura com os arremessos dela (para testes e uso sem thread)."""
        return self.hunt_once()

    # ---------------------------------------------------------------- onde o mouse vai (com o ajuste da tela andando)
    def _aim(self, d: Det, t_scan: float, region, lead_extra: float):
        """(x, y) na área e (gx, gy) na tela. Ajuste pela VELOCIDADE medida das sprites, pela DIREÇÃO do personagem
        (teclas seguradas agora) ou nenhum."""
        c = self.cfg
        W, H = region[2], region[3]
        mode = str(c.get("pokeball_ajuste_modo", "velocidade") or "velocidade")
        ox = oy = 0.0
        if mode == "teclas":
            ux, uy = movement_vector(self._held())
            px = float(c.get("pokeball_ajuste_px", 30) or 0)
            ox, oy = -ux * px, -uy * px       # a tela anda no sentido oposto ao do personagem: a sprite também
        elif mode != "nenhum":
            vx, vy = self._velocity(self.clock())
            lead = (self.clock() - t_scan) + lead_extra
            ox, oy = vx * lead, vy * lead
        ax = min(max(d.x + ox, 0.0), W - 1.0)
        ay = min(max(d.y + oy, 0.0), H - 1.0)
        aim_dy = float(c.get("pokeball_mira_dy_px", 0) or 0)
        return ax, ay, int(round(region[0] + ax)), int(round(region[1] + ay + aim_dy)), (ox, oy)

    # ---------------------------------------------------------------- modo toque
    def _throw(self, d: Det, t_scan: float, region) -> bool:
        c, ball = self.cfg, d.ball
        settle = max(0.0, float(c.get("pokeball_assentar_ms", 15) or 0) / 1000.0)
        hold = max(0.005, float(c.get("pokeball_toque_ms", 20) or 0) / 1000.0)
        ax, ay, gx, gy, (ox, oy) = self._aim(d, t_scan, region, settle + hold / 2)
        try:
            with kb.aim_lock:                 # o mouse é um só (o shooter também usa)
                self._move(gx, gy)
                self._sleep(settle)
                self._tap(ball.tecla, hold)
        except Exception as exc:  # noqa: BLE001
            self._disabled.add(ball.id)
            self._log(ERRO, f"Pokeball: não consegui apertar {ball.tecla.upper()} para {ball.nome} "
                            f"({type(exc).__name__}: {exc}). Perfil desligado até a config mudar.")
            return False
        self.book.note_throw(d, ax, ay, self.clock())
        self.stats[ball.id] += 1
        self._log(BOLA, f"{ball.nome}: toque na tecla {ball.tecla.upper()} em ({gx}, {gy}) | similaridade {d.score:.2f} | "
                        f"{self.clock() - t_scan:.3f}s depois da captura | ajuste {ox:+.0f},{oy:+.0f} px")
        return True

    # ---------------------------------------------------------------- modo segurar
    def _hold_sweep(self, ball: Ball, dets: list[Det], t_scan: float, region, balls: list[Ball]) -> int:
        """Segura a tecla do perfil e leva o mouse por todas as sprites, passada após passada, até acabar o tempo,
        acabarem as sprites ou aparecer uma sprite de prioridade maior. A tecla é SEMPRE solta no fim."""
        c = self.cfg
        hold_s = max(0.1, float(ball.segurar_s))
        dwell = max(0.0, float(c.get("pokeball_demora_sprite_ms", 40) or 0) / 1000.0)
        repeat = max(0.005, float(c.get("pokeball_repetir_ms", 25) or 0) / 1000.0)
        higher = [b for b in balls if b.prioridade < ball.prioridade and b.id not in self._disabled]
        deadline = self.clock() + hold_s
        n0, passes, preempted = len(dets), 0, False
        remaining = dets
        pos = None                                   # onde o mouse está (o caminho começa pela sprite mais perto dele)
        try:
            self._hold_down(ball.tecla)              # a tecla desce uma vez antes do mouse chegar à primeira sprite
            while remaining and not self._stop.is_set():
                passes += 1
                todo = list(remaining)
                while todo:
                    if self._stop.is_set() or self._paused() or self.clock() >= deadline:
                        break
                    if pos is None:
                        d = todo.pop(0)
                    else:
                        d = min(todo, key=lambda q: math.hypot(q.x - pos[0], q.y - pos[1]))
                        todo.remove(d)
                    ax, ay, gx, gy, _ = self._aim(d, t_scan, region, dwell / 2)
                    pos = (d.x, d.y)
                    with kb.aim_lock:
                        self._move(gx, gy)
                        t_end = min(self.clock() + dwell, deadline)
                        while True:                  # a tecla segurada é reenviada enquanto o mouse está em cima da sprite
                            self._hold_down(ball.tecla)
                            left = t_end - self.clock()
                            if left <= 0:
                                break
                            self._sleep(min(repeat, left))
                if self._stop.is_set() or self._paused() or self.clock() >= deadline:
                    break
                # fim da passada: olha de novo o que sobrou (tecla continua segurada) e se apareceu algo mais prioritário
                t_a = self.clock()
                frame = self._grab(region)
                t_scan = (t_a + self.clock()) / 2
                prepared = prepare_frame(frame)
                if any(self._find(b, balls, frame, prepared, []) for b in higher):
                    preempted = True
                    break
                remaining = self._find(ball, balls, frame, prepared, [])
                self.book.associate(remaining, t_scan, self._velocity(t_scan))
                self._learn_velocity(ball, remaining, t_scan)
                pos = None
        except Exception as exc:  # noqa: BLE001
            self._disabled.add(ball.id)
            self._log(ERRO, f"Pokeball: não consegui segurar {ball.tecla.upper()} para {ball.nome} "
                            f"({type(exc).__name__}: {exc}). Perfil desligado até a config mudar.")
            return 0
        finally:
            try:
                self._hold_up(ball.tecla)            # nunca deixa a tecla presa
            except Exception as exc:  # noqa: BLE001
                self._log(ERRO, f"Pokeball: não consegui soltar {ball.tecla.upper()} ({type(exc).__name__}: {exc})")
        now = self.clock()
        for d in dets:
            self.book.note_throw(d, d.x, d.y, now)
        self.stats[ball.id] += n0
        left_n = len(remaining) if not self._stop.is_set() else n0
        why = "apareceu sprite de prioridade maior" if preempted else ("acabaram as sprites" if not remaining else "acabou o tempo")
        self._log(BOLA, f"{ball.nome}: tecla {ball.tecla.upper()} SEGURADA por {hold_s - max(0.0, deadline - now):.2f}s de "
                        f"{hold_s:.1f}s sobre {n0} sprite(s) em {passes} passada(s); {why}"
                        + (f" ({left_n} ainda na tela)" if remaining else ""))
        return n0
