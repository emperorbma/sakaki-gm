# campaigns.py — one SQLite file per campaign, listed in the app config
import sqlite3
import uuid
from pathlib import Path

from db import LEGACY_DB, get_db
from llm_config import CAMPAIGNS_DIR, load_config, save_config


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
    if not cfg["campaigns"]:
        legacy = Path(legacy_db) if legacy_db else None
        if legacy is not None and legacy.is_file():
            cid = _add(cfg, legacy)
        else:
            cid = _create_file(cfg, "New Campaign", campaigns_dir)
        cfg["active_campaign"] = cid
        save_config(cfg, config_path)
    path = active_path(cfg)
    if path is None or not path.is_file():
        cid = _create_file(cfg, "New Campaign", campaigns_dir)
        cfg["active_campaign"] = cid
        save_config(cfg, config_path)
        path = active_path(cfg)
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
    root = Path(campaigns_dir).resolve()
    if path == root or root in path.parents:
        path.unlink(missing_ok=True)
    if not cfg["campaigns"]:
        cid = _create_file(cfg, "New Campaign", campaigns_dir)
        cfg["active_campaign"] = cid
    elif cfg.get("active_campaign") == campaign_id:
        cfg["active_campaign"] = cfg["campaigns"][0]["id"]
    save_config(cfg, config_path)
    return active_path(cfg)


def rename_campaign(db, name: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('campaign_name', ?)",
        (name.strip() or "New Campaign",),
    )
    db.commit()
