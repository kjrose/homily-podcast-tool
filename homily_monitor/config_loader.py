# homily_monitor/config_loader.py

import json
import logging
import os
import sys

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

def get_base_dir():
    """Return the runtime base directory (project root or frozen executable directory)."""
    if getattr(sys, 'frozen', False):  # PyInstaller check
        return os.path.dirname(sys.executable)

    # Move up one level from homily_monitor to the project root (where main.py is)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config():
    """Load config.json from the directory of main.py or the executable."""
    base_dir = get_base_dir()
    config_path = os.path.join(base_dir, "config.json")
    
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
            logger.info(f"✅ Loaded configuration from {config_path}")
            return config
    except FileNotFoundError:
        logger.error(f"❌ Config file not found at {config_path}")
        raise FileNotFoundError(f"Config file not found at {config_path}")
    except json.JSONDecodeError as e:
        logger.error(f"❌ Invalid JSON in config file {config_path}: {e}")
        raise json.JSONDecodeError(f"Invalid JSON in {config_path}: {e}", e.doc, e.pos)
    except Exception as e:
        logger.error(f"❌ Unexpected error loading config from {config_path}: {e}")
        raise Exception(f"Unexpected error loading config from {config_path}: {e}")

# Load config globally, handle errors in main.py
try:
    CFG = load_config()
except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
    logger.critical(f"Failed to load configuration: {e}")
    raise  # Let main.py handle the exception
