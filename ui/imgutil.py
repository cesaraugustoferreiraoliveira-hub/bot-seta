"""Helpers de imagem para a GUI (miniaturas, fundo xadrez para transparência)."""
from __future__ import annotations
import numpy as np
from PIL import Image, ImageTk


def to_photo(img: Image.Image, maxw: int, maxh: int, max_scale: float = 6) -> ImageTk.PhotoImage:
    w, h = img.size
    s = min(maxw / w, maxh / h, max_scale)
    return ImageTk.PhotoImage(img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.NEAREST))


def checkerboard(h: int, w: int, cell: int = 8) -> np.ndarray:
    yy, xx = np.indices((h, w))
    board = (((yy // cell) + (xx // cell)) % 2).astype(np.uint8)
    out = np.where(board[..., None] == 0, 205, 245).astype(np.uint8)
    return np.repeat(out, 3, axis=2)


def over_checkerboard(rgba: np.ndarray) -> Image.Image:
    """rgba: HxWx4 (RGBA). Compõe sobre um fundo xadrez para mostrar a transparência."""
    h, w = rgba.shape[:2]
    bg = checkerboard(h, w).astype(np.float32)
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    out = rgba[..., :3].astype(np.float32) * a + bg * (1 - a)
    return Image.fromarray(out.astype(np.uint8), "RGB")
