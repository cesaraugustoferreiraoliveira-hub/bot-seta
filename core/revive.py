"""Revive: aperta a tecla do revive e a SEGURA até a 'foto' (uma área da tela) mudar; e habilidades por % de vida.

Fluxo do revive (ReviveController.execute):
  1. há uma imagem de referência guardada na memória (tirada ao ligar o bot e depois de cada revive confirmado);
  2. a tecla é pressionada e mantida; a cada ~50 ms a área é capturada e comparada com a referência;
  3. quando a imagem mudou (>= `revive_sens_pct` % dos pixels), o comando foi executado: solta a tecla, guarda a imagem
     nova como referência e espera o próximo comando;
  4. se não mudar, continua segurando até o tempo máximo (`revive_timeout_s`) ou até o bot voltar a andar.

Não depende da GUI: captura de tela e teclas são injetáveis (os testes usam versões falsas).
"""
from __future__ import annotations
import threading
import time

import numpy as np

from . import keys as kb
from .botlog import ALERTA, DETECCAO, ERRO, INFO, TIRO
from .config import ConfigStore, LIFEBAR_PATH
from .lifebar import LifeBarReader, pick_skill
from .vision import SpriteTemplate

POLL_S = 0.05        # intervalo entre capturas enquanto a tecla é segurada (também reenvia a tecla: repetição automática)
PIXEL_DELTA = 30     # diferença (0..255, no pior canal) para um pixel contar como 'mudou'


def _default_grab(region):
    from . import capture      # import tardio: o pyautogui precisa de tela
    return capture.grab(region)


# ====================================================================== foto na memória
class ChangeWatcher:
    """Guarda uma imagem de referência e diz quanto da imagem atual mudou em relação a ela."""

    def __init__(self):
        self.ref: np.ndarray | None = None

    def remember(self, frame: np.ndarray) -> None:
        self.ref = frame.copy()

    def changed_pct(self, frame: np.ndarray) -> float:
        """% de pixels que mudaram (0..100). Tamanho diferente (área redefinida) conta como mudança total."""
        if self.ref is None or self.ref.shape != frame.shape:
            return 100.0
        diff = np.abs(self.ref.astype(np.int16) - frame.astype(np.int16)).max(axis=2)
        return float((diff > PIXEL_DELTA).mean() * 100.0)


class ReviveController:
    def __init__(self, cfg: ConfigStore, log=None, grab=None, key_down=None, key_up=None,
                 sleep=time.sleep, clock=time.time):
        self.cfg = cfg
        self._log_fn = log
        self._grab = grab or _default_grab
        self._down = key_down or kb.hold_down
        self._up = key_up or kb.hold_up
        self._sleep, self._clock = sleep, clock
        self.watcher = ChangeWatcher()
        self.executions = 0

    def _log(self, kind: str, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(kind, msg)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("revive_ativo"))

    def arm(self) -> None:
        """Guarda a foto de referência (chamado quando o bot liga)."""
        region = self.cfg.get("regiao_revive_foto")
        if not self.enabled or not region:
            return
        try:
            self.watcher.remember(self._grab(region))
            self._log(INFO, f"Revive: foto de referência guardada na memória ({region[2]}x{region[3]}px)")
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Revive: não consegui capturar a foto de referência ({type(exc).__name__}: {exc})")

    def execute(self, stop: threading.Event | None = None) -> bool:
        """Segura a tecla do revive até a foto mudar. True = confirmado (a foto mudou)."""
        stop = stop or threading.Event()
        c = self.cfg
        region = c.get("regiao_revive_foto")
        key = str(c.get("revive_tecla") or "").strip()
        if not region:
            self._log(ALERTA, "Revive: defina a área da foto na página revive (sem ela não há como confirmar).")
            return False
        try:
            kb.check_key(key)
        except ValueError as exc:
            self._log(ERRO, f"Revive: tecla inválida ({exc}); confira a página revive.")
            return False
        sens = float(c.get("revive_sens_pct", 2.0))
        timeout = float(c.get("revive_timeout_s", 10.0) or 0)
        w = self.watcher
        try:
            frame = self._grab(region)
            if w.ref is None:
                w.remember(frame)
            elif w.changed_pct(frame) >= sens:    # a foto já difere da memória ANTES de apertar: a memória está velha
                self._log(INFO, "Revive: a foto já era diferente da guardada; uso a de agora como base")
                w.remember(frame)
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Revive: falha ao capturar a foto ({type(exc).__name__}: {exc})")
            return False

        self._log(TIRO, f"Revive: segurando {key.upper()} até a foto mudar"
                        + (f" (no máximo {timeout:.0f}s)" if timeout > 0 else ""))
        t0, best, done = self._clock(), 0.0, False
        try:
            self._down(key)
            while not stop.is_set():
                self._sleep(POLL_S)
                self._down(key)                   # reenvia: repetição automática, como uma tecla realmente segurada
                frame = self._grab(region)
                pct = w.changed_pct(frame)
                best = max(best, pct)
                if pct >= sens:
                    w.remember(frame)             # a imagem nova vira a referência do próximo comando
                    done = True
                    break
                if timeout > 0 and self._clock() - t0 >= timeout:
                    break
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Revive: falha ao segurar a tecla ({type(exc).__name__}: {exc})")
        finally:
            try:
                self._up(key)
            except Exception:  # noqa: BLE001
                pass
        took = self._clock() - t0
        if done:
            self.executions += 1
            self._log(INFO, f"Revive confirmado: a foto mudou ({best:.1f}% dos pixels) após {took:.1f}s; tecla solta")
        elif stop.is_set():
            self._log(ALERTA, f"Revive interrompido após {took:.1f}s sem a foto mudar (o bot voltou a andar)")
        else:
            self._log(ALERTA, f"Revive: a foto NÃO mudou em {took:.0f}s (máximo {best:.1f}% dos pixels, "
                              f"mínimo {sens:.1f}%); tecla solta. Confira a área da foto e a sensibilidade.")
        return done


