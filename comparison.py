"""Exact comparisons without float rounding or unit conversion."""

from decimal import Decimal

import history


def compare_records(rows, first_id, second_id):
    if first_id == second_id:
        raise ValueError("異なる2件の記録を選択してください。")
    groups = [group for group in history.group_records(rows)
              if group[0]["record_id"] in {first_id, second_id}]
    if len(groups) != 2:
        raise ValueError("比較する記録が見つかりません。一覧を更新してください。")
    newer, older = groups
    if newer[0]["machine_name"] != older[0]["machine_name"]:
        raise ValueError("同じ機械の記録を2件選択してください。")
    before = {row["setting_name"].strip(): row for row in older}
    after = {row["setting_name"].strip(): row for row in newer}
    result = []
    for name in dict.fromkeys([*before, *after]):
        left, right = before.get(name), after.get(name)
        if left is None:
            status = "項目追加"
        elif right is None:
            status = "項目なし"
        elif left["unit"].strip() != right["unit"].strip():
            status = "単位変更"
        elif Decimal(left["value"]) != Decimal(right["value"]):
            status = "値変更"
        else:
            status = "変更なし"
        result.append(dict(setting_name=name, before=left, after=right, status=status))
    return older, newer, result
