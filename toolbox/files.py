import os

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = BASE_PATH
DB_PATH = os.path.join(BASE_PATH, "users.db")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_PATH = DATA_DIR
CONFIG_DIR = os.path.join(BASE_DIR, "config") #heir sind die verschidenen tomls

def get_storage(id: str) -> int:
    total_size = 0

    # DATA_DIR/*/id
    try:
        for folder in os.listdir(DATA_DIR):
            path = os.path.join(DATA_DIR, folder, id)

            if os.path.isdir(path):
                total_size += _folder_size(path)
    except FileNotFoundError:
        pass

    return total_size


def _folder_size(folder: str) -> int:
    size = 0

    for root, dirs, files in os.walk(folder):
        for f in files:
            file_path = os.path.join(root, f)
            try:
                size += os.path.getsize(file_path)
            except (FileNotFoundError, PermissionError):
                pass

    return size


def get_lang():
    return request.cookies.get("lang", "deu")


def set_lang(lang):
    resp = make_response()
    resp.set_cookie("lang", lang)
    return resp