"""Load modbus.py and registers.py straight from the integration folder.

Importing them as ``custom_components.weishaupt_wpm.*`` would run the package's
``__init__.py`` and thereby need Home Assistant. Loading the two files directly keeps
the tools runnable with a plain ``python3`` (3.9 or newer).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "weishaupt_wpm"


def _load(name: str):
    module_name = f"_wpm_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, PACKAGE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


modbus = _load("modbus")
registers = _load("registers")
