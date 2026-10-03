"""Envio de teclas ao sistema, com vários métodos para descobrir qual o jogo/janela aceita.

  scancode     SendInput só com scan code (padrão; o que jogos costumam ler)
  vk           SendInput com virtual key + scan code
  keybd_event  API antiga do Windows (alguns programas respondem só a ela)
  pyautogui    biblioteca pyautogui (fora do Windows é o único disponível)
"""
from __future__ import annotations
import contextlib
import ctypes
import sys
import threading

# tecla lógica (w/a/s/d = cima/esquerda/baixo/direita) -> (scan code, tecla "estendida", nome no pyautogui, virtual key)
LAYOUTS = {
    "wasd": {"w": (0x11, False, "w", 0x57), "a": (0x1E, False, "a", 0x41),
             "s": (0x1F, False, "s", 0x53), "d": (0x20, False, "d", 0x44)},
    "setas": {"w": (0x48, True, "up", 0x26), "a": (0x4B, True, "left", 0x25),
              "s": (0x50, True, "down", 0x28), "d": (0x4D, True, "right", 0x27)},
}
METHODS = {"scancode": "SendInput (scan code)", "vk": "SendInput (virtual key)",
           "keybd_event": "keybd_event", "pyautogui": "pyautogui"}
_EXTENDED, _KEYUP, _SCANCODE = 0x0001, 0x0002, 0x0008

_lock = threading.Lock()
_held: set[str] = set()
_method = "scancode"
_layout = "wasd"
_user32 = None


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):  # só para o union ter o tamanho certo
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]


def _win() -> bool:
    return sys.platform == "win32"


def _u32():
    global _user32
    if _user32 is None:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int)
        _user32.SendInput.restype = ctypes.c_uint
        _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        _user32.GetAsyncKeyState.restype = ctypes.c_short
    return _user32


# ---------------------------------------------------------------- configuração
def set_method(name: str) -> None:
    global _method
    _method = name if name in METHODS else "scancode"


def set_layout(name: str) -> None:
    global _layout
    _layout = name if name in LAYOUTS else "wasd"


def _effective_method() -> str:
    return _method if _win() else "pyautogui"


def method_name() -> str:
    return METHODS[_effective_method()]


def layout_name() -> str:
    return "setas do teclado" if _layout == "setas" else "WASD"


# ---------------------------------------------------------------- envio
def _pyautogui():
    import pyautogui
    pyautogui.PAUSE = 0  # sem a espera de 0,1 s por chamada
    return pyautogui


def _send(key: str, up: bool) -> None:
    scan, ext, pyname, vk = LAYOUTS[_layout][key]
    _send_raw(scan, ext, pyname, vk, up)


def _send_raw(scan: int, ext: bool, pyname: str, vk: int, up: bool) -> None:
    method = _effective_method()
    if method == "pyautogui":
        pg = _pyautogui()
        (pg.keyUp if up else pg.keyDown)(pyname)
        return
    user32 = _u32()
    flags = (_KEYUP if up else 0) | (_EXTENDED if ext else 0)
    if method == "keybd_event":
        user32.keybd_event(vk, scan, flags, 0)
        return
    if method == "scancode":
        inp_vk, flags = 0, flags | _SCANCODE
    else:
        inp_vk = vk
    inp = _INPUT(type=1)  # INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(inp_vk, scan, flags, 0, 0)
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
        raise OSError(f"SendInput recusado pelo Windows (erro {ctypes.get_last_error()})")


def key_down(key: str) -> None:
    with _lock:
        _send(key, False)
        _held.add(key)


def key_up(key: str) -> None:
    with _lock:
        _send(key, True)
        _held.discard(key)


def held_keys() -> tuple[str, ...]:
    """Teclas de movimento (lógicas: w/a/s/d) que o bot está segurando agora. A aba pokeball usa para saber para onde
    o personagem está indo naquele instante."""
    with _lock:
        return tuple(_held)


def release_all() -> None:
    """Solta só as teclas que estão pressionadas (barato, pode ser chamado em todo ciclo)."""
    for k in list(_held):
        try:
            key_up(k)
        except Exception:  # noqa: BLE001
            _held.discard(k)


# ---------------------------------------------------------------- teclas RESERVADAS (travas de segurança)
# Uma tecla reservada só desce (tap / hold_down) dentro de `with authorized(tecla):`. Quem autoriza é o combo
# R -> E (core/combo.py), que só o faz depois de conferir as condições NO MOMENTO do envio. Qualquer outro caminho
# (sequência do shooter, habilidades por vida, teste, código futuro) que tente apertar a tecla recebe PermissionError.
# Soltar a tecla (hold_up) nunca é bloqueado: uma tecla nunca pode ficar presa por causa da trava.
_reserved: set[str] = set()
_tls = threading.local()


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def reserve(*names: str) -> None:
    """Define quais teclas estão reservadas (substitui o conjunto anterior). Sem argumentos = nenhuma."""
    global _reserved
    _reserved = {_norm(n) for n in names if _norm(n)}


