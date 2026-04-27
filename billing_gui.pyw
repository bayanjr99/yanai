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
        self._btn_chat = ttk.Button(
            br, text="💬  שאל AI",
            command=self._open_chat, state="disabled",
        )
        self._btn_chat.pack(side=tk.LEFT, padx=8, ipadx=6, ipady=4)

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
                cwd=str(BASE),
            )

            # Decode output safely (handles any encoding)
            stdout = (res.stdout or b"").decode("utf-8", errors="replace")
            stderr = (res.stderr or b"").decode("utf-8", errors="replace")

            for line in stdout.splitlines():
                self.after(0, lambda l=line: self._write(l))

            if stderr:
                # Filter out expected PDF parser warnings
                errors = [l for l in stderr.splitlines()
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

                # Enable AI chat
                self.after(0, lambda: self._btn_chat.configure(state="normal"))

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

    def _open_chat(self):
        if self._billing_path and self._billing_path.exists():
            ChatWindow(self, self._billing_path)


# ---------------------------------------------------------------------------
# AI Chat window
# ---------------------------------------------------------------------------

def _load_api_key() -> str:
    """Load API key from .env file or environment variable."""
    # Try environment variable first
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    # Try .env file next to the script
    env_file = BASE / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _load_billing_context(billing_path: Path) -> str:
    """Load key data from the billing Excel for AI context."""
    try:
        import pandas as pd
        xl = pd.ExcelFile(str(billing_path))

        parts = []

        # Main billing report
        if "דוח חיוב לקוחות" in xl.sheet_names:
            df = xl.parse("דוח חיוב לקוחות", header=2)
            df = df.dropna(how="all")
            parts.append("=== דוח חיוב לקוחות ===")
            parts.append(df.to_string(index=False))

        # Internal summary (profitability)
        if "סיכום פנימי" in xl.sheet_names:
            df = xl.parse("סיכום פנימי", header=2)
            df = df.dropna(how="all")
            parts.append("\n=== סיכום פנימי (רווחיות) ===")
            parts.append(df.to_string(index=False))

        return "\n".join(parts)
    except Exception as e:
        return f"(לא ניתן לטעון נתונים: {e})"


class ChatWindow(tk.Toplevel):
    def __init__(self, parent, billing_path: Path):
        super().__init__(parent)
        self.title("💬 שאל AI על הדוח")
        self.geometry("700x560")
        self.minsize(500, 400)

        self._api_key = _load_api_key()
        self._billing_path = billing_path
        self._history: list[dict] = []
        self._context = ""
        self._thinking = False

        self._build_ui()
        threading.Thread(target=self._load_context, daemon=True).start()

    def _build_ui(self):
        # Title
        ttk.Label(self, text="💬  שאל שאלות על דוח החיוב",
                  font=("Segoe UI", 12, "bold")).pack(pady=(14, 4))
        ttk.Label(self, text='לדוגמה: "מי קיבל שלמות?" | "מה הרווח מולפמן?" | "אילו לקוחות בחיוב יומי?"',
                  font=("Segoe UI", 9), foreground="#666").pack(pady=(0, 8))

        # Chat history display
        chat_frame = ttk.Frame(self)
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 8))

        self._chat_display = scrolledtext.ScrolledText(
            chat_frame, state="disabled", font=("Segoe UI", 10),
            bg="white", wrap=tk.WORD, relief="flat",
        )
        self._chat_display.pack(fill=tk.BOTH, expand=True)
        self._chat_display.tag_config("user",    foreground="#0050c8", font=("Segoe UI", 10, "bold"))
        self._chat_display.tag_config("ai",      foreground="#1a1a1a", font=("Segoe UI", 10))
        self._chat_display.tag_config("system",  foreground="#888888", font=("Segoe UI", 9, "italic"))
        self._chat_display.tag_config("thinking",foreground="#aa6600", font=("Segoe UI", 9, "italic"))

        # Input row
        input_frame = ttk.Frame(self)
        input_frame.pack(fill=tk.X, padx=14, pady=(0, 14))

        self._entry = ttk.Entry(input_frame, font=("Segoe UI", 11))
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self._entry.bind("<Return>", lambda e: self._send())
        self._entry.focus()

        self._send_btn = ttk.Button(input_frame, text="שלח ➤", command=self._send, width=8)
        self._send_btn.pack(side=tk.LEFT, padx=(8, 0), ipady=6)

        # Status
        self._status_var = tk.StringVar(value="טוען נתוני הדוח...")
        ttk.Label(self, textvariable=self._status_var,
                  font=("Segoe UI", 8), foreground="#888").pack(pady=(0, 6))

        self._write_chat("מערכת", "טוען נתוני הדוח...", "system")

    # ---------------------------------------------------------------- context
    def _load_context(self):
        self._context = _load_billing_context(self._billing_path)
        msg = "נתונים נטענו. תוכל לשאול שאלות על הדוח."
        if not self._api_key:
            msg = "⚠  לא נמצא ANTHROPIC_API_KEY — אנא הגדר אותו ב-.env"
        self.after(0, lambda: self._status_var.set(msg))
        self.after(0, lambda: self._update_display("מערכת", msg, "system"))

    # ------------------------------------------------------------------ chat
    def _write_chat(self, speaker: str, text: str, tag: str):
        self._chat_display.configure(state="normal")
        if speaker == "אתה":
            self._chat_display.insert(tk.END, f"\n👤 {speaker}:\n", "user")
        elif speaker == "AI":
            self._chat_display.insert(tk.END, f"\n🤖 {speaker}:\n", "user")
        else:
            self._chat_display.insert(tk.END, f"\n", "system")
        self._chat_display.insert(tk.END, text + "\n", tag)
        self._chat_display.see(tk.END)
        self._chat_display.configure(state="disabled")

    def _update_display(self, speaker: str, text: str, tag: str):
        """Thread-safe chat display update."""
        self._chat_display.configure(state="normal")
        self._chat_display.delete("1.0", tk.END)
        # Rewrite full history
        for msg in self._history:
            spk = "👤 אתה" if msg["role"] == "user" else "🤖 AI"
            t = "user" if msg["role"] == "user" else "ai"
            self._chat_display.insert(tk.END, f"\n{spk}:\n", t)
            self._chat_display.insert(tk.END, msg["content"] + "\n", t)
        if text:
            self._chat_display.insert(tk.END, f"\n{text}\n", tag)
        self._chat_display.see(tk.END)
        self._chat_display.configure(state="disabled")

    def _send(self):
        question = self._entry.get().strip()
        if not question or self._thinking:
            return
        if not self._api_key:
            messagebox.showwarning("חסר API Key",
                "לא נמצא ANTHROPIC_API_KEY.\n"
                "הוסף אותו לקובץ .env בתיקיית המערכת.")
            return

        self._entry.delete(0, tk.END)
        self._history.append({"role": "user", "content": question})
        self._thinking = True
        self._send_btn.configure(state="disabled")
        self._status_var.set("AI חושב...")
        self.after(0, lambda: self._update_display("", "⏳ חושב...", "thinking"))

        threading.Thread(target=self._ask_ai, args=(question,), daemon=True).start()

    def _ask_ai(self, question: str):
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)

            system_prompt = (
                "אתה עוזר פיננסי חכם שמנתח דוחות חיוב של חברת כוח אדם בשם ינאי פרסונל. "
                "ענה תמיד בעברית, בצורה ברורה וקצרה. "
                "כשנשאל על נתונים — חפש בדוח שסופק. "
                "אם הנתון לא קיים בדוח — אמור זאת.\n\n"
                f"דוח החיוב:\n{self._context}"
            )

            messages = [
                {"role": m["role"], "content": m["content"]}
                for m in self._history
            ]

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                system=system_prompt,
                messages=messages,
            )

            answer = response.content[0].text.strip()
            self._history.append({"role": "assistant", "content": answer})

            self.after(0, lambda a=answer: self._finish_reply(a, None))

        except Exception as exc:
            self.after(0, lambda e=str(exc): self._finish_reply(None, e))

    def _finish_reply(self, answer, error):
        self._thinking = False
        self._send_btn.configure(state="normal")
        self._status_var.set("מוכן לשאלה נוספת")

        if error:
            self._history.pop()  # remove failed user message
            self.after(0, lambda: self._update_display("", f"❌ שגיאה: {error}", "system"))
        else:
            self.after(0, lambda: self._update_display("", "", "system"))


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = BillingApp()
    app.mainloop()
