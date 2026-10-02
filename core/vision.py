"""Visão computacional sobre os frames capturados: achar a seta e contar sprites."""
from __future__ import annotations
import cv2
import numpy as np

from .magic_cut import load_template


class SpriteTemplate:
    """Sprite carregada do disco (com máscara de transparência, se houver)."""

    def __init__(self, bgr: np.ndarray, mask: np.ndarray):
        self.bgr = bgr
        self.mask = mask
        self.h, self.w = bgr.shape[:2]
        m = (mask > 0).astype(np.float32)
        self._mask3 = cv2.merge([m, m, m])
        self._npix = float(max(1, (mask > 0).sum()))
        t = bgr.astype(np.float32)
        self._masked_t = t * self._mask3
        self._t_energy = float((t * t * self._mask3).sum())

    @classmethod
    def from_file(cls, path):
        loaded = load_template(path)
        return None if loaded is None else cls(*loaded)


def sprite_scores(frame_bgr: np.ndarray, tmpl: SpriteTemplate) -> np.ndarray | None:
    """Mapa de similaridade 0..1 (1 = idêntico). Só compara os pixels da sprite (máscara),
    então o fundo atrás dela não atrapalha. Mede o erro médio de cor por pixel."""
    if frame_bgr.shape[0] < tmpl.h or frame_bgr.shape[1] < tmpl.w:
        return None
    # Σ m·(T-I)² = Σ m·T² - 2·Σ (m·T)·I + Σ m·I²  (as duas correlações são rápidas, via FFT)
    img = frame_bgr.astype(np.float32)
    cross = cv2.matchTemplate(img, tmpl._masked_t, cv2.TM_CCORR)
    energy = cv2.matchTemplate(img * img, tmpl._mask3, cv2.TM_CCORR)
    sq = tmpl._t_energy - 2.0 * cross + energy
    rmse = np.sqrt(np.maximum(sq, 0) / (tmpl._npix * 3.0))
    return np.clip(1.0 - rmse / 255.0 * 3.0, 0.0, 1.0)  # erro médio de ~85/255 por canal = 0


def find_sprites(frame_bgr: np.ndarray, tmpl: SpriteTemplate, limiar: float):
    """Acha as sprites distintas do frame. Retorna (lista de (cx, cy, similaridade), melhor similaridade).

    (cx, cy) é o CENTRO da sprite, em pixels do frame; a lista vem da melhor para a pior, sem acertos sobrepostos."""
    res = sprite_scores(frame_bgr, tmpl)
    if res is None:
        return [], 0.0
    ys, xs = np.nonzero(res >= limiar)
    kept: list[tuple[int, int]] = []
    hits: list[tuple[float, float, float]] = []
    for i in np.argsort(-res[ys, xs]):
        p = (int(xs[i]), int(ys[i]))
        if all(abs(p[0] - q[0]) > tmpl.w * 0.6 or abs(p[1] - q[1]) > tmpl.h * 0.6 for q in kept):
            kept.append(p)
            hits.append((p[0] + (tmpl.w - 1) / 2, p[1] + (tmpl.h - 1) / 2, float(res[p[1], p[0]])))
    return hits, float(res.max())


def detect_sprites(frame_bgr: np.ndarray, tmpl: SpriteTemplate, limiar: float) -> tuple[int, float]:
    """Retorna (quantas sprites distintas aparecem, melhor similaridade encontrada)."""
    hits, best = find_sprites(frame_bgr, tmpl, limiar)
    return len(hits), best


def count_sprites(frame_bgr: np.ndarray, tmpl: SpriteTemplate, limiar: float) -> int:
    """Quantas sprites distintas aparecem no frame (ignora acertos sobrepostos)."""
    return detect_sprites(frame_bgr, tmpl, limiar)[0]


def find_arrow(frame_bgr: np.ndarray, limiar: int = 240):
    """Centro dos pixels brancos (a seta). Retorna (x, y) ou None."""
    m = (frame_bgr[..., 0] >= limiar) & (frame_bgr[..., 1] >= limiar) & (frame_bgr[..., 2] >= limiar)
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def locate_arrow(frame_bgr: np.ndarray, tmpl: "SpriteTemplate | None", sprite_limiar: float = 0.85,
                 white_limiar: int = 215):
    """Acha a seta no mapa ao vivo. Retorna (pos (x, y) | None, método, melhor similaridade).

    Com a sprite da seta (recortada pelo usuário) usa a mesma comparação por máscara da sprite de parada;
    sem ela, cai no centro dos pixels brancos."""
    if tmpl is not None:
        res = sprite_scores(frame_bgr, tmpl)
        if res is None:
            return None, "sprite", 0.0
        _, best, _, loc = cv2.minMaxLoc(res)
        if best < sprite_limiar:
            return None, "sprite", float(best)
        return (loc[0] + (tmpl.w - 1) / 2, loc[1] + (tmpl.h - 1) / 2), "sprite", float(best)
    pos = find_arrow(frame_bgr, white_limiar)
    return pos, "branco", 1.0 if pos else 0.0
