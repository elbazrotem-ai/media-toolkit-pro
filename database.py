import os
import sqlite3

DB_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.db")
INTERNAL_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal_storage")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         TEXT,
            audio_path       TEXT,
            transcript_path  TEXT,
            timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
