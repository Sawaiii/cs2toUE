# PyInstaller spec for cs2toUE-Setup.exe - a single file that carries the whole program.
#
# Needs dist\cs2toUE to exist first; build.ps1 does both passes in order.
#
# runtime_tmpdir='.' keeps the self-extraction on the same drive as the installer instead
# of %TEMP% on C:, which matters on machines where C: is nearly full.

import os

ROOT = os.path.abspath(os.getcwd())
# build.ps1 puts the application build under workspace\ and points this at it
APP_DIR = os.environ.get('CS2TOUE_APP_DIR') or os.path.join(
    ROOT, 'workspace', 'build', 'app', 'cs2toUE')

a = Analysis(
    ['setup_app.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[(APP_DIR, 'app')],
    hiddenimports=['tkinter', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.ttk'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['pandas', 'numpy', 'demoparser2', 'polars', 'pyarrow', 'matplotlib'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='cs2toUE-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir='.',
    console=False,
    icon=None,
)
