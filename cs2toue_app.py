"""Entry point for the packaged application.

One entry, two faces: started without arguments it opens the window, started with
arguments it behaves like the command line tool.  PyInstaller builds two exes from this
same script - cs2toUE.exe (windowed) and cs2toue-cli.exe (console).
"""

import sys


def force_utf8() -> None:
    """Console output must survive any system locale, not just a Russian one."""
    import os
    os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def main() -> int:
    force_utf8()
    from cs2toue.config import bootstrap_files
    bootstrap_files()

    if len(sys.argv) > 1:
        from cs2toue.cli import main as cli_main
        return cli_main()

    try:
        from cs2toue.gui import main as gui_main
        gui_main()
        return 0
    except Exception as exc:
        # a windowed build has no console, so the only way to say anything is a dialog
        message = f"cs2toUE не смог открыть окно:\n\n{type(exc).__name__}: {exc}\n\n" \
                  f"Командная строка должна работать: cs2toue-cli.exe --help"
        if sys.stdout is not None:
            print(message)
        try:
            import tkinter.messagebox as mb
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            mb.showerror("cs2toUE", message)
            root.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
