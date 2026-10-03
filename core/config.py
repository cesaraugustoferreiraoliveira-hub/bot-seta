"""Configuração persistente (config.json) compartilhada por todos os módulos."""
from __future__ import annotations
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"
ARROW_PATH = PROJECT_DIR / "seta.png"          # sprite da seta (recortada com a varinha mágica)
SPRITE_PATH = PROJECT_DIR / "sprite.png"      # PNG com transparência (saída do recorte mágico)
POKEMON_PATH = PROJECT_DIR / "pokemon.png"    # sprite do nome do seu pokémon (PNG com transparência)
MAP_PATH = PROJECT_DIR / "mapa_completo.png"  # captura do mapa completo
MAPMASK_PATH = PROJECT_DIR / "mapa_mascara.png"  # marcações: verde = corredor, vermelho = obstáculo
LIFEBAR_PATH = PROJECT_DIR / "lifebar.png"    # sprite da barra de vida do pokémon (capturada com a vida CHEIA)
LOG_PATH = PROJECT_DIR / "logs" / "engine.log"  # log da engine de movimentação (gravado em tempo real)
POKEBALL_DIR = PROJECT_DIR / "pokeball_sprites"  # sprites dos pokémon mortos da aba pokeball (uma por perfil: <id>.png)

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
    "shooter_seq_teclas": [{"tecla": "r", "espera_ms": 0}],   # tecla + espera (ms) DEPOIS dela. NÃO ponha a tecla do revive (E) aqui: ela só sai depois do R, pelo revive
    "shooter_confirma_s": 1.5,       # intervalo da ÚLTIMA verificação: com todas as sprites dentro do limite, espera isto e confere de novo se há sprite fora do limite; se não há, dá o R
    "shooter_seq_repetir_s": 1.5,    # o R é apertado VÁRIAS vezes durante este tempo (com 150 ms entre apertos: ~10 vezes em 1,5 s; tempo maior = mais apertos)
    "shooter_seq_intervalo_ms": 150, # intervalo entre um aperto do R e o próximo
    "shooter_espera_max_s": 15.0,    # após o tiro, espera as sprites entrarem na distância por no máx. isto (0 = sem limite)
    "escala_mapa_calibrada": None,   # escala descoberta na última calibração confiável (reaproveitada ao ligar)
    "perda_tolerancia_s": 2.0,       # se a posição se perder, segue na mesma direção por este tempo antes de parar
    "escala_mapa": None,             # None = o bot descobre sozinho; ou fixe (ex.: 0.25 = mapa completo/mapa ao vivo)
    "seta_limiar": 0.85,             # similaridade mínima da sprite da seta (0..1)
    "limiar_seta": 215,              # só sem sprite da seta: brilho mínimo (0..255) dos pixels brancos
    "lookahead": 12,
    "abertura_curva": 0.75,          # 0.5 = rota no meio do corredor; 0.75 = 75% do caminho da parede interna da curva rumo à externa (curvas mais abertas)
    # --- movimento ---
    "passo_ms": 120,
    # --- velocidade conforme as sprites na tela (aba engine); passo 0 = tecla contínua, sem soltar entre os ciclos ---
    "vel_ativo": True,               # False = comportamento antigo (passo_ms fixo, sem previsão)
    "vel_muito_rapido_ate": 0,       # contagem de sprites <= isto: faixa 'muito rápido'
    "vel_rapido_ate": 2,             # contagem <= isto: 'rápido'; acima: 'devagar'
    "vel_muito_rapido_passo_ms": 0,
    "vel_rapido_passo_ms": 200,
    "vel_devagar_passo_ms": 120,
    "vel_muito_rapido_previsao_ms": 100,   # quanto à frente (ms) projeta a posição da seta para já virar
    "vel_rapido_previsao_ms": 50,
    "vel_devagar_previsao_ms": 0,
    "timeout_travado_s": 0.6,
    "movimento_min_px": 0.7,
    "sondagem_px": 4,
    "teclas_movimento": "wasd",      # "wasd" | "setas" (setas do teclado)
    "metodo_teclas": "scancode",     # "scancode" | "vk" | "keybd_event" | "pyautogui"
    # --- revive (página 'revive') ---
    "regiao_vida": None,             # 'Local Life Bar': onde fica a barra de vida do pokémon na pokebar
    "vida_limiar": 0.60,             # similaridade mínima (0..1) da sprite da barra (a cor muda com a vida, então é mais baixa)
    "regiao_revive_foto": None,      # área da 'foto' que muda quando o revive é executado
    "revive_ativo": False,           # aperta o revive quando o shooter conclui (todas as sprites dentro da distância limite)
    "revive_tecla": "e",             # tecla do revive (um toque rápido, confirmado pela foto); só é apertada DEPOIS do R do shooter
    "revive_tentativas": 3,          # quantos toques no E, no máximo, se a foto não mudar (0 = repete até a foto mudar); cada toque espera a janela de verificação antes do próximo
    "revive_intervalo_s": 0.8,       # intervalo entre o fim do R e o E (o programa nunca usa menos que 0,8 s)
    "revive_sens_pct": 2.0,          # % de pixels da foto que precisam mudar para confirmar o revive
    "revive_verifica_s": 2.0,        # depois do toque no E, olha a foto por este tempo; só se não mudar é que toca de novo (nunca dois ao mesmo tempo)
    "revive_toque_ms": 80,           # duração do toque rápido no E
    "vida_hab_ativo": False,         # aperta habilidades conforme a % de vida do pokémon
    "vida_hab_lista": [],            # [{"pct": 50, "tecla": "1", "cooldown_s": 3.0}, ...]  (vida <= pct -> tecla)
    "vida_somente_parado": True,     # só usa as habilidades com o bot parado por excesso de sprites (em combate)
    "vida_intervalo_s": 0.5,         # a cada quantos segundos ler a vida
    # --- pokeball (página 'pokeball'): joga a bola na sprite do pokémon morto (mouse em cima + tecla da bola) ---
    "regiao_pokeball": None,         # onde procurar os pokémon mortos; vazio = usa a 'Área de busca da sprite'
    "pokeball_ativo": False,
    "pokeball_modo": "apos_r",       # "apos_r" = abre a janela de arremessos quando o shooter aperta o R | "sempre" = varre o tempo todo
    "pokeball_janela_s": 8.0,        # quanto tempo, depois do R, o bot procura e joga bolas
    "pokeball_assentar_ms": 15,      # pausa entre levar o mouse até a sprite e apertar a tecla (o jogo precisa ver o cursor)
    "pokeball_toque_ms": 20,         # tempo da tecla apertada
    "pokeball_intervalo_ms": 10,     # pausa entre uma bola e a próxima
    "pokeball_tentativas": 2,        # quantas bolas, no máximo, por sprite (sem reconfirmar antes de jogar: se a sprite continuar na tela depois da recarga, tenta de novo)
    "pokeball_recarga_s": 0.8,       # espera antes de jogar outra bola na MESMA sprite
    "pokeball_varredura_pausa_ms": 20, # pausa entre duas varreduras quando não há nada para jogar
    "pokeball_mira_dy_px": 0,        # o mouse vai este tanto de px ABAIXO do centro da sprite
    "pokeball_demora_sprite_ms": 40, # modo 'segurar': quanto o mouse fica parado em cada sprite, com a tecla apertada
    "pokeball_repetir_ms": 25,       # modo 'segurar': de quanto em quanto tempo a tecla segurada é reenviada (repetição do teclado)
    "pokeball_ajuste_modo": "velocidade",   # mira com a tela andando: "velocidade" (medida pelas sprites) | "teclas" (direção do personagem x px) | "nenhum"
    "pokeball_ajuste_px": 30,        # modo 'teclas': quanto a mira é deslocada, no sentido em que a sprite anda na tela (oposto ao do personagem)
    "pokeball_perfis": [             # bola/sprite/prioridade definidas pelo usuário (prioridade 1 = joga primeiro)
        {"id": "shiny", "nome": "Shiny (Premier Ball)", "tecla": "v", "prioridade": 1, "limiar": 0.90, "ativo": True,
         "modo": "toque", "segurar_s": 2.0},
        {"id": "normal", "nome": "Normal (Ultra Ball)", "tecla": "b", "prioridade": 2, "limiar": 0.90, "ativo": True,
         "modo": "segurar", "segurar_s": 2.0},     # modo: "toque" (um toque na tecla) | "segurar" (tecla segurada enquanto o mouse percorre as sprites)
    ],
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
