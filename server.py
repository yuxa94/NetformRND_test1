import base64
import os
import tempfile
import threading
import uuid
from functools import wraps

import config  # noqa: F401 — loads environment-specific .env

from flask import Flask, request, jsonify, send_from_directory, send_file, render_template, session, redirect, url_for
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, ConstructionMethod, Specification, AdminAuth
from defect_analyzer import analyze_defect, generate_repaired_image
from database import (
    generate_diagnosis_code, save_analysis, update_repaired_image,
    get_analysis_by_code, save_consultant_note, get_stats, get_analyses_list,
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__, static_folder="templates", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32).hex())
_default_db = "sqlite:///" + os.path.join(BASE_DIR, "data.db")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", _default_db)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()
    # Seed default admin password from env if table is empty
    if not AdminAuth.query.first():
        default_pw = os.environ.get("ADMIN_PASSWORD", "admin1234")
        auth = AdminAuth(password_hash=generate_password_hash(default_pw))
        db.session.add(auth)
        db.session.commit()


# ── Auth helpers ──────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        # Check session expiry
        expires = session.get("expires_at")
        if expires and datetime.fromisoformat(expires) < datetime.now():
            session.clear()
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# In-memory store for repair image jobs: { job_id: {"status": ..., ...} }
repair_jobs: dict = {}


def _repair_worker(job_id: str, image_bytes: bytes, defect_result: dict, diagnosis_code: str):
    with app.app_context():
        try:
            img_data, img_mime = generate_repaired_image(image_bytes, defect_result)
            if img_data:
                update_repaired_image(diagnosis_code, img_data)
                repair_jobs[job_id] = {
                    "status": "done",
                    "image": base64.b64encode(img_data).decode(),
                    "mime_type": img_mime,
                }
            else:
                repair_jobs[job_id] = {"status": "error", "error": "모델이 이미지를 생성하지 못했습니다."}
        except Exception as e:
            repair_jobs[job_id] = {"status": "error", "error": str(e)}


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    image_file = request.files["image"]
    description = request.form.get("description", "")

    suffix = os.path.splitext(image_file.filename)[1] or ".jpg"
    image_bytes = image_file.read()

    # Write to temp file for defect analysis
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        result = analyze_defect(tmp_path, description)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        os.unlink(tmp_path)

    # Generate diagnosis code and save to DB
    diagnosis_code = generate_diagnosis_code()
    try:
        save_analysis(result, image_bytes, diagnosis_code)
    except Exception as e:
        print(f"[server] DB save failed: {e}")

    # Kick off image generation in background thread
    job_id = str(uuid.uuid4())
    repair_jobs[job_id] = {"status": "pending"}
    thread = threading.Thread(
        target=_repair_worker,
        args=(job_id, image_bytes, result, diagnosis_code),
        daemon=True,
    )
    thread.start()

    result["repair_job_id"] = job_id
    result["diagnosis_code"] = diagnosis_code
    return jsonify(result)


@app.route("/repair-status/<job_id>")
def repair_status(job_id):
    job = repair_jobs.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)


# ── Login / Logout ─────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next", "/admin"))

    # POST — verify password
    password = (request.json or request.form).get("password", "")
    auth = AdminAuth.query.first()
    if not auth or not check_password_hash(auth.password_hash, password):
        return jsonify({"error": "비밀번호가 올바르지 않습니다."}), 401

    timeout = auth.session_timeout_minutes or 30
    session["authenticated"] = True
    session["expires_at"] = (datetime.now() + timedelta(minutes=timeout)).isoformat()
    next_url = (request.json or request.form).get("next", "/admin")
    return jsonify({"ok": True, "next": next_url})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── API: change admin password
@app.route("/api/auth/password", methods=["PUT"])
@login_required
def change_password():
    d = request.json or {}
    current_pw = d.get("current_password", "")
    new_pw = d.get("new_password", "")
    if not new_pw or len(new_pw) < 4:
        return jsonify({"error": "새 비밀번호는 4자 이상이어야 합니다."}), 400

    auth = AdminAuth.query.first()
    if not check_password_hash(auth.password_hash, current_pw):
        return jsonify({"error": "현재 비밀번호가 올바르지 않습니다."}), 401

    auth.password_hash = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({"ok": True})


# ── Account management page
@app.route("/account")
@login_required
def account():
    return render_template("account.html")


# ── Admin dashboard
@app.route("/admin")
@login_required
def admin():
    return render_template("admin.html")


# ── Consultant lookup page
@app.route("/consult")
@login_required
def consult():
    return render_template("consult.html")


# ── API: analyses list
@app.route("/api/analyses")
def api_analyses():
    limit  = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    search = request.args.get("search", "")
    return jsonify(get_analyses_list(limit=limit, offset=offset, search=search))


