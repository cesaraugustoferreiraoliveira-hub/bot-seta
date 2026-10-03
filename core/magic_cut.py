"""Recorte mágico: isola uma sprite dentro de uma caixa que o usuário desenhou.

Fluxo:
  1. O usuário desenha uma caixa que CONTÉM a sprite (com um pouco de fundo em volta).
  2. O usuário clica em cima da sprite (pontos "é sprite") e, se precisar, no fundo ("não é sprite").
  3. `isolate_sprite` devolve a máscara; `crop_rgba` devolve a sprite recortada com transparência.

Técnica: paleta do fundo (borda da caixa) -> candidatos por diferença de cor -> GrabCut refina
usando as marcações do usuário -> mantém só o(s) pedaço(s) ligado(s) às marcações -> preenche buracos.
Não depende de GUI nem de tela: recebe e devolve arrays numpy.
"""
from __future__ import annotations
import os

import cv2
import numpy as np


def _lab(img_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)


def _ring_mask(h: int, w: int) -> np.ndarray:
    rw = max(2, int(round(min(h, w) * 0.08)))
    m = np.zeros((h, w), bool)
    m[:rw, :] = m[-rw:, :] = m[:, :rw] = m[:, -rw:] = True
    return m


def _disc(mask: np.ndarray, pt, r: int, value=True):
    cv2.circle(mask.view(np.uint8), (int(round(pt[0])), int(round(pt[1]))), r, 1, -1)


def _background_distance(bgr: np.ndarray, ring: np.ndarray, bg_points) -> np.ndarray:
    """Distância (Lab) de cada pixel à paleta dominante do fundo."""
    lab = _lab(bgr)
    samples = lab[ring]
    for p in bg_points:
        x, y = int(round(p[0])), int(round(p[1]))
        patch = lab[max(0, y - 1):y + 2, max(0, x - 1):x + 2].reshape(-1, 3)
        samples = np.vstack([samples, np.repeat(patch, 6, axis=0)])
    k = int(min(5, max(1, len(np.unique(samples.round(), axis=0)))))
    if k > 1 and len(samples) >= k:
        crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 15, 1.0)
        _, _, centers = cv2.kmeans(samples, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    else:
        centers = samples.mean(axis=0, keepdims=True)
    flat = lab.reshape(-1, 3)
    d = np.min(np.linalg.norm(flat[:, None, :] - centers[None, :, :], axis=2), axis=1)
    return d.reshape(bgr.shape[:2])


def _keep_seeded(fg: np.ndarray, fg_points) -> np.ndarray:
    """Mantém só os pedaços conectados (com folga de 2 px) às marcações de sprite."""
    near = cv2.dilate(fg.astype(np.uint8), np.ones((5, 5), np.uint8))
    n, labels = cv2.connectedComponents(near, connectivity=8)
    keep = set()
    h, w = fg.shape
    for p in fg_points:
        x, y = int(round(p[0])), int(round(p[1]))
        x, y = min(max(x, 0), w - 1), min(max(y, 0), h - 1)
        if labels[y, x] > 0:
            keep.add(int(labels[y, x]))
    if not keep:
        return fg
    return fg & np.isin(labels, list(keep))


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    pad = cv2.copyMakeBorder(m, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    ff = pad.copy()
    cv2.floodFill(ff, None, (0, 0), 2)
    holes = (ff[1:-1, 1:-1] == 0)
    return (m.astype(bool) | holes)


def isolate_sprite(bgr: np.ndarray, fg_points, bg_points=(), iters: int = 5):
    """Retorna máscara booleana HxW da sprite, ou None se não houver marcação de sprite.

    fg_points / bg_points: listas de (x, y) em coordenadas da imagem recebida.
    """
    h, w = bgr.shape[:2]
    if h < 6 or w < 6 or not fg_points:
        return None

    ring = _ring_mask(h, w)
    dist = _background_distance(bgr, ring, bg_points)
    # limiar adaptativo: o quanto o próprio fundo varia + folga
    ring_d = dist[ring]
    thr = max(14.0, float(np.percentile(ring_d, 98)) * 1.25)
    candidate = dist > thr

    gc = np.where(candidate, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
    gc[ring & ~candidate] = cv2.GC_BGD
    fg_seed = np.zeros((h, w), np.uint8)
    bg_seed = np.zeros((h, w), np.uint8)
    for p in fg_points:
        cv2.circle(fg_seed, (int(round(p[0])), int(round(p[1]))), 1, 1, -1)
    for p in bg_points:
        cv2.circle(bg_seed, (int(round(p[0])), int(round(p[1]))), 1, 1, -1)
    gc[bg_seed.astype(bool)] = cv2.GC_BGD
    gc[fg_seed.astype(bool)] = cv2.GC_FGD

    fg_like = np.isin(gc, (cv2.GC_FGD, cv2.GC_PR_FGD)).sum()
    bg_like = np.isin(gc, (cv2.GC_BGD, cv2.GC_PR_BGD)).sum()
    result = candidate.copy()
    if fg_like >= 20 and bg_like >= 20:
        try:
            bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
            cv2.grabCut(bgr, gc, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
            result = np.isin(gc, (cv2.GC_FGD, cv2.GC_PR_FGD))
        except cv2.error:
            result = candidate.copy()

    result = result | fg_seed.astype(bool)
    result &= ~bg_seed.astype(bool)
    result = cv2.morphologyEx(result.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)).astype(bool)
    result = _keep_seeded(result, fg_points)
    result = _fill_holes(result)
    if result.sum() < 4:
        return None
    return result


def bbox_of(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def crop_bgra(bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Recorta na caixa justa da sprite; fora da máscara fica transparente."""
    x0, y0, x1, y1 = bbox_of(mask)
    out = np.dstack([bgr, (mask * 255).astype(np.uint8)])
    return out[y0:y1, x0:x1].copy()


def save_template(bgra: np.ndarray, path) -> None:
    cv2.imwrite(str(path), bgra)


def load_template(path):
    """Retorna (bgr, mask_uint8) ou None. Sem canal alfa, a máscara cobre a imagem toda."""
    if not os.path.isfile(str(path)):       # sprite ainda não escolhida: sem o aviso do OpenCV no console
        return None
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 3 and img.shape[2] == 4:
        return img[..., :3].copy(), (img[..., 3] > 127).astype(np.uint8) * 255
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img, np.full(img.shape[:2], 255, np.uint8)
