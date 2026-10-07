PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT,
    status TEXT NOT NULL DEFAULT 'unknown',
    face_feature_path TEXT,
    iris_feature_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cameras (
    camera_id TEXT PRIMARY KEY,
    camera_name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'online',
    video_source TEXT
);

CREATE TABLE IF NOT EXISTS in_out_records (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL,
    record_type TEXT NOT NULL CHECK (record_type IN ('IN', 'OUT')),
    camera_id TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    record_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (person_id) REFERENCES persons(person_id),
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
);

CREATE TABLE IF NOT EXISTS recognition_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT,
    camera_id TEXT NOT NULL,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    image_path TEXT,
    recognize_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_known INTEGER NOT NULL DEFAULT 1 CHECK (is_known IN (0, 1)),
    FOREIGN KEY (person_id) REFERENCES persons(person_id),
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
);

CREATE TABLE IF NOT EXISTS tracks (
    track_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT,
    camera_id TEXT NOT NULL,
    location TEXT NOT NULL,
    track_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_image_path TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    FOREIGN KEY (person_id) REFERENCES persons(person_id),
    FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type TEXT NOT NULL,
    person_id TEXT,
    location TEXT,
    alert_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'unhandled',
    description TEXT,
    handler TEXT,
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
);

CREATE INDEX IF NOT EXISTS idx_in_out_person_time
ON in_out_records(person_id, record_time);

CREATE INDEX IF NOT EXISTS idx_recognition_camera_time
ON recognition_results(camera_id, recognize_time);

CREATE INDEX IF NOT EXISTS idx_tracks_person_time
ON tracks(person_id, track_time);

CREATE INDEX IF NOT EXISTS idx_alerts_status_time
ON alerts(status, alert_time);
