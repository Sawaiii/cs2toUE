"""Entry point for the packaged application.

One entry, two faces: started without arguments it opens the window, started with
arguments it behaves like the command line tool.  PyInstaller builds two exes from this
same script - cs2toUE.exe (windowed) and cs2toue-cli.exe (console).
"""

import sys


def main() -> int:
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
