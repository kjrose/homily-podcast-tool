# homily_monitor/config_loader.py

import json
import logging
import os

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

def load_config(file_path="../config.json"):
    try:
        full_path = os.path.join(os.path.dirname(__file__), file_path)
        with open(full_path, encoding="utf-8") as f:
            config = json.load(f)
            logger.info(f"✅ Loaded configuration from {full_path}")
            return config
    except FileNotFoundError:
        logger.error(f"❌ Config file not found at {full_path}")
        exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON in config file {full_path}: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"❌ Unexpected error loading config from {full_path}: {e}")
        exit(1)

CFG = load_config()  # Global config