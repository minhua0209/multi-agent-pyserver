from __future__ import annotations

import re
import subprocess
from pathlib import Path


FORBIDDEN_LITERALS = [
    "192.168." + "18.94",
    "demo" + "_root_123",
    "root" + "root",
]

FORBIDDEN_REGEXES = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]


def test_tracked_files_do_not_contain_local_secrets() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    candidate_files = [
        path.decode()
        for path in subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_root,
        ).split(b"\0")
        if path
    ]
    leaks = []
    for relative_path in candidate_files:
        path = repo_root / relative_path
        try:
            content = path.read_text()
        except UnicodeDecodeError:
            continue

        for pattern in FORBIDDEN_LITERALS:
            if pattern in content:
                leaks.append(f"{relative_path}: {pattern}")
        for pattern in FORBIDDEN_REGEXES:
            if pattern.search(content):
                leaks.append(f"{relative_path}: {pattern.pattern}")

    assert leaks == []
