"""Leitura visual e validação da Pokébar, sem dependência da interface.

As referências são capturas das barras *cheias*.  Durante a leitura, somente a
parte colorida da referência é comparada e a proporção contínua de colunas que
ainda coincide dá o percentual. Isso torna o resultado independente de teclas
enviadas: a confirmação só acontece após uma alteração visível na tela.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import capture
from .config import POKEBAR_ABILITY_PATH, POKEBAR_LIFE_PATH, ConfigStore


@dataclass(frozen=True)
class BarReading:
    percent: float
    filled: bool
    empty: bool


def _fill_mask(reference: np.ndarray) -> np.ndarray:
    """Pixels coloridos da referência; remove uma borda fina do recorte."""
    hsv = cv2.cvtColor(reference, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] >= 35) & (hsv[:, :, 2] >= 45)
    if min(mask.shape) > 4:
        mask[:1] = mask[-1:] = False
        mask[:, :1] = mask[:, -1:] = False
    return mask


def bar_percent(frame: np.ndarray, reference: np.ndarray) -> float | None:
    """Retorna 0..100 comparando a barra atual à referência cheia.

    A leitura usa colunas consecutivas a partir da esquerda (o formato usual
    das barras) para que a moldura fixa não conte como vida/habilidade cheia.
    """
    if frame is None or reference is None or frame.shape != reference.shape:
        return None
    mask = _fill_mask(reference)
    if not mask.any():
        return None
    distance = np.max(np.abs(frame.astype(np.int16) - reference.astype(np.int16)), axis=2)
    same = (distance <= 55) & mask
    total = mask.sum(axis=0)
    valid = total > 0
    matched = np.divide(same.sum(axis=0), total, out=np.zeros_like(total, dtype=float), where=valid)
    # Cada coluna pertence ao preenchimento se a maioria de seus pixels de cor
    # ainda é a cor da barra cheia. Pequenas lacunas de antialiasing são aceitas.
    columns = (matched >= 0.55) & valid
    indices = np.flatnonzero(valid)
    if not len(indices):
        return None
    run = 0
    for i in indices:
        if columns[i]:
            run += 1
        elif run:
            break
    return round(100.0 * run / len(indices), 1)


class PokebarMonitor:
    """Captura somente a Pokébar Space e produz leituras das duas barras."""
    def __init__(self, cfg: ConfigStore):
        self.cfg = cfg

    def _reference(self, kind: str) -> np.ndarray | None:
        path = POKEBAR_LIFE_PATH if kind == "life" else POKEBAR_ABILITY_PATH
        return cv2.imread(str(path), cv2.IMREAD_COLOR) if Path(path).exists() else None

    def read(self, kind: str, space_frame: np.ndarray | None = None) -> BarReading | None:
        region = self.cfg.get(f"pokebar_{kind}_region")
        space = self.cfg.get("pokebar_space")
        reference = self._reference(kind)
        if not region or not space or reference is None:
            return None
        frame = capture.grab(space) if space_frame is None else space_frame
        x, y, w, h = (int(v) for v in region)
        if x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
            return None
        current = frame[y:y + h, x:x + w]
        percent = bar_percent(current, reference)
        if percent is None:
            return None
        full_at = float(self.cfg.get("pokebar_cheia_pct", 95))
        return BarReading(percent, percent >= full_at, percent <= 2.0)

    def snapshot(self) -> BarReading | None:
        return self.read("ability")


class CommandValidator:
    """Valida R/E pela mudança visual posterior ao envio da tecla."""
    def __init__(self, cfg: ConfigStore, log=None):
        self.cfg, self.monitor, self.log = cfg, PokebarMonitor(cfg), log

    def enabled(self) -> bool:
        return bool(self.cfg.get("pokebar_validar_shooter"))

    def validate(self, key: str, before: BarReading | None) -> bool | None:
        """True confirmado, False não confirmado, None quando não configurado."""
        key = key.lower()
        if not self.enabled() or key not in {"r", "e"}:
            return None
        if before is None:
            self._emit(f"Pokébar: não validei {key.upper()}; Ability Bar não pôde ser lida antes do comando.")
            return False
        deadline = time.monotonic() + float(self.cfg.get("pokebar_validar_timeout_s", 1.5))
        delta = float(self.cfg.get("pokebar_validar_delta_pct", 8))
        while time.monotonic() < deadline:
            after = self.monitor.snapshot()
            if after is not None:
                confirmed = after.percent <= before.percent - delta if key == "r" else after.filled
                if confirmed:
                    self._emit(f"Pokébar: {key.upper()} confirmado visualmente (Ability {before.percent:.0f}% → {after.percent:.0f}%).")
                    return True
            time.sleep(0.08)
        self._emit(f"Pokébar: {key.upper()} NÃO foi confirmado visualmente dentro do tempo configurado.")
        return False

    def _emit(self, message: str) -> None:
        if self.log:
            from .botlog import ALERTA, INFO
            self.log(INFO if "confirmado visualmente" in message and "NÃO" not in message else ALERTA, message)


class LifeActionController:
    """Dispara comandos uma vez ao cruzar cada limite de vida configurado.

    O controlador não estima vida a partir do último comando: ele sempre decide
    a partir da captura mais recente da Life Bar. Ao recuperar vida, os limites
    são rearmados para uma próxima queda.
    """
    def __init__(self, cfg: ConfigStore, log=None):
        self.cfg, self.monitor, self.log = cfg, PokebarMonitor(cfg), log
        self._fired: set[tuple[int, str]] = set()
        self._last_read = 0.0

    def poll(self) -> None:
        if time.monotonic() - self._last_read < 0.25:
            return
        self._last_read = time.monotonic()
        reading = self.monitor.read("life")
        if reading is None:
            return
        rules = self.cfg.get("pokebar_life_actions", []) or []
        active: set[tuple[int, str]] = set()
        for item in rules:
            try:
                threshold = int(float(item.get("percentual", 0)))
            except (AttributeError, TypeError, ValueError):
                continue
            command = str(item.get("comando", "")).strip()
            ident = (threshold, command)
            if reading.percent < threshold:
                active.add(ident)
                if command and ident not in self._fired:
                    self._run(command, threshold, reading.percent)
        # Uma barra recuperada acima do limite permite disparar a regra numa
        # queda futura, mas não repete o comando enquanto ela segue baixa.
        self._fired.intersection_update(active)

    def _run(self, command: str, threshold: int, percent: float) -> None:
        try:
            from . import keys as kb
            kb.check_key(command)
            kb.tap(command)
        except Exception as exc:  # noqa: BLE001
            if self.log:
                from .botlog import ERRO
                self.log(ERRO, f"Pokébar: não consegui executar '{command}' para vida abaixo de {threshold}% ({exc}).")
            return
        self._fired.add((threshold, command))
        if self.log:
            from .botlog import TIRO
            self.log(TIRO, f"Pokébar: Life Bar {percent:.0f}% (< {threshold}%); executei {command.upper()}.")
