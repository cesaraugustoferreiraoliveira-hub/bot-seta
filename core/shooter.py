"""Shooter: com o bot PARADO por excesso de sprites, atira na sprite distante que parou de andar.

Fluxo (a cada captura enquanto o bot está parado):
  1. acha o nome do seu pokémon na área de busca (é o ponto de referência);
  2. mede a distância de cada sprite de parada até ele;
  3. só importam as sprites MAIS LONGE que a distância limite (as que ainda estão chegando);
  4. cada sprite é rastreada de uma captura para a outra. Se uma sprite além do limite fica sem se mexer
     (dentro de uma tolerância) por `shooter_parada_s` segundos, em várias capturas, dispara o callback:
     o mouse vai até ela e a tecla definida pelo usuário é apertada;
  5. depois do tiro, espera TODAS as sprites ficarem dentro da distância limite (ou o tempo máximo de espera
     acabar) antes de voltar a vigiar.

`FarSpriteShooter` é só lógica (recebe posições, devolve decisões) e não depende de tela nem de GUI.
`ShooterController` liga essa lógica à config, ao log, ao mouse e ao teclado, e é o que o runner usa.
"""
from __future__ import annotations
import math
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import keys as kb
from .botlog import ALERTA, DETECCAO, ERRO, INFO, TIRO
from .config import ConfigStore, POKEMON_PATH
from .name_match import NameTemplate, find_name

WATCH, WAIT = "vigiando", "esperando"
MIN_SAMPLES = 3         # capturas mínimas, no mesmo lugar, para a sprite contar como parada
MAX_MISSED = 2          # capturas seguidas sem ver a sprite antes de esquecê-la (a detecção pode falhar num quadro)
AIM_SETTLE_S = 0.06     # pausa entre levar o mouse até a sprite e apertar a tecla (o jogo precisa ver o cursor)
LAST_KNOWN_S = 3.0      # se o nome do pokémon sumir por um quadro, usa a última posição conhecida por este tempo
POKEMON_ROI = 120       # px ao redor da última posição do nome onde ele é procurado primeiro (bem mais rápido)


# ====================================================================== lógica pura
@dataclass
class Track:
    id: int
    x: float
    y: float
    ax: float              # âncora: onde a sprite está "parada" e desde quando
    ay: float
    at: float
    samples: int = 1       # capturas seguidas dentro da tolerância da âncora
    missed: int = 0
    dist: float = 0.0


@dataclass
class Shot:
    x: float               # centro da sprite alvo (pixels da área de busca)
    y: float
    dist: float            # distância dela até o pokémon
    still_s: float         # há quanto tempo estava parada
    track_id: int


@dataclass
class Step:
    shot: Shot | None = None
    event: str | None = None       # "espera_ok" (todas dentro do limite) | "espera_timeout"
    far: int = 0                   # sprites visíveis além da distância limite
    visible: int = 0               # sprites visíveis nesta captura (dentro ou além do limite)
    farthest: float | None = None  # distância da mais longe
    state: str = WATCH


