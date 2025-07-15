# homily_monitor/database.py

import sqlite3

from .config_loader import CFG

DB_PATH = CFG["paths"]["db_path"]

# Global connection
CONN = None

def get_conn():
    global CONN
    if CONN is None:
        CONN = sqlite3.connect(DB_PATH)
        cursor = CONN.cursor()
        
        # Create tables if not exists (unchanged)
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
        cursor.execute("PRAGMA table_info(homilies)")
        columns = {row[1]: row for row in cursor.fetchall()}
        
        if 'liturgical_day' not in columns:
            cursor.execute("ALTER TABLE homilies ADD COLUMN liturgical_day TEXT DEFAULT ''")
        
        if 'lit_year' not in columns:
            cursor.execute("ALTER TABLE homilies ADD COLUMN lit_year TEXT DEFAULT ''")
        
        CONN.commit()
    
    return CONN


def insert_homily(group_key, filename, date, title, description, special, liturgical_day='', lit_year=''):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO homilies (group_key, filename, date, title, description, special, liturgical_day, lit_year)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (group_key, filename, date, title, description, special, liturgical_day, lit_year))
    conn.commit()