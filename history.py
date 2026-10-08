"""Validation and record-level operations, independent of files and menus."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import uuid


FIELDS = ("record_id", "recorded_at", "machine_name", "setting_name", "value", "unit", "note")
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_datetime(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value):
        raise ValueError("記録日時はYYYY-MM-DD HH:MM:SSで入力してください。")
    return datetime.strptime(value, DATETIME_FORMAT)


def parse_date(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日付はYYYY-MM-DDで入力してください。")
    return datetime.strptime(value, "%Y-%m-%d").date()


def validate_setting(setting):
    cleaned = {key: setting.get(key, "").strip() for key in ("setting_name", "value", "unit", "note")}
    if not cleaned["setting_name"]:
        raise ValueError("設定項目名は必須です。")
    try:
        number = Decimal(cleaned["value"])
    except InvalidOperation:
        raise ValueError("設定値には数値を入力してください。") from None
    if not number.is_finite():
        raise ValueError("NaN・無限大は使用できません。")
    return cleaned


def validate_rows(rows):
    """Reject corrupt CSV content before any update; preserve original row order."""
    identities = {}
    names = {}
    for row in rows:
        if set(row) != set(FIELDS) or any(not isinstance(row[key], str) for key in FIELDS):
            raise ValueError("CSVの列または値が不正です。")
        record_id = row["record_id"]
        if not record_id.strip() or not row["machine_name"].strip():
            raise ValueError("記録IDと機械名は必須です。")
        parse_datetime(row["recorded_at"])
        setting = validate_setting(row)
        identity = (row["machine_name"], row["recorded_at"])
        if record_id in identities and identities[record_id] != identity:
            raise ValueError("同じ記録IDに異なる機械名または日時があります。")
        identities[record_id] = identity
        seen = names.setdefault(record_id, set())
        if setting["setting_name"] in seen:
            raise ValueError("同じ記録内に同名の設定項目があります。")
        seen.add(setting["setting_name"])


def create_record(machine_name, recorded_at, settings, existing_rows, now=None):
    validate_rows(existing_rows)
    machine_name = machine_name.strip()
    if not machine_name:
        raise ValueError("機械名は必須です。")
    if not settings:
        raise ValueError("設定項目は1つ以上必要です。")
    cleaned = [validate_setting(setting) for setting in settings]
    if len({setting["setting_name"] for setting in cleaned}) != len(cleaned):
        raise ValueError("同じ入力内に同名の設定項目は登録できません。")
    recorded_at = recorded_at.strip()
    if recorded_at:
        parse_datetime(recorded_at)
    else:
        recorded_at = (now or datetime.now)().strftime(DATETIME_FORMAT)
    existing_ids = {row["record_id"] for row in existing_rows}
    record_id = str(uuid.uuid4())
    while record_id in existing_ids:
        record_id = str(uuid.uuid4())
    return [dict(record_id=record_id, recorded_at=recorded_at, machine_name=machine_name, **setting)
            for setting in cleaned]


def group_records(rows):
    """Newest first; for equal timestamps, later first appearance wins."""
    validate_rows(rows)
    groups = {}
    positions = {}
    for position, row in enumerate(rows):
        record_id = row["record_id"]
        if record_id not in groups:
            groups[record_id] = []
            positions[record_id] = position
        groups[record_id].append(row)
    return sorted(groups.values(),
                  key=lambda group: (group[0]["recorded_at"], positions[group[0]["record_id"]]),
                  reverse=True)


def search_records(rows, machine_name="", start_date="", end_date=""):
    start = parse_date(start_date.strip()) if start_date.strip() else None
    end = parse_date(end_date.strip()) if end_date.strip() else None
    if start and end and start > end:
        raise ValueError("開始日は終了日以前にしてください。")
    machine_name = machine_name.strip()
    return [group for group in group_records(rows)
            if (not machine_name or group[0]["machine_name"] == machine_name)
            and (start is None or parse_datetime(group[0]["recorded_at"]).date() >= start)
            and (end is None or parse_datetime(group[0]["recorded_at"]).date() <= end)]


def count_records_by_machine(rows):
    counts = {}
    for group in group_records(rows):
        machine = group[0]["machine_name"]
        counts[machine] = counts.get(machine, 0) + 1
    return counts


def select_deletion_candidates(rows):
    counts = {}
    candidates = []
    for group in group_records(rows):
        machine = group[0]["machine_name"]
        counts[machine] = counts.get(machine, 0) + 1
        if counts[machine] > 90:
            candidates.append(group)
    return candidates


def remove_records(rows, record_ids):
    validate_rows(rows)
    record_ids = set(record_ids)
    return [row for row in rows if row["record_id"] not in record_ids]
