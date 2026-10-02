"""Localiza a seta no mapa completo a partir do mapa ao vivo (minimapa que anda com o personagem).

Ideia: a seta fica SEMPRE no mesmo ponto da janela ao vivo (a "âncora"); o que muda é o terreno ao redor.
Então não comparamos a janela inteira (que pode ser maior que o mapa completo e vazar para fora dele nas
bordas, criando pontos cegos): comparamos só um RECORTE pequeno ao redor da âncora.

  * o mapa completo ganha uma moldura (PAD px) replicando as bordas -> o recorte nunca "sai" do mapa;
  * as duas imagens viram mapas de CLASSES de cor (verde, verde escuro, cinza, marrom, preto...), então
    antialias/brilho não atrapalham; cores fora da paleta (seta, ícones, jogadores) são ignoradas;
  * a pontuação é a fração de pixels do recorte cuja classe bate com a do mapa completo (0..1);
  * perto da última posição aceita pontuação menor; se mesmo assim não casar, o odômetro (deslocamento do
    terreno entre dois quadros) mantém a posição por alguns instantes em vez de ficar cego.
Sem GUI: recebe e devolve arrays numpy.
"""
from __future__ import annotations
import cv2
import numpy as np

PAD = 200                 # moldura (px do mapa completo) ao redor do mapa completo
PALETA_MIN_PX = 100       # cor do mapa completo só vira classe se tiver pelo menos tantos pixels
PALETA_TOL = 40           # distância máxima (por canal) até a cor da classe; além disso o pixel é ignorado
RASTREIO_RAIO = 40        # px (do mapa completo) ao redor da última posição
RASTREIO_FATOR = 0.75     # perto da última posição o mínimo é min_score * isto
BUSCA_MARGEM = 0.03       # na busca global o melhor pico precisa ganhar do segundo por isso
CAL_MIN_SCORE = 0.60
CAL_MIN_MARGEM = 0.08


def build_palette(full_bgr: np.ndarray) -> np.ndarray:
    px = full_bgr.reshape(-1, 3)
    cols, cnt = np.unique(px, axis=0, return_counts=True)
    keep = cols[cnt >= PALETA_MIN_PX].astype(np.int16)
    if len(keep) < 2:
        keep = cols[np.argsort(-cnt)[:4]].astype(np.int16)
    return keep


