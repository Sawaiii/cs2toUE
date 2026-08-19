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
    except Exception as exc:                      # tkinter missing on a stripped python
        print(f"cannot start the window: {exc}")
        print("use the command line instead: cs2toue-cli.exe --help")
        return 1
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