# ── API: stats for admin dashboard
@app.route("/api/stats")
def api_stats():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    quarter = request.args.get("quarter", type=int)
    material = request.args.get("material", "전체")
    stats = get_stats(year=year, month=month, quarter=quarter, material=material)
    return jsonify(stats)


# ── API: lookup by diagnosis code
@app.route("/api/lookup/<code>")
def api_lookup(code):
    record = get_analysis_by_code(code.upper())
    if not record:
        return jsonify({"error": "진단 코드를 찾을 수 없습니다."}), 404

    # Image URLs are already CDN URLs from S3
    record["original_image_url"] = record.get("original_image_path")
    record["repaired_image_url"] = record.get("repaired_image_path")

    return jsonify(record)


# ── API: save consultant note
@app.route("/api/note/<code>", methods=["POST"])
def api_note(code):
    note = (request.json or {}).get("note", "")
    save_consultant_note(code.upper(), note)
    return jsonify({"ok": True})


# ── Construction methods management page
@app.route("/methods")
def methods():
    return render_template("methods.html")


# ── API: construction methods CRUD
@app.route("/api/methods")
def api_methods_list():
    search = request.args.get("search", "")
    query = ConstructionMethod.query.filter(ConstructionMethod.deleted_at.is_(None))
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                ConstructionMethod.code.like(like),
                ConstructionMethod.method_name.like(like),
                ConstructionMethod.main_use.like(like),
            )
        )
    rows = query.order_by(ConstructionMethod.id).all()
    return jsonify([{
        "id": m.id,
        "code": m.code,
        "method_name": m.method_name,
        "main_use": m.main_use,
        "core_composition": m.core_composition,
        "key_advantages": m.key_advantages,
        "example_link": m.example_link,
    } for m in rows])


@app.route("/api/methods", methods=["POST"])
def api_methods_create():
    d = request.json or {}
    m = ConstructionMethod(
        code=d.get("code", ""),
        method_name=d.get("method_name", ""),
        main_use=d.get("main_use", ""),
        core_composition=d.get("core_composition", ""),
        key_advantages=d.get("key_advantages", ""),
        example_link=d.get("example_link", ""),
    )
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "id": m.id}), 201


@app.route("/api/methods/<int:mid>")
def api_methods_get(mid):
    m = ConstructionMethod.query.get_or_404(mid)
    return jsonify({
        "id": m.id,
        "code": m.code,
        "method_name": m.method_name,
        "main_use": m.main_use,
        "core_composition": m.core_composition,
        "key_advantages": m.key_advantages,
        "example_link": m.example_link,
    })


@app.route("/api/methods/<int:mid>", methods=["PUT"])
def api_methods_update(mid):
    m = ConstructionMethod.query.get_or_404(mid)
    d = request.json or {}
    for field in ("code", "method_name", "main_use", "core_composition", "key_advantages", "example_link"):
        if field in d:
            setattr(m, field, d[field])
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/methods/<int:mid>", methods=["DELETE"])
def api_methods_delete(mid):
    m = ConstructionMethod.query.get_or_404(mid)
    m.deleted_at = datetime.now()
    db.session.commit()
    return jsonify({"ok": True})


# ── API: specifications CRUD
@app.route("/api/specs")
def api_specs_list():
    search = request.args.get("search", "")
    query = Specification.query.filter(Specification.deleted_at.is_(None))
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                Specification.method_name.like(like),
                Specification.spec_link.like(like),
            )
        )
    rows = query.order_by(Specification.id).all()
    return jsonify([{
        "id": s.id,
        "method_name": s.method_name,
        "spec_link": s.spec_link,
    } for s in rows])


@app.route("/api/specs", methods=["POST"])
def api_specs_create():
    d = request.json or {}
    s = Specification(
        method_name=d.get("method_name", ""),
        spec_link=d.get("spec_link", ""),
    )
    db.session.add(s)
    db.session.commit()
    return jsonify({"ok": True, "id": s.id}), 201


@app.route("/api/specs/<int:sid>")
def api_specs_get(sid):
    s = Specification.query.get_or_404(sid)
    return jsonify({
        "id": s.id,
        "method_name": s.method_name,
        "spec_link": s.spec_link,
    })


@app.route("/api/specs/<int:sid>", methods=["PUT"])
def api_specs_update(sid):
    s = Specification.query.get_or_404(sid)
    d = request.json or {}
    for field in ("method_name", "spec_link"):
        if field in d:
            setattr(s, field, d[field])
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/specs/<int:sid>", methods=["DELETE"])
def api_specs_delete(sid):
    s = Specification.query.get_or_404(sid)
    s.deleted_at = datetime.now()
    db.session.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host="0.0.0.0", port=port)
