"""cs2toUE - convert Counter-Strike 2 / CS:GO demos into Unreal Engine scenes.

Pipeline glue around existing tools:
  * HLAE (advancedfx)        - per-demo-version game hook, camera IO, recording
  * demoparser2 (LaihoE)     - fast Rust CS2 demo parser
  * Source 2 Viewer (VRF)    - map / model decompiler to glTF
  * Unreal Engine Python API - Level Sequence builder
"""

__version__ = "1.0.0"
