"""Desktop interface. Run `py gui.py` or `py gui.py --demo` on Windows."""

import argparse
import csv
from pathlib import Path
import shutil
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
import tkinter.font as tkfont

from app_service import SettingsService
from comparison import compare_records
import history

BASE = Path(__file__).resolve().parent
NEW_ITEM = "＋ 新しい項目を追加"
ERRORS = (OSError, ValueError, csv.Error)


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background="#f4f6fa")
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.content = ttk.Frame(self.canvas, padding=4)
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))

    def clear(self):
        for child in self.content.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class SettingRow(ttk.Frame):
    """One selected setting. Only a new setting exposes a name input."""
    def __init__(self, app, parent, state=None):
        super().__init__(parent, padding=(0, 10))
        self.app = app
        self.mode = ""
        self.existing_name = ""
        self.choice = tk.StringVar()
        self.name = tk.StringVar()
        self.value = tk.StringVar()
        self.unit = tk.StringVar()
        self.option_items = []
        for col, text in enumerate(("設定項目", "設定値", "単位")):
            ttk.Label(self, text=text).grid(row=0, column=col, sticky="w", padx=(0, 12))
        self.combo = ttk.Combobox(self, textvariable=self.choice, state="readonly", width=24)
        self.combo.grid(row=1, column=0, sticky="ew", padx=(0, 12))
        self.combo.bind("<<ComboboxSelected>>", self.selected)
        self.value_entry = ttk.Entry(self, textvariable=self.value, width=15, state="disabled")
        self.value_entry.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        self.unit_entry = ttk.Entry(self, textvariable=self.unit, width=12, state="readonly")
        self.unit_entry.grid(row=1, column=2, sticky="ew", padx=(0, 12))
        ttk.Button(self, text="行を削除", command=lambda: app.remove_setting(self)).grid(row=1, column=3)
        self.new_frame = ttk.Frame(self)
        self.new_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0), padx=(0, 12))
        ttk.Label(self.new_frame, text="新しい項目名").pack(anchor="w")
        ttk.Entry(self.new_frame, textvariable=self.name).pack(fill="x")
        self.new_frame.grid_remove()
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.columnconfigure(2, weight=1)
        if state:
            self.mode = state.get("mode", "")
            if self.mode == "existing":
                self.existing_name = state["setting_name"]
            else:
                self.name.set(state.get("setting_name", ""))
            self.value.set(state.get("value", ""))
            self.unit.set(state.get("unit", ""))
            self.apply_mode()

    def refresh_choices(self):
        used = {row.existing_name for row in self.app.setting_rows
                if row is not self and row.mode == "existing"}
        names = [row["setting_name"] for row in self.app.machine_definitions()
                 if row["setting_name"] not in used]
        self.option_items = [("", "項目を選択"), *[("existing", name) for name in names], ("new", NEW_ITEM)]
        self.combo["values"] = [name for mode, name in self.option_items]
        target = (self.mode, self.existing_name if self.mode == "existing" else NEW_ITEM if self.mode == "new" else "項目を選択")
        if target in self.option_items:
            self.combo.current(self.option_items.index(target))
        else:
            self.combo.current(0)

    def selected(self, event=None):
        index = self.combo.current()
        if index < 0:
            return
        self.mode, label = self.option_items[index]
        self.existing_name = label if self.mode == "existing" else ""
        self.name.set("")
        self.value.set("")
        definition = next((row for row in self.app.machine_definitions()
                           if row["setting_name"] == self.existing_name), None)
        self.unit.set(definition["unit"] if definition else "")
        self.apply_mode()
        self.app.refresh_choices()

    def apply_mode(self):
        if self.mode == "new":
            self.new_frame.grid()
        else:
            self.new_frame.grid_remove()
        self.unit_entry.configure(state="normal" if self.mode == "new" else "readonly")
        self.value_entry.configure(state="normal" if self.mode else "disabled")

    def snapshot(self):
        return dict(mode=self.mode,
                    setting_name=self.existing_name if self.mode == "existing" else self.name.get(),
                    value=self.value.get(), unit=self.unit.get())


