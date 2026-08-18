"""Locations used by both the source checkout and the Windows distribution."""
from __future__ import annotations

import sys
from pathlib import Path


# Shared visual asset used by both interactive 3D viewports.  The CFD solver
# keeps its independently repaired, watertight surface in services.cfd.
VISUAL_ROCKET_MODEL_STL = "ensamble_naca_661_212.stl"


def application_root() -> Path:
    """Return the directory containing bundled configs, data and writable outputs."""
    if getattr(sys, "frozen", False):
        # One-file executables expand resources below sys._MEIPASS. One-folder
        # builds expose their _internal directory through the same attribute.
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            return Path(bundle_dir).resolve()
        # PyInstaller's one-folder layout stores bundled resources in _internal.
        # Fall back to the executable directory for alternative packagers.
        executable_dir = Path(sys.executable).resolve().parent
        internal = executable_dir / "_internal"
        return internal if internal.is_dir() else executable_dir
    return Path(__file__).resolve().parents[2]
