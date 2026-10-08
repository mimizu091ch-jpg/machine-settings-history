import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app_service import SettingsService
import catalog
from comparison import compare_records
import history
import storage


def entry(name="温度", value="80", unit="℃", mode="new"):
    return dict(mode=mode, setting_name=name, value=value, unit=unit)


def record(key, name="温度", value="80", unit="℃", machine="架空機械A", stamp="2026-10-04 12:00:00"):
    return dict(record_id=key, recorded_at=stamp, machine_name=machine,
                setting_name=name, value=value, unit=unit, note="架空の備考")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "records.csv"
        self.service = SettingsService(self.path)

    def test_new_definition_survives_restart_without_copying_value(self):
        result = self.service.save_record("架空機械A", "", [entry()])
        restored = SettingsService(self.path)
        self.assertEqual(restored.definitions(), [dict(machine_name="架空機械A", setting_name="温度", unit="℃")])
        self.assertEqual(restored.load_rows()[0]["record_id"], result.record_id)
        self.assertEqual(result.catalog_warning, "")

    def test_existing_uses_registered_unit_and_preserves_old_record(self):
        self.service.save_record("架空機械A", "", [entry()])
        original = self.service.load_rows()[0]
        self.service.save_record("架空機械A", "", [entry(value="82", unit="wrong", mode="existing")])
        self.assertEqual(self.service.load_rows()[0], original)
        self.assertEqual(self.service.load_rows()[-1]["unit"], "℃")

    def test_definition_is_specific_to_machine(self):
        self.service.save_record("架空機械A", "", [entry()])
        with self.assertRaises(ValueError):
            self.service.save_record("架空機械B", "", [entry(mode="existing")])
        self.service.save_record("架空機械B", "", [entry(unit="K")])
        self.assertEqual({r["unit"] for r in self.service.definitions()}, {"℃", "K"})

    def test_new_cannot_recreate_known_name(self):
        self.service.save_record("架空機械A", "", [entry()])
        with self.assertRaises(ValueError):
            self.service.save_record("架空機械A", "", [entry()])
        self.assertEqual(len(self.service.load_rows()), 1)

    def test_invalid_and_duplicate_entries_write_nothing(self):
        for entries in ([], [entry(mode="")], [entry(name="")], [entry(value="NaN")],
                        [entry(), entry(name=" 温度 ")], [entry(mode="existing")]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.service.save_record("架空機械A", "", entries)
            self.assertFalse(self.path.exists())
            self.assertFalse(self.service.catalog_path.exists())

    def test_preview_does_not_write_files(self):
        self.service.prepare_settings("架空機械A", [entry()])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.service.catalog_path.exists())

    def test_bad_datetime_changes_nothing(self):
        with self.assertRaises(ValueError):
            self.service.save_record("架空機械A", "2026-02-30 00:00:00", [entry()])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.service.catalog_path.exists())

    def test_record_failure_does_not_create_definition(self):
        with patch("app_service.storage.save_rows_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.save_record("架空機械A", "", [entry()])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.service.catalog_path.exists())

    def test_catalog_failure_reports_saved_record_and_recovers_choices(self):
        with patch("app_service.catalog.save_definitions_atomic", side_effect=OSError("disk full")):
            result = self.service.save_record("架空機械A", "", [entry()])
        self.assertIn("保存済み", result.catalog_warning)
        self.assertEqual(len(self.service.load_rows()), 1)
        self.assertEqual(SettingsService(self.path).definitions()[0]["setting_name"], "温度")
        self.service.save_record("架空機械A", "", [entry(mode="existing")])
        self.assertTrue(self.service.catalog_path.exists())
        self.assertEqual(len(self.service.load_rows()), 2)

    def test_corrupt_catalog_blocks_save_without_overwriting_records(self):
        self.service.save_record("架空機械A", "", [entry()])
        original = self.path.read_bytes()
        self.service.catalog_path.write_text("bad header\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.save_record("架空機械A", "", [entry(mode="existing")])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.service.catalog_path.read_text(), "bad header\n")

    def test_legacy_import_newest_unit_and_no_rewrite(self):
        rows = [record("old", unit="K"), record("new", unit="℃")]
        storage.save_rows_atomic(self.path, rows)
        original = self.path.read_bytes()
        self.assertEqual(self.service.definitions()[0]["unit"], "℃")
        self.assertEqual(self.path.read_bytes(), original)

    def retained_rows(self):
        return [record("old-only", name="旧設定")]+[record(str(i)) for i in range(90)]

    def test_deletion_keeps_definition_found_only_in_deleted_record(self):
        storage.save_rows_atomic(self.path, self.retained_rows())
        self.assertEqual(self.service.delete_candidates({"old-only"}), 1)
        self.assertEqual(len(history.group_records(self.service.load_rows())), 90)
        self.assertIn("旧設定", {d["setting_name"] for d in SettingsService(self.path).definitions()})

    def test_failed_catalog_save_blocks_deletion(self):
        storage.save_rows_atomic(self.path, self.retained_rows())
        original = self.path.read_bytes()
        with patch("app_service.catalog.save_definitions_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.delete_candidates({"old-only"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_stale_or_non_candidate_deletion_rejected(self):
        storage.save_rows_atomic(self.path, self.retained_rows())
        original = self.path.read_bytes()
        for ids in ({"0"}, set(), {"old-only", "0"}):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.service.delete_candidates(ids)
        self.assertEqual(self.path.read_bytes(), original)

    def test_no_candidate_delete_is_no_op(self):
        self.assertEqual(self.service.delete_candidates(set()), 0)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.service.catalog_path.exists())


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "settings.csv"
        self.rows = [dict(machine_name="架空機械A", setting_name="温度", unit="℃")]

    def test_roundtrip_and_bom(self):
        catalog.save_definitions_atomic(self.path, self.rows)
        self.path.write_bytes(b"\xef\xbb\xbf" + self.path.read_bytes())
        self.assertEqual(catalog.load_definitions(self.path), self.rows)

    def test_duplicate_or_missing_name_is_rejected(self):
        for rows in (self.rows * 2, [dict(machine_name="", setting_name="温度", unit="")]):
            with self.assertRaises(ValueError):
                catalog.save_definitions_atomic(self.path, rows)
        self.assertFalse(self.path.exists())

    def test_invalid_file_not_overwritten(self):
        self.path.write_bytes(b"bad\n")
        with self.assertRaises(ValueError):
            catalog.save_definitions_atomic(self.path, self.rows)
        self.assertEqual(self.path.read_bytes(), b"bad\n")

    def test_failure_before_replace_preserves_original(self):
        catalog.save_definitions_atomic(self.path, self.rows)
        original = self.path.read_bytes()
        for target in ("catalog.os.fsync", "catalog.os.replace", "catalog.csv.DictWriter.writerows"):
            with self.subTest(target=target), patch(target, side_effect=OSError("failure")):
                with self.assertRaises(OSError):
                    catalog.save_definitions_atomic(self.path, [])
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(list(self.path.parent.iterdir()), [self.path])


class ComparisonTests(unittest.TestCase):
    def test_numeric_equality_is_exact_and_supports_large_exponents(self):
        for before, after, expected in (("1", "1.0", "変更なし"), ("1e9999", "10e9998", "変更なし"),
                                        ("9007199254740992", "9007199254740993", "値変更")):
            with self.subTest(before=before):
                older, newer, result = compare_records([record("a", value=before), record("b", value=after)], "a", "b")
                self.assertEqual(result[0]["status"], expected)
                self.assertEqual(older[0]["record_id"], "a")
                self.assertEqual(newer[0]["record_id"], "b")

    def test_added_missing_and_unit_change(self):
        rows = [record("a", name="温度"), record("a", name="旧項目"),
                record("b", name="温度", unit="K"), record("b", name="新項目")]
        result = compare_records(rows, "a", "b")[2]
        self.assertEqual({r["setting_name"]: r["status"] for r in result},
                         {"温度": "単位変更", "旧項目": "項目なし", "新項目": "項目追加"})

    def test_invalid_pair(self):
        rows = [record("a"), record("b", machine="架空機械B")]
        for pair in (("a", "a"), ("a", "missing"), ("a", "b")):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                compare_records(rows, *pair)


if __name__ == "__main__":
    unittest.main()
