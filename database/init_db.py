from pathlib import Path
import sqlite3
import hashlib


BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.sql"
DB_PATH = BASE_DIR / "mine_security.db"


def _add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _apply_migrations(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(connection, "persons", "iris_feature_path", "TEXT")
    _add_column_if_missing(connection, "persons", "employment_status", "TEXT NOT NULL DEFAULT 'active'")
    _add_column_if_missing(connection, "cameras", "video_source", "TEXT")
    _add_column_if_missing(connection, "alerts", "handler", "TEXT")


def _seed_if_empty(connection: sqlite3.Connection, table: str, statement: str, values: list[tuple]) -> None:
    if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0:
        connection.executemany(statement, values)


def _password_hash(password: str) -> str:
    salt = b"mine-safety-demo-salt"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"


def initialize_database() -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        _apply_migrations(connection)
        _seed_if_empty(
            connection,
            "persons",
            "INSERT INTO persons (person_id, name, department, status, face_feature_path) VALUES (?, ?, ?, ?, ?)",
            [
                ("P001", "张三", "采掘组", "underground", "data/faces/P001"),
                ("P002", "李四", "机电组", "out", "data/faces/P002"),
                ("P003", "王五", "通风组", "unknown", "data/faces/P003"),
            ],
        )
        _seed_if_empty(
            connection,
            "users",
            "INSERT INTO users (username, password_hash, role, person_id) VALUES (?, ?, ?, ?)",
            [
                ("admin", _password_hash("123456"), "admin", None),
                ("guard", _password_hash("123456"), "guard", None),
                ("miner", _password_hash("123456"), "miner", "P003"),
            ],
        )
        _seed_if_empty(
            connection,
            "cameras",
            "INSERT INTO cameras (camera_id, camera_name, location, status) VALUES (?, ?, ?, ?)",
            [("C001", "井口摄像头", "井口", "online"), ("C002", "运输巷摄像头", "运输巷", "online"), ("C003", "副井底摄像头", "副井底", "offline")],
        )
        _seed_if_empty(
            connection,
            "in_out_records",
            "INSERT INTO in_out_records (person_id, record_type, camera_id, confidence, record_time) VALUES (?, ?, ?, ?, ?)",
            [("P001", "IN", "C001", 0.96, "2026-09-24 08:10:00"), ("P002", "OUT", "C001", 0.94, "2026-09-24 17:40:00")],
        )
        _seed_if_empty(
            connection,
            "recognition_results",
            "INSERT INTO recognition_results (person_id, camera_id, confidence, image_path, recognize_time, is_known) VALUES (?, ?, ?, ?, ?, ?)",
            [("P001", "C002", 0.88, "data/results/P001_C002_001.jpg", "2026-09-24 08:35:00", 1)],
        )
        _seed_if_empty(
            connection,
            "tracks",
            "INSERT INTO tracks (person_id, camera_id, location, track_time, confidence) VALUES (?, ?, ?, ?, ?)",
            [("P001", "C002", "运输巷", "2026-09-24 08:35:00", 0.88)],
        )
        _seed_if_empty(
            connection,
            "alerts",
            "INSERT INTO alerts (alert_type, person_id, location, alert_time, status, description) VALUES (?, ?, ?, ?, ?, ?)",
            [("未出井", "P001", "井下", "2026-09-24 18:00:00", "unhandled", "人员入井后尚未检测到出井记录")],
        )
    print(f"Database initialized: {DB_PATH}")


if __name__ == "__main__":
    initialize_database()