class SettingsApp:
    def __init__(self, root, service, demo=False):
        self.root, self.service, self.demo = root, service, demo
        self.rows, self.definitions = [], []
        self.setting_rows, self.drafts = [], {}
        self.active_machine = ""
        self.selected_ids = set()
        self.check_vars = {}
        self.catalog_error = ""
        self.confirm_window = None
        root.title("機械設定 履歴管理" + (" — デモ" if demo else ""))
        root.geometry("1080x780")
        root.minsize(820, 600)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        available = tkfont.families(root)
        family = next((name for name in ("Yu Gothic UI", "Meiryo", "Noto Sans CJK JP") if name in available), "TkDefaultFont")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=family, size=10)
        style.configure("TFrame", background="#f4f6fa")
        style.configure("TLabel", background="#f4f6fa", foreground="#203044")
        style.configure("Title.TLabel", font=(family, 18, "bold"))
        style.configure("Heading.TLabel", font=(family, 12, "bold"))
        style.configure("Muted.TLabel", foreground="#526377")
        style.configure("Error.TLabel", foreground="#a32c35")
        style.configure("TButton", padding=(10, 7))
        style.configure("TNotebook.Tab", padding=(18, 10))
        style.configure("Treeview", rowheight=32, font=(family, 10))
        style.configure("Treeview.Heading", font=(family, 10, "bold"))
        shell = ttk.Frame(root, padding=18)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="機械設定 履歴管理", style="Title.TLabel").pack(anchor="w")
        ttk.Label(shell, text="デモ・架空データ（終了時に破棄）" if demo else "機械ごとの設定を記録・検索・比較",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 12))
        self.tabs = ttk.Notebook(shell)
        self.tabs.pack(fill="both", expand=True)
        self.list_page, self.entry_page, self.compare_page = [ttk.Frame(self.tabs, padding=14) for _ in range(3)]
        for page, title in ((self.list_page, "履歴一覧・検索"), (self.entry_page, "記録登録"), (self.compare_page, "設定比較")):
            self.tabs.add(page, text=title)
        self.status = tk.StringVar()
        ttk.Label(shell, textvariable=self.status, style="Muted.TLabel", wraplength=960).pack(anchor="w", pady=(8, 0))
        self._build_list()
        self._build_entry()
        self.compare_body = ScrollFrame(self.compare_page)
        self.compare_body.pack(fill="both", expand=True)
        ttk.Label(self.compare_body.content, text="履歴一覧で同じ機械の記録を2件選び、比較してください。").pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_all(event, self.scroll_at_pointer)
        self.refresh_data()

    def scroll_at_pointer(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if isinstance(widget, ScrollFrame):
                direction = -1 if getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0 else 1
                widget.canvas.yview_scroll(direction * 3, "units")
                return
            widget = getattr(widget, "master", None)

    def _build_list(self):
        filters = ttk.Frame(self.list_page)
        filters.pack(fill="x", pady=(0, 12))
        self.filter_machine, self.filter_start, self.filter_end = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for col, text in enumerate(("機械名（空欄＝すべて）", "開始日 YYYY-MM-DD", "終了日 YYYY-MM-DD")):
            ttk.Label(filters, text=text).grid(row=0, column=col, sticky="w")
            filters.columnconfigure(col, weight=1)
        self.filter_combo = ttk.Combobox(filters, textvariable=self.filter_machine, width=22)
        self.filter_combo.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Entry(filters, textvariable=self.filter_start, width=17).grid(row=1, column=1, sticky="ew", padx=(0, 10))
        ttk.Entry(filters, textvariable=self.filter_end, width=17).grid(row=1, column=2, sticky="ew", padx=(0, 10))
        ttk.Button(filters, text="検索", command=self.search).grid(row=1, column=3)
        self.list_count = tk.StringVar()
        controls = ttk.Frame(self.list_page)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, textvariable=self.list_count).pack(side="left")
        ttk.Button(controls, text="＋ 記録を追加", command=self.open_entry).pack(side="right")
        self.list_body = ScrollFrame(self.list_page)
        self.list_body.pack(fill="both", expand=True)
        actions = ttk.Frame(self.list_page)
        actions.pack(fill="x", pady=(10, 0))
        self.selection_text = tk.StringVar(value="0件を選択中")
        ttk.Label(actions, textvariable=self.selection_text).pack(side="left")
        self.compare_button = ttk.Button(actions, text="選択した2件を比較", command=self.show_comparison, state="disabled")
        self.compare_button.pack(side="right")
        ttk.Button(actions, text="削除候補を確認", command=self.show_candidates).pack(side="right", padx=10)

    def _build_entry(self):
        self.entry_body = ScrollFrame(self.entry_page)
        self.entry_body.pack(fill="both", expand=True)
        body = self.entry_body.content
        top = ttk.Frame(body)
        top.pack(fill="x", pady=(0, 14))
        self.machine, self.stamp = tk.StringVar(), tk.StringVar()
        ttk.Label(top, text="機械名（選択または新規入力）").grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="記録日時（空欄＝保存時の現在日時）").grid(row=0, column=1, sticky="w")
        self.machine_combo = ttk.Combobox(top, textvariable=self.machine, width=27)
        self.machine_combo.grid(row=1, column=0, sticky="ew", padx=(0, 18))
        self.machine_combo.bind("<<ComboboxSelected>>", self.change_machine)
        self.machine_combo.bind("<FocusOut>", self.change_machine)
        ttk.Entry(top, textvariable=self.stamp, width=26).grid(row=1, column=1, sticky="ew")
        ttk.Label(top, text="YYYY-MM-DD HH:MM:SS", style="Muted.TLabel").grid(row=2, column=1, sticky="w")
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        self.template_info = tk.StringVar()
        ttk.Label(body, textvariable=self.template_info, style="Muted.TLabel").pack(anchor="w")
        self.settings_frame = ttk.Frame(body)
        self.settings_frame.pack(fill="x")
        ttk.Button(body, text="＋ 入力する項目を増やす", command=self.add_setting).pack(anchor="w", pady=8)
        ttk.Label(body, text="備考・変更理由（任意）").pack(anchor="w", pady=(12, 4))
        self.note = tk.Text(body, height=4, wrap="word", relief="solid", borderwidth=1, font="TkTextFont")
        self.note.pack(fill="x")
        self.entry_error = tk.StringVar()
        ttk.Label(self.entry_page, textvariable=self.entry_error, style="Error.TLabel", wraplength=920).pack(anchor="w", pady=(8, 4))
        actions = ttk.Frame(self.entry_page)
        actions.pack(fill="x")
        ttk.Button(actions, text="入力内容を確認", command=self.preview_record).pack(side="left")
        ttk.Button(actions, text="一覧へ戻る", command=lambda: self.tabs.select(self.list_page)).pack(side="left", padx=10)

    def refresh_data(self):
        try:
            self.rows = self.service.load_rows()
        except ERRORS as error:
            self.status.set(f"履歴を読み込めません: {error}")
            return False
        try:
            self.definitions = self.service.definitions(self.rows)
            self.catalog_error = ""
        except ERRORS as error:
            self.definitions = []
            self.catalog_error = str(error)
            self.status.set(f"項目定義を確認してください。登録・削除は停止します: {error}")
        names = sorted({r["machine_name"] for r in self.rows} | {r["machine_name"] for r in self.definitions})
        self.filter_combo["values"] = ["", *names]
        self.machine_combo["values"] = names
        if not self.machine.get() and names:
            self.machine.set(names[0])
        self.change_machine()
        self.refresh_choices()
        self.search(reload=False)
        return True

    def search(self, reload=True):
        try:
            rows = self.service.load_rows() if reload else self.rows
            groups = history.search_records(rows, self.filter_machine.get(), self.filter_start.get(), self.filter_end.get())
        except ERRORS as error:
            messagebox.showerror("検索できません", str(error), parent=self.root)
            return
        self.rows = rows
        self.selected_ids.clear()
        self.check_vars.clear()
        self.list_body.clear()
        self.list_count.set(f"{len(groups)}件の記録（新しい順）")
        if not groups:
            ttk.Label(self.list_body.content, text="該当する記録はありません。").pack(anchor="w", pady=20)
        for group in groups:
            self._record_card(group)
        self.selection_changed()

    def _record_card(self, group):
        first = group[0]
        card = ttk.LabelFrame(self.list_body.content, text=f"{first['machine_name']}  ·  {first['recorded_at']}", padding=12)
        card.pack(fill="x", pady=(0, 12))
        flag = tk.BooleanVar(value=False)
        self.check_vars[first["record_id"]] = flag
        ttk.Checkbutton(card, text="比較対象に選択", variable=flag,
                        command=self.selection_changed).pack(anchor="w")
        values = ttk.Frame(card)
        values.pack(fill="x", pady=8)
        cells = []
        for row in group:
            cell = ttk.Frame(values, padding=(0, 5, 15, 5))
            ttk.Label(cell, text=row["setting_name"], style="Muted.TLabel", wraplength=200).pack(anchor="w")
            ttk.Label(cell, text=f"{row['value']} {row['unit']}", style="Heading.TLabel", wraplength=200).pack(anchor="w")
            cells.append(cell)
        def reflow(event):
            columns = max(1, min(4, event.width // 220))
            for index, cell in enumerate(cells):
                cell.grid(row=index // columns, column=index % columns, sticky="nw")
            for col in range(4):
                values.columnconfigure(col, weight=1 if col < columns else 0)
        values.bind("<Configure>", reflow)
        for index, cell in enumerate(cells):
            cell.grid(row=index // 3, column=index % 3, sticky="nw")
        notes = list(dict.fromkeys(row["note"] for row in group if row["note"]))
        note = ttk.Label(card, text="備考：" + (" / ".join(notes) if notes else "なし"), style="Muted.TLabel", wraplength=850)
        note.pack(anchor="w", fill="x")
        card.bind("<Configure>", lambda e: note.configure(wraplength=max(200, e.width - 30)))

    def selection_changed(self):
        self.selected_ids = {key for key, flag in self.check_vars.items() if flag.get()}
        text = f"{len(self.selected_ids)}件を選択中"
        machines = {r["machine_name"] for r in self.rows if r["record_id"] in self.selected_ids}
        valid = len(self.selected_ids) == 2 and len(machines) == 1
        if len(self.selected_ids) == 2 and not valid:
            text += " — 同じ機械を選んでください"
        elif len(self.selected_ids) > 2:
            text += " — 2件に絞ってください"
        self.selection_text.set(text)
        self.compare_button.configure(state="normal" if valid else "disabled")

    def machine_definitions(self):
        return [row for row in self.definitions if row["machine_name"] == self.active_machine]

    def change_machine(self, event=None):
        machine = self.machine.get().strip()
        if machine == self.active_machine and self.setting_rows:
            return
        if self.active_machine or self.setting_rows:
            self.drafts[self.active_machine] = dict(entries=[row.snapshot() for row in self.setting_rows],
                                                   stamp=self.stamp.get(), note=self.note.get("1.0", "end-1c"))
        self.active_machine = machine
        for row in self.setting_rows:
            row.destroy()
        self.setting_rows = []
        draft = self.drafts.get(machine, {})
        self.stamp.set(draft.get("stamp", ""))
        self.note.delete("1.0", "end")
        self.note.insert("1.0", draft.get("note", ""))
        for state in draft.get("entries", [{}]):
            self.add_setting(state)
        self.template_info.set(f"{machine or '機械を指定してください'} · 登録済み{len(self.machine_definitions())}項目から選択")
        self.entry_error.set("")

    def add_setting(self, state=None):
        row = SettingRow(self, self.settings_frame, state)
        self.setting_rows.append(row)
        row.pack(fill="x")
        self.refresh_choices()

    def remove_setting(self, row):
        self.setting_rows.remove(row)
        row.destroy()
        self.refresh_choices()

    def refresh_choices(self):
        for row in self.setting_rows:
            row.refresh_choices()

    def open_entry(self):
        self.tabs.select(self.entry_page)

    def preview_record(self):
        self.change_machine()
        machine, stamp = self.machine.get().strip(), self.stamp.get().strip()
        entries = [row.snapshot() for row in self.setting_rows]
        note = self.note.get("1.0", "end-1c")
        try:
            settings = self.service.prepare_settings(machine, entries, note)
            if stamp:
                history.parse_datetime(stamp)
        except ERRORS as error:
            self.entry_error.set(str(error))
            return
        self.entry_error.set("")
        dialog = self._dialog("登録内容の確認")
        self.confirm_window = dialog
        body = ScrollFrame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Label(body.content, text=machine, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body.content, text=stamp or "保存時の現在日時").pack(anchor="w", pady=(0, 12))
        for setting in settings:
            ttk.Label(body.content, text=f"{setting['setting_name']}：{setting['value']} {setting['unit']}", wraplength=620).pack(anchor="w", pady=5)
        ttk.Label(body.content, text="備考：" + (note.strip() or "なし"), wraplength=620).pack(anchor="w", pady=12)
        new_count = sum(entry["mode"] == "new" for entry in entries)
        if new_count:
            ttk.Label(body.content, text=f"新しい{new_count}項目は次回から選択できます。").pack(anchor="w")
        bar = ttk.Frame(dialog, padding=16)
        bar.pack(fill="x")
        def save():
            button.configure(state="disabled")
            try:
                result = self.service.save_record(machine, stamp, entries, note)
            except ERRORS as error:
                button.configure(state="normal")
                messagebox.showerror("保存できません", str(error), parent=dialog)
                return
            dialog.destroy()
            self.confirm_window = None
            # Clear the committed draft so reopening cannot resubmit old values.
            self.drafts.pop(machine, None)
            for row in self.setting_rows:
                row.destroy()
            self.setting_rows = []
            self.active_machine = ""
            self.stamp.set("")
            self.note.delete("1.0", "end")
            self.filter_machine.set("")
            self.filter_start.set("")
            self.filter_end.set("")
            self.refresh_data()
            self.tabs.select(self.list_page)
            self.status.set(f"1件の記録を保存しました。ID: {result.record_id}")
            if result.catalog_warning:
                messagebox.showwarning("記録は保存済みです", result.catalog_warning, parent=self.root)
        button = ttk.Button(bar, text="この内容で登録", command=save)
        button.pack(side="left")
        ttk.Button(bar, text="入力に戻る", command=dialog.destroy).pack(side="left", padx=10)

    def _dialog(self, title):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("720x520")
        dialog.minsize(580, 400)
        dialog.transient(self.root)
        dialog.grab_set()
        return dialog

    def show_comparison(self):
        if len(self.selected_ids) != 2:
            return
        try:
            older, newer, result = compare_records(self.service.load_rows(), *self.selected_ids)
        except ERRORS as error:
            messagebox.showerror("比較できません", str(error), parent=self.root)
            return
        self.compare_body.clear()
        parent = self.compare_body.content
        ttk.Label(parent, text=older[0]["machine_name"], style="Heading.TLabel").pack(anchor="w")
        for caption, group in (("基準の記録", older), ("比較する記録", newer)):
            notes = " / ".join(dict.fromkeys(r["note"] for r in group if r["note"])) or "なし"
            ttk.Label(parent, text=f"{caption}：{group[0]['recorded_at']}\n備考：{notes}", wraplength=850).pack(anchor="w", pady=8)
        ttk.Label(parent, text="黄色の背景と【変更あり】の見出しで変更箇所を示します。単位の自動換算は行いません。", style="Muted.TLabel").pack(anchor="w", pady=8)
        heading_font = tkfont.nametofont("TkDefaultFont").actual()
        heading_font = (heading_font["family"], heading_font["size"], "bold")
        for item in result:
            changed = item["status"] != "変更なし"
            background = "#fff3cd" if changed else "#ffffff"
            foreground = "#664d03" if changed else "#203044"
            border = "#b58105" if changed else "#cbd5e1"
            # Native Tk colors avoid platform-theme overrides on comparison cards.
            panel = tk.Frame(parent, background=background, highlightbackground=border,
                             highlightthickness=2 if changed else 1, padx=12, pady=12)
            panel.pack(fill="x", pady=(0, 8))
            prefix = "【変更あり】" if changed else ""
            tk.Label(panel, text=f"{prefix}{item['setting_name']}  ·  {item['status']}",
                     background=background, foreground=foreground, font=heading_font,
                     anchor="w", wraplength=760, justify="left").grid(
                         row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
            for col, key in enumerate(("before", "after")):
                row = item[key]
                value = f"{row['value']} {row['unit']}" if row else "—（項目なし）"
                tk.Label(panel, text=("基準：" if col == 0 else "比較：") + value,
                         wraplength=360, justify="left", background=background,
                         foreground=foreground).grid(row=1, column=col, sticky="w")
                panel.columnconfigure(col, weight=1)
        count = sum(item["status"] != "変更なし" for item in result)
        ttk.Label(parent, text=f"{count}項目に変更があります。", style="Heading.TLabel").pack(anchor="w", pady=12)
        self.tabs.select(self.compare_page)

    def show_candidates(self):
        try:
            rows = self.service.load_rows()
            candidates = history.select_deletion_candidates(rows)
        except ERRORS as error:
            messagebox.showerror("確認できません", str(error), parent=self.root)
            return
        if not candidates:
            messagebox.showinfo("削除候補", "候補は0件です。各機械の最新90件を保持します。", parent=self.root)
            return
        dialog = self._dialog("削除候補の確認")
        body = ScrollFrame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        ttk.Label(body.content, text=f"各機械の91件目以降：{len(candidates)}件", style="Heading.TLabel").pack(anchor="w")
        for group in candidates:
            first = group[0]
            ttk.Label(body.content, text=f"{first['machine_name']} · {first['recorded_at']} · {len(group)}項目\nID: {first['record_id']}", wraplength=640).pack(anchor="w", pady=8)
        approved = tk.BooleanVar(value=False)
        bar = ttk.Frame(dialog, padding=16)
        bar.pack(fill="x")
        def remove():
            if not approved.get():
                return
            try:
                count = self.service.delete_candidates({g[0]["record_id"] for g in candidates})
            except ERRORS as error:
                messagebox.showerror("削除できません", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh_data()
            self.status.set(f"確認した{count}件を削除しました。項目の選択肢は保持しています。")
        button = ttk.Button(bar, text="確認した候補を削除", state="disabled", command=remove)
        ttk.Checkbutton(bar, text="上の候補を削除することを確認しました", variable=approved,
                        command=lambda: button.configure(state="normal" if approved.get() else "disabled")).pack(anchor="w", pady=(0, 10))
        button.pack(side="left")
        ttk.Button(bar, text="キャンセル", command=dialog.destroy).pack(side="left", padx=10)

    def close(self):
        states = [row.snapshot() for row in self.setting_rows]
        changed = any(row["mode"] or row["value"] for row in states) or self.note.get("1.0", "end-1c").strip() or self.stamp.get().strip()
        changed = changed or any(any(r.get("mode") or r.get("value") for r in draft.get("entries", []))
                                 or draft.get("note", "").strip() or draft.get("stamp", "").strip()
                                 for draft in self.drafts.values())
        if changed and not messagebox.askyesno("終了の確認", "未保存の入力があります。保存せず終了しますか？", parent=self.root):
            return
        self.root.destroy()


def run():
    parser = argparse.ArgumentParser(description="機械設定 履歴管理")
    parser.add_argument("--demo", action="store_true", help="架空データで起動。変更は終了時に破棄")
    parser.add_argument("--data-dir", type=Path, default=BASE / "data", help="保存先フォルダー")
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix="machine-settings-demo-") if args.demo else None
    try:
        data_dir = Path(temporary.name) if temporary else args.data_dir
        if args.demo:
            shutil.copyfile(BASE / "examples" / "records.csv", data_dir / "records.csv")
        root = tk.Tk()
        SettingsApp(root, SettingsService(data_dir / "records.csv"), demo=args.demo)
        root.mainloop()
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    run()
