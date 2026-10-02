"""Configuração persistente (config.json) compartilhada por todos os módulos."""
from __future__ import annotations
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"
ARROW_PATH = PROJECT_DIR / "seta.png"          # sprite da seta (recortada com a varinha mágica)
SPRITE_PATH = PROJECT_DIR / "sprite.png"      # PNG com transparência (saída do recorte mágico)
POKEMON_PATH = PROJECT_DIR / "pokemon.png"    # sprite do nome do seu pokémon (PNG com transparência)
POKEBAR_HEALTH_PATH = PROJECT_DIR / "pokebar_vida.png"
POKEBAR_SKILL_PATH = PROJECT_DIR / "pokebar_habilidades.png"
MAP_PATH = PROJECT_DIR / "mapa_completo.png"  # captura do mapa completo
MAPMASK_PATH = PROJECT_DIR / "mapa_mascara.png"  # marcações: verde = corredor, vermelho = obstáculo
LOG_PATH = PROJECT_DIR / "logs" / "engine.log"  # log da engine de movimentação (gravado em tempo real)

DEFAULTS = {
    # --- áreas de captura (x, y, largura, altura) ---
    "regiao_sprite": None,           # onde procurar as sprites
    "regiao_mapa": None,             # mapa ao vivo (só para achar a seta)
    "regiao_mapa_completo": None,    # onde o mapa completo foi capturado (referência; a imagem fica em mapa_completo.png)
    # --- sprite de parada ---
    "sprite_qtd": 7,                 # parar a seta com >= esta quantidade
    "sprite_limiar": 0.90,           # similaridade mínima (0..1); baixe se não detectar, suba se detectar demais
    "sprite_intervalo_s": 0.35,      # a cada quantos segundos checar as sprites
    # --- rota ---
    "sentido": "horario",            # "horario" | "antihorario"
    "cores_livres": [[0, 204, 0], [153, 102, 51], [192, 192, 192], [255, 0, 0], [255, 255, 255], [255, 255, 0]],  # legado: não é mais usado (o corredor agora é marcado na interface)
    "tolerancia_cor": 12,
    "loc_limiar": 0.55,              # similaridade mínima (busca no mapa todo); perto da última posição aceita 60% disso
    "loc_raio": 48,                  # raio (px do mapa ao vivo) do recorte ao redor da seta usado para achar a posição
    # --- shooter ---
    "pokemon_limiar": 0.70,          # nota mínima (0..1) para aceitar o nome do pokémon; a busca ignora a cor do nome
    "shooter_ativo": False,          # só age com o bot PARADO por excesso de sprites; desligado = comportamento antigo
    "shooter_distancia_px": 250,     # distância limite (px) entre o pokémon e a sprite; além dela a sprite é candidata ao tiro
    "shooter_parada_s": 1.5,         # janela: a sprite distante precisa ficar sem se mexer por este tempo (várias capturas)
    "shooter_tolerancia_px": 6,      # quanto ela pode oscilar (px) e ainda contar como parada
    "shooter_tecla": "q",            # tecla apertada com o mouse sobre a sprite
    "shooter_ref_dy_px": 0,          # desce o centro do círculo de distância: o nome fica ACIMA do pokémon, então desce até o corpo
    "shooter_mira_dy_px": 0,         # o clique/tiro vai este tanto de px ABAIXO do centro da sprite (a sprite é o emblema do selvagem)
    "shooter_seq_ativo": False,      # aperta a sequência de teclas quando todas as sprites reconhecidas estão dentro da distância limite
    "shooter_seq_teclas": [{"tecla": "r", "espera_ms": 300}, {"tecla": "e", "espera_ms": 0}],   # tecla + espera (ms) DEPOIS dela
    "shooter_espera_max_s": 15.0,    # após o tiro, espera as sprites entrarem na distância por no máx. isto (0 = sem limite)
    # --- pokebar ---
    "regiao_pokebar": None,          # espaço onde ficam as barras do pokémon
    "pokebar_limiar": 0.70,          # similaridade para localizar cada barra no pokebar space
    "pokebar_intervalo_s": 0.20,
    "pokebar_ativo": False,
    "pokebar_regras_vida": [],       # [{"percentual": 30, "tecla": "f"}]
    "pokebar_validar_sequencia": True,
    "pokebar_tolerancia_validacao_s": 1.0,
    "escala_mapa_calibrada": None,   # escala descoberta na última calibração confiável (reaproveitada ao ligar)
    "perda_tolerancia_s": 2.0,       # se a posição se perder, segue na mesma direção por este tempo antes de parar
    "escala_mapa": None,             # None = o bot descobre sozinho; ou fixe (ex.: 0.25 = mapa completo/mapa ao vivo)
    "seta_limiar": 0.85,             # similaridade mínima da sprite da seta (0..1)
    "limiar_seta": 215,              # só sem sprite da seta: brilho mínimo (0..255) dos pixels brancos
    "lookahead": 12,
    # --- movimento ---
    "passo_ms": 120,
    "timeout_travado_s": 0.6,
    "movimento_min_px": 0.7,
    "sondagem_px": 4,
    "teclas_movimento": "wasd",      # "wasd" | "setas" (setas do teclado)
    "metodo_teclas": "scancode",     # "scancode" | "vk" | "keybd_event" | "pyautogui"
    # --- engine (página da UI) ---
    "atalho_ativar": "f7",           # tecla/combinação que liga e desliga o bot
    "log_detalhado": False,          # True = registra cada checagem/decisão, mesmo sem mudança
    "log_pasta_salvar": None,        # última pasta usada em 'Salvar log'
}


class ConfigStore:
    """Dicionário de configuração com load/save. Passe a mesma instância a todos os módulos."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self.data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, save: bool = True) -> None:
        self.data[key] = value
        if save:
            self.save()
