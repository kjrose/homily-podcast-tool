# homily_monitor/database.py

import sqlite3
import logging

from .config_loader import CFG

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

DB_PATH = CFG["paths"]["db_path"]

# Global connection
CONN = None

def get_conn():
    global CONN
    if CONN is None:
        try:
            CONN = sqlite3.connect(DB_PATH)
            cursor = CONN.cursor()
            
            # Create tables if not exists
            logger.info(f"Creating tables in {DB_PATH} if not exists...")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS homilies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_key TEXT,
                filename TEXT,
                date TEXT,
                title TEXT,
                description TEXT,
                special TEXT,
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS compared_groups (
                group_key TEXT PRIMARY KEY,
                compared_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            
            # Explicit migration for new columns
            logger.info("Checking and migrating homilies table schema...")
            cursor.execute("PRAGMA table_info(homilies)")
            columns = {row[1]: row for row in cursor.fetchall()}
            
            if 'liturgical_day' not in columns:
                logger.info("Adding liturgical_day column to homilies table...")
                cursor.execute("ALTER TABLE homilies ADD COLUMN liturgical_day TEXT DEFAULT ''")
            
            if 'lit_year' not in columns:
                logger.info("Adding lit_year column to homilies table...")
                cursor.execute("ALTER TABLE homilies ADD COLUMN lit_year TEXT DEFAULT ''")
            
            CONN.commit()
            logger.info(f"✅ Database connection established and schema updated for {DB_PATH}")
        except sqlite3.Error as e:
            logger.error(f"❌ Database error connecting to {DB_PATH}: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error initializing database {DB_PATH}: {e}")
            raise
    
    return CONN


def insert_homily(group_key, filename, date, title, description, special, liturgical_day='', lit_year=''):
    try:
        conn = get_conn()
        cursor = conn.cursor()
        logger.info(f"Inserting homily: {filename} with group_key {group_key}")
        cursor.execute("""
            INSERT INTO homilies (group_key, filename, date, title, description, special, liturgical_day, lit_year)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (group_key, filename, date, title, description, special, liturgical_day, lit_year))
        conn.commit()
        logger.info(f"✅ Successfully inserted homily: {filename}")
    except sqlite3.IntegrityError as e:
        logger.error(f"❌ Integrity error inserting homily {filename}: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ Error inserting homily {filename}: {e}")
        raise