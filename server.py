import base64
import os
import tempfile
import threading
import uuid

from flask import Flask, request, jsonify, send_from_directory, send_file, render_template
from defect_analyzer import analyze_defect, generate_repaired_image
from database import (
    generate_diagnosis_code, save_analysis, update_repaired_image,
    get_analysis_by_code, save_consultant_note, get_stats, get_analyses_list,
)

app = Flask(__name__, static_folder="templates", template_folder="templates")

# In-memory store for repair image jobs: { job_id: {"status": ..., ...} }
repair_jobs: dict = {}


def _repair_worker(job_id: str, image_bytes: bytes, defect_result: dict, diagnosis_code: str):
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


# ── Admin dashboard
@app.route("/admin")
def admin():
    return render_template("admin.html")


# ── Consultant lookup page
@app.route("/consult")
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

    # Attach image as base64 if available
    orig_path = record.get("original_image_path")
    if orig_path and os.path.exists(orig_path):
        with open(orig_path, "rb") as f:
            record["original_image_b64"] = base64.b64encode(f.read()).decode()
    repair_path = record.get("repaired_image_path")
    if repair_path and os.path.exists(repair_path):
        with open(repair_path, "rb") as f:
            record["repaired_image_b64"] = base64.b64encode(f.read()).decode()

    return jsonify(record)


# ── API: save consultant note
@app.route("/api/note/<code>", methods=["POST"])
def api_note(code):
    note = (request.json or {}).get("note", "")
    save_consultant_note(code.upper(), note)
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=False, host="0.0.0.0", port=port)
