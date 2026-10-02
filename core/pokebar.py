"""Leitura das barras de vida/habilidades e validação de comandos do pokémon."""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from . import capture, keys as kb, vision
from .botlog import ALERTA, ERRO, INFO, TIRO
from .config import ConfigStore, POKEBAR_HEALTH_PATH, POKEBAR_SKILL_PATH


def fill_percent(bar_bgr: np.ndarray) -> float:
    """Estima o preenchimento horizontal de uma barra colorida em 0..100.

    Ignora a moldura e conta uma coluna como preenchida quando ela tem cor
    suficientemente saturada. Isso funciona tanto para verde (vida) quanto
    azul/amarelo (habilidades), sem depender da cor exata do jogo.
    """
    if bar_bgr is None or min(bar_bgr.shape[:2]) < 3:
        return 0.0
    h, w = bar_bgr.shape[:2]
    x0, x1 = max(0, round(w * .08)), min(w, max(round(w * .92), 1))
    y0, y1 = max(0, round(h * .20)), min(h, max(round(h * .80), 1))
    roi = bar_bgr[y0:y1, x0:x1]
    if roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    colored = (hsv[..., 1] >= 55) & (hsv[..., 2] >= 55)
    columns = colored.mean(axis=0) >= .25
    # Pequenos brilhos/ícones no fim não podem transformar uma barra vazia em cheia.
    run = 0
    for on in columns:
        if on:
            run += 1
        else:
            break
    return round(100.0 * run / max(1, len(columns)), 1)


def is_full(percent: float, tolerance: float = 2.0) -> bool:
    return percent >= 100.0 - tolerance


class PokeBarController:
    """Captura o *pokebar space*, mede as duas barras e executa regras de vida."""
    def __init__(self, cfg: ConfigStore, log=None):
        self.cfg, self._log_fn = cfg, log
        self._health_tmpl = self._skill_tmpl = None
        self._loaded = False
        self._last_rule: tuple[int, str] | None = None
        self.last_health = self.last_skill = None
        self._lock = threading.Lock()

    def _log(self, kind, message):
        if self._log_fn:
            self._log_fn(kind, message)

    @property
    def configured(self) -> bool:
        return bool(self.cfg.get("regiao_pokebar"))

    def _templates(self):
        if not self._loaded:
            self._loaded = True
            self._health_tmpl = vision.SpriteTemplate.from_file(POKEBAR_HEALTH_PATH)
            self._skill_tmpl = vision.SpriteTemplate.from_file(POKEBAR_SKILL_PATH)
        return self._health_tmpl, self._skill_tmpl

    def read(self):
        """Retorna ``(vida, habilidades)`` em percentual; cada item pode ser ``None``."""
        region = self.cfg.get("regiao_pokebar")
        if not region:
            return None, None
        health_tmpl, skill_tmpl = self._templates()
        frame = capture.grab(region)
        values = []
        for tmpl in (health_tmpl, skill_tmpl):
            if tmpl is None:
                values.append(None)
                continue
            score_map = vision.sprite_scores(frame, tmpl)
            if score_map is None:
                values.append(None)
                continue
            _, score, _, loc = cv2.minMaxLoc(score_map)
            if score < float(self.cfg.get("pokebar_limiar", .7)):
                values.append(None)
                continue
            patch = frame[loc[1]:loc[1] + tmpl.h, loc[0]:loc[0] + tmpl.w]
            values.append(fill_percent(patch))
        self.last_health, self.last_skill = values
        return tuple(values)

    def step(self) -> tuple[float | None, float | None]:
        """Atualiza a leitura e aperta uma regra de emergência, se aplicável."""
        if not self.cfg.get("pokebar_ativo") or not self.configured:
            return None, None
        with self._lock:
            health, skill = self.read()
            if health is None:
                return health, skill
            if health > 100:  # proteção para dados de uma implementação customizada
                health = 100.0
            rules = sorted(self.cfg.get("pokebar_regras_vida") or [], key=lambda r: float(r.get("percentual", 0)))
            matching = next((r for r in rules if health <= float(r.get("percentual", -1))), None)
            if matching is None:
                self._last_rule = None  # recuperou: permite usar a cura numa próxima queda
                return health, skill
            key = str(matching.get("tecla") or "").strip()
            ident = (int(float(matching.get("percentual", 0))), key)
            if not key or ident == self._last_rule:
                return health, skill
            try:
                kb.check_key(key)
                kb.tap(key)
            except Exception as exc:  # noqa: BLE001
                self._log(ERRO, f"Pokebar: não consegui apertar {key.upper()} para salvar a vida ({exc})")
                return health, skill
            self._last_rule = ident
            self._log(TIRO, f"Pokebar: vida em {health:.0f}% (limite {ident[0]}%); apertei {key.upper()}")
            return health, skill

    def run_sequence(self, steps, stop: threading.Event) -> bool:
        """Executa a sequência do shooter e, para R/E, confirma a resposta das barras."""
        validate = bool(self.cfg.get("pokebar_validar_sequencia")) and self.configured
        before_h, before_s = self.read() if validate else (None, None)
        for i, (key, delay_ms) in enumerate(steps):
            if stop.is_set():
                return False
            kb.tap(key)
            if i < len(steps) - 1 and stop.wait(delay_ms / 1000.0):
                return False
            key_l = key.lower()
            if validate and key_l in {"r", "e"}:
                deadline = time.monotonic() + float(self.cfg.get("pokebar_tolerancia_validacao_s", 1.0))
                valid = False
                while not stop.is_set() and time.monotonic() < deadline:
                    _, skill = self.read()
                    if skill is not None:
                        valid = skill < (before_s or 100) - 2 if key_l == "r" else is_full(skill)
                        if valid:
                            break
                    stop.wait(.08)
                if not valid:
                    self._log(ALERTA, f"Pokebar: {key.upper()} não foi confirmado pela barra de habilidades")
                    return False
                before_s = skill
        if validate and before_h is not None:
            self._log(INFO, f"Pokebar: sequência validada; vida {self.last_health:.0f}% e habilidades {self.last_skill:.0f}%")
        return True
