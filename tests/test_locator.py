"""Teste sintético: gera o mapa ao vivo a partir do próprio mapa completo (janela que anda com o personagem,
seta fixa na âncora, ícones soltos) e mede em quantos pontos do corredor a posição é achada corretamente.

    python tests/test_locator.py            # zoom igual
    python tests/test_locator.py 2          # mapa ao vivo com zoom 2x
"""
import os, sys, time
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.locator import MapLocator
from core.mapmask import MapMask
from core.config import MAP_PATH, MAPMASK_PATH

LW, LH = 173, 312          # tamanho da janela ao vivo (mesmo do config.json)
ANCHOR = (86, 156)         # onde a seta fica na janela
rng = np.random.default_rng(1)


def make_live(full, x, y, zoom, pad=400):
    big = cv2.resize(full, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST) if zoom != 1 else full
    big = cv2.copyMakeBorder(big, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    cx, cy = int(round(x * zoom)) + pad, int(round(y * zoom)) + pad
    x0, y0 = cx - ANCHOR[0], cy - ANCHOR[1]
    live = big[y0:y0 + LH, x0:x0 + LW].copy()
    for _ in range(4):  # ícones de outros jogadores
        ix, iy = rng.integers(0, LW - 3), rng.integers(0, LH - 3)
        live[iy:iy + 3, ix:ix + 3] = (230, 46, 0) if rng.random() < .5 else (46, 0, 230)
    ax, ay = ANCHOR      # seta: cruz clara com borda escura
    live[ay - 5:ay + 6, ax - 5:ax + 6] = (0, 20, 0)
    live[ay - 3:ay + 4, ax - 1:ax + 2] = (230, 245, 230)
    live[ay - 1:ay + 2, ax - 3:ax + 4] = (230, 245, 230)
    return live


def main():
    zoom = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    mm = MapMask.load(MAP_PATH, MAPMASK_PATH)
    full, walk = mm.bgr, mm.walkable()
    loc = MapLocator(full, 0.55)
    ys, xs = np.nonzero(walk)
    cy0, cx0 = int(np.median(ys)), int(np.median(xs))
    i = np.argmin(np.hypot(xs - cx0, ys - cy0)); cx0, cy0 = xs[i], ys[i]
    t0 = time.time()
    ok = loc.calibrate(make_live(full, cx0, cy0, zoom), ANCHOR)
    print(f"calibração: ok={ok} escala={loc.scale} (esperado {1/zoom:.3f}) "
          f"score={loc.cal_score:.2f} margem={loc.cal_margin:.2f} ({time.time()-t0:.1f}s)")
    if not ok:
        return
    pts = [(x, y) for y, x in zip(ys[::2], xs[::2])]
    err_stateless, fails, edge_fails = [], 0, 0
    t0 = time.time()
    for (x, y) in pts:
        loc._last_tl = None
        p = loc.locate(make_live(full, x, y, zoom), ANCHOR)
        if p is None or np.hypot(p[0] - x, p[1] - y) > 3:
            fails += 1
        else:
            err_stateless.append(np.hypot(p[0] - x, p[1] - y))
    dt = (time.time() - t0) / len(pts)
    print(f"SEM histórico (pior caso): {len(pts)-fails}/{len(pts)} pontos certos ({100*(len(pts)-fails)/len(pts):.1f}%), "
          f"erro médio {np.mean(err_stateless):.2f}px, {dt*1000:.0f} ms por quadro")
    # rastreio ao longo de todo o corredor, na ordem dos pixels do loop
    from core import mapping
    loop = mapping.build_loop(walk, "horario")
    path = []
    for a, b in zip(loop, np.roll(loop, -1, axis=0)):
        n = int(max(abs(b[0]-a[0]), abs(b[1]-a[1]))) + 1
        for t in np.linspace(0, 1, n, endpoint=False):
            path.append((a[0] + (b[0]-a[0]) * t, a[1] + (b[1]-a[1]) * t))
    loc._last_tl = None
    fails = 0
    for (x, y) in path:
        p = loc.locate(make_live(full, x, y, zoom), ANCHOR)
        if p is None or np.hypot(p[0] - x, p[1] - y) > 3:
            fails += 1
    print(f"COM rastreio ao longo da rota: {len(path)-fails}/{len(path)} quadros certos ({100*(len(path)-fails)/len(path):.1f}%)")


if __name__ == "__main__":
    main()
