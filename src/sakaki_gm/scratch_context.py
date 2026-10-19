"""Per-turn scratch workspace for Ask GM retrieval.

The campaign database remains canonical. Scratch holds snapshots of records
retrieved for one turn, with stable record IDs the model (or app) can cite.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from copy import deepcopy
from typing import Any


DEFAULT_MAX_ROUNDS = 7
DEFAULT_MAX_RECORDS = 40
DEFAULT_MAX_CHARS = 24_000


class ScratchBudgetExceeded(RuntimeError):
    """Raised when a turn would exceed configured scratch budgets."""


class ScratchContext:
    """Short-lived staging pad for one Ask GM turn."""

    def __init__(
        self,
        *,
        context_id: str | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_records: int = DEFAULT_MAX_RECORDS,
        max_chars: int = DEFAULT_MAX_CHARS,
    ):
        self.context_id = context_id or uuid.uuid4().hex
        self.max_rounds = max_rounds
        self.max_records = max_records
        self.max_chars = max_chars
        self.created_at = time.time()
        self.rounds_used = 0
        self._records: dict[str, dict[str, Any]] = {}

    def begin_round(self) -> None:
        if self.rounds_used >= self.max_rounds:
            raise ScratchBudgetExceeded(
                f"Scratch context {self.context_id} exceeded {self.max_rounds} rounds."
            )
        self.rounds_used += 1

    def require_context_id(self, context_id) -> None:
        if not isinstance(context_id, str) or context_id != self.context_id:
            raise ValueError(
                "context_id does not match this turn's scratch workspace."
            )

    def stage(
        self,
        record_type: str,
        canonical_key: str,
        snapshot: dict,
        *,
        revision: str | None = None,
    ) -> str:
        """Store or refresh a snapshot; return the stable scratch record_id."""
        record_id = _record_id(record_type, canonical_key)
        existing = self._records.get(record_id)
        payload = deepcopy(snapshot)
        rev = revision or _revision(payload)
        if existing is None and len(self._records) >= self.max_records:
            raise ScratchBudgetExceeded(
                f"Scratch context {self.context_id} exceeded "
                f"{self.max_records} staged records."
            )
        self._records[record_id] = {
            "record_id": record_id,
            "type": record_type,
            "canonical_key": str(canonical_key),
            "revision": rev,
            "snapshot": payload,
        }
        if self.char_count() > self.max_chars:
            # Roll back the last write if it blew the character budget.
            if existing is None:
                del self._records[record_id]
            else:
                self._records[record_id] = existing
            raise ScratchBudgetExceeded(
                f"Scratch context {self.context_id} exceeded "
                f"{self.max_chars} staged characters."
            )
        return record_id

    def stage_document(self, document: dict) -> str:
        doc_id = document.get("id")
        if doc_id is None:
            raise ValueError("document snapshot requires an id.")
        return self.stage("document", str(doc_id), document)

    def stage_object(self, obj: dict) -> str:
        object_id = obj.get("object_id")
        if not object_id:
            raise ValueError("object snapshot requires object_id.")
        return self.stage("object", str(object_id), obj)

    def get(self, record_id: str) -> dict | None:
        entry = self._records.get(record_id)
        return deepcopy(entry) if entry else None

    def has(self, record_id: str) -> bool:
        return record_id in self._records

    def record_ids(self) -> list[str]:
        return sorted(self._records)

    def char_count(self) -> int:
        total = 0
        for entry in self._records.values():
            total += len(repr(entry["snapshot"]))
        return total

    def export_prompt_pack(self, *, include_ids: bool = True) -> dict:
        """Bounded read-only pack for injecting into a weak-model prompt."""
        documents = []
        objects = []
        for record_id, entry in sorted(
            self._records.items(),
            key=lambda item: item[0],
        ):
            snap = deepcopy(entry["snapshot"])
            if entry["type"] == "document":
                item = {
                    "title": snap.get("title"),
                    "excerpt": snap.get("excerpt"),
                }
                if include_ids:
                    item["record_id"] = record_id
                    item["id"] = snap.get("id")
                documents.append(item)
            elif entry["type"] == "object":
                item = {
                    "name": snap.get("name"),
                    "object_type": snap.get("object_type"),
                    "geography_kind": snap.get("geography_kind"),
                    "description": snap.get("description"),
                }
                if include_ids:
                    item["record_id"] = record_id
                    item["object_id"] = snap.get("object_id")
                # Keep nested location hints when present, but strip noise.
                if "recorded_locations" in snap:
                    item["recorded_locations"] = snap["recorded_locations"]
                if "location_context" in snap:
                    item["location_context"] = snap["location_context"]
                if "properties" in snap:
                    item["properties"] = snap["properties"]
                objects.append(item)
        return {
            "context_id": self.context_id,
            "documents": documents,
            "objects": objects,
            "record_count": len(self._records),
            "rounds_used": self.rounds_used,
        }

    def unknown_citations(self, record_ids) -> list[str]:
        if not record_ids:
            return []
        unknown = []
        for record_id in record_ids:
            if not isinstance(record_id, str) or not self.has(record_id):
                unknown.append(record_id)
        return unknown


def _record_id(record_type: str, canonical_key: str) -> str:
    kind = record_type.strip().casefold() or "record"
    return f"{kind}:{canonical_key}"


def _revision(snapshot: dict) -> str:
    blob = repr(sorted(snapshot.items())).encode("utf-8", errors="replace")
    return hashlib.sha256(blob).hexdigest()[:16]
