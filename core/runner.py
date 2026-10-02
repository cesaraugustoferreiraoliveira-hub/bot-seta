"""Orquestra tudo: captura -> visão -> motor -> teclas. Roda em thread própria."""
from __future__ import annotations
import threading
import time

from collections import Counter

from . import capture, keys as kb, mapping, vision
from .locator import MapLocator
from .mapmask import MapMask, live_to_full
from .botlog import (ALERTA, DECISAO, DETECCAO, ERRO, INFO, PAROU, RETOMOU, TRAVADO, BotLog)
from .config import ARROW_PATH, ConfigStore, MAP_PATH, MAPMASK_PATH, SPRITE_PATH
from .engine import MovementEngine, should_halt
from .shooter import ShooterController
from .revive import LifeSkillMonitor, ReviveController

SENTIDOS = {"horario": "horário", "antihorario": "anti-horário"}


def release_all() -> None:
    kb.release_all()


class BotRunner:
    def __init__(self, cfg: ConfigStore, on_status=None, log: BotLog | None = None):
        self.cfg = cfg
        self.on_status = on_status or (lambda msg: None)
        self.log = log
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: threading.Thread | None = None

    def _log(self, kind: str, msg: str) -> None:
        if self.log is not None:
            self.log.add(kind, msg)

    # ---------------------------------------------------------------- controle
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stopping(self) -> bool:
        return self.running and self._stop.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def toggle_pause(self) -> bool:
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        self._log(INFO, "Pausado" if self._pause.is_set() else "Retomado após pausa")
        return self._pause.is_set()

    # ---------------------------------------------------------------- preparo
    def _prepare(self):
        c = self.cfg
        live = c["regiao_mapa"]
        if not live:
            raise RuntimeError("Defina 'Mapa onde a seta está' na aba sprites/capture.")
        mm = MapMask.load(MAP_PATH, MAPMASK_PATH)
        if mm is None:
            raise RuntimeError("Capture o mapa completo na aba sprites/capture.")
        walk = mm.walkable()
        if not walk.any():
            raise RuntimeError("Marque o corredor do mapa completo (sprites/capture > Definir obstáculos e corredor).")
        loop = mapping.build_loop(walk, c["sentido"])
        engine = MovementEngine(walk, loop, lookahead=c["lookahead"], probe_px=c["sondagem_px"],
                                stuck_timeout_s=c["timeout_travado_s"], min_move_px=c["movimento_min_px"])
        tmpl = vision.SpriteTemplate.from_file(SPRITE_PATH) if c.get("regiao_sprite") else None
        arrow_tmpl = vision.SpriteTemplate.from_file(ARROW_PATH)
        if arrow_tmpl is not None:
            self._log(INFO, f"Seta localizada pela sprite da seta ({arrow_tmpl.w}x{arrow_tmpl.h}px, "
                            f"similaridade mínima {c['seta_limiar']})")
        else:
            self._log(ALERTA, f"Sem sprite da seta: usando os pixels brancos (brilho >= {c['limiar_seta']}). "
                              "Recomendo selecionar a sprite da seta em sprites/capture.")
        full_h, full_w = walk.shape
        st = mm.stats()
        self._log(INFO, f"Mapa completo {full_w}x{full_h}: {st['corredor']:.0%} corredor, {st['obstaculo']:.0%} obstáculo, "
                        f"{st['sem_marcar']:.0%} sem marcar (conta como obstáculo)")
        self._log(INFO, f"Rota calculada: {len(loop)} pontos, sentido {SENTIDOS.get(c['sentido'], c['sentido'])}")
        self._log(INFO, f"Tolerância de travamento: {c['timeout_travado_s'] * 1000:.0f} ms sem a seta sair do lugar")
        if tmpl is None:
            self._log(ALERTA, "Detecção de sprites DESLIGADA (sem área de busca ou sem sprite.png)")
        else:
            self._log(INFO, f"Detecção de sprites ligada: parar com >= {c['sprite_qtd']} "
                            f"(limiar {c['sprite_limiar']}, a cada {c['sprite_intervalo_s']}s)")
        fixed, saved = c.get("escala_mapa"), c.get("escala_mapa_calibrada")
        locator = MapLocator(mm.bgr, c["loc_limiar"], fixed or saved, int(c.get("loc_raio", 48)))
        self._used_saved = bool(saved and not fixed)
        how = (f"escala fixa {fixed}" if fixed else
               f"escala salva {saved} (recalibra sozinho se não casar)" if saved else "escala descoberta na calibração")
        self._log(INFO, f"Posição da seta: um recorte de {2 * int(c.get('loc_raio', 48)) + 1}px ao redor da seta será comparado com o mapa completo ({how})")
        return engine, tmpl, arrow_tmpl, locator, ((live[2], live[3]), (full_w, full_h))

    # ---------------------------------------------------------------- loop principal
    def _loop(self) -> None:
        self._log(INFO, "Bot ATIVADO")
        kb.set_method(self.cfg.get("metodo_teclas", "scancode"))
        kb.set_layout(self.cfg.get("teclas_movimento", "wasd"))
        self._log(INFO, f"Envio de teclas: {kb.method_name()} | movimento por {kb.layout_name()}")
        if kb.is_admin() is False:
            self._log(ALERTA, "O bot NÃO está como administrador: se o jogo roda como administrador, "
                              "o Windows ignora as teclas enviadas. Abra o terminal como administrador.")
        try:
            engine, tmpl, arrow_tmpl, locator, (live_size, full_size) = self._prepare()
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Não foi possível iniciar: {exc}")
            self.on_status(f"Erro ao preparar: {exc}")
            return
        c = self.cfg
        self.on_status("Rodando")
        revive = ReviveController(c, self._log)
        revive.arm()                  # guarda a foto de referência do revive (se a página revive estiver configurada)
        life = LifeSkillMonitor(c, self._log)
        shooter = ShooterController(c, self._log, sprite_w=tmpl.w if tmpl is not None else 0, revive=revive)
        hits, sprite_frame, fresh = [], None, False   # posições das sprites e o quadro da última detecção (o shooter usa)
        count, last_check = 0, 0.0
        last_count = None            # última contagem registrada
        halted, halt_t = False, 0.0  # parado por excesso de sprites?
        arrow_ok = True
        last_keys, last_stuck = None, 0
        last_focus = object()
        last_reg = None  # o Windows registrou a tecla enviada?
        cur_dir, calibrated = c["sentido"], False
        mode, loc_ok, reg_fail = "match", True, 0   # mode: "match" (compara mapas) ou "escala" (só proporção)
        used_saved, first_fix, cal_fail = self._used_saved, True, 0
        lost_t, last_nudge, last_lost_log, last_press = None, 0.0, 0.0, []
        anchor_votes, anchor, anchor_warned = Counter(), None, False   # a seta fica sempre no mesmo ponto do mapa ao vivo
        last_mpos, odo_frames, odo_max = None, 0, 25                   # odômetro: segura a posição quando o mapa não casa

        def calibrate_now(frame_, anchor_) -> bool:
            t0 = time.time()
            ok = locator.calibrate(frame_, anchor_)
            if ok:
                self._log(INFO, f"Calibração: mapa completo = {locator.scale:.3f} x mapa ao vivo "
                                f"(similaridade {locator.cal_score:.2f}, distinção {locator.cal_margin:.2f}, "
                                f"{time.time() - t0:.1f}s)")
                if locator.cal_score >= 0.5 and locator.cal_margin >= 0.25:
                    c.set("escala_mapa_calibrada", round(locator.scale, 4))  # reaproveita ao ligar de novo
            return ok
        try:
            while not self._stop.is_set():
                verbose = bool(c.get("log_detalhado"))
                engine.stuck_timeout_s = c["timeout_travado_s"]  # editável na aba engine, vale na hora
                if c["sentido"] != cur_dir:  # usuário trocou o sentido na aba engine: vale na hora
                    engine.loop = engine.loop[::-1].copy()
                    cur_dir, last_keys = c["sentido"], None
                    self._log(INFO, f"Sentido alterado para {SENTIDOS.get(cur_dir, cur_dir)}")
                if self._pause.is_set():
                    release_all()
                    engine.notify_paused()
                    time.sleep(0.1)
                    continue

                fresh = False
                if tmpl is not None and time.time() - last_check >= c["sprite_intervalo_s"]:
                    sprite_frame = capture.grab(c["regiao_sprite"])
                    hits, best = vision.find_sprites(sprite_frame, tmpl, c["sprite_limiar"])
                    count, fresh = len(hits), True
                    last_check = time.time()
                    if verbose or count != last_count:
                        self._log(DETECCAO, f"{count} sprite(s) na tela (melhor similaridade {best:.2f}; "
                                            f"para com >= {c['sprite_qtd']})")
                    last_count = count

                life.step(halted=should_halt(count, c["sprite_qtd"]))   # habilidades por % de vida (revive page)

                if should_halt(count, c["sprite_qtd"]):
                    if not halted:
                        halted, halt_t = True, time.time()
                        self._log(PAROU, f"Parou: {count} sprites na tela (limite {c['sprite_qtd']})")
                        shooter.begin()
                    release_all()
                    engine.notify_paused()
                    if fresh:   # bot parado: o shooter olha onde estão as sprites e atira na distante que parou de andar
                        shooter.step(sprite_frame, hits)
                    extra = shooter.status()
                    self.on_status(f"Parado: {count} sprites na tela" + (f" | {extra}" if extra else ""))
                    time.sleep(0.1)
                    last_check = 0.0  # reavalia já no próximo ciclo
                    continue
                if halted:
                    halted = False
                    last_keys = None
                    shooter.end()
                    self._log(RETOMOU, f"Voltou a andar: {count} sprites (limite {c['sprite_qtd']}), "
                                       f"ficou parado {time.time() - halt_t:.1f}s")

                frame = capture.grab(c["regiao_mapa"])
                pos, method, score = vision.locate_arrow(frame, arrow_tmpl, c["seta_limiar"], c["limiar_seta"])
                if pos is not None:   # a posição mais votada vira a âncora (a seta quase nunca sai dela)
                    anchor_votes[(int(round(pos[0])), int(round(pos[1])))] += 1
                    anchor = anchor_votes.most_common(1)[0][0]
                if pos is None and anchor is not None and sum(anchor_votes.values()) >= 3:
                    if not anchor_warned:
                        anchor_warned = True
                        self._log(INFO, f"Seta não reconhecida neste quadro: uso a posição fixa dela no mapa ao vivo {anchor}")
                    pos, method, score = (float(anchor[0]), float(anchor[1])), "ancora", 1.0
                if pos is None:
                    release_all()
                    if arrow_ok:
                        arrow_ok = False
                        detail = (f" (melhor similaridade {score:.2f}, mínimo {c['seta_limiar']})"
                                  if method == "sprite" else " (nenhum pixel branco)")
                        self._log(ALERTA, f"Seta não encontrada no mapa{detail}; teclas liberadas")
                    self.on_status("Seta não encontrada")
                    time.sleep(0.15)
                    continue
                if not arrow_ok:
                    arrow_ok = True
                    self._log(INFO, f"Seta reencontrada no mapa ao vivo em ({pos[0]:.0f}, {pos[1]:.0f})")

                focus = kb.foreground_title()
                if focus != last_focus:
                    last_focus = focus
                    if focus is not None:
                        self._log(ALERTA if focus.startswith("Bot da seta") else INFO,
                                  f"Janela que recebe as teclas: {focus!r}"
                                  + ("  <- é a janela do bot, não a do jogo!" if focus.startswith("Bot da seta") else ""))

                if mode == "match":
                    locator.min_score = c["loc_limiar"]
                    apos = anchor if anchor is not None else pos
                    odo = locator.odometry(frame, apos)   # deslocamento do terreno desde o quadro anterior
                    if locator.scale is None:  # primeira vez: descobre a escala entre os dois mapas
                        if calibrate_now(frame, apos):
                            cal_fail = 0
                        else:
                            cal_fail += 1
                            if cal_fail >= 3:
                                mode = "escala"
                                self._log(ALERTA, f"Não consegui casar o mapa ao vivo com o mapa completo (melhor similaridade "
                                                  f"{locator.cal_score:.2f}, distinção {locator.cal_margin:.2f}). Vou usar só a "
                                                  f"proporção entre os tamanhos ({live_size[0]}x{live_size[1]} -> "
                                                  f"{full_size[0]}x{full_size[1]}), que erra a posição se o mapa ao vivo for uma "
                                                  "janela que anda com o personagem. Confira se os dois mapas mostram a mesma "
                                                  "região e as mesmas cores.")
                            else:
                                self._log(ALERTA, f"Calibração falhou (tentativa {cal_fail}/3: similaridade "
                                                  f"{locator.cal_score:.2f}, distinção {locator.cal_margin:.2f}); tentando de novo")
                                release_all()
                                time.sleep(0.3)
                                continue
                if mode == "match":
                    mpos = locator.locate(frame, apos)
                    if mpos is None and first_fix and used_saved:   # a escala salva não serviu: recalibra uma vez
                        self._log(ALERTA, "A escala salva não casou com o mapa atual; recalibrando")
                        locator.scale, locator._last_tl = None, None
                        if calibrate_now(frame, apos):
                            mpos = locator.locate(frame, apos)
                    first_fix = False
                    if mpos is None and odo is not None and last_mpos is not None and odo_frames < odo_max:
                        odo_frames += 1     # sem casar com o mapa completo: segue pelo deslocamento medido do terreno
                        mpos = (last_mpos[0] + odo[0], last_mpos[1] + odo[1])
                        if odo_frames == 1:
                            self._log(ALERTA, f"Mapa não casou (similaridade {locator.last_score:.2f}): estimo a posição "
                                              f"pelo deslocamento do terreno por até {odo_max} quadros")
                        last_mpos = mpos
                    elif mpos is not None:
                        odo_frames = 0
                    if mpos is None:
                        now = time.time()
                        grace = float(c.get("perda_tolerancia_s", 2.0))
                        if lost_t is None:
                            lost_t = now
                        if loc_ok:
                            loc_ok, last_lost_log = False, now
                            try:   # guarda o quadro que não casou, para conferir o que o bot enxergou
                                import cv2
                                from .config import PROJECT_DIR
                                d = PROJECT_DIR / "logs"; d.mkdir(exist_ok=True)
                                if len(list(d.glob("falha_*.png"))) < 20:
                                    cv2.imwrite(str(d / f"falha_{time.strftime('%H%M%S')}.png"), frame)
                            except Exception:  # noqa: BLE001
                                pass
                            self._log(ALERTA, f"Perdi a posição: o mapa ao vivo não casa com o mapa completo (similaridade "
                                              f"{locator.last_score:.2f}, mínimo {c['loc_limiar']}). Sigo na mesma direção por "
                                              f"até {grace:.1f}s e depois ando 0,5s a cada 2s até casar de novo.")
                        elif now - last_lost_log >= 5:
                            last_lost_log = now
                            self._log(ALERTA, f"Ainda sem posição há {now - lost_t:.0f}s (similaridade {locator.last_score:.2f})")
                        self.on_status("Posição perdida no mapa")
                        engine.notify_paused()
                        in_grace = now - lost_t <= grace
                        if last_press and (in_grace or now - last_nudge >= 2.0):
                            last_nudge = now   # segue na última direção (ou, passada a tolerância, anda 0,5s) até o mapa casar
                            for k in last_press:
                                kb.key_down(k)
                            time.sleep(c["passo_ms"] / 1000 if in_grace else 0.5)
                            for k in last_press:
                                kb.key_up(k)
                        else:
                            release_all()
                            time.sleep(0.15)
                        continue
                    if not loc_ok:
                        loc_ok, lost_t = True, None
                        self._log(INFO, f"Posição reencontrada no mapa completo (similaridade {locator.last_score:.2f}, "
                                        f"{locator.last_mode})")
                else:
                    mpos = live_to_full(pos, live_size, full_size)
                if not calibrated:
                    calibrated = True
                    ix, iy = int(round(mpos[0])), int(round(mpos[1]))
                    fh, fw = engine.walk.shape
                    if not (0 <= ix < fw and 0 <= iy < fh):
                        self._log(ALERTA, f"Seta caiu FORA do mapa completo ({ix}, {iy}): confira as áreas capturadas.")
                    elif not engine.walk[iy, ix]:
                        self._log(ALERTA, f"A seta começou sobre OBSTÁCULO no mapa completo ({ix}, {iy}): confira se "
                                          "'Mapa onde a seta está' corresponde ao mapa completo e as marcações.")
                    else:
                        self._log(INFO, f"Seta localizada no mapa completo em ({ix}, {iy}), sobre corredor")
                last_mpos = mpos
                keys = engine.decide(mpos)
                if engine.stuck_level != last_stuck:
                    if engine.stuck_level > last_stuck:
                        self._log(TRAVADO, f"Seta sem sair do lugar há {c['timeout_travado_s'] * 1000:.0f} ms; "
                                           f"tentativa alternativa nº {engine.stuck_level}")
                    else:
                        self._log(INFO, "Seta voltou a se mover (destravou)")
                    last_stuck = engine.stuck_level
                if verbose or keys != last_keys:
                    tgt = engine.target_for(mpos)
                    sim = f" | similaridade {locator.last_score:.2f} ({locator.last_mode})" if mode == "match" else ""
                    self._log(DECISAO, f"Teclas {'+'.join(k.upper() for k in keys)} | "
                                       f"pos ({mpos[0]:.0f}, {mpos[1]:.0f}) -> alvo ({tgt[0]}, {tgt[1]}){sim}")
                    last_keys = keys
                self.on_status("Rodando")
                last_press = list(keys)
                for k in keys:
                    kb.key_down(k)
                time.sleep(c["passo_ms"] / 1000)
                reg = kb.is_down(keys[0])  # confere com o Windows, ainda com a tecla pressionada
                if reg is not None:
                    reg_fail = 0 if reg else reg_fail + 1
                    if reg and last_reg is not True:
                        last_reg = True
                        self._log(INFO, f"Windows registrou a tecla enviada ({kb.method_name()}). "
                                        "Se mesmo assim a janela não reage, ela filtra teclas enviadas por software.")
                    elif reg_fail >= 3 and last_reg is not False:  # 3 falhas seguidas (1 só pode ser troca de foco)
                        last_reg = False
                        self._log(ALERTA, f"O Windows NÃO registrou as teclas enviadas ({kb.method_name()}): "
                                          "troque o método de envio na aba engine.")
                for k in keys:
                    kb.key_up(k)
        except Exception as exc:  # noqa: BLE001
            self._log(ERRO, f"Falha no loop: {type(exc).__name__}: {exc}")
        finally:
            release_all()
            self._log(INFO, "Bot DESATIVADO")
            self.on_status("Parado")
