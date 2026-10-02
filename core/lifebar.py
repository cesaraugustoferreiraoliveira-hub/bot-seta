"""Leitura da % de vida do pokémon a partir da barra de vida na pokebar.

Sem tela e sem GUI: recebe e devolve arrays numpy (por isso é testável).

Como funciona:
  - A 'sprite life bar' (capturada pelo usuário com a vida CHEIA) diz ONDE está a barra dentro do 'Local Life Bar'
    e quantas colunas ela ocupa quando cheia. Sem a sprite, a LARGURA da área é tratada como a da barra cheia (sobrar altura não atrapalha).
  - Uma coluna da barra conta como 'cheia' se tem cor viva (verde, amarelo, vermelho: saturação e brilho altos) nas
    linhas da barra (achadas sozinhas). A parte vazia (cinza, preta, branca) tem saturação baixa e não conta.
  - % = colunas cheias agora / colunas cheias da barra cheia.
"""
from __future__ import annotations
from dataclasses import dataclass

import cv2
import numpy as np

from .vision import SpriteTemplate, sprite_scores

SAT_MIN = 80        # saturação mínima (0..255) para um pixel ser 'cor de vida'
VAL_MIN = 80        # brilho mínimo (0..255)
COL_FRACTION = 0.5  # fração das linhas da barra que precisa ser colorida para a coluna contar como cheia
ROW_FRACTION = 0.5  # uma linha pertence à barra se tem pelo menos esta fração dos pixels coloridos da linha mais cheia


def filled_columns(bar_bgr: np.ndarray) -> int:
    """Quantas colunas têm cor de vida. Primeiro acha as linhas da barra (a área pode ter margem em volta dela),
    depois conta as colunas coloridas nessas linhas."""
    hsv = cv2.cvtColor(np.ascontiguousarray(bar_bgr), cv2.COLOR_BGR2HSV)
    colored = (hsv[..., 1] >= SAT_MIN) & (hsv[..., 2] >= VAL_MIN)
    per_row = colored.sum(axis=1)
    if per_row.max() == 0:
        return 0
    rows = per_row >= ROW_FRACTION * per_row.max()
    return int((colored[rows].mean(axis=0) >= COL_FRACTION).sum())


def structure_template(tmpl: SpriteTemplate) -> SpriteTemplate:
    """A cor do preenchimento muda com a vida (verde, amarelo, vermelho) e o comprimento também: então a sprite é
    casada só pelas partes SEM cor viva (moldura, fundo, detalhes). Se sobrar muito pouco (recorte colado no
    preenchimento), usa a sprite inteira."""
    hsv = cv2.cvtColor(tmpl.bgr, cv2.COLOR_BGR2HSV)
    keep = (tmpl.mask > 0) & (hsv[..., 1] < SAT_MIN)
    if keep.sum() < max(16, 0.15 * tmpl._npix):
        return tmpl
    return SpriteTemplate(tmpl.bgr, keep.astype(np.uint8) * 255)


@dataclass
class LifeReading:
    pct: float | None          # 0..100, ou None se a barra não foi achada
    box: tuple | None = None   # (x, y, w, h) da barra dentro do frame
    score: float = 0.0         # similaridade da sprite (1.0 se não há sprite)
    cols: int = 0              # colunas cheias agora
    full_cols: int = 0         # colunas cheias da barra cheia (referência)


class LifeBarReader:
    def __init__(self, tmpl: SpriteTemplate | None, min_score: float = 0.6):
        self.tmpl = tmpl
        self.min_score = min_score
        self.full_cols = max(1, filled_columns(tmpl.bgr)) if tmpl is not None else 0
        self._match = structure_template(tmpl) if tmpl is not None else None
        self._last_box: tuple | None = None

    def read(self, frame_bgr: np.ndarray) -> LifeReading:
        h, w = frame_bgr.shape[:2]
        if self.tmpl is None:
            box, score, full = (0, 0, w, h), 1.0, w            # sem sprite: a área inteira é a barra
        else:
            t = self.tmpl
            res = sprite_scores(frame_bgr, self._match)
            if res is None:
                return LifeReading(None)
            _, best, _, loc = cv2.minMaxLoc(res)
            if best >= self.min_score:
                box, score = (loc[0], loc[1], t.w, t.h), float(best)
                self._last_box = box
            elif self._last_box is not None and self._last_box[0] + t.w <= w and self._last_box[1] + t.h <= h:
                box, score = self._last_box, float(best)       # a cor mudou demais: usa onde a barra estava
            else:
                return LifeReading(None, None, float(best))
            full = self.full_cols
        x, y, bw, bh = box
        cols = filled_columns(frame_bgr[y:y + bh, x:x + bw])
        pct = float(np.clip(100.0 * cols / max(1, full), 0.0, 100.0))
        return LifeReading(pct, box, score, cols, full)


def pick_skill(skills: list[dict], pct: float) -> dict | None:
    """Regra mais específica que vale para esta vida: a de MENOR limite ainda >= pct (vida <= limite)."""
    ok = [s for s in skills if pct <= float(s["pct"])]
    return min(ok, key=lambda s: float(s["pct"])) if ok else None
