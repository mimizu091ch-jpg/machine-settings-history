import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import history
import storage


def sample(record_id="example-1"):
    return dict(record_id=record_id, recorded_at="2026-09-09 12:00:00", machine_name="架空機械A",
                setting_name="温度", value="-12.50", unit="℃", note='架空,備考 "引用"\n2行目')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "data" / "records.csv"

    def test_first_save_and_round_trip(self):
        self.assertEqual(storage.load_rows(self.path), [])
        self.assertFalse(self.path.parent.exists())
        storage.save_rows_atomic(self.path, [sample()])
        self.assertEqual(storage.load_rows(self.path), [sample()])
        with self.path.open(encoding="utf-8", newline="") as source:
            self.assertEqual(next(csv.reader(source)), list(history.FIELDS))

    def test_empty_history_header_only(self):
        storage.save_rows_atomic(self.path, [])
        self.assertEqual(storage.load_rows(self.path), [])

    def test_order_survives_append_reload_and_delete(self):
        rows = [sample("first"), sample("middle"), sample("last")]
        storage.save_rows_atomic(self.path, rows[:2])
        storage.save_rows_atomic(self.path, storage.load_rows(self.path) + rows[2:])
        self.assertEqual(storage.load_rows(self.path), rows)
        self.assertEqual([g[0]["record_id"] for g in history.group_records(storage.load_rows(self.path))], ["last", "middle", "first"])
        storage.save_rows_atomic(self.path, history.remove_records(storage.load_rows(self.path), {"middle"}))
        self.assertEqual(storage.load_rows(self.path), [rows[0], rows[2]])

    def test_bom_is_readable(self):
        storage.save_rows_atomic(self.path, [sample()])
        self.path.write_bytes(b"\xef\xbb\xbf" + self.path.read_bytes())
        self.assertEqual(storage.load_rows(self.path), [sample()])

    def test_bad_csv_is_not_overwritten(self):
        self.path.parent.mkdir()
        header = ",".join(history.FIELDS) + "\n"
        invalid = [b"", b"wrong,header\n", b"\xff", (header + "only-one-field\n").encode(),
                   (header + '"unclosed\n').encode(),
                   (header + "x,2026-09-09 12:00:00,A,S,1,,,extra\n").encode(),
                   (header + "x,2026-09-09 12:00:00,A,S,NaN,,\n").encode()]
        for content in invalid:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises((ValueError, csv.Error)):
                    storage.load_rows(self.path)
                with self.assertRaises((ValueError, csv.Error)):
                    storage.save_rows_atomic(self.path, [sample()])
                self.assertEqual(self.path.read_bytes(), content)

    def test_invalid_new_rows_preserve_existing(self):
        storage.save_rows_atomic(self.path, [sample()])
        original = self.path.read_bytes()
        bad = sample("bad")
        bad["value"] = "inf"
        with self.assertRaises(ValueError):
            storage.save_rows_atomic(self.path, [sample(), bad])
        self.assertEqual(self.path.read_bytes(), original)

    def test_read_permission_failure_is_not_empty_history(self):
        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                storage.load_rows(self.path)
            with patch("storage.tempfile.NamedTemporaryFile") as create:
                with self.assertRaises(PermissionError):
                    storage.save_rows_atomic(self.path, [sample()])
                create.assert_not_called()

    def assert_original_preserved(self, target):
        storage.save_rows_atomic(self.path, [sample()])
        original = self.path.read_bytes()
        with patch(target, side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                storage.save_rows_atomic(self.path, [sample(), sample("new")])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_temporary_creation_failure(self):
        self.assert_original_preserved("storage.tempfile.NamedTemporaryFile")

    def test_write_failure(self):
        self.assert_original_preserved("storage.csv.DictWriter.writerows")

    def test_fsync_failure(self):
        self.assert_original_preserved("storage.os.fsync")

    def test_replace_failure(self):
        self.assert_original_preserved("storage.os.replace")

    def test_flush_and_close_failures_preserve_existing(self):
        real_create = tempfile.NamedTemporaryFile
        for stage in ("flush", "close"):
            with self.subTest(stage=stage):
                storage.save_rows_atomic(self.path, [sample()])
                original = self.path.read_bytes()

                class FailingFile:
                    def __init__(self, *args, **kwargs):
                        self.file = real_create(*args, **kwargs)

                    def __getattr__(self, name):
                        return getattr(self.file, name)

                    def __enter__(self):
                        return self

                    def flush(self):
                        if stage == "flush":
                            raise OSError("flush failed")
                        self.file.flush()

                    def __exit__(self, *args):
                        self.file.close()
                        if stage == "close":
                            raise OSError("close failed")

                with patch("storage.tempfile.NamedTemporaryFile", FailingFile):
                    with self.assertRaises(OSError):
                        storage.save_rows_atomic(self.path, [sample("new")])
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_after_sync_and_close_in_same_directory(self):
        real_create = tempfile.NamedTemporaryFile
        real_replace = os.replace
        real_sync = os.fsync
        opened = []
        synced = []

        def create(*args, **kwargs):
            target = real_create(*args, **kwargs)
            opened.append(target)
            return target

        def sync(fd):
            synced.append(fd)
            real_sync(fd)

        def replace(source, destination):
            self.assertTrue(opened[0].closed)
            self.assertEqual(len(synced), 1)
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(storage.load_rows(source), [sample()])
            real_replace(source, destination)

        with patch("storage.tempfile.NamedTemporaryFile", side_effect=create), patch("storage.os.fsync", side_effect=sync), patch("storage.os.replace", side_effect=replace):
            storage.save_rows_atomic(self.path, [sample()])


if __name__ == "__main__":
    unittest.main()