class FarSpriteShooter:
    def __init__(self, distance_px: float = 250.0, still_s: float = 1.5, tol_px: float = 6.0,
                 wait_max_s: float = 15.0, assoc_px: float = 80.0):
        self.distance_px = distance_px
        self.still_s = still_s
        self.tol_px = tol_px
        self.wait_max_s = wait_max_s
        self.assoc_px = assoc_px          # deslocamento máximo entre duas capturas para ser a MESMA sprite
        self.reset()

    def reset(self) -> None:
        self.tracks: list[Track] = []
        self.state = WATCH
        self._shot_t = 0.0
        self._next_id = 1

    def cancel_wait(self, now: float) -> None:
        self.state = WATCH
        self._restart_stall_timers(now)

    # ---------------------------------------------------------------- rastreio entre capturas
    def _track(self, pts: list[tuple[float, float]], now: float) -> None:
        pairs = sorted((math.hypot(t.x - px, t.y - py), ti, pi)
                       for ti, t in enumerate(self.tracks) for pi, (px, py) in enumerate(pts))
        used_t: set[int] = set()
        used_p: set[int] = set()
        for d, ti, pi in pairs:                      # os pares mais próximos primeiro
            if d > self.assoc_px:
                break
            if ti in used_t or pi in used_p:
                continue
            used_t.add(ti)
            used_p.add(pi)
            t, (px, py) = self.tracks[ti], pts[pi]
            t.x, t.y, t.missed = px, py, 0
            if math.hypot(px - t.ax, py - t.ay) <= self.tol_px:
                t.samples += 1                       # continua parada
            else:
                t.ax, t.ay, t.at, t.samples = px, py, now, 1   # mexeu: recomeça a contar
        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.missed += 1
        self.tracks = [t for t in self.tracks if t.missed <= MAX_MISSED]
        for pi, (px, py) in enumerate(pts):
            if pi not in used_p:
                self.tracks.append(Track(self._next_id, px, py, px, py, now))
                self._next_id += 1

    def _restart_stall_timers(self, now: float) -> None:
        for t in self.tracks:
            t.ax, t.ay, t.at, t.samples = t.x, t.y, now, 1

    # ---------------------------------------------------------------- decisão
    def update(self, ref: tuple[float, float], sprites: list[tuple[float, float]], now: float) -> Step:
        """ref = posição do pokémon; sprites = centros das sprites de parada nesta captura."""
        self._track(sprites, now)
        for t in self.tracks:
            t.dist = math.hypot(t.x - ref[0], t.y - ref[1])
        far = [t for t in self.tracks if t.missed == 0 and t.dist > self.distance_px]
        step = Step(far=len(far), visible=sum(1 for t in self.tracks if t.missed == 0), farthest=max((t.dist for t in far), default=None), state=self.state)

        if self.state == WAIT:                       # já atirou: espera todas entrarem na distância limite
            if not far:
                self.state, step.event = WATCH, "espera_ok"
                self._restart_stall_timers(now)
            elif self.wait_max_s > 0 and now - self._shot_t >= self.wait_max_s:
                self.state, step.event = WATCH, "espera_timeout"
                self._restart_stall_timers(now)      # não atira de novo na hora: precisa parar de novo por still_s
            step.state = self.state
            return step

        stalled = [t for t in far if t.samples >= MIN_SAMPLES and now - t.at >= self.still_s]
        if stalled:
            t = max(stalled, key=lambda k: k.dist)   # entre as paradas, a mais distante
            self.state, self._shot_t = WAIT, now
            step.shot = Shot(t.x, t.y, t.dist, now - t.at, t.id)
            step.state = WAIT
        return step


def parse_sequence(raw) -> list[tuple[str, int]]:
    """[{"tecla": "r", "espera_ms": 300}, ...] -> [("r", 300), ...]; ignora linhas sem tecla."""
    out = []
    for item in raw or []:
        key = str((item or {}).get("tecla") or "").strip()
        if not key:
            continue
        try:
            ms = max(0, int(float((item or {}).get("espera_ms", 0) or 0)))
        except (TypeError, ValueError):
            ms = 0
        out.append((key, ms))
    return out


# ====================================================================== nome do pokémon
def locate_pokemon(frame: np.ndarray, tmpl: NameTemplate, limiar: float, near=None, margin: int = POKEMON_ROI):
    """Centro (x, y) do nome do pokémon no frame, ou None. Com `near` (posição anterior) procura primeiro só ao
    redor dela, que é bem mais rápido do que varrer a área inteira, e cai para a área inteira se não achar."""
    if near is not None:
        h, w = frame.shape[:2]
        x0, y0 = max(0, int(near[0] - tmpl.text_w / 2 - margin)), max(0, int(near[1] - tmpl.text_h / 2 - margin))
        x1, y1 = min(w, int(near[0] + tmpl.text_w / 2 + margin)), min(h, int(near[1] + tmpl.text_h / 2 + margin))
        m = find_name(np.ascontiguousarray(frame[y0:y1, x0:x1]), tmpl, limiar)
        if m is not None and m.found:
            return x0 + m.x + (m.w - 1) / 2, y0 + m.y + (m.h - 1) / 2
    m = find_name(frame, tmpl, limiar)
    if m is not None and m.found:
        return m.x + (m.w - 1) / 2, m.y + (m.h - 1) / 2
    return None


