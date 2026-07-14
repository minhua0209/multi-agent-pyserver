from __future__ import annotations

import os
import runpy
import sys
import types
from pathlib import Path


def test_main_can_be_run_as_script(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    script_path = project_root / "app" / "main.py"
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs) -> None:
        called["args"] = args
        called["kwargs"] = kwargs

    monkeypatch.setenv("DISABLE_DEFAULT_DATABASE_URL", "true")
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=fake_run))

    filtered_path = [
        entry
        for entry in sys.path
        if Path(entry or os.getcwd()).resolve() != project_root
    ]
    monkeypatch.setattr(sys, "path", [str(script_path.parent), *filtered_path])

    runpy.run_path(str(script_path), run_name="__main__")

    assert called["args"][0].title == "TaskHub MVP"
    assert called["kwargs"] == {"host": "127.0.0.1", "port": 8000}
    assert str(project_root) in sys.path
