"""Real Tk interaction checks. Requires a display (Windows or Xvfb)."""

from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
    from tkinter import ttk
    from gui import SettingsApp
except ImportError:
    tk = None

from app_service import SettingsService
import storage


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@unittest.skipIf(tk is None, "Tkinter is not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            root = tk.Tk()
            root.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest(f"GUI display is unavailable: {error}")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "records.csv"
        shutil.copyfile(Path(__file__).parent / "examples" / "records.csv", self.path)
        self.service = SettingsService(self.path)
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.app = SettingsApp(self.root, self.service, demo=True)
        self.root.update()

    def tearDown(self):
        self.assertEqual(self.callback_errors, [])

    def select(self, row, label):
        index = next(i for i, pair in enumerate(row.option_items) if pair[1] == label)
        row.combo.current(index)
        row.combo.event_generate("<<ComboboxSelected>>")
        self.root.update()

    def button(self, parent, text):
        return next(w for w in descendants(parent)
                    if isinstance(w, ttk.Button) and w.cget("text") == text)

    def test_history_displays_values_without_detail_action(self):
        labels = [w.cget("text") for w in descendants(self.app.list_body)
                  if isinstance(w, ttk.Label)]
        self.assertIn("120 回/分", labels)
        self.assertIn("0.4 MPa", labels)
        self.assertEqual(self.app.list_count.get(), "3件の記録（新しい順）")

    def test_new_definition_confirmation_save_and_reselection(self):
        self.app.open_entry()
        first = self.app.setting_rows[0]
        self.select(first, "温度")
        self.assertEqual(first.unit.get(), "℃")
        first.value.set("82")
        self.app.add_setting()
        second = self.app.setting_rows[1]
        self.assertNotIn(("existing", "温度"), second.option_items)
        self.select(second, "＋ 新しい項目を追加")
        second.name.set("待機時間")
        second.unit.set("秒")
        second.value.set("30")
        original = self.path.read_bytes()
        self.app.preview_record()
        self.root.update()
        self.assertEqual(self.path.read_bytes(), original)
        self.button(self.app.confirm_window, "この内容で登録").invoke()
        self.root.update()
        self.assertEqual(len(self.service.load_rows()), 10)
        row = self.app.setting_rows[0]
        self.select(row, "待機時間")
        self.assertEqual(row.unit.get(), "秒")
        self.assertEqual(row.value.get(), "")

    def test_invalid_input_and_cancel_do_not_save(self):
        self.app.preview_record()
        self.assertTrue(self.app.entry_error.get())
        row = self.app.setting_rows[0]
        self.select(row, "温度")
        row.value.set("85")
        original = self.path.read_bytes()
        self.app.preview_record()
        dialog = self.app.confirm_window
        self.button(dialog, "入力に戻る").invoke()
        self.root.update()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(row.value.get(), "85")
        # Global wheel handling must not retain callbacks to a destroyed dialog.
        self.root.event_generate("<MouseWheel>", delta=-120, x=30, y=250)
        self.root.update()

    def test_machine_switch_preserves_draft_and_isolates_options(self):
        row = self.app.setting_rows[0]
        self.select(row, "圧力")
        row.value.set("0.42")
        self.app.note.insert("1.0", "架空のメモ")
        self.app.machine.set("架空機械B")
        self.app.change_machine()
        self.assertNotIn(("existing", "圧力"), self.app.setting_rows[0].option_items)
        self.assertEqual(self.app.note.get("1.0", "end-1c"), "")
        self.app.machine.set("架空機械A")
        self.app.change_machine()
        self.assertEqual(self.app.setting_rows[0].value.get(), "0.42")
        self.assertEqual(self.app.note.get("1.0", "end-1c"), "架空のメモ")

    def test_search_and_comparison(self):
        self.app.filter_machine.set("架空機械A")
        self.app.search()
        self.assertEqual(self.app.list_count.get(), "2件の記録（新しい順）")
        for flag in self.app.check_vars.values():
            flag.set(True)
        self.app.selection_changed()
        self.assertNotIn("disabled", self.app.compare_button.state())
        self.app.compare_button.invoke()
        self.root.update()
        labels = [w.cget("text") for w in descendants(self.app.compare_body)
                  if isinstance(w, ttk.Label)]
        self.assertIn("2項目に変更があります。", labels)

    def test_partial_save_warns_but_clears_committed_form(self):
        row = self.app.setting_rows[0]
        self.select(row, "温度")
        row.value.set("82")
        self.app.preview_record()
        with patch("app_service.catalog.save_definitions_atomic", side_effect=OSError("failure")), \
                patch("gui.messagebox.showwarning") as warning:
            self.button(self.app.confirm_window, "この内容で登録").invoke()
        self.root.update()
        warning.assert_called_once()
        self.assertEqual(len(self.service.load_rows()), 9)
        self.assertEqual(self.app.setting_rows[0].value.get(), "")

    def test_retention_requires_checkbox_and_preserves_definitions(self):
        rows = [dict(record_id=str(i), recorded_at="2026-10-04 12:00:00", machine_name="架空機械A",
                     setting_name="古い項目" if i == 0 else "温度", value="1", unit="", note="") for i in range(91)]
        storage.save_rows_atomic(self.path, rows)
        self.app.refresh_data()
        original = self.path.read_bytes()
        self.app.show_candidates()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        button = self.button(dialog, "確認した候補を削除")
        self.assertIn("disabled", button.state())
        button.invoke()
        self.assertEqual(self.path.read_bytes(), original)
        dialog.destroy()
        self.assertEqual(self.path.read_bytes(), original)
        self.app.show_candidates()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel))
        next(w for w in descendants(dialog) if isinstance(w, ttk.Checkbutton)).invoke()
        self.button(dialog, "確認した候補を削除").invoke()
        self.root.update()
        self.assertEqual(len(self.service.load_rows()), 90)
        self.assertIn("古い項目", {r["setting_name"] for r in self.service.definitions()})


if __name__ == "__main__":
    unittest.main()
