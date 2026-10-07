from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database.init_db import initialize_database


ROOT_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT_DIR / "database" / "mine_security.db"
UPLOAD_DIR = ROOT_DIR / "uploads"
FACE_DIR = UPLOAD_DIR / "faces"
VIDEO_DIR = UPLOAD_DIR / "videos"
RESULT_DIR = UPLOAD_DIR / "results"
FACE_THRESHOLD = 0.55
UNRETURNED_ALERT_MINUTES = 30

for folder in (FACE_DIR, VIDEO_DIR, RESULT_DIR):
    folder.mkdir(parents=True, exist_ok=True)


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class PersonPayload(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    department: str | None = Field(default=None, max_length=100)
    status: Literal["underground", "out", "unknown"] = "unknown"


class InOutPayload(BaseModel):
    person_id: str
    record_type: Literal["IN", "OUT", "UNKNOWN"]
    camera_id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class AlertPayload(BaseModel):
    status: Literal["unhandled", "handled"]
    handler: str | None = Field(default=None, max_length=50)


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    role: Literal["admin", "guard", "miner"]
    person_id: str | None = None


class UserStatusPayload(BaseModel):
    status: Literal["active", "disabled"]


ROLE_NAMES = {"admin": "系统管理员", "guard": "井口安检员", "miner": "井下作业人员"}


def password_hash(password: str) -> str:
    salt = hashlib.sha256(f"mine-safety:{password}".encode()).digest()[:16]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2_sha256$120000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer demo-") or not authorization.endswith("-token"):
        raise HTTPException(status_code=401, detail="Authentication required")
    username = authorization.removeprefix("Bearer demo-").removesuffix("-token")
    with db() as connection:
        account = connection.execute("SELECT u.*, p.name FROM users u LEFT JOIN persons p ON p.person_id = u.person_id WHERE u.username = ?", (username,)).fetchone()
    if account is None or account["status"] != "active":
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"username": username, "role": account["role"], "name": account["name"] or ROLE_NAMES[account["role"]], "person_id": account["person_id"]}


