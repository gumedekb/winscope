"""Incremental ingest bookkeeping.

output/.manifest.json maps  relative filename -> {sha1, size, rows, ingested_at}
so a re-run only parses CSVs that are new or whose bytes changed. `--full-rescan`
ignores it entirely.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

MANIFEST_NAME = ".manifest.json"


def sha1_of(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class Manifest:
    def __init__(self, out_dir: str):
        self.path = os.path.join(out_dir, MANIFEST_NAME)
        self.entries: dict[str, dict] = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.entries = json.load(fh).get("files", {})
            except (json.JSONDecodeError, OSError):
                self.entries = {}

    def is_new_or_changed(self, name: str, digest: str) -> bool:
        prev = self.entries.get(name)
        return prev is None or prev.get("sha1") != digest

    def record(self, name: str, digest: str, size: int, rows: int) -> None:
        self.entries[name] = {
            "sha1": digest,
            "size": size,
            "rows": rows,
            "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": self.entries,
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)


def discover(src_dir: str, pattern: str = ".csv") -> list[tuple[str, str, str, int]]:
    """-> [(name, abspath, sha1, size), ...] sorted by name."""
    found = []
    if not os.path.isdir(src_dir):
        return found
    for name in sorted(os.listdir(src_dir)):
        path = os.path.join(src_dir, name)
        if not os.path.isfile(path) or not name.lower().endswith(pattern):
            continue
        found.append((name, path, sha1_of(path), os.path.getsize(path)))
    return found
