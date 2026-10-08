import contextlib
from datetime import datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import history
import main
import storage


def record(record_id="id-1", machine="架空機械A", stamp="2026-09-09 12:00:00", names=("温度",)):
    return [dict(record_id=record_id, recorded_at=stamp, machine_name=machine,
                 setting_name=name, value="12.5", unit="", note="") for name in names]


def many_records(count, machine="架空機械A"):
    return [row for index in range(count)
            for row in record(f"{machine}-{index}", machine, names=("温度", "速度"))]


class HistoryTests(unittest.TestCase):
    def test_multiple_settings_share_uuid_and_one_current_time(self):
        now = Mock(return_value=datetime(2026, 9, 9, 12, 34, 56, 789))
        with patch("history.uuid.uuid4", return_value="new-id"):
            rows = history.create_record(" 架空機械A ", "", record(names=("温度", "速度")), [], now=now)
        self.assertEqual({row["record_id"] for row in rows}, {"new-id"})
        self.assertEqual({row["recorded_at"] for row in rows}, {"2026-09-09 12:34:56"})
        self.assertEqual({row["machine_name"] for row in rows}, {"架空機械A"})
        now.assert_called_once_with()

    def test_uuid_collision_is_retried(self):
        with patch("history.uuid.uuid4", side_effect=["id-1", "id-1", "new-id"]) as generate:
            rows = history.create_record("架空機械A", "2026-09-09 12:00:00", record(), record())
        self.assertEqual(rows[0]["record_id"], "new-id")
        self.assertEqual(generate.call_count, 3)

    def test_explicit_time_does_not_read_clock(self):
        now = Mock()
        rows = history.create_record("架空機械A", "2026-01-01 00:00:00", record(), [], now=now)
        self.assertEqual(rows[0]["recorded_at"], "2026-01-01 00:00:00")
        now.assert_not_called()

    def test_finite_numbers(self):
        for value in ("0", "-12", "+12", "0.25", "-.5", "1e3", "1e9999"):
            with self.subTest(value=value):
                self.assertEqual(history.validate_setting(dict(setting_name="温度", value=value))["value"], value)

    def test_invalid_numbers(self):
        for value in ("", " ", "ON", "NaN", "sNaN", "Infinity", "-inf", "1,000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                history.validate_setting(dict(setting_name="温度", value=value))

    def test_required_fields_and_duplicate_names(self):
        cases = [(" ", record()), ("架空機械A", []),
                 ("架空機械A", record(names=(" ",))),
                 ("架空機械A", record(names=("温度", " 温度 ")))]
        for machine, settings in cases:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                history.create_record(machine, "", settings, [])

    def test_bad_setting_prevents_record_and_does_not_modify_existing(self):
        rows = record()
        settings = record(names=("温度", "速度"))
        settings[1]["value"] = "ON"
        with patch("history.uuid.uuid4") as generate, self.assertRaises(ValueError):
            history.create_record("架空機械A", "", settings, rows)
        generate.assert_not_called()
        self.assertEqual(rows, record())

    def test_strict_datetime(self):
        for value in ("2026-9-09 12:00:00", "2026-02-30 12:00:00", "2026-09-09", "2026-09-09 24:00:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                history.parse_datetime(value)

    def test_conflicting_record_identity(self):
        for extra in (record(machine="架空機械B", names=("速度",)),
                      record(stamp="2026-09-08 12:00:00", names=("速度",))):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                history.validate_rows(record() + extra)

    def test_sort_by_time_then_save_order_without_mutation(self):
        rows = record("first") + record("newer", stamp="2026-09-10 00:00:00") + record("last")
        original = list(rows)
        groups = history.group_records(rows)
        self.assertEqual([group[0]["record_id"] for group in groups], ["newer", "last", "first"])
        self.assertEqual(rows, original)

    def test_search_inclusive_dates_and_exact_machine(self):
        rows = (record("a", stamp="2026-09-01 00:00:00") +
                record("b", stamp="2026-09-30 23:59:59") +
                record("c", stamp="2026-10-01 00:00:00") + record("d", machine="架空機械B"))
        found = history.search_records(rows, "架空機械A", "2026-09-01", "2026-09-30")
        self.assertEqual([group[0]["record_id"] for group in found], ["b", "a"])
        self.assertEqual(len(history.search_records(rows, start_date="2026-10-01")), 1)
        self.assertEqual(len(history.search_records(rows, end_date="2026-09-01")), 1)
        self.assertEqual(len(history.search_records(rows)), 4)
        self.assertEqual(history.search_records(rows, "架空"), [])

    def test_invalid_search_dates(self):
        for start, end in (("2026-9-01", ""), ("", "2026-02-30"), ("2026-09-10", "2026-09-09")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                history.search_records([], start_date=start, end_date=end)

    def test_record_count_and_retention_boundaries(self):
        for count in (0, 89, 90, 91, 92):
            with self.subTest(count=count):
                rows = many_records(count)
                self.assertEqual(history.count_records_by_machine(rows).get("架空機械A", 0), count)
                candidates = history.select_deletion_candidates(rows)
                self.assertEqual(len(candidates), max(0, count - 90))
                self.assertEqual([group[0]["record_id"] for group in candidates],
                                 [f"架空機械A-{index}" for index in reversed(range(max(0, count - 90)))])

    def test_retention_per_machine(self):
        rows = many_records(91) + many_records(90, "架空機械B")
        self.assertEqual(history.count_records_by_machine(rows), {"架空機械B": 90, "架空機械A": 91})
        self.assertEqual([group[0]["record_id"] for group in history.select_deletion_candidates(rows)], ["架空機械A-0"])

    def test_backdated_new_record_is_candidate(self):
        rows = many_records(90) + record("old", stamp="2020-01-01 00:00:00")
        self.assertEqual(history.select_deletion_candidates(rows)[0][0]["record_id"], "old")

    def test_remove_all_target_rows_preserves_others_and_order(self):
        rows = record("a", names=("温度", "速度")) + record("b") + record("c")
        self.assertEqual(history.remove_records(rows, {"a"}), record("b") + record("c"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(history.remove_records(rows, set()), rows)


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "records.csv"
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_deletion_not_approved_never_saves(self):
        storage.save_rows_atomic(self.path, many_records(91))
        original = self.path.read_bytes()
        for answer in ("", "no", "cancel", "y", "YES"):
            with self.subTest(answer=answer), patch("builtins.input", return_value=answer), patch("main.storage.save_rows_atomic") as save:
                main.confirm_deletion(self.path)
                save.assert_not_called()
            self.assertEqual(self.path.read_bytes(), original)

    def test_deletion_approved_removes_displayed_candidates_only(self):
        rows = many_records(92) + many_records(90, "架空機械B")
        storage.save_rows_atomic(self.path, rows)
        with patch("builtins.input", return_value="yes"):
            main.confirm_deletion(self.path)
        self.assertEqual(storage.load_rows(self.path), rows[4:])
        displayed = self.output.getvalue()
        self.assertIn("ID: 架空機械A-0", displayed)
        self.assertIn("ID: 架空機械A-1", displayed)
        self.assertIn("2026-09-09 12:00:00 | 2設定項目", displayed)

    def test_no_candidates_does_not_prompt_or_save(self):
        with patch("builtins.input") as prompt, patch("main.storage.save_rows_atomic") as save:
            main.confirm_deletion(self.path)
        prompt.assert_not_called()
        save.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_input_repairs_errors_and_saves_once(self):
        answers = ["", "架空機械A", "bad-date", "2026-09-09 12:00:00", "", "温度", "ON", "NaN", "20.5", "℃", "温度", "速度", "10", "", "", "架空の共通備考"]
        with patch("builtins.input", side_effect=answers), patch("main.storage.save_rows_atomic", wraps=storage.save_rows_atomic) as save:
            main.input_record(self.path)
        save.assert_called_once()
        rows = storage.load_rows(self.path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["record_id"] for row in rows}), 1)
        self.assertEqual([row["value"] for row in rows], ["20.5", "10"])
        self.assertEqual([row["note"] for row in rows], ["架空の共通備考"] * 2)

    def test_common_note_prompted_once_after_all_settings(self):
        for note in ("架空の共通備考", ""):
            with self.subTest(note=note):
                answers = ["架空機械A", "", "温度", "20", "℃", "速度", "10", "", "", note]
                with patch("builtins.input", side_effect=answers) as prompt:
                    main.input_record(self.path)
                prompts = [call.args[0] for call in prompt.call_args_list]
                self.assertEqual(sum(text.startswith("備考") for text in prompts), 1)
                self.assertTrue(prompts[-1].startswith("備考"))
                rows = storage.load_rows(self.path)[-2:]
                self.assertEqual([row["note"] for row in rows], [note, note])
                self.assertEqual(len({row["record_id"] for row in rows}), 1)

    def test_interrupted_note_never_saves(self):
        for existing in (False, True):
            for interruption in (EOFError(), KeyboardInterrupt()):
                with self.subTest(existing=existing, interruption=type(interruption)):
                    if existing:
                        storage.save_rows_atomic(self.path, record())
                    original = self.path.read_bytes() if existing else None
                    answers = ["1", "架空機械A", "", "温度", "20", "℃", "速度", "10", "", "", interruption]
                    with patch("builtins.input", side_effect=answers) as prompt, patch("main.storage.save_rows_atomic") as save:
                        main.main(self.path)
                    self.assertTrue(prompt.call_args.args[0].startswith("備考"))
                    save.assert_not_called()
                    if existing:
                        self.assertEqual(self.path.read_bytes(), original)
                    else:
                        self.assertFalse(self.path.exists())

    def test_interrupted_input_never_saves_partial_record(self):
        for interruption in (EOFError(), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption)), patch("builtins.input", side_effect=["架空機械A", "", "温度", "20", "", "速度", "ON", interruption]), patch("main.storage.save_rows_atomic") as save:
                with self.assertRaises(type(interruption)):
                    main.input_record(self.path)
                save.assert_not_called()
            self.assertFalse(self.path.exists())

    def test_restart_list_search_and_delete_use_full_history(self):
        storage.save_rows_atomic(self.path, many_records(91) + record("b", machine="架空機械B"))
        with patch("builtins.input", side_effect=["3", "架空機械B", "2026-09-09", "2026-09-09", "4", "", "0"]):
            main.main(self.path)
        self.assertIn("表示: 1件", self.output.getvalue())
        self.assertIn("ID: 架空機械A-0", self.output.getvalue())
        with patch("builtins.input", side_effect=["2", "0"]):
            main.main(self.path)
        self.assertIn("表示: 92件", self.output.getvalue())

    def test_corrupt_csv_blocks_registration(self):
        self.path.write_text("bad header\n", encoding="utf-8")
        with patch("builtins.input") as prompt, patch("main.storage.save_rows_atomic") as save:
            with self.assertRaises(ValueError):
                main.input_record(self.path)
        prompt.assert_not_called()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