def require_roles(*roles: str):
    def checker(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker


app = FastAPI(title="Mine Safety API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONTEND_DIR = ROOT_DIR / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    error = exc.errors()[0]
    field = ".".join(str(item) for item in error.get("loc", [])[1:])
    return JSONResponse(status_code=422, content={"error": f"{field}: {error.get('msg', 'Invalid request')}"})


@contextmanager
def db() -> Any:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def current_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def require_person(connection: sqlite3.Connection, person_id: str) -> sqlite3.Row:
    person = connection.execute("SELECT * FROM persons WHERE person_id = ?", (person_id,)).fetchone()
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def require_active_person(connection: sqlite3.Connection, person_id: str) -> sqlite3.Row:
    person = require_person(connection, person_id)
    if person["employment_status"] != "active":
        raise HTTPException(status_code=409, detail="Person is inactive")
    return person


def require_camera(connection: sqlite3.Connection, camera_id: str | None) -> sqlite3.Row | None:
    if camera_id is None:
        return None
    camera = connection.execute("SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)).fetchone()
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


def save_file(upload: UploadFile, folder: Path, prefix: str, allowed: set[str]) -> Path:
    suffix = Path(upload.filename or "upload.bin").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    path = folder / f"{prefix}_{datetime.now():%Y%m%d%H%M%S%f}{suffix}"
    with path.open("wb") as target:
        shutil.copyfileobj(upload.file, target)
    return path


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT_DIR)).replace("\\", "/")


def fallback_feature(path: Path) -> list[float]:
    raw = hashlib.sha256(path.read_bytes()).digest()
    # 扩展成 512 维，保持和 insightface 一致
    return [(raw[i % 32] / 255.0) for i in range(512)]


@lru_cache
def face_analyzer() -> Any:
    from insightface.app import FaceAnalysis

    analyzer = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    analyzer.prepare(ctx_id=0, det_size=(640, 640))
    return analyzer


@lru_cache
def person_detector() -> Any:
    from ultralytics import YOLO

    return YOLO("yolo11n.pt")


def face_feature(path: Path) -> tuple[list[float], str]:
    try:
        import cv2
        import insightface  # noqa: F401
    except ImportError:
        return fallback_feature(path), "development-fallback"
    image = cv2.imread(str(path))
    faces = face_analyzer().get(image) if image is not None else []
    if not faces:
        raise HTTPException(status_code=400, detail="No face detected")
    return faces[0].normed_embedding.astype(float).tolist(), "insightface"


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def load_feature(value: str | None) -> list[float] | None:
    if not value:
        return None
    path = ROOT_DIR / value
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def best_match(connection, feature):
    candidate, best_score = None, 0.0
    for person in connection.execute("SELECT * FROM persons WHERE face_feature_path IS NOT NULL"):
        saved = load_feature(person["face_feature_path"])
        if saved is not None and len(saved) == len(feature):   # ← 维度必须一致
            score = cosine(feature, saved)
            if score > best_score:
                candidate, best_score = person, score
    return candidate, best_score


def unknown_alert(connection: sqlite3.Connection, location: str, description: str) -> int:
    existing = connection.execute(
        "SELECT alert_id FROM alerts WHERE alert_type = '未知人员' AND location = ? AND status = 'unhandled' LIMIT 1",
        (location,),
    ).fetchone()
    if existing:
        return int(existing["alert_id"])
    cursor = connection.execute(
        "INSERT INTO alerts (alert_type, location, alert_time, status, description) VALUES ('未知人员', ?, ?, 'unhandled', ?)",
        (location, current_time(), description),
    )
    return int(cursor.lastrowid)


def sync_unreturned_alert(connection: sqlite3.Connection, person_id: str) -> None:
    latest = connection.execute(
        "SELECT record_type, record_time FROM in_out_records WHERE person_id = ? ORDER BY record_time DESC, record_id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    if latest is None:
        return
    if latest["record_type"] != "IN":
        connection.execute(
            "UPDATE alerts SET status = 'handled', handler = COALESCE(handler, 'system') WHERE alert_type = '未出井' AND person_id = ? AND status = 'unhandled'",
            (person_id,),
        )
        return
    try:
        record_time = datetime.strptime(latest["record_time"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return
    if datetime.now() - record_time < timedelta(minutes=UNRETURNED_ALERT_MINUTES):
        return
    # 同一条入井记录只允许生成一次“未出井”告警。
    # 不能只检查 status = 'unhandled'，否则管理员处理告警后，
    # /api/alerts 刷新时会再次生成同一条告警，形成循环。
    exists = connection.execute(
        "SELECT alert_id FROM alerts "
        "WHERE alert_type = '未出井' AND person_id = ? AND alert_time >= ? "
        "LIMIT 1",
        (person_id, latest["record_time"]),
    ).fetchone()
    if not exists:
        connection.execute(
            "INSERT INTO alerts (alert_type, person_id, location, alert_time, status, description) VALUES ('未出井', ?, '井下', ?, 'unhandled', '人员入井后尚未检测到出井记录')",
            (person_id, current_time()),
        )


def next_person_id(connection: sqlite3.Connection) -> str:
    values = [row["person_id"] for row in connection.execute("SELECT person_id FROM persons")]
    numbers = [int(value[1:]) for value in values if value.startswith("P") and value[1:].isdigit()]
    return f"P{max(numbers, default=0) + 1:03d}"


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": current_time()}


@app.post("/api/auth/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    with db() as connection:
        account = connection.execute("SELECT u.*, p.name FROM users u LEFT JOIN persons p ON p.person_id = u.person_id WHERE u.username = ?", (payload.username,)).fetchone()
    if account is None or account["status"] != "active" or not verify_password(payload.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": f"demo-{payload.username}-token", "role": account["role"], "name": account["name"] or ROLE_NAMES[account["role"]], "person_id": account["person_id"]}


@app.get("/api/users")
def list_users(_: dict[str, Any] = Depends(require_roles("admin"))) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute("SELECT u.user_id, u.username, u.role, u.person_id, p.name, u.status, u.created_at FROM users u LEFT JOIN persons p ON p.person_id = u.person_id ORDER BY u.user_id").fetchall()
    return [as_dict(row) for row in rows]


@app.post("/api/users", status_code=201)
def create_user(payload: UserCreatePayload, _: dict[str, Any] = Depends(require_roles("admin"))) -> dict[str, Any]:
    if payload.role == "miner" and not payload.person_id:
        raise HTTPException(status_code=400, detail="Miner account must be linked to a person")
    with db() as connection:
        if connection.execute("SELECT 1 FROM users WHERE username = ?", (payload.username,)).fetchone():
            raise HTTPException(status_code=409, detail="Username already exists")
        if payload.person_id and connection.execute("SELECT 1 FROM persons WHERE person_id = ?", (payload.person_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Person not found")
        cursor = connection.execute("INSERT INTO users (username, password_hash, role, person_id) VALUES (?, ?, ?, ?)", (payload.username, password_hash(payload.password), payload.role, payload.person_id))
    return {"user_id": int(cursor.lastrowid), "username": payload.username, "role": payload.role, "person_id": payload.person_id}


@app.put("/api/users/{user_id}/status")
def update_user_status(user_id: int, payload: UserStatusPayload, _: dict[str, Any] = Depends(require_roles("admin"))) -> dict[str, Any]:
    with db() as connection:
        if connection.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("UPDATE users SET status = ? WHERE user_id = ?", (payload.status, user_id))
    return {"user_id": user_id, "status": payload.status}


@app.post("/api/persons", status_code=201)
def create_person(payload: PersonPayload, _: dict[str, Any] = Depends(require_roles("admin"))) -> dict[str, str]:
    with db() as connection:
        person_id, created_at = next_person_id(connection), current_time()
        connection.execute(
            "INSERT INTO persons (person_id, name, department, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (person_id, payload.name, payload.department, payload.status, created_at),
        )
    return {"person_id": person_id, "created_at": created_at}


@app.put("/api/persons/{person_id}/employment-status")
def update_person_employment_status(person_id: str, payload: UserStatusPayload, _: dict[str, Any] = Depends(require_roles("admin"))) -> dict[str, str]:
    if payload.status not in {"active", "disabled"}:
        raise HTTPException(status_code=422, detail="Invalid employment status")
    employment_status = "active" if payload.status == "active" else "inactive"
    with db() as connection:
        require_person(connection, person_id)
        connection.execute("UPDATE persons SET employment_status = ? WHERE person_id = ?", (employment_status, person_id))
        connection.execute("UPDATE users SET status = ? WHERE person_id = ? AND role = 'miner'", ("active" if employment_status == "active" else "disabled", person_id))
    return {"person_id": person_id, "employment_status": employment_status}


@app.get("/api/persons")
def list_persons(status: Literal["underground", "out", "unknown"] | None = None, user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    with db() as connection:
        conditions, values = [], []
        if status:
            conditions.append("status = ?")
            values.append(status)
        if user["role"] == "miner":
            conditions.append("person_id = ?")
            values.append(user["person_id"])
        query = "SELECT * FROM persons" + (" WHERE " + " AND ".join(conditions) if conditions else "") + " ORDER BY created_at DESC, person_id DESC"
        rows = connection.execute(query, values).fetchall()
    return [as_dict(row) for row in rows]


@app.get("/api/persons/{person_id}")
def get_person(person_id: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if user["role"] == "miner" and person_id != user["person_id"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    with db() as connection:
        person = require_person(connection, person_id)
    return as_dict(person) or {}


@app.post("/api/persons/{person_id}/face")
def register_face(person_id: str, file: UploadFile = File(...), _: dict[str, Any] = Depends(require_roles("admin"))) -> dict[str, Any]:
    with db() as connection:
        require_active_person(connection, person_id)
        image = save_file(file, FACE_DIR, person_id, {".jpg", ".jpeg", ".png", ".webp"})
        feature, engine = face_feature(image)
        feature_file = FACE_DIR / f"{person_id}.json"
        feature_file.write_text(json.dumps(feature), encoding="utf-8")
        feature_path = relative(feature_file)
        connection.execute("UPDATE persons SET face_feature_path = ? WHERE person_id = ?", (feature_path, person_id))
    return {"person_id": person_id, "feature_extracted": True, "face_feature_path": feature_path, "engine": engine}


@app.post("/api/face/recognize")
def recognize_face(file: UploadFile = File(...), camera_id: str = Form("C001"), _: dict[str, Any] = Depends(require_roles("admin", "guard"))) -> dict[str, Any]:
    with db() as connection:
        require_camera(connection, camera_id)
        image = save_file(file, RESULT_DIR, "face", {".jpg", ".jpeg", ".png", ".webp"})
        feature, engine = face_feature(image)
        person, confidence = best_match(connection, feature)
        confidence = max(0.0, min(1.0, float(confidence)))   # ← 关键修复
        known = person is not None and confidence >= FACE_THRESHOLD
        connection.execute(
            "INSERT INTO recognition_results (person_id, camera_id, confidence, image_path, recognize_time, is_known) VALUES (?, ?, ?, ?, ?, ?)",
            (person["person_id"] if known else None, camera_id, confidence, relative(image), current_time(), int(known)),
        )
        alert_id = None if known else unknown_alert(connection, "井口", "井口识别到未登记人员")
    return {"person_id": person["person_id"] if known else None, "name": person["name"] if known else None, "confidence": round(confidence, 4), "is_known": int(known), "alert_id": alert_id, "engine": engine}

@app.post("/api/records/in-out", status_code=201)
def create_record(payload: InOutPayload, user: dict[str, Any] = Depends(require_roles("admin", "guard"))) -> dict[str, Any]:
    with db() as connection:
        require_active_person(connection, payload.person_id)
        require_camera(connection, payload.camera_id)
        record_time = current_time()
        cursor = connection.execute(
            "INSERT INTO in_out_records (person_id, record_type, camera_id, confidence, record_time) VALUES (?, ?, ?, ?, ?)",
            (payload.person_id, payload.record_type, payload.camera_id, payload.confidence, record_time),
        )
        status = {"IN": "underground", "OUT": "out", "UNKNOWN": "unknown"}[payload.record_type]
        connection.execute("UPDATE persons SET status = ? WHERE person_id = ?", (status, payload.person_id))
        sync_unreturned_alert(connection, payload.person_id)
    return {"record_id": int(cursor.lastrowid), "record_time": record_time, "status": status}


@app.get("/api/records/in-out")
def list_records(person_id: str | None = None, user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    if user["role"] == "miner":
        person_id = user["person_id"]
    with db() as connection:
        if person_id:
            require_person(connection, person_id)
        query = "SELECT r.*, p.name, p.department, c.camera_name, c.location FROM in_out_records r JOIN persons p ON p.person_id = r.person_id LEFT JOIN cameras c ON c.camera_id = r.camera_id"
        query += " WHERE r.person_id = ?" if person_id else ""
        rows = connection.execute(query + " ORDER BY r.record_time DESC, r.record_id DESC", (person_id,) if person_id else ()).fetchall()
    return [as_dict(row) for row in rows]


def detect_people(
    video_path: Path, camera_id: str, location: str, connection: sqlite3.Connection
) -> tuple[list[dict[str, Any]], str]:
    try:
        import cv2
        import ultralytics  # noqa: F401
    except ImportError:
        return [], "YOLO dependencies are not installed; video was saved but not analyzed"
    results: list[dict[str, Any]] = []
    resolved_tracks: dict[int, tuple[str | None, float]] = {}
    for frame_number, result in enumerate(person_detector().track(str(video_path), classes=[0], stream=True, verbose=False), start=1):
        for box in result.boxes:
            track_id = int(box.id[0]) if box.id is not None else frame_number
            bbox = [round(float(item), 1) for item in box.xyxy[0].tolist()]
            person_id, similarity = resolved_tracks.get(track_id, (None, 0.0))
            crop_path = None
            if track_id not in resolved_tracks:
                x1, y1, x2, y2 = (max(0, int(value)) for value in bbox)
                crop = result.orig_img[y1:y2, x1:x2]
                if crop.size:
                    crop_path = RESULT_DIR / f"track_{camera_id}_{frame_number}_{track_id}.jpg"
                    cv2.imwrite(str(crop_path), crop)
                    try:
                        feature, _ = face_feature(crop_path)
                        person, similarity = best_match(connection, feature)
                        person_id = person["person_id"] if person is not None and similarity >= FACE_THRESHOLD else None
                    except HTTPException:
                        person_id, similarity = None, 0.0
                resolved_tracks[track_id] = (person_id, similarity)
            results.append({"person_id": person_id, "camera_id": camera_id, "location": location, "track_id": track_id, "frame_number": frame_number, "bbox": bbox, "confidence": round(float(box.conf[0]), 4), "identity_confidence": round(similarity, 4), "is_known": int(person_id is not None), "image_path": relative(crop_path) if crop_path else None})
    return results, "Video analyzed"


@app.post("/api/videos", status_code=201)
def recognize_video(file: UploadFile = File(...), camera_id: str = Form("C002"), _: dict[str, Any] = Depends(require_roles("admin", "guard"))) -> dict[str, Any]:
    with db() as connection:
        camera = require_camera(connection, camera_id)
        video = save_file(file, VIDEO_DIR, "video", {".mp4", ".avi", ".mov", ".mkv"})
        results, message = detect_people(video, camera_id, camera["location"], connection)
        for item in results:
            image_path = item["image_path"] or relative(video)
            connection.execute("INSERT INTO recognition_results (person_id, camera_id, confidence, image_path, recognize_time, is_known) VALUES (?, ?, ?, ?, ?, ?)", (item["person_id"], camera_id, item["identity_confidence"] or item["confidence"], image_path, current_time(), item["is_known"]))
            connection.execute("INSERT INTO tracks (person_id, camera_id, location, track_time, source_image_path, confidence) VALUES (?, ?, ?, ?, ?, ?)", (item["person_id"], camera_id, item["location"], current_time(), image_path, item["identity_confidence"] or item["confidence"]))
        alert_id = unknown_alert(connection, camera["location"], "井下视频识别到待人工确认人员") if results else None
    return {"video_path": relative(video), "camera_id": camera_id, "recognition_count": len(results), "results": results, "alert_id": alert_id, "message": message}


@app.get("/api/tracks/{person_id}")
def tracks(person_id: str, user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    if user["role"] == "miner" and person_id != user["person_id"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    with db() as connection:
        require_person(connection, person_id)
        rows = connection.execute("SELECT t.*, p.name, c.camera_name FROM tracks t JOIN persons p ON p.person_id = t.person_id LEFT JOIN cameras c ON c.camera_id = t.camera_id WHERE t.person_id = ? ORDER BY t.track_time, t.track_id", (person_id,)).fetchall()
    return [as_dict(row) for row in rows]


@app.get("/api/alerts")
def alerts(_: dict[str, Any] = Depends(require_roles("admin", "guard"))) -> list[dict[str, Any]]:
    with db() as connection:
        person_ids = connection.execute("SELECT person_id FROM persons").fetchall()
        for row in person_ids:
            sync_unreturned_alert(connection, row["person_id"])
        rows = connection.execute("SELECT a.*, p.name, p.department FROM alerts a LEFT JOIN persons p ON p.person_id = a.person_id ORDER BY CASE a.status WHEN 'unhandled' THEN 0 ELSE 1 END, a.alert_time DESC, a.alert_id DESC").fetchall()
    return [as_dict(row) for row in rows]


@app.put("/api/alerts/{alert_id}")
def update_alert(alert_id: int, payload: AlertPayload, _: dict[str, Any] = Depends(require_roles("admin", "guard"))) -> dict[str, Any]:
    with db() as connection:
        if connection.execute("SELECT alert_id FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        connection.execute("UPDATE alerts SET status = ?, handler = ? WHERE alert_id = ?", (payload.status, payload.handler, alert_id))
    return {"alert_id": alert_id, "status": payload.status, "handler": payload.handler}


@app.get("/api/cameras")
def cameras(_: dict[str, Any] = Depends(require_roles("admin", "guard"))) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute("SELECT * FROM cameras ORDER BY camera_id").fetchall()
    return [as_dict(row) for row in rows]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=5000, reload=True)
