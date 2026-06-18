# toolbox.py
import os
import tomllib
import sqlite3
import time
from flask import request, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from toolbox.files import *

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_DIR = BASE_PATH
DB_PATH = os.path.join(BASE_PATH, "users.db")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DATA_DIR = os.path.join(BASE_DIR, "data")





def load_configs(config_dir: str) -> dict:
    config = {}

    for root, _, files in os.walk(config_dir):
        rel_path = os.path.relpath(root, config_dir)

        current = config
        if rel_path != ".":
            for part in rel_path.split(os.sep):
                current = current.setdefault(part, {})

        for file in files:
            if not file.endswith(".toml"):
                continue

            name = os.path.splitext(file)[0]

            with open(os.path.join(root, file), "rb") as f:
                current[name] = tomllib.load(f)

    return config


config = load_configs(CONFIG_DIR)








if __name__ == "__main__":
    print(
        "dies ist die ToolBox man soll sie nur importiren nic	ht direckt asuführen"
    )
