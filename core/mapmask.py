"""Mapa completo + o que o usuário marcou como corredor ou obstáculo.

Arquivos:
  mapa_completo.png   captura do mapa inteiro
  mapa_mascara.png    marcações (verde puro = corredor, vermelho puro = obstáculo, preto = sem marcar)
Sem GUI: recebe e devolve arrays numpy, então dá para testar sem tela.
"""
from __future__ import annotations
import os
from collections import deque

import cv2
import numpy as np

UNSET, CORRIDOR, OBSTACLE = 0, 1, 2
_TINT = {CORRIDOR: (0, 200, 0), OBSTACLE: (0, 0, 230)}  # BGR


class MapMask:
    def __init__(self, bgr: np.ndarray, state: np.ndarray | None = None):
        self.bgr = bgr
        self.state = np.zeros(bgr.shape[:2], np.uint8) if state is None else state.astype(np.uint8).copy()
        self._undo: deque[np.ndarray] = deque(maxlen=50)

    # ---------------------------------------------------------------- arquivos
    @classmethod
    def load(cls, map_path, mask_path) -> "MapMask | None":
        img = cv2.imread(str(map_path))
        if img is None:
            return None
        state = None
        if os.path.exists(mask_path):
            m = cv2.imread(str(mask_path))
            if m is not None and m.shape[:2] == img.shape[:2]:
                b, g, r = m[..., 0], m[..., 1], m[..., 2]
                state = np.zeros(img.shape[:2], np.uint8)
                state[(g > 200) & (b < 60) & (r < 60)] = CORRIDOR
                state[(r > 200) & (b < 60) & (g < 60)] = OBSTACLE
        return cls(img, state)

    def save_mask(self, path) -> None:
        out = np.zeros((*self.state.shape, 3), np.uint8)
        out[self.state == CORRIDOR] = (0, 255, 0)
        out[self.state == OBSTACLE] = (0, 0, 255)
        cv2.imwrite(str(path), out)

    # ---------------------------------------------------------------- edição
    def push_undo(self) -> None:
        self._undo.append(self.state.copy())

    def undo(self) -> bool:
        if not self._undo:
            return False
        self.state = self._undo.pop()
        return True

    def apply_color(self, x: int, y: int, value: int, tol: int, global_: bool = True) -> int:
        """Varinha: marca com `value` os pixels de cor parecida com a do pixel (x, y).
        global_=True: em todo o mapa; False: só a região ligada ao ponto clicado."""
        h, w = self.state.shape
        x, y = min(max(int(x), 0), w - 1), min(max(int(y), 0), h - 1)
        img = self.bgr.astype(np.int16)
        sel = np.abs(img - img[y, x]).max(axis=2) <= tol
        if not global_:
            _, lab = cv2.connectedComponents(sel.astype(np.uint8), connectivity=4)
            sel = lab == lab[y, x]
        self.state[sel] = value
        return int(sel.sum())

    def paint(self, x: int, y: int, radius: int, value: int) -> None:
        cv2.circle(self.state, (int(x), int(y)), max(0, int(radius)), int(value), -1)

    def clear(self) -> None:
        self.state[:] = UNSET

    def unset_to_obstacle(self) -> None:
        self.state[self.state == UNSET] = OBSTACLE

    # ---------------------------------------------------------------- consulta
    def walkable(self) -> np.ndarray:
        """Só o que o usuário marcou como corredor é caminhável (sem marcar = obstáculo)."""
        return self.state == CORRIDOR

    def stats(self) -> dict:
        n = float(self.state.size)
        return {"corredor": (self.state == CORRIDOR).sum() / n, "obstaculo": (self.state == OBSTACLE).sum() / n,
                "sem_marcar": (self.state == UNSET).sum() / n}

    def overlay(self, loop=None, scale: int = 1) -> np.ndarray:
        """Mapa tingido (verde = corredor, vermelho = obstáculo) e, se houver, a rota com a direção."""
        vis = self.bgr.astype(np.float32)
        for val, color in _TINT.items():
            m = self.state == val
            vis[m] = vis[m] * 0.55 + np.array(color, np.float32) * 0.45
        vis = vis.astype(np.uint8)
        if scale != 1:
            vis = cv2.resize(vis, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        if loop is not None and len(loop) > 2:
            pts = (np.asarray(loop) * scale + scale / 2).astype(np.int32)
            cv2.polylines(vis, [pts.reshape(-1, 1, 2)], True, (0, 255, 255), max(1, scale // 2))
            p0, p1 = tuple(int(v) for v in pts[0]), tuple(int(v) for v in pts[min(len(pts) - 1, max(2, len(pts) // 12))])
            cv2.arrowedLine(vis, p0, p1, (255, 255, 255), max(1, scale // 2), tipLength=0.6)
        return vis


def live_to_full(pos, live_size, full_size):
    """Posição (x, y) da seta no mapa ao vivo -> posição no mapa completo (mesma área, escalas diferentes)."""
    return pos[0] * full_size[0] / live_size[0], pos[1] * full_size[1] / live_size[1]