def classify(img_bgr: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """Classe (0..K-1) de cada pixel pela cor mais próxima da paleta; -1 se nenhuma estiver perto."""
    d = np.abs(img_bgr.astype(np.int16)[:, :, None, :] - palette[None, None, :, :]).max(axis=3)
    idx = d.argmin(axis=2).astype(np.int8)
    idx[d.min(axis=2) > PALETA_TOL] = -1
    return idx


class MapLocator:
    def __init__(self, full_bgr: np.ndarray, min_score: float = 0.55, scale: float | None = None,
                 patch_r: int = 48):
        self.full = full_bgr
        self.palette = build_palette(full_bgr)
        self.K = len(self.palette)
        lab = classify(full_bgr, self.palette)
        self.H, self.W = lab.shape
        lab_p = cv2.copyMakeBorder(lab, PAD, PAD, PAD, PAD, cv2.BORDER_REPLICATE)
        self._onehot = [(lab_p == k).astype(np.float32) for k in range(self.K)]
        self.min_score = float(min_score)
        self.scale = scale                  # mapa completo / mapa ao vivo (None = descobrir na calibração)
        self.patch_r = int(patch_r)         # raio do recorte ao redor da seta (px do mapa ao vivo)
        self.last_score = 0.0
        self.last_mode = ""
        self.cal_score = 0.0
        self.cal_margin = 0.0
        self._last_tl: tuple[int, int] | None = None     # canto do recorte (coords da moldura) na última localização
        self._prev_patch: np.ndarray | None = None       # para o odômetro

    # ---------------------------------------------------------------- recorte
    def _patch(self, live_bgr: np.ndarray, anchor, r: int | None = None):
        """Recorte (classes) ao redor da âncora e a posição da âncora dentro do recorte."""
        r = self.patch_r if r is None else int(r)
        h, w = live_bgr.shape[:2]
        ax, ay = int(round(anchor[0])), int(round(anchor[1]))
        x0, y0, x1, y1 = max(0, ax - r), max(0, ay - r), min(w, ax + r + 1), min(h, ay + r + 1)
        lab = classify(live_bgr[y0:y1, x0:x1], self.palette)
        # a seta (e a borda escura dela) não existe no mapa completo: ignora um quadrado ao redor
        ex = 9
        lab[max(0, ay - y0 - ex):ay - y0 + ex + 1, max(0, ax - x0 - ex):ax - x0 + ex + 1] = -1
        return lab, (ax - x0, ay - y0)

    @staticmethod
    def _resize_labels(lab: np.ndarray, s: float) -> np.ndarray:
        if abs(s - 1.0) < 1e-3:
            return lab
        size = (max(1, int(round(lab.shape[1] * s))), max(1, int(round(lab.shape[0] * s))))
        return cv2.resize(lab, size, interpolation=cv2.INTER_NEAREST)

    # ---------------------------------------------------------------- comparação
    def _score_map(self, lab: np.ndarray, roi):
        x0, y0, x1, y1 = roi
        th, tw = lab.shape
        if th < 4 or tw < 4 or th > y1 - y0 or tw > x1 - x0:
            return None, 0
        n = int((lab >= 0).sum())
        if n < 0.25 * lab.size:         # recorte quase todo ignorado: não serve para comparar
            return None, 0
        acc = None
        for k in range(self.K):
            t = (lab == k).astype(np.float32)
            if not t.any():
                continue
            res = cv2.matchTemplate(self._onehot[k][y0:y1, x0:x1], t, cv2.TM_CCORR)
            acc = res if acc is None else acc + res
        return (None, 0) if acc is None else (acc / n, n)

    def _peak(self, lab: np.ndarray, roi):
        sm, n = self._score_map(lab, roi)
        if sm is None:
            return None
        _, best, _, (bx, by) = cv2.minMaxLoc(sm)
        r = sm.copy()
        ex, ey = max(2, lab.shape[1] // 3), max(2, lab.shape[0] // 3)
        r[max(0, by - ey):by + ey + 1, max(0, bx - ex):bx + ex + 1] = -1.0
        return float(best), (bx + roi[0], by + roi[1]), float(r.max())

    def _full_roi(self):
        return (0, 0, self.W + 2 * PAD, self.H + 2 * PAD)

    # ---------------------------------------------------------------- calibração
    def calibrate(self, live_bgr: np.ndarray, anchor) -> bool:
        """Descobre a escala (mapa completo / mapa ao vivo). True se achou uma correspondência confiável."""
        lab, _ = self._patch(live_bgr, anchor)
        roi = self._full_roi()

        def objective(s):
            r = self._peak(self._resize_labels(lab, s), roi)
            return None if r is None else (r[0] + (r[0] - max(r[2], 0.0)), r[0], r[0] - r[2])

        def sweep(lo, hi, step):
            out, s = [], lo
            while s <= hi:
                o = objective(s)
                if o is not None:
                    out.append((o, s))
                s *= step
            return out

        res = sweep(0.4, 2.6, 1.05) + ([(o, 1.0)] if (o := objective(1.0)) else [])
        if not res:
            return False
        s0 = max(res, key=lambda t: t[0][0])[1]
        res = sweep(s0 / 1.06, s0 * 1.06, 1.012)
        s1 = max(res, key=lambda t: t[0][0])[1]
        res = sweep(s1 / 1.015, s1 * 1.015, 1.003)
        (_, best, margin), scale = max(res, key=lambda t: t[0][0])
        if abs(scale - 1.0) < 0.03:       # escalas quase 1 são 1 (zoom igual): evita reamostrar à toa
            o = objective(1.0)
            if o is not None and o[1] >= best - 0.02:
                (_, best, margin), scale = o, 1.0
        self.cal_score, self.cal_margin = best, margin
        if best < CAL_MIN_SCORE or margin < CAL_MIN_MARGEM:
            return False
        self.scale = scale
        self._last_tl = None
        return True

    # ---------------------------------------------------------------- localização
    def locate(self, live_bgr: np.ndarray, anchor):
        """Posição (x, y) da seta no mapa completo, ou None se não casar."""
        if self.scale is None:
            return None
        s = self.scale
        best_seen, found, mode, used_r = 0.0, None, "", self.patch_r
        for r in (self.patch_r, int(self.patch_r * 1.6)):      # se o recorte pequeno não casar, tenta um maior
            lab0, (px, py) = self._patch(live_bgr, anchor, r)
            lab = self._resize_labels(lab0, s)
            found, mode = None, ""
            if self._last_tl is not None:
                cx, cy = self._last_tl
                R = RASTREIO_RAIO
                roi = (max(0, cx - R), max(0, cy - R), min(self.W + 2 * PAD, cx + lab.shape[1] + R),
                       min(self.H + 2 * PAD, cy + lab.shape[0] + R))
                p = self._peak(lab, roi)
                if p is not None:
                    best_seen = max(best_seen, p[0])
                    if p[0] >= self.min_score * RASTREIO_FATOR:
                        found, mode = p, "rastreio"
            if found is None:
                p = self._peak(lab, self._full_roi())
                if p is not None:
                    best_seen = max(best_seen, p[0])
                    if p[0] >= self.min_score and p[0] - p[2] >= BUSCA_MARGEM:
                        found, mode = p, "busca"
            if found is not None:
                used_r = r
                break
        self.last_score = best_seen
        if found is None:
            return None      # mantém _last_tl: o rastreio continua tentando perto do último ponto bom
        self.last_mode = mode
        self._last_tl = found[1]
        return (found[1][0] - PAD + (px + 0.5) * s - 0.5, found[1][1] - PAD + (py + 0.5) * s - 0.5)

    # ---------------------------------------------------------------- odômetro
    def odometry(self, live_bgr: np.ndarray, anchor):
        """Quanto o personagem andou (px do mapa completo) desde a última chamada, pelo deslocamento do
        terreno no mapa ao vivo. Retorna (dx, dy) ou None se não deu para medir."""
        lab, _ = self._patch(live_bgr, anchor)
        img = np.where(lab >= 0, (lab.astype(np.float32) + 1) * 40.0, 0.0).astype(np.float32)
        prev, self._prev_patch = self._prev_patch, img
        if prev is None or prev.shape != img.shape or img.std() < 1.0:
            return None
        win = cv2.createHanningWindow((img.shape[1], img.shape[0]), cv2.CV_32F)
        (sx, sy), resp = cv2.phaseCorrelate(prev, img, win)
        if resp < 0.1:
            return None
        s = self.scale or 1.0
        return (-sx * s, -sy * s)       # o terreno anda no sentido oposto ao do personagem
