"""Revive e habilidades por vida.

O módulo é independente da interface: ``ImageChangeMonitor`` guarda/compara o
quadro de uma região e ``ReviveController`` é chamado pelo evento já calculado
do Shooter quando todas as sprites entram no limite.
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from . import capture, keys as kb, vision
from .botlog import ALERTA, ERRO, INFO, TIRO
from .config import ConfigStore, LIFE_BAR_PATH, REVIVE_REFERENCE_PATH


def image_difference_percent(reference: np.ndarray, current: np.ndarray, pixel_delta: int = 18) -> float:
    """Percentual de pixels cuja diferença de cor supera ``pixel_delta``.

    Um limiar por pixel elimina ruído e anti-aliasing; o percentual é a segunda
    barreira contra uma animação pequena disparar o comando por engano.
    """
    if reference.shape != current.shape:
        return 100.0
    diff = cv2.absdiff(reference, current)
    return float((diff.max(axis=2) >= pixel_delta).mean() * 100.0)


class ImageChangeMonitor:
    """Referência persistida em PNG para uma região e comparação tolerante a ruído."""
    def __init__(self, path=REVIVE_REFERENCE_PATH):
        self.path = path
        self.reference: np.ndarray | None = self._load()

    def _load(self):
        image = cv2.imread(str(self.path), cv2.IMREAD_COLOR)
        return image

    def set_reference(self, frame: np.ndarray) -> None:
        self.reference = frame.copy()
        cv2.imwrite(str(self.path), self.reference)

    def capture_reference(self, region) -> None:
        self.set_reference(capture.grab(region))

    def changed(self, frame: np.ndarray, threshold_pct: float) -> tuple[bool, float]:
        if self.reference is None:
            return False, 0.0
        pct = image_difference_percent(self.reference, frame)
        return pct >= threshold_pct, pct


def life_bar_percentage(frame: np.ndarray, template: vision.SpriteTemplate, threshold: float) -> tuple[float | None, float]:
    """Localiza a barra e estima sua vida pela extensão horizontal da parte colorida.

    Barras de vida dos jogos normalmente são preenchidas da esquerda para a
    direita. Depois de localizar o recorte pelo mesmo matcher de sprites do
    projeto, são contadas colunas com pixels saturados (verde, amarelo ou
    vermelho); o último trecho contínuo colorido determina a porcentagem.
    """
    scores = vision.sprite_scores(frame, template)
    if scores is None:
        return None, 0.0
    _, best, _, loc = cv2.minMaxLoc(scores)
    if best < threshold:
        return None, float(best)
    x, y = loc
    bar = frame[y:y + template.h, x:x + template.w]
    hsv = cv2.cvtColor(bar, cv2.COLOR_BGR2HSV)
    colored = (hsv[..., 1] >= 70) & (hsv[..., 2] >= 60)
    columns = colored.mean(axis=0) >= 0.16
    # ignora bordas decorativas: usa o primeiro e o último grupo de colunas úteis
    if not columns.any():
        return 0.0, float(best)
    first = int(np.argmax(columns))
    filled = first
    for i in range(first, len(columns)):
        if columns[i]:
            filled = i + 1
        elif i - filled > 2:
            break
    width = max(1, len(columns) - first)
    return round(min(100.0, max(0.0, 100.0 * (filled - first) / width)), 1), float(best)


class ReviveController:
    """Executa revive até mudança visual e dispara habilidades uma vez por queda de vida."""
    def __init__(self, cfg: ConfigStore, log=None):
        self.cfg, self._log_fn = cfg, log
        self.monitor = ImageChangeMonitor()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._armed_rules: set[int] = set(range(len(cfg.get("revive_habilidades") or [])))
        self._rules_seen = len(self._armed_rules)
        self._warned: set[str] = set()
        self._life_template = None

    def _log(self, kind, message):
        if self._log_fn:
            self._log_fn(kind, message)

    def _warn_once(self, key, message):
        if key not in self._warned:
            self._warned.add(key); self._log(ALERTA, message)

    def start(self) -> None:
        """Captura a referência ao iniciar o bot, se ainda não houver uma válida."""
        region = self.cfg.get("regiao_revive_monitor")
        if self.cfg.get("revive_ativo") and region and self.monitor.reference is None:
            try:
                self.monitor.capture_reference(region)
                self._log(INFO, "Revive: imagem de referência capturada")
            except Exception as exc:  # noqa: BLE001
                self._warn_once("reference", f"Revive: não consegui capturar a referência ({exc})")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        try:
            kb.key_up_any(str(self.cfg.get("revive_tecla") or ""))
        except Exception:  # noqa: BLE001
            pass

    def trigger(self) -> None:
        """Chamado somente pelo Shooter ao atingir o estado 'todas dentro'."""
        if not self.cfg.get("revive_ativo") or (self._thread and self._thread.is_alive()):
            return
        region, key = self.cfg.get("regiao_revive_monitor"), str(self.cfg.get("revive_tecla") or "").strip()
        if not region:
            self._warn_once("region", "Revive: defina a região de monitoramento na aba Revive.")
            return
        try:
            kb.check_key(key)
        except ValueError as exc:
            self._warn_once("key", f"Revive: tecla inválida ({exc}).")
            return
        if self.monitor.reference is None:
            self.monitor.capture_reference(region)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_revive, args=(region, key), daemon=True)
        self._thread.start()

    def _run_revive(self, region, key) -> None:
        threshold = float(self.cfg.get("revive_diferenca_pct", 4.0))
        self._log(TIRO, f"Revive: segurando {key.upper()} até a região mudar ({threshold:.1f}% mín.)")
        try:
            kb.key_down_any(key)
            while not self._stop.wait(0.10):
                frame = capture.grab(region)
                changed, pct = self.monitor.changed(frame, threshold)
                if changed:
                    self.monitor.set_reference(frame)
                    self._log(INFO, f"Revive concluído: região mudou {pct:.1f}%; referência atualizada")
                    return
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Revive: falha ao executar ({type(exc).__name__}: {exc})")
        finally:
            try:
                kb.key_up_any(key)
            except Exception:  # noqa: BLE001
                pass

    def check_life(self) -> None:
        """Lê a vida e dispara regras cruzadas; chamado no loop do bot sem bloquear."""
        if not self.cfg.get("revive_ativo"):
            return
        region = self.cfg.get("regiao_life_bar")
        if not region:
            return
        if self._life_template is None:
            self._life_template = vision.SpriteTemplate.from_file(LIFE_BAR_PATH)
            if self._life_template is None:
                self._warn_once("life_sprite", "Revive: selecione a Sprite Life Bar na aba Revive.")
                return
        life, _ = life_bar_percentage(capture.grab(region), self._life_template, float(self.cfg.get("life_bar_limiar", .85)))
        if life is None:
            return
        rules = self.cfg.get("revive_habilidades") or []
        if len(rules) > self._rules_seen:
            self._armed_rules.update(range(self._rules_seen, len(rules)))
        self._rules_seen = len(rules)
        for i, rule in enumerate(rules):
            try:
                limit, key = float(rule.get("vida")), str(rule.get("tecla") or "").strip()
            except (AttributeError, TypeError, ValueError):
                continue
            if life > limit:
                self._armed_rules.add(i)  # recuperou vida: pode disparar novamente numa nova queda
            elif i in self._armed_rules:
                try:
                    kb.check_key(key); kb.tap(key)
                    self._armed_rules.discard(i)
                    self._log(TIRO, f"Vida {life:.1f}% ≤ {limit:.0f}%: habilidade {key.upper()}")
                except ValueError:
                    self._warn_once(f"rule-{i}", f"Revive: habilidade {i + 1} tem tecla inválida.")
