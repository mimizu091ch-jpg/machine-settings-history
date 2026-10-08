"""CSV persistence using a temporary file in the destination directory."""

import csv
import os
from pathlib import Path
import tempfile

from history import FIELDS, validate_rows


def load_rows(path):
    try:
        source = Path(path).open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError:
        return []
    with source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("CSVのヘッダーが指定の列構成と一致しません。")
        rows = list(reader)
    validate_rows(rows)
    return rows


def save_rows_atomic(path, rows):
    rows = list(rows)
    validate_rows(rows)
    path = Path(path)
    # Never overwrite unreadable or invalid existing content.
    load_rows(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=".records-", suffix=".tmp",
                                         delete=False) as target:
            temporary_path = Path(target.name)
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # Cleanup failure must not hide the original save error.
                pass
