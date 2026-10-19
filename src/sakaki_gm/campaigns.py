# campaigns.py — one SQLite file per campaign, listed in the app config
from datetime import datetime
import sqlite3
import uuid
from pathlib import Path

from sakaki_gm.db import LEGACY_DB, get_db
from sakaki_gm.llm_config import (
    CAMPAIGNS_DIR,
    LEGACY_CAMPAIGNS_DIR,
    load_config,
    save_config,
)


def campaign_name(path) -> str:
    path = Path(path)
    if not path.is_file():
        return "(missing)"
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT value FROM metadata WHERE key='campaign_name'"
        ).fetchone()
        return row["value"] if row and row["value"] else path.stem
    except sqlite3.Error:
        return path.stem
    finally:
        db.close()


def _add(cfg: dict, path: Path) -> str:
    resolved = str(path.resolve())
    for item in cfg["campaigns"]:
        if str(Path(item["path"]).resolve()) == resolved:
            return item["id"]
    cid = uuid.uuid4().hex[:12]
    cfg["campaigns"].append({"id": cid, "path": resolved})
    return cid


def bootstrap(config_path=None, legacy_db=LEGACY_DB, campaigns_dir=CAMPAIGNS_DIR):
    """Return (config, path) for the active bottle.

    An empty registry registers legacy_db in place when that file exists.
    Otherwise it creates an empty bottle under campaigns_dir.
    """
    cfg = load_config(config_path)
    changed = False
    if not cfg["campaigns"]:
        legacy = Path(legacy_db) if legacy_db else None
        if legacy is not None and legacy.is_file():
            cid = _add(cfg, legacy)
        else:
            cid = _create_file(cfg, "New Campaign", campaigns_dir)
        cfg["active_campaign"] = cid
        changed = True
    path = active_path(cfg)
    if path is None or not path.is_file():
        existing = next(
            (
                item for item in cfg["campaigns"]
                if Path(item["path"]).is_file()
            ),
            None,
        )
        legacy = Path(legacy_db) if legacy_db else None
        if existing is not None:
            cfg["active_campaign"] = existing["id"]
            path = Path(existing["path"])
        elif legacy is not None and legacy.is_file():
            cfg["active_campaign"] = _add(cfg, legacy)
            path = legacy
        else:
            cfg["active_campaign"] = _create_file(cfg, "New Campaign", campaigns_dir)
            path = active_path(cfg)
        changed = True
    if changed:
        save_config(cfg, config_path)
    return cfg, path


def active_path(cfg: dict):
    cid = cfg.get("active_campaign")
    for item in cfg["campaigns"]:
        if item["id"] == cid:
            return Path(item["path"])
    if cfg["campaigns"]:
        return Path(cfg["campaigns"][0]["path"])
    return None


def create_campaign(config_path, name: str, campaigns_dir=CAMPAIGNS_DIR) -> str:
    cfg = load_config(config_path)
    cid = _create_file(cfg, name.strip() or "New Campaign", campaigns_dir)
    cfg["active_campaign"] = cid
    save_config(cfg, config_path)
    return cid


def _create_file(cfg: dict, name: str, campaigns_dir) -> str:
    directory = Path(campaigns_dir)
    directory.mkdir(parents=True, exist_ok=True)
    cid = uuid.uuid4().hex[:12]
    path = directory / f"{cid}.db"
    db = get_db(path)
    try:
        db.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('campaign_name', ?)",
            (name,),
        )
        db.commit()
    finally:
        db.close()
    cfg["campaigns"].append({"id": cid, "path": str(path.resolve())})
    return cid


def set_active(config_path, campaign_id: str) -> Path:
    cfg = load_config(config_path)
    if not any(item["id"] == campaign_id for item in cfg["campaigns"]):
        raise KeyError(campaign_id)
    cfg["active_campaign"] = campaign_id
    save_config(cfg, config_path)
    return active_path(cfg)


def register_file(config_path, db_path) -> str:
    cfg = load_config(config_path)
    cid = _add(cfg, Path(db_path))
    cfg["active_campaign"] = cid
    save_config(cfg, config_path)
    return cid


