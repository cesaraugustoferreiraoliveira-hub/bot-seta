"""Análise do mapa completo: o que é caminhável e qual é a rota central do loop."""
from __future__ import annotations
import math
import os
import cv2
import numpy as np


def build_walkable(bgr: np.ndarray, cores_livres, tolerancia: int, mask_path=None) -> np.ndarray:
    """Máscara booleana de pixels caminháveis. Qualquer cor fora de `cores_livres` é obstáculo.
    `mask_path` (opcional): PNG do mesmo tamanho; vermelho puro = obstáculo, verde puro = livre."""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(int)
    walk = np.zeros(rgb.shape[:2], bool)
    for c in cores_livres:
        walk |= (np.abs(rgb - np.array(c)).max(axis=2) <= tolerancia)
    if mask_path and os.path.exists(mask_path):
        m = cv2.imread(str(mask_path))
        if m is not None and m.shape[:2] == walk.shape:
            m = cv2.cvtColor(m, cv2.COLOR_BGR2RGB)
            walk[(m[..., 0] > 200) & (m[..., 1] < 60) & (m[..., 2] < 60)] = False
            walk[(m[..., 1] > 200) & (m[..., 0] < 60) & (m[..., 2] < 60)] = True
    return cv2.morphologyEx(walk.astype(np.uint8), cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)).astype(bool)


def build_loop(walk: np.ndarray, sentido: str = "horario", bins: int = 180) -> np.ndarray:
    """Rota central: para cada ângulo ao redor da ilha escolhe o pixel caminhável mais
    afastado das paredes (distance transform). Retorna array Nx2 (x, y) na ordem do sentido."""
    ys, xs = np.nonzero(walk)
    if len(xs) < 10:
        raise ValueError("Mapa sem área caminhável: ajuste 'cores_livres' ou a máscara.")
    dist = cv2.distanceTransform(walk.astype(np.uint8), cv2.DIST_L2, 3)
    pts = np.column_stack([xs, ys]).astype(np.float32)
    M = cv2.moments(cv2.convexHull(pts))
    if M["m00"] == 0:
        raise ValueError("O corredor está reto/fino demais para formar um loop: marque uma área maior.")
    cx, cy = M["m10"] / M["m00"], M["m01"] / M["m00"]
    ang = np.arctan2(ys - cy, xs - cx)           # y para baixo: ângulo crescente = horário na tela
    b = ((ang + math.pi) / (2 * math.pi) * bins).astype(int) % bins
    loop = []
    for i in range(bins):
        sel = np.nonzero(b == i)[0]
        if len(sel):
            j = sel[np.argmax(dist[ys[sel], xs[sel]])]
            loop.append((float(xs[j]), float(ys[j])))
    arr = np.array(loop)
    return arr[::-1].copy() if sentido == "antihorario" else arr


def preview_image(bgr: np.ndarray, walk: np.ndarray, loop: np.ndarray, scale: int = 3) -> np.ndarray:
    vis = bgr.copy()
    vis[~walk] = (vis[~walk] * 0.4).astype(np.uint8)
    cv2.polylines(vis, [loop.astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 255), 1)
    return cv2.resize(vis, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
