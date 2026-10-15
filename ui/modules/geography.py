"""MUD-style campaign setting and room navigator."""
import tkinter as tk
from tkinter import ttk

from object_graph_db import (
    get_encounter_tables,
    get_geography_objects,
    get_navigation_exits,
)


SETTING_ROOT = "__campaign_setting__"
GEOGRAPHY_FILTERS = (
    "Regions and settlements",
    "Region",
    "Settlement",
    "Area",
    "Point",
    "All geographic kinds",
)


class GeographyModule:
    def __init__(self, notebook, db):
        self.db = db
        self.frame = ttk.Frame(notebook)
        self._stack = [SETTING_ROOT]
        self._rows = []
        self._loading = False

        header = ttk.Frame(self.frame)
        header.pack(fill=tk.X, padx=12, pady=(12, 4))
        self.title = ttk.Label(header, font=("Georgia", 14, "bold"))
        self.title.pack(side=tk.LEFT)
        self.back_button = ttk.Button(
            header, text="Back", command=self.go_back
        )
        self.back_button.pack(side=tk.RIGHT)

        filters = ttk.Frame(self.frame)
        filters.pack(fill=tk.X, padx=12, pady=(4, 8))
        ttk.Label(filters, text="Show").pack(side=tk.LEFT)
        self.kind_filter = ttk.Combobox(
            filters,
            state="readonly",
            values=GEOGRAPHY_FILTERS,
            width=25,
        )
        self.kind_filter.current(0)
        self.kind_filter.pack(side=tk.LEFT, padx=(6, 12))
        self.kind_filter.bind("<<ComboboxSelected>>", self._refresh_exits)
        ttk.Label(filters, text="Search objects").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search = ttk.Entry(filters, textvariable=self.search_var)
        self.search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.search_var.trace_add("write", self._refresh_exits)

        body = ttk.Panedwindow(self.frame, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
        exit_frame = ttk.Frame(body)
        details_frame = ttk.Frame(body)
        body.add(exit_frame, weight=1)
        body.add(details_frame, weight=2)

        ttk.Label(exit_frame, text="Related objects").pack(anchor="w", pady=(0, 4))
        list_frame = ttk.Frame(exit_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.exits = ttk.Treeview(
            list_frame,
            columns=("relation", "destination", "kind"),
            show="headings",
            selectmode="browse",
        )
        self.exits.heading("relation", text="Exit")
        self.exits.heading("destination", text="Destination")
        self.exits.heading("kind", text="Kind")
        self.exits.column("relation", width=120, stretch=False)
        self.exits.column("destination", width=220)
        self.exits.column("kind", width=100, stretch=False)
        scrollbar = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.exits.yview
        )
        self.exits.configure(yscrollcommand=scrollbar.set)
        self.exits.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.exits.bind("<Double-1>", self._open_selected)
        ttk.Button(
            exit_frame,
            text="Open selected",
            command=self._open_selected,
        ).pack(anchor="w", pady=(6, 0))

        ttk.Label(details_frame, text="Current object").pack(anchor="w", pady=(0, 4))
        self.details = tk.Text(
            details_frame, wrap=tk.WORD, font=("Georgia", 11), state=tk.DISABLED
        )
        self.details.pack(fill=tk.BOTH, expand=True)
        self._refresh_exits()

    def set_db(self, db):
        self.db = db
        self._stack = [SETTING_ROOT]
        self._refresh_exits()

    def _current_object_id(self):
        return self._stack[-1]

    def _refresh_exits(self, *_args):
        current = self._current_object_id()
        is_root = current == SETTING_ROOT
        self._loading = True
        self.exits.delete(*self.exits.get_children())
        self._rows = []
        self.kind_filter.configure(state="readonly" if is_root else "disabled")
        self.title.configure(
            text="Campaign Setting" if is_root else self._node_name(current)
        )
        self.back_button.configure(state="disabled" if is_root else "normal")

        query = self.search_var.get().strip().casefold()
        if is_root:
            self._show_setting_details()
            rows = [
                {
                    "relation": "setting",
                    "target": node,
                    "link": {},
                }
                for node in get_geography_objects(
                    self.db, self.kind_filter.get()
                )
            ]
        else:
            self._show_node_details(current)
            rows = get_navigation_exits(self.db, current)

        for item in rows:
            target = item["target"]
            searchable = " ".join(
                (
                    item["relation"],
                    target["name"],
                    target["object_type"],
                    target["geography_kind"],
                    target["description"],
                    item["link"].get("direction", ""),
                    item["link"].get("notes", ""),
                )
            ).casefold()
            if query and query not in searchable:
                continue
            self._rows.append(item)
            label = self._exit_label(item)
            self.exits.insert(
                "",
                tk.END,
                iid=str(len(self._rows) - 1),
                values=(label, target["name"], target["geography_kind"]),
            )
        self._loading = False

    def _node_name(self, object_id):
        row = self.db.execute(
            "SELECT name FROM objects WHERE object_id=?", (object_id,)
        ).fetchone()
        return row["name"] if row else object_id

    def _exit_label(self, item):
        if item.get("inference") == "inverse_contains":
            return "exit (inferred from containment)"
        if item["relation"] != "route":
            return item["relation"]
        link = item["link"]
        label = "route"
        if link["direction"]:
            label += f" (recorded: {link['direction']})"
        costs = []
        if link["distance_min_miles"] is not None:
            distance = link["distance_min_miles"]
            if link["distance_max_miles"] not in (None, distance):
                distance = (
                    f"{distance:g}–{link['distance_max_miles']:g} mi"
                )
            else:
                distance = f"{distance:g} mi"
            costs.append(distance)
        if link["day_minutes"] is not None:
            costs.append(f"{link['day_minutes']} min day")
        if link["night_minutes"] is not None:
            costs.append(f"{link['night_minutes']} min night")
        if costs:
            label += " (" + ", ".join(costs) + ")"
        return label

    def _write_details(self, text):
        self.details.configure(state=tk.NORMAL)
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state=tk.DISABLED)

    def _show_setting_details(self):
        self._write_details(
            "Geography is a query view over objects classified by geographic "
            "kind.\n\n"
            "Its results may have any other relationships in the object graph. "
            "Choose a geographic kind and search to narrow the objects."
        )

    def _show_node_details(self, object_id):
        node = self.db.execute(
            "SELECT * FROM objects WHERE object_id=?", (object_id,)
        ).fetchone()
        if node is None:
            self._write_details("This object is no longer in the graph.")
            return
        lines = [
            node["name"],
            f"Object type: {node['object_type'] or 'unspecified'}",
            f"Geography kind: {node['geography_kind'] or 'not classified'}",
        ]
        if node["description"]:
            lines.extend(("", node["description"]))
        if node["coordinate_x"] is not None or node["coordinate_y"] is not None:
            lines.extend(
                (
                    "",
                    "Coordinates: "
                    f"{node['coordinate_x'] if node['coordinate_x'] is not None else '?'}"
                    ", "
                    f"{node['coordinate_y'] if node['coordinate_y'] is not None else '?'}",
                )
            )
            if node["coordinate_precision"]:
                lines.append(f"Precision: {node['coordinate_precision']}")
            if node["coordinate_basis"]:
                lines.append(f"Basis: {node['coordinate_basis']}")
        tables = get_encounter_tables(self.db, object_id)
        if tables:
            lines.extend(("", "Encounter tables:"))
            lines.extend(
                f"  {table['name']} ({table['roll_die']})" for table in tables
            )
        self._write_details("\n".join(lines))

    def _open_selected(self, _event=None):
        selected = self.exits.selection()
        if not selected:
            return
        row_index = int(selected[0])
        target_id = self._rows[row_index]["target"]["object_id"]
        self._stack.append(target_id)
        self.search_var.set("")
        self._refresh_exits()

    def go_back(self):
        if len(self._stack) <= 1:
            return
        self._stack.pop()
        self.search_var.set("")
        self._refresh_exits()
