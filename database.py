import json
import os
import uuid
from datetime import datetime

from sqlalchemy import or_, func, extract
from models import db, Analysis
from s3_storage import upload_original, upload_generated

MATERIAL_MAP = {
    "AS": "아스팔트슁글",
    "MT": "금속기와",
}


def generate_diagnosis_code() -> str:
    """Generate AI-YYYYMMDD-XXXX format code using UUID suffix."""
    today = datetime.now().strftime("%Y%m%d")
    short_id = uuid.uuid4().hex[:6].upper()
    return f"AI-{today}-{short_id}"


def save_analysis(result: dict, orig_img_bytes: bytes, diagnosis_code: str) -> int:
    """Save analysis result and original image to DB. Returns row id.
    Retries with a new code on duplicate key conflict."""
    img_url = upload_original(orig_img_bytes)

    area_code = result.get("area", {}).get("code", "")
    detailed_area_code = result.get("detailed_area", {}).get("code", "")

    if area_code == "RF":
        material_type = MATERIAL_MAP.get(detailed_area_code, "기타")
    else:
        material_type = None

    report = result.get("report") or {}
    cm = result.get("construction_method") or {}

    for attempt in range(5):
        try:
            analysis = Analysis(
                diagnosis_code=diagnosis_code,
                field_code=result.get("field", {}).get("code", ""),
                area_code=area_code,
                detailed_area_code=detailed_area_code,
                part_code=result.get("part", {}).get("code", ""),
                defect_type_code=result.get("defect_type", {}).get("code", ""),
                defect_code=result.get("defect_code", ""),
                material_type=material_type,
                urgency=report.get("urgency", ""),
                confidence=report.get("confidence"),
                risk_percentage=report.get("risk_percentage"),
                summary=result.get("summary", ""),
                report_json=json.dumps(report, ensure_ascii=False),
                construction_method_json=json.dumps(cm, ensure_ascii=False),
                original_image_path=img_url,
            )
            db.session.add(analysis)
            db.session.commit()
            return analysis.id, diagnosis_code
        except Exception:
            db.session.rollback()
            # Regenerate code to avoid duplicate
            diagnosis_code = generate_diagnosis_code()
    raise RuntimeError(f"Failed to save analysis after 5 attempts")


def update_repaired_image(diagnosis_code: str, repaired_img_bytes: bytes):
    """Save repaired image to S3 and update DB URL."""
    img_url = upload_generated(repaired_img_bytes)

    analysis = Analysis.query.filter_by(diagnosis_code=diagnosis_code).first()
    if analysis:
        analysis.repaired_image_path = img_url
        db.session.commit()


def get_analysis_by_code(diagnosis_code: str) -> dict | None:
    """Fetch full analysis record by diagnosis code."""
    analysis = Analysis.query.filter_by(diagnosis_code=diagnosis_code).first()
    if not analysis:
        return None
    d = analysis.to_dict()
    # Convert datetime to string for JSON serialization
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    d["report"] = json.loads(d["report_json"] or "{}")
    d["construction_method"] = json.loads(d["construction_method_json"] or "{}")
    return d


def get_analyses_list(limit: int = 50, offset: int = 0, search: str = "") -> dict:
    """Return paginated list of analyses (lightweight, no images)."""
    query = Analysis.query

    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Analysis.diagnosis_code.like(like),
                Analysis.defect_code.like(like),
                Analysis.summary.like(like),
            )
        )

    total = query.count()
    rows = (
        query.order_by(Analysis.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for r in rows:
        items.append({
            "diagnosis_code": r.diagnosis_code,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "defect_code": r.defect_code,
            "material_type": r.material_type,
            "urgency": r.urgency,
            "confidence": r.confidence,
            "summary": r.summary,
        })

    return {"total": total, "items": items}


def save_consultant_note(diagnosis_code: str, note: str):
    analysis = Analysis.query.filter_by(diagnosis_code=diagnosis_code).first()
    if analysis:
        analysis.consultant_notes = note
        db.session.commit()


def _base_filters():
    """Build reusable filter conditions list."""
    return []


def get_stats(year: int = None, month: int = None, quarter: int = None, material: str = None) -> dict:
    """Return stats for admin dashboard. Filters: year, month, quarter, material."""
    now = datetime.now()
    year = year or now.year

    filters = [extract("year", Analysis.created_at) == year]

    if month:
        filters.append(extract("month", Analysis.created_at) == month)
    elif quarter:
        month_ranges = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
        start_m, end_m = month_ranges.get(quarter, (1, 12))
        filters.append(extract("month", Analysis.created_at).between(start_m, end_m))

    if material and material != "전체":
        filters.append(Analysis.material_type == material)

    # Use subquery for filtered IDs to avoid N+1
    id_subquery = db.session.query(Analysis.id).filter(*filters).subquery()

    total = db.session.query(func.count()).select_from(Analysis).filter(Analysis.id.in_(id_subquery)).scalar()

    danger = (
        db.session.query(func.count())
        .select_from(Analysis)
        .filter(Analysis.id.in_(id_subquery), Analysis.urgency.in_(["위험", "높음"]))
        .scalar()
    )

    # Daily counts
    daily_rows = (
        db.session.query(
            func.date(Analysis.created_at).label("day"),
            func.count().label("cnt"),
        )
        .filter(Analysis.id.in_(id_subquery))
        .group_by(func.date(Analysis.created_at))
        .order_by(func.date(Analysis.created_at))
        .all()
    )
    daily = [{"day": str(r.day), "count": r.cnt} for r in daily_rows]

    # Material breakdown
    mat_label = func.coalesce(Analysis.material_type, "기타")
    mat_rows = (
        db.session.query(
            mat_label.label("mat"),
            func.count().label("cnt"),
        )
        .filter(Analysis.id.in_(id_subquery))
        .group_by(mat_label)
        .all()
    )
    material_breakdown = [{"material": r.mat, "count": r.cnt} for r in mat_rows]

    # Defect type breakdown
    defect_rows = (
        db.session.query(
            Analysis.defect_type_code,
            func.count().label("cnt"),
        )
        .filter(Analysis.id.in_(id_subquery))
        .group_by(Analysis.defect_type_code)
        .order_by(func.count().desc())
        .all()
    )
    defect_breakdown = [{"code": r.defect_type_code, "count": r.cnt} for r in defect_rows]

    # Monthly counts — use extract for DB portability
    year_col = extract("year", Analysis.created_at)
    month_col = extract("month", Analysis.created_at)
    monthly_rows = (
        db.session.query(
            year_col.label("y"),
            month_col.label("m"),
            func.count().label("cnt"),
        )
        .filter(Analysis.id.in_(id_subquery))
        .group_by(year_col, month_col)
        .order_by(year_col, month_col)
        .all()
    )
    monthly = [{"month": f"{int(r.y)}-{int(r.m):02d}", "count": r.cnt} for r in monthly_rows]

    return {
        "total": total,
        "danger": danger,
        "daily": daily,
        "monthly": monthly,
        "material_breakdown": material_breakdown,
        "defect_breakdown": defect_breakdown,
    }
