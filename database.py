import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"
UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

MATERIAL_MAP = {
    "AS": "아스팔트슁글",
    "MT": "금속기와",
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                diagnosis_code          TEXT UNIQUE NOT NULL,
                created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
                field_code              TEXT,
                area_code               TEXT,
                detailed_area_code      TEXT,
                part_code               TEXT,
                defect_type_code        TEXT,
                defect_code             TEXT,
                material_type           TEXT,
                urgency                 TEXT,
                confidence              INTEGER,
                risk_percentage         INTEGER,
                summary                 TEXT,
                report_json             TEXT,
                construction_method_json TEXT,
                original_image_path     TEXT,
                repaired_image_path     TEXT,
                consultant_notes        TEXT DEFAULT ''
            )
        """)
        conn.commit()


def generate_diagnosis_code() -> str:
    """Generate AI-YYYYMMDD-XXXX format code (sequential per day)."""
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"AI-{today}-"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM analyses WHERE diagnosis_code LIKE ?",
            (prefix + "%",)
        ).fetchone()
        seq = (row["cnt"] or 0) + 1
    return f"{prefix}{seq:04d}"


def save_analysis(result: dict, orig_img_bytes: bytes, diagnosis_code: str) -> int:
    """Save analysis result and original image to DB. Returns row id."""
    # Save original image file
    img_filename = f"orig_{diagnosis_code}.png"
    img_path = UPLOADS_DIR / img_filename
    img_path.write_bytes(orig_img_bytes)

    area_code = result.get("area", {}).get("code", "")
    detailed_area_code = result.get("detailed_area", {}).get("code", "")

    # Material type: only meaningful for RF area
    if area_code == "RF":
        material_type = MATERIAL_MAP.get(detailed_area_code, "기타")
    else:
        material_type = None

    report = result.get("report") or {}
    cm = result.get("construction_method") or {}

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO analyses (
                diagnosis_code, field_code, area_code, detailed_area_code,
                part_code, defect_type_code, defect_code, material_type,
                urgency, confidence, risk_percentage, summary,
                report_json, construction_method_json, original_image_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            diagnosis_code,
            result.get("field", {}).get("code", ""),
            area_code,
            detailed_area_code,
            result.get("part", {}).get("code", ""),
            result.get("defect_type", {}).get("code", ""),
            result.get("defect_code", ""),
            material_type,
            report.get("urgency", ""),
            report.get("confidence"),
            report.get("risk_percentage"),
            result.get("summary", ""),
            json.dumps(report, ensure_ascii=False),
            json.dumps(cm, ensure_ascii=False),
            str(img_path),
        ))
        conn.commit()
        return cur.lastrowid


def update_repaired_image(diagnosis_code: str, repaired_img_bytes: bytes):
    """Save repaired image and update DB path."""
    img_filename = f"repair_{diagnosis_code}.png"
    img_path = UPLOADS_DIR / img_filename
    img_path.write_bytes(repaired_img_bytes)
    with get_conn() as conn:
        conn.execute(
            "UPDATE analyses SET repaired_image_path = ? WHERE diagnosis_code = ?",
            (str(img_path), diagnosis_code)
        )
        conn.commit()


def get_analysis_by_code(diagnosis_code: str) -> dict | None:
    """Fetch full analysis record by diagnosis code."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE diagnosis_code = ?",
            (diagnosis_code,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["report"] = json.loads(d["report_json"] or "{}")
    d["construction_method"] = json.loads(d["construction_method_json"] or "{}")
    return d


def get_analyses_list(limit: int = 50, offset: int = 0, search: str = "") -> dict:
    """Return paginated list of analyses (lightweight, no images)."""
    with get_conn() as conn:
        if search:
            like = f"%{search}%"
            rows = conn.execute(
                """SELECT diagnosis_code, created_at, defect_code, material_type,
                          urgency, confidence, summary
                   FROM analyses
                   WHERE diagnosis_code LIKE ? OR defect_code LIKE ? OR summary LIKE ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (like, like, like, limit, offset)
            ).fetchall()
            total = conn.execute(
                """SELECT COUNT(*) as cnt FROM analyses
                   WHERE diagnosis_code LIKE ? OR defect_code LIKE ? OR summary LIKE ?""",
                (like, like, like)
            ).fetchone()["cnt"]
        else:
            rows = conn.execute(
                """SELECT diagnosis_code, created_at, defect_code, material_type,
                          urgency, confidence, summary
                   FROM analyses ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (limit, offset)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) as cnt FROM analyses").fetchone()["cnt"]
    return {"total": total, "items": [dict(r) for r in rows]}


def save_consultant_note(diagnosis_code: str, note: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE analyses SET consultant_notes = ? WHERE diagnosis_code = ?",
            (note, diagnosis_code)
        )
        conn.commit()


def get_stats(year: int = None, month: int = None, quarter: int = None, material: str = None) -> dict:
    """Return stats for admin dashboard. Filters: year, month, quarter, material."""
    now = datetime.now()
    year = year or now.year

    # Build WHERE clause
    conditions = ["strftime('%Y', created_at) = ?"]
    params = [str(year)]

    if month:
        conditions.append("strftime('%m', created_at) = ?")
        params.append(f"{month:02d}")
    elif quarter:
        months = {1: ("01","02","03"), 2: ("04","05","06"), 3: ("07","08","09"), 4: ("10","11","12")}
        m_tuple = months.get(quarter, ("01","12"))
        conditions.append("strftime('%m', created_at) BETWEEN ? AND ?")
        params += [m_tuple[0], m_tuple[2]]

    if material and material != "전체":
        conditions.append("material_type = ?")
        params.append(material)

    where = "WHERE " + " AND ".join(conditions)

    with get_conn() as conn:
        # Total count
        total = conn.execute(f"SELECT COUNT(*) as cnt FROM analyses {where}", params).fetchone()["cnt"]

        # Danger count
        danger = conn.execute(
            f"SELECT COUNT(*) as cnt FROM analyses {where} AND urgency IN ('위험','높음')", params
        ).fetchone()["cnt"]

        # Daily counts
        daily_rows = conn.execute(
            f"SELECT strftime('%Y-%m-%d', created_at) as day, COUNT(*) as cnt "
            f"FROM analyses {where} GROUP BY day ORDER BY day",
            params
        ).fetchall()
        daily = [{"day": r["day"], "count": r["cnt"]} for r in daily_rows]

        # Material breakdown
        mat_rows = conn.execute(
            f"SELECT COALESCE(material_type,'기타') as mat, COUNT(*) as cnt "
            f"FROM analyses {where} GROUP BY mat",
            params
        ).fetchall()
        material_breakdown = [{"material": r["mat"], "count": r["cnt"]} for r in mat_rows]

        # Defect type breakdown
        defect_rows = conn.execute(
            f"SELECT defect_type_code, COUNT(*) as cnt "
            f"FROM analyses {where} GROUP BY defect_type_code ORDER BY cnt DESC",
            params
        ).fetchall()
        defect_breakdown = [{"code": r["defect_type_code"], "count": r["cnt"]} for r in defect_rows]

        # Monthly counts (for year view)
        monthly_rows = conn.execute(
            f"SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt "
            f"FROM analyses {where} GROUP BY month ORDER BY month",
            params
        ).fetchall()
        monthly = [{"month": r["month"], "count": r["cnt"]} for r in monthly_rows]

    return {
        "total": total,
        "danger": danger,
        "daily": daily,
        "monthly": monthly,
        "material_breakdown": material_breakdown,
        "defect_breakdown": defect_breakdown,
    }


# Initialize DB on import
init_db()
