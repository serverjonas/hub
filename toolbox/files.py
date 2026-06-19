import os
import tomllib

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = BASE_PATH
DB_PATH = os.path.join(BASE_PATH, "users.db")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_PATH = DATA_DIR
CONFIG_DIR = os.path.join(BASE_DIR, "config")  # heir sind die verschidenen tomls


# ─── Storage config ────────────────────────────────────────────────────────
# Limits sind in GB, werden in Bytes umgerechnet.
_STORAGE_DEFAULTS = {
    "max_storage_user": 15,
    "max_storage_VIP": 50,
    "max_storage_admin": 150,
}


def _load_storage_config() -> dict:
    """Lädt config/storage.toml mit sicheren Defaults."""
    path = os.path.join(CONFIG_DIR, "storage.toml")
    if not os.path.isfile(path):
        return dict(_STORAGE_DEFAULTS)
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        user_data = dict(data.get("user_data", {}))
        for k, v in _STORAGE_DEFAULTS.items():
            user_data.setdefault(k, v)
        return user_data
    except Exception:
        return dict(_STORAGE_DEFAULTS)


_STORAGE_CONFIG = _load_storage_config()


# ─── Helpers ───────────────────────────────────────────────────────────────


def _folder_size(folder: str) -> int:
    size = 0
    try:
        for root, dirs, files in os.walk(folder):
            for f in files:
                file_path = os.path.join(root, f)
                try:
                    size += os.path.getsize(file_path)
                except (FileNotFoundError, PermissionError, OSError):
                    pass
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return size


