"""Abre a interface de configuração:  python main.py

No Windows pede permissão de administrador (UAC) ao abrir: sem isso, o Windows ignora as teclas
enviadas para jogos que rodam como administrador.  Para abrir sem pedir:  python main.py --sem-admin
"""
import ctypes
import os
import subprocess
import sys


def _relaunch_as_admin() -> bool:
    """Reabre este script elevado. True = a cópia elevada foi iniciada (esta deve encerrar)."""
    here = os.path.dirname(os.path.abspath(__file__))
    args = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:] + ["--sem-admin"])
    ok = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, here, 1)
    return ok > 32  # <= 32: o usuário recusou o UAC ou deu erro


if __name__ == "__main__":
    if sys.platform == "win32" and "--sem-admin" not in sys.argv:
        try:
            if not ctypes.windll.shell32.IsUserAnAdmin() and _relaunch_as_admin():
                sys.exit(0)
        except Exception:  # noqa: BLE001
            pass  # segue sem administrador
    from ui.app import run
    run()
