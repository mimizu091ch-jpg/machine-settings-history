"""GUI use cases, independent of Tk. A single local process is assumed."""

import csv
from dataclasses import dataclass
from pathlib import Path

import catalog
import history
import storage


@dataclass(frozen=True)
class SaveResult:
    record_id: str
    catalog_warning: str = ""


class SettingsService:
    def __init__(self, record_path):
        self.record_path = Path(record_path)
        self.catalog_path = self.record_path.with_name("settings.csv")

    def load_rows(self):
        return storage.load_rows(self.record_path)

    def definitions(self, rows=None):
        if rows is None:
            rows = self.load_rows()
        return catalog.merge_history(catalog.load_definitions(self.catalog_path), rows)

    def prepare_settings(self, machine, entries, note="", rows=None):
        """An entry is either a selected definition or an explicitly new item."""
        machine = machine.strip()
        if not machine:
            raise ValueError("機械名を入力してください。")
        definitions = {row["setting_name"]: row for row in self.definitions(rows)
                       if row["machine_name"] == machine}
        if not entries:
            raise ValueError("設定項目を1つ以上追加してください。")
        settings, seen = [], set()
        for entry in entries:
            mode, name = entry.get("mode", ""), entry.get("setting_name", "").strip()
            if mode not in {"existing", "new"} or not name:
                raise ValueError("設定項目を選択するか、新しい項目名を入力してください。")
            if name in seen:
                raise ValueError(f"「{name}」が重複しています。1回だけ選択してください。")
            seen.add(name)
            if mode == "existing":
                if name not in definitions:
                    raise ValueError(f"「{name}」はこの機械の登録済み項目ではありません。")
                unit = definitions[name]["unit"]
            else:
                if name in definitions:
                    raise ValueError(f"「{name}」は登録済みです。選択肢から選んでください。")
                unit = entry.get("unit", "").strip()
            settings.append(history.validate_setting(dict(
                setting_name=name, value=entry.get("value", ""), unit=unit, note=note)))
        return settings

    def save_record(self, machine, recorded_at, entries, note=""):
        # Re-read before writing so a long-lived entry screen cannot append to
        # a stale in-memory copy. This is not a multi-process locking protocol.
        rows = self.load_rows()
        definitions = self.definitions(rows)  # Invalid catalog blocks changes.
        settings = self.prepare_settings(machine, entries, note, rows)
        new_rows = history.create_record(machine, recorded_at, settings, rows)
        merged = catalog.merge_history(definitions, rows + new_rows)
        storage.save_rows_atomic(self.record_path, rows + new_rows)
        try:
            catalog.save_definitions_atomic(self.catalog_path, merged)
        except (OSError, ValueError, csv.Error) as error:
            # Record is already durable. Do not ask the caller to retry it.
            # Definitions remain recoverable from records on the next load.
            return SaveResult(new_rows[0]["record_id"],
                              f"記録は保存済みです。項目定義の保存に失敗しました: {error}\n"
                              "同じ記録を再登録せず、保存先を確認してください。"
                              "選択肢は保存済みの履歴から再取得できます。")
        return SaveResult(new_rows[0]["record_id"])

    def delete_candidates(self, displayed_ids):
        """Call only after the user confirms the displayed candidate records."""
        displayed_ids = set(displayed_ids)
        rows = self.load_rows()
        current_ids = {group[0]["record_id"]
                       for group in history.select_deletion_candidates(rows)}
        if displayed_ids != current_ids:
            raise ValueError("削除候補が変わっています。候補一覧を開き直してください。")
        if not displayed_ids:
            return 0
        # Persist every known definition BEFORE deleting any history. If this
        # fails, all history stays intact, including definitions needing repair.
        catalog.save_definitions_atomic(self.catalog_path, self.definitions(rows))
        storage.save_rows_atomic(self.record_path, history.remove_records(rows, displayed_ids))
        return len(displayed_ids)
