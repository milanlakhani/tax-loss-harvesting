from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class StatementStorage(Protocol):
    def save(self, name: str, data: bytes) -> Path: ...

    def load(self, name: str) -> bytes: ...

    def path_for(self, name: str) -> Path: ...


class LocalStatementStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "statements").mkdir(parents=True, exist_ok=True)

    def save(self, name: str, data: bytes) -> Path:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def load(self, name: str) -> bytes:
        return self.path_for(name).read_bytes()

    def path_for(self, name: str) -> Path:
        safe = name.replace("\\", "/").lstrip("/")
        return self.root / "statements" / safe
