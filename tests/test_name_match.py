"""Teste sintético da busca do nome do pokémon (muda de cor com a vida).

Gera quadros com fundo de mapa, o nome nas 3 cores (verde / amarelo / vermelho), outros nomes parecidos como
distratores, barras de vida e ruído. A sprite modelo é recortada com UMA cor (verde) e deve achar as três.

    python tests/test_name_match.py            # fonte bitmap sem suavização, com contorno preto
    python tests/test_name_match.py aa         # fonte suavizada (anti-aliasing)
    python tests/test_name_match.py semcontorno
"""
import os, sys, time
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.name_match import NameTemplate, find_name, health_color_name
from core.magic_cut import crop_bgra, bbox_of

MODE = sys.argv[1] if len(sys.argv) > 1 else "bitmap"
COLORS = {"verde": (0, 230, 0), "amarela": (230, 230, 0), "vermelha": (230, 0, 0)}   # RGB
rng = np.random.default_rng(7)
BG = cv2.imread(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mapa_completo.png"))
BG = cv2.resize(BG, None, fx=7, fy=7, interpolation=cv2.INTER_NEAREST)
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
CRISP = MODE != "aa"       # True = sem suavização (como fontes bitmap de jogos); "aa" = com anti-aliasing


def text_layer(text, rgb, outline=True):
    """Camada RGBA com o texto (e contorno preto de 1 px) — como o jogo desenha."""
    l, t, r, b = FONT.getbbox(text)
    w, h = r - l + 6, b - t + 6
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.fontmode = "1" if CRISP else "L"
    if outline and MODE != "semcontorno":
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx or dy:
                    d.text((3 - l + dx, 3 - t + dy), text, font=FONT, fill=(0, 0, 0, 255))
    d.text((3 - l, 3 - t), text, font=FONT, fill=rgb + (255,))
    return np.array(im)


def paste(bg, layer, x, y):
    h, w = layer.shape[:2]
    a = layer[..., 3:4].astype(np.float32) / 255
    roi = bg[y:y + h, x:x + w].astype(np.float32)
    bg[y:y + h, x:x + w] = (layer[..., 2::-1][:, :, ::-1].astype(np.float32) * 0 + layer[..., [2, 1, 0]] * a + roi * (1 - a)).astype(np.uint8)


def make_frame(name, color, W=900, H=420, present=True, distract=("Pikachu", "Charmander", "Gengar", "Magikarp"), bgx=None):
    x0 = int(rng.integers(0, BG.shape[1] - W)); y0 = int(rng.integers(0, BG.shape[0] - H))
    f = BG[y0:y0 + H, x0:x0 + W].copy()
    f = np.clip(f.astype(np.int16) + rng.integers(-6, 7, f.shape), 0, 255).astype(np.uint8)
    pos = None
    for d in distract:
        lay = text_layer(d, COLORS[rng.choice(list(COLORS))] if rng.random() < .7 else (255, 255, 255))
        h, w = lay.shape[:2]
        paste(f, lay, int(rng.integers(0, W - w)), int(rng.integers(0, H - h)))
        bx, by = int(rng.integers(0, W - 60)), int(rng.integers(0, H - 8))      # barra de vida lisa
        f[by:by + 5, bx:bx + 50] = COLORS[rng.choice(list(COLORS))][::-1]
        f[by - 1, bx:bx + 50] = 0; f[by + 5, bx:bx + 50] = 0
    if present:
        lay = text_layer(name, COLORS[color])
        h, w = lay.shape[:2]
        x, y = int(rng.integers(0, W - w)), int(rng.integers(0, H - h))
        paste(f, lay, x, y)
        pos = (x, y, w, h)
    return f, pos


def build_template(name):
    """Recorta a sprite do nome verde como a varinha mágica faria: máscara = tudo que é do texto."""
    lay = text_layer(name, COLORS["verde"])
    a = lay[..., 3] > 127 if CRISP else lay[..., 3] > 40
    bgr = lay[..., [2, 1, 0]].copy()
    global OFF
    x0, y0, _, _ = bbox_of(a); OFF = (x0, y0)      # onde a sprite recortada começa dentro da camada
    return crop_bgra(bgr, a)


def main():
    name = "Charizard"
    bgra = build_template(name)
    t = NameTemplate(bgra[..., :3], bgra[..., 3])
    print(f"modo={MODE} ({"sem suavização" if CRISP else "com anti-aliasing"}): sprite {t.text_w}x{t.text_h}px, preenchimento {int(t.nF)}px, contorno {int(t.nO)}px, anel+contorno {int(t.nC)}px")
    ok = {c: 0 for c in COLORS}; scores = {c: [] for c in COLORS}; fp_scores = []; n = 25; tt = []
    for c in COLORS:
        for _ in range(n):
            f, pos = make_frame(name, c)
            t0 = time.time(); m = find_name(f, t, 0.7); tt.append(time.time() - t0)
            hit = m is not None and abs(m.x - (pos[0] + OFF[0])) <= 1 and abs(m.y - (pos[1] + OFF[1])) <= 1
            ok[c] += bool(hit and m.found); scores[c].append(m.score)
    for _ in range(60):   # sem o nome na tela (só distratores)
        f, _p = make_frame(name, "verde", present=False)
        fp_scores.append(find_name(f, t, 0.7).score)
    for c in COLORS:
        print(f"  {c:9s} achou {ok[c]}/{n}  nota média {np.mean(scores[c]):.2f} (mín {np.min(scores[c]):.2f})")
    print(f"  SEM o nome na tela: nota máxima {np.max(fp_scores):.2f}, média {np.mean(fp_scores):.2f}  (limiar 0.70)")
    print(f"  tempo por busca em 900x420: {np.mean(tt)*1000:.0f} ms")


if __name__ == "__main__":
    main()
