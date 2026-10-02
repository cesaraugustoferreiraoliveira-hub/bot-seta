"""Acha um texto/sprite pelo FORMATO, sem depender da cor de preenchimento.

Caso de uso: o nome do pokémon sobre a cabeça dele muda de cor com a vida (verde, amarelo, vermelho) mas
tem sempre a mesma forma. Comparar pixel a pixel (como `vision.sprite_scores`) falharia em 2 das 3 cores.

Como funciona: a sprite recortada (com transparência) é dividida em três grupos de pixels:
  F  preenchimento: os pixels coloridos das letras (todos da mesma cor num dado momento);
  O  contorno escuro (se o jogo desenha um): comparado com o próprio contorno da sprite, que não muda de cor;
  C  contraste: o próprio contorno escuro (ou, se não houver contorno, um anel de 2 px de fundo ao redor das letras).
Em cada posição da área de busca o bot mede, SEM olhar qual é a cor:
  * as letras são de uma cor só?   (desvio dos pixels de F em relação à média deles, rF)
  * essa cor se destaca do que está em volta?   (distância de C até a cor média de F, rC)
  * o contorno escuro está onde deveria?   (erro de O em relação à sprite)
Uma região lisa não passa (rC ~ 0), um fundo cheio de detalhes não passa (rF alto) e outro nome, de outro
formato, também não, porque seus pixels não caem todos nas letras do modelo.
Tudo é feito com correlações (cv2.matchTemplate), então a busca na área inteira é rápida.
Sem GUI: recebe e devolve arrays numpy.
"""
from __future__ import annotations
from dataclasses import dataclass

import cv2
import numpy as np

from .magic_cut import load_template

DARK_V = 90        # pixel da sprite com max(B,G,R) abaixo disso conta como contorno escuro
FILL_TOL = 24      # distância máxima (por canal) até a cor dominante para contar como preenchimento
RING = 2           # espessura (px) do anel de fundo usado no contraste
SEP_FULL = 100.0   # separação (rC - 2*rF) que vale nota 1.0
OUT_FULL = 90.0    # erro médio do contorno (por canal) que zera a nota do contorno
MIN_FILL_PX = 6


class NameTemplate:
    """Sprite do nome, já separada em preenchimento / contorno / anel."""

    def __init__(self, bgr: np.ndarray, mask: np.ndarray):
        m = mask > 0
        v = bgr.max(axis=2)
        dark = m & (v < DARK_V)
        bright = m & ~dark
        if int(bright.sum()) < MIN_FILL_PX:
            raise ValueError("A sprite quase não tem pixels coloridos (só contorno escuro). "
                             "Selecione de novo, clicando nas letras.")
        med = np.median(bgr[bright].reshape(-1, 3), axis=0)
        fill = bright & (np.abs(bgr.astype(np.float32) - med).max(axis=2) <= FILL_TOL)
        if int(fill.sum()) < MIN_FILL_PX:
            raise ValueError("Poucos pixels de preenchimento na sprite. Selecione de novo, clicando nas letras.")

        p = RING   # moldura: o anel precisa de espaço ao redor da sprite
        self.pad = p

        def padded(a):
            return cv2.copyMakeBorder(a.astype(np.uint8), p, p, p, p, cv2.BORDER_CONSTANT, value=0)

        m_p, fill_p, dark_p = padded(m), padded(fill), padded(dark)
        ring = cv2.dilate(m_p, np.ones((3, 3), np.uint8), iterations=RING) & (1 - m_p)
        # com contorno escuro, o contraste é medido contra ele (o fundo atrás das letras varia e pode ser parecido
        # com a cor do nome, ex.: nome verde sobre grama); sem contorno, contra o anel de fundo
        contrast = (dark_p if int(dark.sum()) >= 4 else ring).astype(np.float32)
        self.F = fill_p.astype(np.float32)
        self.C = contrast
        self.O = dark_p.astype(np.float32)
        self.nF, self.nC, self.nO = float(self.F.sum()), float(contrast.sum()), float(self.O.sum())
        self.h, self.w = m_p.shape                       # tamanho com moldura
        self.text_h, self.text_w = m.shape              # tamanho da sprite em si
        self.fill_bgr = tuple(int(x) for x in med)      # cor do preenchimento na hora da captura (só informativo)
        t = bgr.astype(np.float32) - 127.5
        t_p = cv2.copyMakeBorder(t, p, p, p, p, cv2.BORDER_CONSTANT, value=0)
        o3 = cv2.merge([self.O] * 3)
        self._o_t = t_p * o3
        self._o_energy = float((t_p * t_p * o3).sum())
        self._o_mask3 = o3

    @classmethod
    def from_file(cls, path) -> "NameTemplate | None":
        loaded = load_template(path)
        return None if loaded is None else cls(*loaded)


