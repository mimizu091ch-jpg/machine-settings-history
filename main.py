"""Japanese command-line interface for machine settings history."""

import csv
from pathlib import Path

import history
import storage
from app_service import SettingsService


DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "records.csv"


def input_record(path):
    rows = storage.load_rows(path)
    while True:
        machine = input("機械名: ").strip()
        if machine:
            break
        print("機械名は必須です。")
    while True:
        recorded_at = input("記録日時（Enterで現在日時／過去の記録は YYYY-MM-DD HH:MM:SS）: ").strip()
        try:
            if recorded_at:
                history.parse_datetime(recorded_at)
            break
        except ValueError as error:
            print(f"入力エラー: {error}")
    settings = []
    while True:
        name = input("設定項目名 (空欄=入力完了): ").strip()
        if not name:
            if settings:
                break
            print("設定項目は1つ以上必要です。")
            continue
        if any(setting["setting_name"] == name for setting in settings):
            print("同名の設定項目は登録できません。")
            continue
        while True:
            value = input("設定値 (数値): ").strip()
            try:
                history.validate_setting({"setting_name": name, "value": value})
                break
            except ValueError as error:
                print(f"入力エラー: {error}")
        settings.append(dict(setting_name=name, value=value,
                             unit=input("単位 (省略可): ")))
    note = input("備考 (今回の記録に共通、省略可): ")
    for setting in settings:
        setting["note"] = note
    new_rows = history.create_record(machine, recorded_at, settings, rows)
    storage.save_rows_atomic(path, rows + new_rows)
    print(f"1件 ({len(new_rows)}設定項目) を保存しました。ID: {new_rows[0]['record_id']}")


def show_history(path, search=False):
    rows = storage.load_rows(path)
    machine = input("機械名 (完全一致、空欄=すべて): ") if search else ""
    start = input("開始日 (YYYY-MM-DD、省略可): ") if search else ""
    end = input("終了日 (YYYY-MM-DD、省略可): ") if search else ""
    groups = history.search_records(rows, machine, start, end)
    print(f"表示: {len(groups)}件")
    for group in groups:
        first = group[0]
        print(f"{first['recorded_at']} | {first['machine_name']} | ID: {first['record_id']}")
        for row in group:
            print(f"  {row['setting_name']}: {row['value']} {row['unit']} | {row['note']}")
    for machine, count in history.count_records_by_machine(rows).items():
        print(f"全履歴件数: {machine} = {count}件")


def confirm_deletion(path):
    rows = storage.load_rows(path)
    candidates = history.select_deletion_candidates(rows)
    if not candidates:
        print("削除候補はありません。各機械の最新90件は保持します。")
        return
    print("削除候補 (各機械の全履歴で91件目以降):")
    for group in candidates:
        first = group[0]
        print(f"{first['machine_name']} | ID: {first['record_id']} | "
              f"{first['recorded_at']} | {len(group)}設定項目")
    if input(f"表示した{len(candidates)}件をすべて削除しますか？ 承認は yes: ").strip() != "yes":
        print("削除をキャンセルしました。")
        return
    ids = {group[0]["record_id"] for group in candidates}
    SettingsService(path).delete_candidates(ids)
    print(f"{len(ids)}件を削除しました。")


def main(path=DEFAULT_PATH):
    while True:
        print("\n1: 記録  2: 履歴一覧  3: 検索  4: 削除候補・確認  0: 終了")
        try:
            choice = input("選択: ").strip()
            if choice == "0":
                return
            if choice == "1":
                input_record(path)
            elif choice == "2":
                show_history(path)
            elif choice == "3":
                show_history(path, search=True)
            elif choice == "4":
                confirm_deletion(path)
            else:
                print("0～4を選択してください。")
        except (EOFError, KeyboardInterrupt):
            print("\n入力を中止して終了します。")
            return
        except (OSError, ValueError, csv.Error) as error:
            print(f"処理に失敗しました: {error}")


if __name__ == "__main__":
    main()
