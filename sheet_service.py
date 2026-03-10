import csv
import io
import ssl
import urllib.request

SPREADSHEET_ID = "1TVJRGZyoA6URDBd95SIb_i9AhXFUJUcwdYuezi_oQnU"
SHEET_NAME = "Sheet1"
SHEET2_NAME = "Sheet2"

_cache: list | None = None
_cache2: list | None = None


def _fetch_sheet_data() -> list:
    global _cache
    if _cache is not None:
        return _cache

    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"
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
    rows = list(reader)

    # Skip header row, keep rows with at least 5 cols and a non-empty code
    data = [row for row in rows[1:] if len(row) >= 5 and row[0].strip()]
    _cache = data
    return _cache


def _fetch_sheet2_data() -> list:
    global _cache2
    if _cache2 is not None:
        return _cache2

    url = (
        f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={SHEET2_NAME}"
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
    rows = list(reader)

    # Parse header to find column indices for 공법명 and 시방서 링크
    if not rows:
        _cache2 = []
        return _cache2

    header = [h.strip() for h in rows[0]]
    try:
        method_col = header.index("공법명")
    except ValueError:
        method_col = 0
    try:
        spec_col = header.index("시방서 링크")
    except ValueError:
        spec_col = 1

    data = []
    for row in rows[1:]:
        if len(row) > max(method_col, spec_col) and row[method_col].strip():
            data.append({
                "method_name": row[method_col].strip(),
                "spec_link": row[spec_col].strip() if len(row) > spec_col else "",
            })

    _cache2 = data
    return _cache2


def get_specification_link(method_name: str) -> str | None:
    """Look up 시방서 링크 from Sheet2 by exact 공법명 match (case-insensitive)."""
    try:
        data = _fetch_sheet2_data()
    except Exception as e:
        print(f"[sheet_service] Failed to fetch Sheet2: {e}")
        return None

    normalized = method_name.strip().lower()
    for entry in data:
        if entry["method_name"].lower() == normalized:
            return entry["spec_link"] or None
    return None


def _normalize(code: str) -> str:
    """Strip brackets and normalize separators to dot for comparison."""
    return code.replace("[", "").replace("]", "").replace("-", ".").strip()


def _calculate_similarity(code1: str, code2: str) -> int:
    parts1 = _normalize(code1).split(".")
    parts2 = _normalize(code2).split(".")
    if len(parts1) != 5 or len(parts2) != 5:
        return 0
    return sum(1 for a, b in zip(parts1, parts2) if a == b)


def find_construction_method(defect_code: str) -> dict | None:
    try:
        data = _fetch_sheet_data()
    except Exception as e:
        print(f"[sheet_service] Failed to fetch sheet: {e}")
        return None

    if not data:
        return None

    best_row = None
    best_score = -1
    is_exact = False
    normalized_input = _normalize(defect_code)

    for row in data:
        if not row or not row[0].strip():
            continue
        sheet_code = row[0].strip()

        if _normalize(sheet_code) == normalized_input:
            best_row = row
            is_exact = True
            break

        score = _calculate_similarity(defect_code, sheet_code)
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None:
        return None

    code             = best_row[0].strip() if len(best_row) > 0 else ""
    method_name      = best_row[1].strip() if len(best_row) > 1 else "N/A"
    main_use         = best_row[2].strip() if len(best_row) > 2 else "N/A"
    core_composition = best_row[3].strip() if len(best_row) > 3 else "N/A"
    key_advantages   = best_row[4].strip() if len(best_row) > 4 else "N/A"
    example_link     = best_row[5].strip() if len(best_row) > 5 else ""

    # If the best method has no example link, find the most similar row that does
    if not example_link:
        best_ex_row = None
        best_ex_score = -1
        for row in data:
            if not row or len(row) < 6 or not row[0].strip() or not row[5].strip():
                continue
            score = _calculate_similarity(defect_code, row[0])
            if score > best_ex_score:
                best_ex_score = score
                best_ex_row = row
        if best_ex_row:
            example_link = best_ex_row[5].strip()

    spec_link = get_specification_link(method_name) if method_name and method_name != "N/A" else None

    return {
        "code": code,
        "method_name": method_name,
        "main_use": main_use,
        "core_composition": core_composition,
        "key_advantages": key_advantages,
        "is_similar_match": not is_exact,
        "example_link": example_link or None,
        "spec_link": spec_link,
    }