def _conv(x: np.ndarray, k: np.ndarray) -> np.ndarray:
    return cv2.matchTemplate(x, k, cv2.TM_CCORR)


def name_scores(frame_bgr: np.ndarray, t: NameTemplate):
    """Mapa de nota 0..1 (1 = formato idêntico, qualquer cor) e o mapa da cor média do preenchimento.
    O índice (y, x) do mapa é o canto superior esquerdo da sprite (sem a moldura) na área de busca."""
    p = t.pad
    img = cv2.copyMakeBorder(frame_bgr, p, p, p, p, cv2.BORDER_REPLICATE)
    if img.shape[0] < t.h or img.shape[1] < t.w:
        return None, None
    I = img.astype(np.float32) - 127.5          # tirar a média mantém as contas pequenas e precisas
    I2 = (I * I).sum(axis=2)
    ch = [np.ascontiguousarray(I[..., c]) for c in range(3)]

    mu = [_conv(c, t.F) / t.nF for c in ch]                       # cor média do preenchimento
    mu2 = sum(m * m for m in mu)
    r_fill = np.sqrt(np.maximum(_conv(I2, t.F) / t.nF - mu2, 0) / 3.0)          # quão "de uma cor só"
    sum_c = [_conv(c, t.C) for c in ch]
    d_c = _conv(I2, t.C) / t.nC - 2.0 * sum(m * s for m, s in zip(mu, sum_c)) / t.nC + mu2
    r_con = np.sqrt(np.maximum(d_c, 0) / 3.0)                                   # quão diferente é o entorno
    score = np.clip((r_con - 2.0 * r_fill) / SEP_FULL, 0.0, 1.0)

    if t.nO >= 4:                                                # contorno escuro igual ao da sprite
        cross = _conv(I, t._o_t)
        energy = _conv(I2, t.O)
        rmse = np.sqrt(np.maximum(t._o_energy - 2.0 * cross + energy, 0) / (t.nO * 3.0))
        score *= np.sqrt(np.clip(1.0 - rmse / OUT_FULL, 0.0, 1.0))
    return score.astype(np.float32), [m + 127.5 for m in mu]


@dataclass
class NameMatch:
    x: int
    y: int
    w: int
    h: int
    score: float
    second: float               # melhor nota fora da vizinhança do melhor (distinção)
    fill_bgr: tuple             # cor média das letras ENCONTRADAS (mostra a cor atual)
    found: bool


def find_name(frame_bgr: np.ndarray, t: NameTemplate, limiar: float = 0.70) -> NameMatch | None:
    """Melhor posição do nome na área (ou None se a área for menor que a sprite). `found` diz se passou do limiar."""
    sc, mu = name_scores(frame_bgr, t)
    if sc is None:
        return None
    _, best, _, (bx, by) = cv2.minMaxLoc(sc)
    r = sc.copy()
    ex, ey = max(2, t.text_w // 2), max(2, t.text_h)
    r[max(0, by - ey):by + ey + 1, max(0, bx - ex):bx + ex + 1] = 0.0
    color = tuple(int(round(float(m[by, bx]))) for m in mu)
    return NameMatch(int(bx), int(by), t.text_w, t.text_h, float(best), float(r.max()), color, best >= limiar)


def health_color_name(bgr) -> str:
    """Verde / amarela / vermelha (ou 'outra cor') pela matiz — só para mostrar ao usuário."""
    px = np.uint8([[[min(255, max(0, int(c))) for c in bgr]]])
    h, s, v = [int(x) for x in cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0, 0]]
    if s < 90 or v < 90:
        return "outra cor"
    if h < 12 or h > 165:
        return "vermelha"
    if h < 45:
        return "amarela"
    if h < 90:
        return "verde"
    return "outra cor"
