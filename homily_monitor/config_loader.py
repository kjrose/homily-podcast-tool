# homily_monitor/config_loader.py

import json
import logging
import os
import sys

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

def load_config():
    """Load config.json from the directory of the executable or script."""
    # Determine the base directory (EXE or script location)
    if getattr(sys, 'frozen', False):  # PyInstaller check
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    config_path = os.path.join(base_dir, "config.json")
    
    try:
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
            logger.info(f"Loaded configuration from {config_path}")
            return config
    except FileNotFoundError:
        logger.error(f"Config file not found at {config_path}")
        raise FileNotFoundError(f"Config file not found at {config_path}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {config_path}: {e}")
        raise json.JSONDecodeError(f"Invalid JSON in {config_path}: {e}", e.doc, e.pos)
    except Exception as e:
        logger.error(f"Unexpected error loading config from {config_path}: {e}")
        raise Exception(f"Unexpected error loading config from {config_path}: {e}")

# Load config globally, handle errors in main.py
try:
    CFG = load_config()
except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
    logger.critical(f"Failed to load configuration: {e}")
    raise  # Let main.py handle the exception