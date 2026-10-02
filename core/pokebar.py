"""Leitura das barras de vida/habilidades e validação de comandos do pokémon."""
from __future__ import annotations

import threading

import cv2
import numpy as np

from . import capture, keys as kb, vision
from .botlog import ERRO, INFO, TIRO
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
        """Executa a sequência do shooter.

        Quando a pokebar está configurada, ``R`` arma uma recuperação obrigatória:
        após uma pausa curta o bot chama ``E`` e a chama novamente em intervalos
        curtos até a barra de habilidades ficar cheia. Não exige que a leitura
        consiga enxergar a queda causada por R: em jogos rápidos ela pode já ter
        voltado a 100% no primeiro quadro, e isso não significa que R falhou.
        """
        validate = bool(self.cfg.get("pokebar_validar_sequencia")) and self.configured
        if not validate:
            return self._run_plain_sequence(steps, stop)
        with self._lock:
            i = 0
            while i < len(steps):
                if stop.is_set():
                    return False
                key, delay_ms = steps[i]
                key_l = key.lower()
                try:
                    kb.tap(key)
                except Exception as exc:  # noqa: BLE001
                    self._log(ERRO, f"Pokebar: falha ao apertar {key.upper()} ({exc})")
                    return False
                if key_l == "r":
                    # A espera configurada na sequência pode ser 800 ms ou mais;
                    # para R/E ela é perigosa, então a limitamos à pausa curta da pokebar.
                    if stop.wait(max(0, float(self.cfg.get("pokebar_espera_e_ms", 100))) / 1000.0):
                        return False
                    restored = self._restore_skills(stop)
                    if not restored:
                        return False
                    # O E seguinte na sequência já foi feito por _restore_skills.
                    if i + 1 < len(steps) and steps[i + 1][0].lower() == "e":
                        i += 1
                elif i < len(steps) - 1 and stop.wait(delay_ms / 1000.0):
                    return False
                i += 1
        return True

    def _run_plain_sequence(self, steps, stop: threading.Event) -> bool:
        """Mantém o comportamento configurável original quando não há pokebar para confirmar."""
        for i, (key, delay_ms) in enumerate(steps):
            if stop.is_set():
                return False
            try:
                kb.tap(key)
            except Exception as exc:  # noqa: BLE001
                self._log(ERRO, f"Pokebar: falha ao apertar {key.upper()} ({exc})")
                return False
            if i < len(steps) - 1 and stop.wait(delay_ms / 1000.0):
                return False
        return True

    def _restore_skills(self, stop: threading.Event) -> bool:
        """Aperta E repetidamente até confirmar a barra cheia, sem esperar demais.

        Não há prazo de desistência: depois de R, só parar de mandar E sem ver
        100% pode deixar o pokémon sem habilidades. O ciclo acaba somente ao
        confirmar a barra cheia ou quando o shooter/bot é interrompido.
        """
        retry_s = max(.02, float(self.cfg.get("pokebar_retentativa_e_ms", 75)) / 1000.0)
        attempts = 0
        while not stop.is_set():
            try:
                kb.tap("e")
            except Exception as exc:  # noqa: BLE001
                self._log(ERRO, f"Pokebar: falha ao apertar E ({exc})")
                return False
            attempts += 1
            # Espera apenas o suficiente para o jogo redesenhar a barra; E nunca fica
            # parado aguardando a confirmação de R.
            if stop.wait(retry_s):
                return False
            _, skill = self.read()
            if skill is not None and is_full(skill):
                self._log(INFO, f"Pokebar: E confirmado após {attempts} tentativa(s); habilidades em {skill:.0f}%")
                return True
        return False
