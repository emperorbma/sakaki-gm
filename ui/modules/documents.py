# ui/modules/documents.py — reference files stored in the campaign database
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ui.modules.importer import import_documents


class DocumentsModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._rows = []
        self._loaded_id = None
        self._loading = False

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        ttk.Label(header, text="Documents", font=("Georgia", 14, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="Import Folder", command=self.import_folder).pack(side=tk.RIGHT)
        ttk.Label(
            self.frame,
            text="Reference files stored in this campaign. Character sheets stay on the Characters tab.",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        body = ttk.Frame(self.frame)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        self.list = tk.Listbox(body, width=36, font=("Georgia", 11))
        self.list.pack(side=tk.LEFT, fill=tk.Y)
        self.list.bind("<<ListboxSelect>>", self._on_select)
        self.text = tk.Text(body, wrap=tk.WORD, font=("Georgia", 12))
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        buttons = ttk.Frame(self.frame)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(buttons, text="Save", command=self.save_current).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Remove", command=self.remove_current).pack(side=tk.LEFT, padx=6)

        self.reload()

    def set_db(self, db):
        self._loaded_id = None
        self.db = db
        self.reload()

    def reload(self, keep_id=None):
        self._loading = True
        self._rows = self.db.execute(
            "SELECT id, title FROM notes ORDER BY title"
        ).fetchall()
        self.list.delete(0, tk.END)
        selected = None
        for index, row in enumerate(self._rows):
            self.list.insert(tk.END, row["title"])
            if keep_id is not None and row["id"] == keep_id:
                selected = index
        self._loading = False
        if selected is None and self._rows:
            selected = 0
        if selected is None:
            self._loaded_id = None
            self.text.delete("1.0", tk.END)
            return
        self.list.selection_set(selected)
        self.list.see(selected)
        self._show(selected)

    def import_folder(self):
        folder = filedialog.askdirectory(title="Import documents into this campaign")
        if not folder:
            return
        self.save_current(quiet=True)
        try:
            summary = import_documents(self.db, folder)
        except Exception as exc:
            messagebox.showerror("Import", str(exc))
            return
        keep = self._loaded_id
        self.reload(keep_id=keep)
        messagebox.showinfo("Import", summary)

    def save_current(self, quiet=False):
        if self._loaded_id is None:
            return
        content = self.text.get("1.0", "end").strip()
        self.db.execute(
            "UPDATE notes SET content=? WHERE id=?",
            (content, self._loaded_id),
        )
        self.db.commit()
        if not quiet:
            messagebox.showinfo("Saved", "Document saved.")

    def remove_current(self):
        if self._loaded_id is None:
            return
        title = self._title_for(self._loaded_id)
        if not messagebox.askyesno("Remove document", f"Remove {title} from this campaign?"):
            return
        self.db.execute("DELETE FROM notes WHERE id=?", (self._loaded_id,))
        self.db.commit()
        self._loaded_id = None
        self.reload()

    def _on_select(self, _event=None):
        if self._loading:
            return
        selected = self.list.curselection()
        if not selected:
            return
        index = selected[0]
        if index >= len(self._rows):
            return
        chosen = self._rows[index]["id"]
        if chosen == self._loaded_id:
            return
        self.save_current(quiet=True)
        self._show(index)

    def _show(self, index):
        row = self.db.execute(
            "SELECT id, content FROM notes WHERE id=?",
            (self._rows[index]["id"],),
        ).fetchone()
        if row is None:
            return
        self._loading = True
        self._loaded_id = row["id"]
        self.text.delete("1.0", tk.END)
        if row["content"]:
            self.text.insert("1.0", row["content"])
        self._loading = False

    def _title_for(self, note_id):
        for row in self._rows:
            if row["id"] == note_id:
                return row["title"]
        return "this document"
