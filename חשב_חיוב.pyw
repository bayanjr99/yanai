#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
One-click billing calculator — Yanai Personnel.
Double-click this file (or run.bat) to open the billing window.
No command line needed.
"""
import glob, os, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

BASE = Path(__file__).parent
DATA = BASE / "data"
OUT  = BASE / "output"


class BillingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("מערכת חיוב | ינאי פרסונל")
        self.geometry("680x540")
        self.resizable(True, True)
        self.minsize(500, 420)

        self._billing_path = None
        self._issues_path  = None

        self.pdf_var  = tk.StringVar(value=str(DATA / "hours.pdf"))
        self.agr_var  = tk.StringVar(value=str(DATA / "agreements.xlsx"))
        self.cost_var = tk.StringVar(value=str(DATA / "employees_cost.xlsx"))

        self._build_ui()
        self._check_defaults()

    # ------------------------------------------------------------------ build
    def _build_ui(self):
        s = ttk.Style()
        try:
            s.theme_use("vista")
        except Exception:
            pass

        # ── title ──
        ttk.Label(
            self,
            text="💰  מערכת חיוב — ינאי פרסונל",
            font=("Segoe UI", 15, "bold"),
        ).pack(pady=(18, 6))
        ttk.Label(
            self,
            text="בחר קבצים, לחץ חשב — הדוח יפתח אוטומטית.",
            font=("Segoe UI", 10),
            foreground="#555",
        ).pack(pady=(0, 14))

        # ── file pickers ──
        pf = ttk.LabelFrame(self, text="  קבצי קלט  ", padding=(14, 10))
        pf.pack(fill=tk.X, padx=16, pady=(0, 10))

        rows = [
            ("📄  קובץ שעות (PDF):",    self.pdf_var,  [("PDF", "*.pdf")]),
            ("📋  הסכמים (Excel):",     self.agr_var,  [("Excel", "*.xlsx *.xls")]),
            ("💼  עלות מעביד (Excel):", self.cost_var, [("Excel", "*.xlsx *.xls")]),
        ]
        for i, (lbl, var, ft) in enumerate(rows):
            ttk.Label(pf, text=lbl, width=24, anchor="e").grid(
                row=i, column=0, sticky="e", padx=(0, 8), pady=5)
            ent = ttk.Entry(pf, textvariable=var, width=46)
            ent.grid(row=i, column=1, sticky="ew", pady=5)
            ttk.Button(
                pf, text="…", width=3,
                command=lambda v=var, f=ft: self._browse(v, f),
            ).grid(row=i, column=2, padx=(6, 0))
        pf.columnconfigure(1, weight=1)

        # ── run button ──
        self._run_btn = ttk.Button(
            self, text="    🚀  חשב חיוב    ",
            command=self._start,
        )
        self._run_btn.pack(pady=10, ipadx=8, ipady=8)

        # ── progress bar ──
        self._progress = ttk.Progressbar(self, mode="indeterminate", length=300)
        self._progress.pack(pady=(0, 8))

        # ── log ──
        lf = ttk.LabelFrame(self, text="  יומן  ", padding=6)
        lf.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))
        self._logbox = scrolledtext.ScrolledText(
            lf, height=7, state="disabled",
            font=("Consolas", 9), bg="#f8f8f8", wrap=tk.WORD,
        )
        self._logbox.pack(fill=tk.BOTH, expand=True)
        self._logbox.tag_config("ok",    foreground="#006600")
        self._logbox.tag_config("err",   foreground="#bb0000")
        self._logbox.tag_config("info",  foreground="#333333")
        self._logbox.tag_config("warn",  foreground="#994400")

        # ── result buttons ──
        br = ttk.Frame(self)
        br.pack(pady=(0, 14))
        self._btn_billing = ttk.Button(
            br, text="📂  פתח דוח חיוב",
            command=self._open_billing, state="disabled",
        )
        self._btn_billing.pack(side=tk.LEFT, padx=8, ipadx=6, ipady=4)
        self._btn_issues = ttk.Button(
            br, text="⚠️  פתח דוח חריגים",
            command=self._open_issues, state="disabled",
        )
        self._btn_issues.pack(side=tk.LEFT, padx=8, ipadx=6, ipady=4)

    # --------------------------------------------------------------- helpers
    def _check_defaults(self):
        """Highlight missing default files."""
        for var in (self.pdf_var, self.agr_var, self.cost_var):
            if not Path(var.get()).exists():
                self._write(f"⚠  לא נמצא: {var.get()}", "warn")

    def _browse(self, var, filetypes):
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)

    def _write(self, text: str, tag: str = "info"):
        self._logbox.configure(state="normal")
        self._logbox.insert(tk.END, text + "\n", tag)
        self._logbox.see(tk.END)
        self._logbox.configure(state="disabled")
        self.update_idletasks()

    def _clear_log(self):
        self._logbox.configure(state="normal")
        self._logbox.delete("1.0", tk.END)
        self._logbox.configure(state="disabled")

    # ------------------------------------------------------------------ run
    def _start(self):
        for label, path in [
            ("קובץ שעות",    self.pdf_var.get()),
            ("הסכמים",       self.agr_var.get()),
            ("עלות מעביד",   self.cost_var.get()),
        ]:
            if not Path(path).exists():
                messagebox.showerror("קובץ חסר", f"הקובץ לא נמצא:\n{path}\n\nלחץ '…' לבחירת הקובץ.")
                return

        self._run_btn.configure(state="disabled")
        self._btn_billing.configure(state="disabled")
        self._btn_issues.configure(state="disabled")
        self._billing_path = self._issues_path = None
        self._clear_log()
        self._progress.start(12)

        threading.Thread(target=self._pipeline, daemon=True).start()

    def _pipeline(self):
        session = tempfile.mkdtemp(prefix="billing_")
        try:
            data_dir = Path(session) / "data"
            data_dir.mkdir()

            shutil.copy(self.pdf_var.get(),  data_dir / "hours.pdf")
            shutil.copy(self.agr_var.get(),  data_dir / "agreements.xlsx")
            shutil.copy(self.cost_var.get(), data_dir / "employees_cost.xlsx")

            self.after(0, lambda: self._write("🔄  מחשב חיוב..."))

            res = subprocess.run(
                [sys.executable, str(BASE / "main.py"), session],
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(BASE),
            )

            for line in res.stdout.splitlines():
                self.after(0, lambda l=line: self._write(l))

            if res.stderr:
                # Filter out expected PDF parser warnings (not real errors)
                errors = [l for l in res.stderr.splitlines()
                          if l.strip() and "no daily rows" not in l
                          and "parsed" not in l.lower()]
                for line in errors[:15]:
                    self.after(0, lambda l=line: self._write(l, "err"))

            if res.returncode == 0:
                OUT.mkdir(exist_ok=True)

                billing_files = sorted(
                    glob.glob(str(Path(session) / "output" / "final_*.xlsx")))
                issues_files  = sorted(
                    glob.glob(str(Path(session) / "output" / "issues_*.xlsx")))

                if billing_files:
                    dest = OUT / Path(billing_files[-1]).name
                    shutil.copy(billing_files[-1], dest)
                    self._billing_path = dest

                if issues_files:
                    dest = OUT / Path(issues_files[-1]).name
                    shutil.copy(issues_files[-1], dest)
                    self._issues_path = dest

                name = self._billing_path.name if self._billing_path else ""
                self.after(0, lambda: self._write(f"\n✅  הדוח מוכן:  {name}", "ok"))
                self.after(0, lambda: self._btn_billing.configure(state="normal"))

                if self._issues_path:
                    self.after(0, lambda: self._btn_issues.configure(state="normal"))

                # Auto-open the billing report
                if self._billing_path:
                    self.after(500, self._open_billing)

            else:
                self.after(0, lambda: self._write(
                    "\n❌  החישוב נכשל — ראה פירוט ביומן", "err"))

        except Exception as exc:
            self.after(0, lambda e=str(exc): self._write(f"שגיאה: {e}", "err"))

        finally:
            shutil.rmtree(session, ignore_errors=True)
            self.after(0, self._progress.stop)
            self.after(0, lambda: self._run_btn.configure(state="normal"))

    # --------------------------------------------------------------- outputs
    def _open_billing(self):
        if self._billing_path and self._billing_path.exists():
            os.startfile(str(self._billing_path))

    def _open_issues(self):
        if self._issues_path and self._issues_path.exists():
            os.startfile(str(self._issues_path))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = BillingApp()
    app.mainloop()