def _format_bytes(n) -> str:
    """Wandelt Bytes in eine lesbare Form (z. B. '2.4 GB')."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ─── Storage queries ───────────────────────────────────────────────────────


def get_storage(id, exclude_paths=None) -> int:
    """Gesamter Speicher (Bytes), den ein User über alle Module belegt.

    Zählt Dateien in allen Ordnern, deren Pfad die User-ID als Pfadkomponente
    enthält (Cloud, Musik, Filme). Memes werden zusätzlich per
    Dateinamen-Präfix (`{id}-…`) erkannt, da sie flach unter
    `data/memes/files/` liegen.

    `exclude_paths` ist eine optionale Liste von Pfaden (Dateien oder Ordner),
    die nicht mitgezählt werden sollen — nützlich, wenn man gerade hochlädt
    und verhindern will, dass die gerade geschriebenen Dateien doppelt
    gezählt werden.
    """
    if id is None:
        return 0
    id_str = str(id)
    if not os.path.isdir(DATA_DIR):
        return 0

    exclude_set = set()
    for p in (exclude_paths or []):
        try:
            exclude_set.add(os.path.abspath(p))
        except (TypeError, ValueError):
            pass

    def is_excluded(path: str) -> bool:
        if not exclude_set:
            return False
        try:
            return os.path.abspath(path) in exclude_set
        except (TypeError, ValueError):
            return False

    total_size = 0

    # 1) Pattern: jeder Ordner unter DATA_DIR, dessen Pfad die id als
    #    Pfadkomponente enthält (cloud/{id}, music/library/{id},
    #    films/data/{id}/films/{fid}, …).
    for root, dirs, files in os.walk(DATA_DIR):
        if is_excluded(root):
            # nicht in diesen Pfad absteigen
            dirs.clear()
            continue
        try:
            rel = os.path.relpath(root, DATA_DIR)
        except ValueError:
            continue
        if rel in (".", ""):
            continue
        parts = rel.split(os.sep)
        # memes/files/* wird separat per Dateinamen-Präfix gehandhabt.
        if parts[:2] == ["memes", "files"]:
            continue
        if id_str not in parts:
            continue
        for f in files:
            fp = os.path.join(root, f)
            if is_excluded(fp):
                continue
            try:
                total_size += os.path.getsize(fp)
            except (FileNotFoundError, PermissionError, OSError):
                pass

    # 2) Pattern: Memes (`{id}-{timestamp}{ext}`).
    meme_files_dir = os.path.join(DATA_DIR, "memes", "files")
    if is_excluded(meme_files_dir):
        # wenn das ganze memes/files-Verzeichnis ausgeschlossen ist,
        # brauchen wir die Präfix-Suche nicht
        pass
    elif os.path.isdir(meme_files_dir):
        prefix = f"{id_str}-"
        try:
            for f in os.listdir(meme_files_dir):
                if f.startswith(prefix):
                    fp = os.path.join(meme_files_dir, f)
                    if is_excluded(fp):
                        continue
                    try:
                        total_size += os.path.getsize(fp)
                    except (FileNotFoundError, PermissionError, OSError):
                        pass
        except (FileNotFoundError, PermissionError, OSError):
            pass

    return total_size


def get_storage_limit(user_id, infos=None) -> int:
    """Speicherlimit in Bytes, basierend auf Rolle (admin > vip > user)."""
    if infos is None:
        # Lazy import, um zirkuläre Imports zu vermeiden
        from toolbox.user import get_infos
        infos = get_infos(user_id) if user_id else None

    gb = _STORAGE_CONFIG.get("max_storage_user", _STORAGE_DEFAULTS["max_storage_user"])
    if infos and infos.get("admin"):
        gb = _STORAGE_CONFIG.get(
            "max_storage_admin", _STORAGE_DEFAULTS["max_storage_admin"]
        )
    elif infos and infos.get("vip"):
        gb = _STORAGE_CONFIG.get(
            "max_storage_VIP", _STORAGE_DEFAULTS["max_storage_VIP"]
        )
    return int(gb * 1024 * 1024 * 1024)


def get_storage_info(user_id, infos=None, exclude_paths=None) -> dict:
    """Ausführliche Speicherinformationen fürs UI / Logging.

    `exclude_paths` wird an `get_storage` weitergereicht (siehe dort).
    """
    if infos is None:
        from toolbox.user import get_infos
        infos = get_infos(user_id) if user_id else None

    used = get_storage(user_id, exclude_paths=exclude_paths)
    limit = get_storage_limit(user_id, infos)
    remaining = max(0, limit - used)
    percent = round((used / limit) * 100, 1) if limit > 0 else 0.0
    if infos and infos.get("admin"):
        role = "admin"
    elif infos and infos.get("vip"):
        role = "vip"
    else:
        role = "user"

    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "percent": percent,
        "role": role,
        "used_human": _format_bytes(used),
        "limit_human": _format_bytes(limit),
        "remaining_human": _format_bytes(remaining),
        "exceeded": used > limit,
    }


def check_storage(user_id, additional_bytes=0, infos=None, exclude_paths=None):
    """Prüft, ob `additional_bytes` zusätzlich passen.

    Liefert ein Tupel (ok, info). `info` enthält zusätzlich
    `incoming`, `would_use` und `would_exceed`.
    Negative `additional_bytes` werden als 0 behandelt (z. B. Löschungen).

    `exclude_paths` ist eine optionale Liste von Pfaden (Dateien/Ordner),
    die beim aktuellen Stand NICHT mitgezählt werden — typischerweise
    gerade geschriebene Upload-Temp-Dateien, damit es kein
    Double-Counting gibt.
    """
    info = get_storage_info(user_id, infos, exclude_paths=exclude_paths)
    try:
        incoming = max(0, int(additional_bytes or 0))
    except (TypeError, ValueError):
        incoming = 0
    info["incoming"] = incoming
    info["would_use"] = info["used"] + incoming
    info["would_exceed"] = info["would_use"] > info["limit"]
    return (not info["would_exceed"]), info


def get_lang():
    return request.cookies.get("lang", "deu")


def set_lang(lang):
    resp = make_response()
    resp.set_cookie("lang", lang)
    return resp
