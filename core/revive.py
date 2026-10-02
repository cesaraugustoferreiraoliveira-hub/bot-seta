"""Revive: toque rápido na tecla do revive (E) e verificação por uma 'foto' (uma área da tela); e habilidades por % de vida.

O revive tem CUSTO, então ele é usado uma vez por necessidade:
  - o E é UM toque rápido (não fica segurado nem repete);
  - depois do toque o programa só OLHA a foto, por `revive_verifica_s`; se a foto mudou (>= `revive_sens_pct` % dos
    pixels) o revive foi usado: acabou, e a imagem nova vira a referência;
  - só se a janela acabar sem a foto mudar é que um NOVO toque é permitido (nunca dois ao mesmo tempo: o ciclo em
    core/combo.py recusa um segundo toque antes do fim da janela), até `revive_tentativas` toques (0 = até conseguir);
  - o E só existe dentro de um `Cycle` em que o R já foi executado e o intervalo depois dele já passou; isso é
    conferido antes de CADA toque, e a tecla é reservada em keys.py.

Não depende da GUI: captura de tela e teclas são injetáveis (os testes usam versões falsas).
"""
from __future__ import annotations
import threading
import time

import numpy as np

from . import keys as kb
from .botlog import ALERTA, DETECCAO, ERRO, INFO, TIRO
from .combo import ComboBlocked, Cycle
from .config import ConfigStore, LIFEBAR_PATH
from .lifebar import LifeBarReader, pick_skill
from .vision import SpriteTemplate

POLL_S = 0.05        # intervalo entre capturas da foto enquanto verifica se o E foi usado
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

    def execute(self, cycle: Cycle | None) -> bool:
        """Segura a tecla do revive até a foto mudar. True = confirmado (a foto mudou).
        Sem um ciclo (ou com o ciclo cancelado / sem o R executado / dentro do intervalo) NÃO aperta nada."""
        if cycle is None:
            self._log(ERRO, "Revive: recusado — o E só pode ser apertado dentro do ciclo R -> E do shooter.")
            return False
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
        if kb._norm(key) != kb._norm(cycle.revive_key):
            self._log(ERRO, f"Revive: a tecla da config ({key.upper()}) não é a do ciclo ({cycle.revive_key.upper()}); recusado.")
            return False
        sens = float(c.get("revive_sens_pct", 2.0))
        verify_s = max(0.2, float(c.get("revive_verifica_s", 2.0) or 0))
        tap_s = max(0.01, float(c.get("revive_toque_ms", 80) or 0) / 1000.0)

        if not cycle.r_done:
            self._log(ALERTA, "Revive: recusado — o R deste ciclo não foi executado, então o E não pode ser apertado.")
            cycle.finish()
            return False
        if not cycle.wait_ready():                # espera o intervalo depois do R (sem apertar nada)
            self._log(ALERTA, "Revive: cancelado antes de apertar o E (o bot foi desligado); nenhuma tecla foi enviada.")
            cycle.finish()
            return False

        w = self.watcher
        try:                                      # foto de referência tirada AGORA, logo antes do primeiro toque
            w.remember(self._grab(region))
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Revive: falha ao capturar a foto ({type(exc).__name__}: {exc}); nenhum E foi apertado")
            cycle.finish()
            return False

        attempts = max(0, int(c.get("revive_tentativas", 3) or 0))    # 0 = repete até a foto mudar
        t_all = self._clock()
        done, blocked, best, attempt = False, None, 0.0, 0
        while attempts == 0 or attempt < attempts:
            attempt += 1
            self._log(TIRO, f"Revive: toque rápido em {key.upper()} e verifico a foto por {verify_s:.1f}s"
                            + (f" — tentativa {attempt}" + (f"/{attempts}" if attempts else "")))
            done, blocked, pct_best = self._tap_and_verify(cycle, key, region, sens, verify_s, tap_s)
            best = max(best, pct_best)
            if done or blocked is not None or cycle.cancelled:
                break
            self._log(ALERTA, f"Revive: a foto NÃO mudou em {verify_s:.1f}s (máximo {pct_best:.1f}% dos pixels, mínimo {sens:.1f}%): "
                              "o E não foi usado" + ("; tento de novo" if attempts == 0 or attempt < attempts else ""))
        cycle.finish()
        took = self._clock() - t_all
        gap = cycle.e_gap_s
        gap_txt = f" (E saiu {gap:.2f}s depois do R)" if gap is not None else ""
        if done:
            self.executions += 1
            self._log(INFO, f"Revive confirmado: a foto mudou ({best:.1f}% dos pixels) após {took:.1f}s e {cycle.presses} toque(s) no E{gap_txt}")
        elif blocked is not None or cycle.cancelled:
            self._log(ALERTA, f"Revive interrompido após {took:.1f}s sem a foto mudar ({blocked or 'o bot foi desligado'})")
        else:
            self._log(ALERTA, f"Revive: a foto NÃO mudou em {cycle.presses} toque(s) no E (máximo {best:.1f}% dos pixels, "
                              f"mínimo {sens:.1f}%). Confira a área da foto e a sensibilidade.")
        return done

    def _tap_and_verify(self, cycle: Cycle, key: str, region, sens: float, verify_s: float, tap_s: float):
        """UM toque rápido no E e depois só OLHA a foto por `verify_s` segundos (sem apertar mais nada).
        O próximo toque só pode vir depois dessa janela (o ciclo também impõe isso). -> (confirmado, bloqueio|None, maior %)."""
        w = self.watcher
        best, done, blocked, pressed = 0.0, False, None, False
        try:
            with cycle.revive_press():
                self._down(key)
            pressed = True
            t0 = self._clock()
            self._sleep(tap_s)
            self._up(key)
            pressed = False
            while not cycle.cancelled:
                pct = w.changed_pct(self._grab(region))
                best = max(best, pct)
                if pct >= sens:
                    w.remember(self._grab(region))     # a imagem nova vira a referência do próximo comando
                    done = True
                    break
                if self._clock() - t0 >= verify_s:
                    break
                self._sleep(POLL_S)
        except ComboBlocked as exc:
            blocked = str(exc)
        except Exception as exc:  # noqa: BLE001
            blocked = f"falha ao enviar a tecla ou capturar a foto: {type(exc).__name__}: {exc}"   # não insiste num erro
            self._log(ERRO, f"Revive: {blocked}")
        finally:
            if pressed:                           # soltar nunca é bloqueado: a tecla não pode ficar presa
                try:
                    self._up(key)
                except Exception:  # noqa: BLE001
                    pass
        if not done and blocked is None and not cycle.cancelled and cycle.retry_gap_s > 0:
            # a janela de verificação acabou: respeita o intervalo mínimo entre toques antes de deixar tentar de novo
            cycle.wait_ready()
        return done, blocked, best


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
