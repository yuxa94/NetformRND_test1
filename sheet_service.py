from models import db, ConstructionMethod, Specification


def _normalize(code: str) -> str:
    """Strip brackets and normalize separators to dot for comparison."""
    return code.replace("[", "").replace("]", "").replace("-", ".").strip()


def _calculate_similarity(code1: str, code2: str) -> int:
    parts1 = _normalize(code1).split(".")
    parts2 = _normalize(code2).split(".")
    if len(parts1) != 5 or len(parts2) != 5:
        return 0
    return sum(1 for a, b in zip(parts1, parts2) if a == b)


def get_specification_link(method_name: str) -> str | None:
    """Look up 시방서 링크 by exact 공법명 match (case-insensitive)."""
    spec = Specification.query.filter(
        db.func.lower(Specification.method_name) == method_name.strip().lower(),
        Specification.deleted_at.is_(None),
    ).first()
    if spec and spec.spec_link:
        return spec.spec_link
    return None


def find_construction_method(defect_code: str) -> dict | None:
    """Find the best matching construction method for a defect code."""
    methods = ConstructionMethod.query.filter(ConstructionMethod.deleted_at.is_(None)).all()
    if not methods:
        return None

    best_row = None
    best_score = -1
    is_exact = False
    normalized_input = _normalize(defect_code)

    for m in methods:
        sheet_code = m.code.strip()
        if _normalize(sheet_code) == normalized_input:
            best_row = m
            is_exact = True
            break

        score = _calculate_similarity(defect_code, sheet_code)
        if score > best_score:
            best_score = score
            best_row = m

    if best_row is None:
        return None

    example_link = best_row.example_link or ""

    # If no example link, find the most similar row that has one
    if not example_link:
        best_ex_row = None
        best_ex_score = -1
        for m in methods:
            if not m.example_link:
                continue
            score = _calculate_similarity(defect_code, m.code)
            if score > best_ex_score:
                best_ex_score = score
                best_ex_row = m
        if best_ex_row:
            example_link = best_ex_row.example_link

    spec_link = get_specification_link(best_row.method_name) if best_row.method_name and best_row.method_name != "N/A" else None

    return {
        "code": best_row.code,
        "method_name": best_row.method_name or "N/A",
        "main_use": best_row.main_use or "N/A",
        "core_composition": best_row.core_composition or "N/A",
        "key_advantages": best_row.key_advantages or "N/A",
        "is_similar_match": not is_exact,
        "example_link": example_link or None,
        "spec_link": spec_link,
    }