def is_reserved(name: str) -> bool:
    return _norm(name) in _reserved


@contextlib.contextmanager
def authorized(name: str):
    """Libera, NESTA thread e só dentro do bloco, o envio da tecla reservada `name`."""
    ok = getattr(_tls, "ok", set())
    _tls.ok = ok | {_norm(name)}
    try:
        yield
    finally:
        _tls.ok = ok


def _check_reserved(name: str) -> None:
    n = _norm(name)
    if n in _reserved and n not in getattr(_tls, "ok", ()):
        raise PermissionError(f"a tecla {n.upper()} é reservada ao revive e só pode ser apertada depois do R "
                              "(bloqueada pela trava de segurança)")


# ---------------------------------------------------------------- tecla qualquer e mouse (shooter)
_NAMED_VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "backspace": 0x08,
             "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "caps lock": 0x14,
             "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
             "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22}
_EXTENDED_VK = {0x26, 0x28, 0x25, 0x27, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22}


def _vk_of(name: str) -> int:
    """Virtual key de uma tecla pelo nome (os mesmos nomes da biblioteca 'keyboard': q, 1, f5, space, page up...)."""
    n = name.strip().lower()
    if n in _NAMED_VK:
        return _NAMED_VK[n]
    if len(n) == 1 and n.isascii() and n.isalnum():
        return ord(n.upper())               # a-z e 0-9: a virtual key é o próprio caractere
    if n.startswith("f") and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x70 + int(n[1:]) - 1        # F1 = 0x70
    if len(n) == 1 and _win():              # pontuação: pergunta ao layout do teclado
        vk = _u32().VkKeyScanW(ord(n))
        if vk != -1:
            return vk & 0xFF
    raise ValueError(f"Tecla desconhecida: {name!r}")


def check_key(name: str) -> None:
    """Levanta ValueError se `name` não for uma tecla que o tap() sabe apertar."""
    _vk_of(name or "")


def tap(name: str, hold_s: float = 0.05) -> None:
    """Aperta e solta uma tecla qualquer (não só W/A/S/D), pelo mesmo método de envio das teclas de movimento."""
    import time
    vk = _vk_of(name)                        # valida antes de apertar
    _check_reserved(name)                    # tecla do revive: só dentro de `authorized`
    scan = _u32().MapVirtualKeyW(vk, 0) if _win() else 0   # 0 = MAPVK_VK_TO_VSC
    ext = vk in _EXTENDED_VK
    pyname = name.strip().lower().replace(" ", "")          # 'page up' (keyboard) -> 'pageup' (pyautogui)
    with _lock:
        _send_raw(scan, ext, pyname, vk, False)
    try:
        time.sleep(hold_s)
    finally:
        with _lock:
            _send_raw(scan, ext, pyname, vk, True)


def _key_args(name: str):
    vk = _vk_of(name)
    scan = _u32().MapVirtualKeyW(vk, 0) if _win() else 0
    return scan, vk in _EXTENDED_VK, name.strip().lower().replace(" ", ""), vk


def hold_down(name: str) -> None:
    """Pressiona uma tecla qualquer e NÃO solta (solte com hold_up). Chamar de novo reenvia o 'tecla para baixo',
    como o teclado faz na repetição automática enquanto a tecla está segurada."""
    scan, ext, pyname, vk = _key_args(name)
    _check_reserved(name)
    with _lock:
        _send_raw(scan, ext, pyname, vk, False)


def hold_up(name: str) -> None:
    scan, ext, pyname, vk = _key_args(name)
    with _lock:
        _send_raw(scan, ext, pyname, vk, True)


# O mouse é um só: quem leva o cursor até um alvo E aperta a tecla ali (shooter, pokeball) faz isso dentro deste
# lock, para um não tirar o cursor de cima do alvo do outro entre o movimento e o aperto da tecla.
aim_lock = threading.RLock()


def move_mouse(x: int, y: int) -> None:
    """Leva o cursor para (x, y) na tela. No Windows usa SetCursorPos: o pyautogui.moveTo aborta se o mouse
    estiver parado num canto da tela (fail-safe), o que derrubaria o bot."""
    if _win():
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    else:
        _pyautogui().moveTo(int(x), int(y))


# ---------------------------------------------------------------- diagnóstico
def is_down(key: str) -> bool | None:
    """O Windows considera a tecla pressionada agora? (None fora do Windows)."""
    if not _win():
        return None
    try:
        return bool(_u32().GetAsyncKeyState(LAYOUTS[_layout][key][3]) & 0x8000)
    except Exception:  # noqa: BLE001
        return None


def is_admin() -> bool | None:
    if not _win():
        return None
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return None


def foreground_title() -> str | None:
    """Título da janela que está recebendo as teclas agora."""
    if not _win():
        return None
    try:
        user32 = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(user32.GetForegroundWindow(), buf, 256)
        return buf.value or "(sem título)"
    except Exception:  # noqa: BLE001
        return None
