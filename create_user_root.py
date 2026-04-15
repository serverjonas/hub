from time import sleep
import json
import os
import subprocess

BASE_DIR = os.path.dirname(__file__)
accounts_file = os.path.join(BASE_DIR, "accounts.json")
creater_file = os.path.join(BASE_DIR, "create_user.py")

def load_data(data):
    for line in data:
        if line.get("name") is not None and line.get("password") is not None:
            print(line.get("name"), line.get("password"))
            subprocess.run(["python3", creater_file, line.get("name"), line.get("password")])

while True:
    if os.path.isfile(accounts_file):
        with open(accounts_file) as f:
            data = json.load(f)
        load_data(data)

        new_data = []  # ← jetzt innerhalb des if-Blocks
        with open(accounts_file, "w") as f:
            json.dump(new_data, f)

    sleep(2)
