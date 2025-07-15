import json


def load_config(file_path="config.json"):
    try:
        with open(file_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ Config file not found.")
        exit(1)
    # ... other exceptions


CFG = load_config()  # Global config
