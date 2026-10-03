"""Página 'pokeball': joga a bola (mouse em cima da sprite + tecla da bola) nos pokémon que o R matou, sem parar o bot.
Aba 'Bolas': ativação e o perfil de cada bola (sprite, tecla, prioridade). Aba 'Ajustes': área de busca, velocidade dos
arremessos e testes."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk

from core.botlog import BotLog
from core.config import ConfigStore
from .areas_panel import AreasPanel
from .fields import number_row
from .key_capture import KeyCapture
from .pokeball_panel import BallProfilesPanel
from .pokeball_test_panel import PokeballTestPanel

POKEBALL_AREAS = [
    ("regiao_pokeball", "Área de busca da pokeball (opcional)",
     "Vazia = usa a área da sprite. Menor (ao redor do personagem) = mais rápido."),
]


class ActivationPanel(ttk.LabelFrame):
    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Ativação ")
        self.cfg = cfg
        self.columnconfigure(2, weight=1)
        self.ativo = tk.BooleanVar(value=bool(cfg.get("pokeball_ativo")))
        ttk.Checkbutton(self, text="Ativar a pokeball (joga bolas nos pokémon mortos sem parar o bot)", variable=self.ativo,
                        command=lambda: cfg.set("pokeball_ativo", bool(self.ativo.get()))
                        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))
        ttk.Label(self, text="Quando procurar").grid(row=1, column=0, sticky="w", padx=10, pady=3)
        self.modo = tk.StringVar(value=cfg.get("pokeball_modo", "apos_r"))
        modes = ttk.Frame(self)
        modes.grid(row=1, column=1, columnspan=2, sticky="w", pady=3)
        for text, value in (("Depois do R do shooter", "apos_r"), ("O tempo todo", "sempre")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self.modo,
                            command=lambda: cfg.set("pokeball_modo", self.modo.get())).pack(side="left", padx=(0, 14))
        number_row(self, 2, cfg, "Procurar por", "pokeball_janela_s", 1, 60, 0.5, "s",
                   "depois do R, durante este tempo (as bolas saem logo em seguida)", float)
        ttk.Label(self, foreground="#666", text=(
            "O R é apertado pelo shooter (aba shooter). A pokeball roda ao lado do movimento: nunca para o bot para jogar bola.")
                  ).grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))


class AdjustPanel(ttk.LabelFrame):
    """Como a mira acompanha a tela andando: pela velocidade medida, pela direção do personagem (ajuste automático) ou nenhum."""

    def __init__(self, master, cfg: ConfigStore):
        super().__init__(master, text=" Mira com o personagem andando ")
        self.cfg = cfg
        self.columnconfigure(2, weight=1)
        self.modo = tk.StringVar(value=cfg.get("pokeball_ajuste_modo", "velocidade"))
        options = (("Ajuste automático pela direção do personagem", "teclas",
                    "indo para cima a mira desce; para a direita, vai para a esquerda; diagonais compõem"),
                   ("Pela velocidade medida das sprites", "velocidade", "o bot mede quanto a tela andou entre as varreduras"),
                   ("Sem ajuste", "nenhum", "a mira vai exatamente onde a sprite foi vista"))
        for r, (text, value, hint) in enumerate(options):
            ttk.Radiobutton(self, text=text, value=value, variable=self.modo,
                            command=lambda: cfg.set("pokeball_ajuste_modo", self.modo.get())
                            ).grid(row=r, column=0, columnspan=2, sticky="w", padx=10, pady=(6 if r == 0 else 1, 1))
            ttk.Label(self, text=hint, foreground="#666").grid(row=r, column=2, sticky="w", padx=8)
        number_row(self, 3, cfg, "Distância do ajuste automático", "pokeball_ajuste_px", 0, 400, 1, "px",
                   "quanto a mira é deslocada no ajuste pela direção (só vale nesse modo)", int)


class PokeballPage(ttk.Frame):
    def __init__(self, master, cfg: ConfigStore, log: BotLog | None = None):
        super().__init__(master)
        keys = KeyCapture(self)
        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=4, pady=4)

        bolas = ttk.Frame(tabs)
        self.activation = ActivationPanel(bolas, cfg)
        self.profiles_panel = BallProfilesPanel(bolas, cfg, keys)
        self.activation.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        self.profiles_panel.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)

        ajustes = ttk.Frame(tabs)
        self.areas_panel = AreasPanel(ajustes, cfg, POKEBALL_AREAS, " Área de captura na tela ")
        self.areas_panel.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        ttk.Button(self.areas_panel, text="Usar a área da sprite", command=lambda: self._clear_area(cfg)
                   ).grid(row=2, column=1, sticky="w", padx=6, pady=(0, 10))

        self.adjust_panel = AdjustPanel(ajustes, cfg)
        self.adjust_panel.grid(row=1, column=0, sticky="ew", padx=10, pady=4)

        speed = ttk.LabelFrame(ajustes, text=" Arremesso ")
        speed.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        speed.columnconfigure(2, weight=1)
        number_row(speed, 0, cfg, "Pausa do mouse até a tecla", "pokeball_assentar_ms", 0, 500, 5, "ms",
                   "o jogo precisa ver o cursor na sprite antes da tecla (menor = mais rápido)", int)
        number_row(speed, 1, cfg, "Tempo da tecla", "pokeball_toque_ms", 5, 500, 5, "ms",
                   "quanto a tecla da bola fica apertada", int)
        number_row(speed, 2, cfg, "Pausa entre as bolas", "pokeball_intervalo_ms", 0, 1000, 5, "ms",
                   "depois de uma bola, antes da próxima", int)
        number_row(speed, 7, cfg, "Parado em cada sprite (segurar)", "pokeball_demora_sprite_ms", 0, 1000, 5, "ms",
                   "modo Segurar: quanto o mouse fica em cada sprite com a tecla apertada", int)
        number_row(speed, 8, cfg, "Reenviar a tecla segurada a cada", "pokeball_repetir_ms", 5, 500, 5, "ms",
                   "modo Segurar: repetição da tecla, como o teclado faz segurando", int)
        number_row(speed, 3, cfg, "Ajuste da mira (para baixo)", "pokeball_mira_dy_px", -200, 200, 1, "px",
                   "o mouse vai este tanto abaixo do centro da sprite", int)
        number_row(speed, 4, cfg, "Bolas por sprite (máx.)", "pokeball_tentativas", 1, 10, 1, "",
                   "não reconfirma antes de jogar: se a sprite continuar após a recarga, tenta de novo", int)
        number_row(speed, 5, cfg, "Recarga na mesma sprite", "pokeball_recarga_s", 0.2, 30, 0.1, "s",
                   "espera antes de jogar outra bola na mesma sprite", float)
        number_row(speed, 6, cfg, "Pausa entre varreduras", "pokeball_varredura_pausa_ms", 0, 2000, 10, "ms",
                   "só vale quando não há nada para jogar (aumente se o PC ficar pesado)", int)

        self.test_panel = PokeballTestPanel(ajustes, cfg, log)
        self.test_panel.grid(row=3, column=0, sticky="ew", padx=10, pady=4)

        tabs.add(bolas, text="Bolas")
        tabs.add(ajustes, text="Ajustes")

    def _clear_area(self, cfg: ConfigStore) -> None:
        cfg.set("regiao_pokeball", None)
        self.areas_panel.refresh_all()