# ====================================================================== habilidades por % de vida
def normalize_skills(raw) -> list[dict]:
    """[{"pct": 50, "tecla": "1", "cooldown_s": 3}, ...] -> só linhas com tecla e % válidos (0..100)."""
    out = []
    for i, item in enumerate(raw or []):
        key = str((item or {}).get("tecla") or "").strip()
        try:
            pct = float((item or {}).get("pct"))
            cd = max(0.0, float((item or {}).get("cooldown_s", 3.0) or 0))
        except (TypeError, ValueError):
            continue
        if key and 0 <= pct <= 100:
            out.append({"id": i, "pct": pct, "tecla": key, "cooldown_s": cd})
    return out


class LifeSkillMonitor:
    """Lê a vida do pokémon e aperta a tecla da regra que vale (vida <= %). Cada regra tem seu intervalo mínimo."""

    def __init__(self, cfg: ConfigStore, log=None, grab=None, tap=None, clock=time.time):
        self.cfg = cfg
        self._log_fn = log
        self._grab = grab or _default_grab
        self._tap = tap or kb.tap
        self._clock = clock
        self.reader: LifeBarReader | None = None
        self.last_pct: float | None = None
        self._last_read = -1e9
        self._last_press: dict[int, float] = {}
        self._warned: set[str] = set()
        self._logged_pct: float | None = None

    def _log(self, kind: str, msg: str) -> None:
        if self._log_fn is not None:
            self._log_fn(kind, msg)

    def _warn_once(self, key: str, kind: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self._log(kind, msg)

    def step(self, halted: bool) -> None:
        c = self.cfg
        if not c.get("vida_hab_ativo"):
            return
        if c.get("vida_somente_parado", True) and not halted:
            return
        now = self._clock()
        if now - self._last_read < float(c.get("vida_intervalo_s", 0.5)):
            return
        self._last_read = now
        region = c.get("regiao_vida")
        if not region:
            self._warn_once("sem_area", ALERTA, "Habilidades por vida: defina o 'Local Life Bar' na página revive.")
            return
        skills = normalize_skills(c.get("vida_hab_lista"))
        if not skills:
            return
        if self.reader is None:
            self.reader = LifeBarReader(SpriteTemplate.from_file(LIFEBAR_PATH), float(c.get("vida_limiar", 0.6)))
            self._log(INFO, "Habilidades por vida ligadas: " + ", ".join(
                f"≤{s['pct']:g}% → {s['tecla'].upper()}" for s in sorted(skills, key=lambda s: s["pct"])) +
                (" (barra achada pela sprite life bar)" if self.reader.tmpl is not None
                 else " (sem sprite life bar: a área inteira é a barra)"))
        self.reader.min_score = float(c.get("vida_limiar", 0.6))
        try:
            r = self.reader.read(self._grab(region))
        except Exception as exc:  # noqa: BLE001
            self._warn_once("leitura", ERRO, f"Habilidades por vida: falha ao ler a barra ({type(exc).__name__}: {exc})")
            return
        if r.pct is None:
            self._warn_once("sem_barra", ALERTA, f"Habilidades por vida: barra de vida NÃO encontrada (similaridade "
                                                 f"{r.score:.2f}, mínimo {self.reader.min_score:.2f}).")
            return
        self._warned.discard("sem_barra")
        self.last_pct = r.pct
        if self._logged_pct is None or abs(r.pct - self._logged_pct) >= 5 or c.get("log_detalhado"):
            self._logged_pct = r.pct
            self._log(DETECCAO, f"Vida do pokémon: {r.pct:.0f}%")
        s = pick_skill(skills, r.pct)
        if s is None or now - self._last_press.get(s["id"], -1e9) < s["cooldown_s"]:
            return
        try:
            self._tap(s["tecla"])
        except Exception as exc:  # noqa: BLE001
            self._last_press[s["id"]] = now      # não insiste a cada leitura
            self._warn_once(f"tecla{s['id']}", ERRO, f"Habilidades por vida: não consegui apertar {s['tecla'].upper()} ({exc})")
            return
        self._last_press[s["id"]] = now
        self._log(TIRO, f"Vida {r.pct:.0f}% (≤ {s['pct']:g}%): tecla {s['tecla'].upper()}")