# ====================================================================== integração com o runner
class ShooterController:
    """Usado pelo BotRunner: begin() quando o bot para, step() a cada captura enquanto parado, end() quando volta a andar.
    Os valores da config são relidos a cada captura, então mudar na interface vale na hora."""

    def __init__(self, cfg: ConfigStore, log=None, sprite_w: int = 0, sequence_runner=None):
        self.cfg = cfg
        self._log_fn = log
        self.shots = 0
        self._seq_thread: threading.Thread | None = None
        self._seq_stop = threading.Event()
        self._sequence_runner = sequence_runner
        self.logic = FarSpriteShooter(assoc_px=max(80.0, 1.5 * sprite_w))
        self.begin()

    def _log(self, kind: str, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(kind, msg)

    def _warn_once(self, key: str, kind: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self._log(kind, msg)

    # ---------------------------------------------------------------- ciclo de vida
    def begin(self) -> None:
        """O bot acabou de parar por excesso de sprites."""
        self.logic.reset()
        self._tmpl: NameTemplate | None = None
        self._tmpl_loaded = False
        self._last_ref, self._last_ref_t = None, 0.0
        self._warned: set[str] = set()
        self._last_far = None
        self._announced = False
        self._disabled = False
        self._seq_armed = True            # a sequência dispara de novo cada vez que "todas dentro" volta a ser verdade
        self._stop_seq()

    def end(self) -> None:
        """O bot voltou a andar."""
        self.logic.reset()
        self._stop_seq()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("shooter_ativo"))

    def status(self) -> str:
        if not self.enabled or self._disabled:
            return ""
        return "shooter esperando as sprites chegarem" if self.logic.state == WAIT else "shooter vigiando"

    # ---------------------------------------------------------------- uma captura
    def step(self, frame: np.ndarray, hits: list) -> None:
        """frame = área de busca; hits = [(cx, cy, similaridade)] de vision.find_sprites."""
        if not self.enabled or self._disabled:
            return
        c = self.cfg
        now = time.time()
        lg = self.logic
        lg.distance_px = float(c.get("shooter_distancia_px", 250))
        lg.still_s = float(c.get("shooter_parada_s", 1.5))
        lg.tol_px = float(c.get("shooter_tolerancia_px", 6))
        lg.wait_max_s = float(c.get("shooter_espera_max_s", 15))
        if not self._announced:
            self._announced = True
            self._log(INFO, f"Shooter ligado: atira com a tecla {str(c.get('shooter_tecla') or '?').upper()} na sprite além de "
                            f"{lg.distance_px:.0f} px do pokémon que ficar parada por {lg.still_s:.1f}s "
                            f"(oscilação até {lg.tol_px:.0f} px)")

        ref = self._pokemon_pos(frame, now)
        if ref is None:
            return
        st = lg.update(ref, [(x, y) for x, y, _ in hits], now)

        if st.far != self._last_far or (c.get("log_detalhado") and st.far):
            self._last_far = st.far
            far_txt = f"{st.far} sprite(s) além de {lg.distance_px:.0f} px do pokémon"
            self._log(DETECCAO, f"Shooter: {far_txt}" + (f" (a mais distante: {st.farthest:.0f} px)" if st.far else "")
                      + f" | {st.state}")
        if st.event == "espera_ok":
            self._log(INFO, f"Shooter: todas as sprites dentro de {lg.distance_px:.0f} px; voltou a vigiar")
        elif st.event == "espera_timeout":
            self._log(ALERTA, f"Shooter: passaram {lg.wait_max_s:.0f}s e ainda há sprite além de {lg.distance_px:.0f} px; "
                              "voltou a vigiar (ela precisa ficar parada de novo para receber outro tiro)")
        if st.shot is not None:
            self._fire(st.shot, now)
        self._check_sequence(st, lg.distance_px)

    # ---------------------------------------------------------------- sequência "todas dentro do limite"
    def _check_sequence(self, st: Step, limit: float) -> None:
        if st.far > 0:
            self._seq_armed = True                  # alguma sprite ainda longe: rearma
            return
        if not self._seq_armed or st.visible == 0 or not self.cfg.get("shooter_seq_ativo"):
            return
        steps = parse_sequence(self.cfg.get("shooter_seq_teclas"))
        if not steps:
            return
        try:
            for key, _ in steps:
                kb.check_key(key)
        except ValueError as exc:
            self._seq_armed = False
            self._log(ERRO, f"Shooter: sequência de teclas inválida ({exc}); confira a aba shooter.")
            return
        self._seq_armed = False
        txt = " → ".join(f"{k.upper()}" + (f" (+{ms} ms)" if i < len(steps) - 1 else "") for i, (k, ms) in enumerate(steps))
        self._log(TIRO, f"Todas as {st.visible} sprite(s) dentro de {limit:.0f} px: sequência {txt}")
        self._stop_seq()
        self._seq_stop = threading.Event()
        self._seq_thread = threading.Thread(target=self._run_sequence, args=(steps, self._seq_stop), daemon=True)
        self._seq_thread.start()

    def _run_sequence(self, steps, stop: threading.Event) -> None:
        if self._sequence_runner is not None:
            try:
                self._sequence_runner(steps, stop)
            except Exception as exc:  # noqa: BLE001
                self._log(ERRO, f"Shooter: falha ao validar a sequência pela pokebar ({type(exc).__name__}: {exc})")
            return
        for i, (key, ms) in enumerate(steps):
            if stop.is_set():
                return
            try:
                kb.tap(key)
            except Exception as exc:  # noqa: BLE001
                self._log(ERRO, f"Shooter: falha ao apertar {key.upper()} na sequência ({type(exc).__name__}: {exc})")
                return
            if i < len(steps) - 1 and stop.wait(ms / 1000.0):
                return

    def _stop_seq(self) -> None:
        self._seq_stop.set()

    # ---------------------------------------------------------------- referência: o pokémon
    def _pokemon_pos(self, frame: np.ndarray, now: float):
        if not self._tmpl_loaded:
            self._tmpl_loaded = True
            try:
                self._tmpl = NameTemplate.from_file(POKEMON_PATH)
                if self._tmpl is None:
                    self._log(ALERTA, "Shooter sem efeito: selecione o nome do pokémon na aba shooter "
                                      "(sem ele não há como medir a distância).")
            except ValueError as exc:
                self._tmpl = None
                self._log(ALERTA, f"Shooter sem efeito: {exc}")
        if self._tmpl is None:
            return None
        pos = locate_pokemon(frame, self._tmpl, float(self.cfg["pokemon_limiar"]), near=self._last_ref)
        dy = float(self.cfg.get("shooter_ref_dy_px", 0) or 0)   # o nome fica acima do pokémon: desce até o corpo
        if pos is not None:
            self._last_ref, self._last_ref_t = pos, now
            self._warned.discard("nome_sumiu")
            return pos[0], pos[1] + dy
        if self._last_ref is not None and now - self._last_ref_t <= LAST_KNOWN_S:
            return self._last_ref[0], self._last_ref[1] + dy
        self._warn_once("nome_sumiu", ALERTA, "Shooter: nome do pokémon não encontrado na área de busca; "
                                              "sem ele não dá para medir as distâncias (confira a similaridade mínima).")
        return None

    # ---------------------------------------------------------------- o callback: mouse + tecla
    def _fire(self, shot: Shot, now: float) -> None:
        c = self.cfg
        key = str(c.get("shooter_tecla") or "").strip()
        region = c.get("regiao_sprite")
        try:
            kb.check_key(key)                       # valida antes de mexer o mouse
            aim_dy = float(c.get("shooter_mira_dy_px", 0) or 0)      # clica ABAIXO do emblema, onde o selvagem está
            gx, gy = int(round(region[0] + shot.x)), int(round(region[1] + shot.y + aim_dy))
            kb.move_mouse(gx, gy)
            time.sleep(AIM_SETTLE_S)
            kb.tap(key)
        except Exception as exc:  # noqa: BLE001
            self._disabled = True                   # tecla inválida/envio recusado: não insiste enquanto o bot estiver parado
            self.logic.cancel_wait(now)
            self._log(ERRO, f"Shooter: não consegui atirar ({type(exc).__name__}: {exc}). "
                            "Desativado até o bot voltar a andar; confira a tecla na aba shooter.")
            return
        self.shots += 1
        self._log(TIRO, f"Tecla {key.upper()} em ({gx}, {gy}) na tela ({aim_dy:+.0f} px da sprite): sprite a {shot.dist:.0f} px do pokémon "
                        f"(limite {self.logic.distance_px:.0f}), parada há {shot.still_s:.1f}s. "
                        "Aguardando todas as sprites entrarem na distância limite")
