"""
Seed script: Google Sheets → DB (construction_methods, specifications)

Usage:
    source venv/bin/activate
    python seed_sheets.py
"""
import csv
import io
import os
import ssl
import urllib.request

import config  # noqa: F401 — loads environment-specific .env

from flask import Flask
from models import db, ConstructionMethod, Specification

SPREADSHEET_ID = "1TVJRGZyoA6URDBd95SIb_i9AhXFUJUcwdYuezi_oQnU"


def _fetch_csv(sheet_name: str) -> list[list[str]]:
    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={sheet_name}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        import certifi
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_ctx = ssl.create_default_context()

    with urllib.request.urlopen(req, context=ssl_ctx) as resp:
        csv_text = resp.read().decode("utf-8")

    reader = csv.reader(io.StringIO(csv_text))
    return list(reader)


def seed_construction_methods():
    """Seed Sheet1 → construction_methods table."""
    rows = _fetch_csv("Sheet1")
    if not rows:
        print("Sheet1: no data")
        return

    # Clear existing data
    ConstructionMethod.query.delete()

    count = 0
    for row in rows[1:]:  # skip header
        if len(row) < 5 or not row[0].strip():
            continue
        cm = ConstructionMethod(
            code=row[0].strip(),
            method_name=row[1].strip() if len(row) > 1 else "",
            main_use=row[2].strip() if len(row) > 2 else "",
            core_composition=row[3].strip() if len(row) > 3 else "",
            key_advantages=row[4].strip() if len(row) > 4 else "",
            example_link=row[5].strip() if len(row) > 5 else "",
        )
        db.session.add(cm)
        count += 1

    db.session.commit()
    print(f"construction_methods: {count} rows seeded")


def seed_specifications():
    """Seed Sheet2 → specifications table."""
    rows = _fetch_csv("Sheet2")
    if not rows:
        print("Sheet2: no data")
        return

    header = [h.strip() for h in rows[0]]
    try:
        method_col = header.index("공법명")
    except ValueError:
        method_col = 0
    try:
        spec_col = header.index("시방서 링크")
    except ValueError:
        spec_col = 1

    # Clear existing data
    Specification.query.delete()

    count = 0
    for row in rows[1:]:
        if len(row) > max(method_col, spec_col) and row[method_col].strip():
            spec = Specification(
                method_name=row[method_col].strip(),
                spec_link=row[spec_col].strip() if len(row) > spec_col else "",
            )
            db.session.add(spec)
            count += 1

    db.session.commit()
    print(f"specifications: {count} rows seeded")


def main():
    base_dir = os.path.abspath(os.path.dirname(__file__))
    default_db = "sqlite:///" + os.path.join(base_dir, "data.db")

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", default_db)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()
        print("Fetching Google Sheets data...")
        seed_construction_methods()
        seed_specifications()
        print("Done!")


if __name__ == "__main__":
    main()
