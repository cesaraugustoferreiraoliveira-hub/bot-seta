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


def _ray(walk: np.ndarray, x: float, y: float, dx: float, dy: float, max_px: int = 200, gap: int = 1) -> float:
    """Distância (px) de (x, y) até o primeiro obstáculo andando na direção (dx, dy).
    Buracos de até `gap` px dentro do corredor não contam como parede."""
    h, w = walk.shape
    last_ok, miss = 0.0, 0
    for i in range(1, max_px):
        px, py = int(round(x + dx * i)), int(round(y + dy * i))
        if not (0 <= px < w and 0 <= py < h):
            break
        if walk[py, px]:
            last_ok, miss = float(i), 0
        else:
            miss += 1
            if miss > gap:
                break
    return last_ok


def _smooth_closed(pts: np.ndarray, k: int) -> np.ndarray:
    """Média móvel circular (a rota é um laço fechado)."""
    if k <= 1 or len(pts) < 2 * k:
        return pts
    ker = np.ones(k) / k
    pad = k // 2
    out = np.empty_like(pts)
    for c in range(2):
        v = np.concatenate([pts[-pad:, c], pts[:, c], pts[:pad, c]])
        out[:, c] = np.convolve(v, ker, mode="valid")[:len(pts)]
    return out


def widen_turns(walk: np.ndarray, loop: np.ndarray, abertura: float = 0.75, suavizar: int = 15, folga: float = 3.0) -> np.ndarray:
    """Afasta a rota da parede INTERNA da curva (a que fica do lado da ilha).

    `loop` precisa estar em sentido horário. Em cada ponto mede, na perpendicular à direção, a distância até
    a parede interna (d_in) e até a externa (d_out) e recoloca o ponto a `abertura` da largura contando a
    partir da parede interna: 0.5 = meio do corredor (comportamento antigo), 0.75 = 75% do caminho rumo à
    parede externa. A seta faz curvas abertas e o pokémon (que vem atrás e corta caminho) não encosta."""
    n = len(loop)
    if n < 8 or abs(abertura - 0.5) < 1e-6:
        return loop
    base = _smooth_closed(_smooth_closed(loop.astype(np.float64), max(3, suavizar)), max(3, suavizar // 2))
    new = base.copy()
    # mede as paredes numa versão "alisada" do corredor (buracos pequenos fechados, farpas removidas)
    wm = walk.astype(np.uint8)
    wm = cv2.morphologyEx(wm, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    wm = cv2.morphologyEx(wm, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))).astype(bool)
    wm = wm & walk | (wm & ~walk & False) | (wm & walk)      # nunca mais permissiva que o corredor real
    if not wm.any():
        wm = walk
    for i in range(n):
        t = base[(i + 2) % n] - base[(i - 2) % n]
        L = float(np.hypot(*t))
        if L < 1e-6:
            continue
        tx, ty = t / L
        nx, ny = -ty, tx                     # lado interno em sentido horário (y para baixo)
        d_in = _ray(wm, base[i, 0], base[i, 1], nx, ny, gap=0)
        d_out = _ray(wm, base[i, 0], base[i, 1], -nx, -ny, gap=0)
        width = d_in + d_out
        if width < 4:                        # corredor estreito: não há o que abrir
            continue
        off = (abertura * width) - d_in      # quanto andar rumo à parede externa (negativo = rumo à interna)
        # nunca chega mais perto que `folga` px de nenhuma das duas paredes (a seta também não pode bater)
        off = min(off, max(0.0, d_out - folga))
        off = max(off, -max(0.0, d_in - folga))
        new[i] = base[i] + np.array((-nx, -ny)) * off
    new = _smooth_closed(_smooth_closed(new, max(3, suavizar)), max(3, suavizar // 2))
    # segurança: ponto que caiu em obstáculo (ou colado nele) recua aos poucos rumo ao ponto original,
    # em vez de pular de uma vez (pular cria zigue-zague na rota)
    h, w = walk.shape
    dist = cv2.distanceTransform(walk.astype(np.uint8), cv2.DIST_L2, 3)

    def livre(p):
        x, y = int(round(p[0])), int(round(p[1]))
        return 0 <= x < w and 0 <= y < h and walk[y, x] and dist[y, x] >= 1.5

    ys_l, xs_l = np.nonzero(walk & (dist >= 1.5))

    def mais_proximo_livre(p):
        x0, y0 = int(round(p[0])), int(round(p[1]))
        m = (np.abs(xs_l - x0) <= 6) & (np.abs(ys_l - y0) <= 6)
        if not m.any():
            return None
        k = np.argmin((xs_l[m] - p[0]) ** 2 + (ys_l[m] - p[1]) ** 2)
        return np.array([float(xs_l[m][k]), float(ys_l[m][k])])

    for _ in range(2):                       # corrige, alisa e confere de novo
        for i in range(n):
            if not livre(new[i]):
                q = mais_proximo_livre(new[i])
                new[i] = q if q is not None else base[i]
        new = _smooth_closed(new, max(3, suavizar // 2))
    for i in range(n):
        if not livre(new[i]):
            q = mais_proximo_livre(new[i])
            new[i] = q if q is not None else base[i]
    return new


def build_loop(walk: np.ndarray, sentido: str = "horario", bins: int = 180, abertura: float = 0.75) -> np.ndarray:
    """Rota do loop: para cada ângulo ao redor da ilha escolhe o pixel caminhável mais afastado das paredes
    (distance transform) e depois abre as curvas (`abertura`, ver widen_turns).
    Retorna array Nx2 (x, y) na ordem do sentido."""
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
    arr = widen_turns(walk, np.array(loop), abertura)     # sempre calculada no sentido horário
    return arr[::-1].copy() if sentido == "antihorario" else arr


def preview_image(bgr: np.ndarray, walk: np.ndarray, loop: np.ndarray, scale: int = 3) -> np.ndarray:
    vis = bgr.copy()
    vis[~walk] = (vis[~walk] * 0.4).astype(np.uint8)
    cv2.polylines(vis, [loop.astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 255), 1)
    return cv2.resize(vis, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