def campaign_backup_dir(db_path, backup_dir=None) -> Path:
    """Return the directory that stores timestamped backups for a campaign file."""
    source_path = Path(db_path).resolve()
    return Path(backup_dir) if backup_dir else source_path.parent / "backups"


def list_campaign_backups(db_path, backup_dir=None) -> list[Path]:
    """Return backup files for a campaign, newest first."""
    directory = campaign_backup_dir(db_path, backup_dir)
    if not directory.is_dir():
        return []
    return sorted(
        (
            path for path in directory.iterdir()
            if path.is_file() and path.suffix.casefold() == ".db"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def create_campaign_backup(db, db_path, backup_dir=None) -> Path:
    """Create a timestamped SQLite snapshot without changing campaign state."""
    source_path = Path(db_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Campaign database does not exist: {source_path}")
    directory = campaign_backup_dir(source_path, backup_dir)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = directory / f"{source_path.stem}-{timestamp}{source_path.suffix}"
    temporary_path = backup_path.with_suffix(backup_path.suffix + ".tmp")
    if backup_path.exists() or temporary_path.exists():
        raise FileExistsError(f"Campaign backup already exists: {backup_path}")

    db.commit()
    snapshot = sqlite3.connect(temporary_path)
    try:
        db.backup(snapshot)
        snapshot.close()
        temporary_path.replace(backup_path)
    except Exception:
        snapshot.close()
        temporary_path.unlink(missing_ok=True)
        raise
    return backup_path


def restore_campaign_from_backup(
    db,
    db_path,
    backup_path,
    *,
    make_safety_backup=True,
    backup_dir=None,
) -> Path | None:
    """Replace the live campaign database contents with a backup snapshot.

    The open connection is reused: backup rows are copied into it rather than
    swapping the file underneath. When make_safety_backup is true, a timestamped
    snapshot of the current campaign is written first and its path is returned.
    """
    source_path = Path(db_path).resolve()
    snapshot_path = Path(backup_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Campaign database does not exist: {source_path}")
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"Campaign backup does not exist: {snapshot_path}")
    if snapshot_path == source_path:
        raise ValueError("Cannot restore a campaign from its own live database file.")

    safety_path = None
    if make_safety_backup:
        safety_path = create_campaign_backup(db, source_path, backup_dir=backup_dir)

    uri = snapshot_path.as_uri() + "?mode=ro"
    snapshot = sqlite3.connect(uri, uri=True)
    try:
        snapshot.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        db.commit()
        snapshot.backup(db)
        db.commit()
    finally:
        snapshot.close()
    return safety_path


def remove_campaign(config_path, campaign_id: str, campaigns_dir=CAMPAIGNS_DIR) -> Path:
    """Drop a bottle from the registry.

    The file is deleted only when it lives under campaigns_dir. A project
    database such as pygm_campaign.db stays on disk.
    """
    cfg = load_config(config_path)
    entry = next((item for item in cfg["campaigns"] if item["id"] == campaign_id), None)
    if entry is None:
        raise KeyError(campaign_id)
    cfg["campaigns"] = [item for item in cfg["campaigns"] if item["id"] != campaign_id]
    path = Path(entry["path"]).resolve()
    roots = {Path(campaigns_dir).resolve(), LEGACY_CAMPAIGNS_DIR.resolve()}
    if any(path == root or root in path.parents for root in roots):
        path.unlink(missing_ok=True)
    active = next(
        (
            item for item in cfg["campaigns"]
            if item["id"] == cfg.get("active_campaign")
            and Path(item["path"]).is_file()
        ),
        None,
    )
    existing = next(
        (item for item in cfg["campaigns"] if Path(item["path"]).is_file()),
        None,
    )
    if active is None:
        active = existing
    legacy = LEGACY_DB.resolve()
    if active is None and legacy.is_file() and legacy != path:
        cid = _add(cfg, legacy)
        active = next(item for item in cfg["campaigns"] if item["id"] == cid)
    if active is None:
        cfg["campaigns"] = []
        cfg["active_campaign"] = _create_file(cfg, "New Campaign", campaigns_dir)
    else:
        cfg["active_campaign"] = active["id"]
    save_config(cfg, config_path)
    return active_path(cfg)


def rename_campaign(db, name: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('campaign_name', ?)",
        (name.strip() or "New Campaign",),
    )
    db.commit()
