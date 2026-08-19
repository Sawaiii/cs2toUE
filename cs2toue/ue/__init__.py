"""Scripts that run inside Unreal Engine (they import the `unreal` module).

In a frozen build these are copied next to the exe on first run, because
UnrealEditor-Cmd loads them by path and cannot read them from inside the bundle.
"""

from ..config import UE_SCRIPT_DIR

UE_DIR = UE_SCRIPT_DIR
BUILD_SEQUENCE = UE_DIR / "build_sequence.py"
IMPORT_MAP = UE_DIR / "import_map.py"
IMPORT_MODELS = UE_DIR / "import_models.py"
