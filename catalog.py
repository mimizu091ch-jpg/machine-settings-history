"""Per-machine setting definitions. History values are never copied here."""

import csv
import os
from pathlib import Path
import tempfile

import history

FIELDS = ("machine_name", "setting_name", "unit")


def validate_definitions(rows):
    seen = set()
    for row in rows:
        if set(row) != set(FIELDS) or any(not isinstance(row[k], str) for k in FIELDS):
            raise ValueError("項目定義CSVの列または値が不正です。")
        if not row["machine_name"].strip() or not row["setting_name"].strip():
            raise ValueError("項目定義の機械名・設定項目名は必須です。")
        if any(row[k] != row[k].strip() for k in FIELDS):
            raise ValueError("項目定義の前後に不要な空白があります。")
        key = row["machine_name"], row["setting_name"]
        if key in seen:
            raise ValueError("同じ機械に同名の設定項目が重複しています。")
        seen.add(key)


def load_definitions(path):
    try:
        source = Path(path).open(encoding="utf-8-sig", newline="")
    except FileNotFoundError:
        return []
    with source:
        reader = csv.DictReader(source, strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("項目定義CSVのヘッダーが不正です。")
        rows = list(reader)
    validate_definitions(rows)
    return rows


def merge_history(definitions, rows):
    """Recover missing definitions from history; existing definitions win.

    A legacy history may use different units over time. Only when a definition
    is missing do we adopt the newest record's unit. History remains unchanged.
    """
    validate_definitions(definitions)
    result = [dict(row) for row in definitions]
    seen = {(row["machine_name"], row["setting_name"]) for row in result}
    for group in history.group_records(rows):
        for row in group:
            definition = {key: row[key].strip() for key in FIELDS}
            key = definition["machine_name"], definition["setting_name"]
            if key not in seen:
                result.append(definition)
                seen.add(key)
    validate_definitions(result)
    return result


def save_definitions_atomic(path, rows):
    rows = list(rows)
    validate_definitions(rows)
    path = Path(path)
    load_definitions(path)  # Never replace a corrupt/unreadable catalog.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=".settings-",
                                         suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
