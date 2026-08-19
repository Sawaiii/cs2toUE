# PyInstaller spec for cs2toUE.
#
# Produces one folder with two executables sharing the same runtime:
#   cs2toUE.exe      - windowed, opens the GUI
#   cs2toue-cli.exe  - console, the command line
#
# Build with build_exe.ps1 (it keeps every temp file on this drive).

import os

block_cipher = None
ROOT = os.path.abspath(os.getcwd())

a = Analysis(
    ['cs2toue_app.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[
        ('data', 'data'),                       # hlae index + rules, unpacked on first run
        ('cs2toue/ue', 'ue'),                   # scripts Unreal runs by path
        ('README.md', '.'),
    ],
    hiddenimports=[
        'demoparser2',
        'pandas',
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.ttk',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # pulled in as transitive dependencies but never used by cs2toUE
        'polars', 'pyarrow', 'matplotlib', 'scipy', 'IPython', 'jupyter',
        'PyQt5', 'PySide6', 'pytest', 'setuptools._distutils',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='cs2toUE',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

exe_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='cs2toue-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.zipfiles, a.datas,
    strip=False,
    upx=False,
    name='cs2toUE',
)
