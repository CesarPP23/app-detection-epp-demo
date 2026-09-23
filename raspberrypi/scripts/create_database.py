import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database" / "offline_cache.db"


def setup_local_database(db_path: Path):
    """Crea la tabla para la cola si no existe."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offline_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    setup_local_database(DB_PATH)