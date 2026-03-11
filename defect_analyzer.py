import io
import json
import os
import sys
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image, ImageOps
from sheet_service import find_construction_method

API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = "gemini-3-flash-preview"

SYSTEM_INSTRUCTION = """
You are an expert building maintenance inspector from Korea. Your task is to analyze an image and an optional user-provided description of a building defect and generate a standardized defect code.

Analyze the user's input (image and text) and provide a JSON response with the code for each category and a brief, human-readable summary of the defect.

You must classify the defect based on the following five categories and their corresponding codes. Respond ONLY with a JSON object that follows the provided schema. The 'description' in your JSON response should be the Korean text from the table.

**IMPORTANT RULE FOR ROOF (RF):**
If the Area is 'RF' (Roof), you MUST classify the Detailed Area as either 'AS' (Asphalt Shingle) or 'MT' (Metal Tile) if the material resembles them.
- Use 'AS' for flat, overlapping shingle patterns.
- Use 'MT' for curved, metallic tile patterns.
- Only use 'OS' or 'XX' if it is definitively neither.

**1. 분야 (Field):**
- W: 방수 (Waterproofing)
- P: 도장 (Paint/Repaint)
- M: 보수/보강 (Maintenance)
- R: 실내 리페어/마감 (Repair/Finishing)

**2. 영역 (Area):**
- RF: 지붕 (Roof) -> Must be paired with AS or MT if applicable.
- RT: 옥상(슬라브) (Rooftop - Slab)
- EX: 외벽 (Exterior Wall)
- AC: 아스콘 (Asphalt Concrete)
- PB: 보도블럭 (Paving Block)
- BP: 지하주차장 (Basement Parking Floor)
- IT: 실내 (Interior)

**3. 세부영역 (Detailed Area):**
- AS: 아스팔트슁글 (Asphalt Shingle)
- MT: 금속기와 (Metal tile)
- OS: 기타구조물 (Other Structures)
- EG: 측구/경계석 (Edge/Gutter)
- OF: 배수/드레인 (Outflow)
- BR: 욕실 (Bathroom)
- KT: 주방 (Kitchen)
- LV: 거실 (Living Room)
- BD: 침실 (Bedroom)
- EN: 현관/출입구 (Entrance)
- XX: 일반 (Generic)

**4. 부위 (Part):**
- CL: 천장 (Ceiling)
- FL: 바닥 (Floor)
- WL: 벽체 (Wall)
- CC: 모서리/곡각지점 (Corners/Curved Point)
- FS: 후레싱 (Flashing)
- JT: 이음부/조인트 (Joint)
- OF: 배수/드레인 (Outflow)
- WD: 문틀/창틀 (Window/Door Frame)
- XX: 일반 (Generic)

**5. 하자유형 (Defect Type):**
- DL: 자재탈락/파손 (Detach/Loss)
- LE: 누수 (Leak)
- CR: 균열 (Crack)
- PE: 박리/박락 (Peeling/Delamination)
- BL: 도막 들뜸 (Blistering/Lifting)
- TE: 찢김 (Tear)
- DR: 노후화 (Deterioration)
- ST: 오염/곰팡이 (Stain/Mold)
- CO: 결로 (Condensation)
- SL: 미끄럼/바닥안전 (Slippery Surface)
- CL: 변색/오염착색 (Color Fading/Stain)

Respond with a JSON object in this exact format:
{
  "field": { "code": "...", "description": "..." },
  "area": { "code": "...", "description": "..." },
  "detailed_area": { "code": "...", "description": "..." },
  "part": { "code": "...", "description": "..." },
  "defect_type": { "code": "...", "description": "..." },
  "defect_code": "...",
  "summary": "..."
}

The "defect_code" field must be the concatenation: FIELD-AREA-DETAILED_AREA-PART-DEFECT_TYPE (e.g. "W-RF-AS-FS-LE").
The "summary" field must be a brief Korean sentence describing the defect.
"""


def load_image(image_path: str) -> tuple[bytes, str]:
    """Load image from local file path. Returns (bytes, mime_type)."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    suffix = path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    mime_type = mime_map.get(suffix, "image/jpeg")
    return path.read_bytes(), mime_type


def analyze_defect(image_path: str, user_description: str = "") -> dict:
    """
    Analyze a building defect image and return a structured defect code.

    Args:
        image_path: Path to the building image file.
        user_description: Optional text description of the defect.

    Returns:
        dict with defect classification and code.
    """
    client = genai.Client(api_key=API_KEY)

    image_bytes, mime_type = load_image(image_path)

    text_prompt = user_description if user_description else "이미지에서 건물 하자를 분석하고 하자 코드를 생성해주세요."

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            text_prompt,
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
        ),
    )

    result = json.loads(response.text)

    construction = find_construction_method(result.get("defect_code", ""))
    if construction:
        result["construction_method"] = construction

    report = generate_report_content(result)
    if report:
        result["report"] = report

    return result


def generate_report_content(defect_result: dict) -> dict | None:
    """Generate rich Korean inspection report content via a second Gemini call."""
    client = genai.Client(api_key=API_KEY)

    field   = defect_result.get("field", {})
    area    = defect_result.get("area", {})
    detail  = defect_result.get("detailed_area", {})
    part    = defect_result.get("part", {})
    dtype   = defect_result.get("defect_type", {})
    cm      = defect_result.get("construction_method", {})

    prompt = f"""당신은 대한민국 건물 하자 진단 전문가입니다. 아래 하자 분석 결과를 바탕으로 상세 진단 리포트 내용을 JSON으로 생성하세요.

[하자 분석 결과]
- 하자 코드: {defect_result.get("defect_code", "N/A")}
- 분야: {field.get("code", "")} - {field.get("description", "")}
- 영역: {area.get("code", "")} - {area.get("description", "")}
- 세부영역: {detail.get("code", "")} - {detail.get("description", "")}
- 부위: {part.get("code", "")} - {part.get("description", "")}
- 하자유형: {dtype.get("code", "")} - {dtype.get("description", "")}
- 요약: {defect_result.get("summary", "")}
- 추천 공법: {cm.get("method_name", "N/A")}

아래 JSON 스키마에 맞게 응답하세요 (모든 텍스트는 한국어):
{{
  "report_title": "구체적 리포트 제목 (예: 지붕 금속기와 누수 진단 리포트)",
  "urgency": "위험 | 높음 | 보통 | 낮음 중 하나",
  "confidence": 70~99 사이 정수,
  "diagnosis_paragraph_1": "하자 현상과 직접 원인을 설명하는 2~3문장",
  "diagnosis_paragraph_2": "심화 원인과 구조적 취약점을 설명하는 2~3문장",
  "mechanism_root_cause": "근본 원인 1~2문장",
  "mechanism_progression": "진행 과정 (→ 기호 활용, 1~2문장)",
  "mechanism_accelerator": "촉진 요인 1~2문장",
  "mechanism_current_state": "현 상태와 방치 시 위험 1~2문장",
  "repair_recommendation_text": "단순 보수의 한계와 추천 공법 필요성 1~2문장",
  "risk_percentage": 0~100 사이 정수 (현재 재발 위험도),
  "risk_level": "매우 높음 | 높음 | 보통 | 낮음 중 하나",
  "risk_after_repair_percentage": 0~30 사이 정수 (공법 적용 후 위험도),
  "risk_improvement_text": "추천 공법 적용 시 위험도 감소 효과 설명 1~2문장"
}}"""

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=[types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"[defect_analyzer] generate_report_content failed: {e}")
        return None


_SUPPORTED_RATIOS = {
    "1:1":  1 / 1,
    "3:4":  3 / 4,
    "4:3":  4 / 3,
    "9:16": 9 / 16,
    "16:9": 16 / 9,
}


def resize_to_closest_ratio(image_bytes: bytes) -> tuple[bytes, str, str]:
    """
    Center-crop the image to the closest supported aspect ratio, then return
    the result as JPEG bytes plus the matched ratio label.

    Supported ratios: 1:1, 3:4, 4:3, 9:16, 16:9

    Returns:
        (jpeg_bytes, "image/jpeg", ratio_label)  e.g. ("...", "image/jpeg", "16:9")
    """
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    w, h = img.size
    current_ratio = w / h

    # Pick the closest supported ratio
    ratio_label, target_ratio = min(
        _SUPPORTED_RATIOS.items(),
        key=lambda kv: abs(kv[1] - current_ratio),
    )

    # Center-crop to target ratio
    if target_ratio > current_ratio:
        # Target is wider → crop height
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    else:
        # Target is taller → crop width
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), "image/png", ratio_label


_SOL_IMG_DIR = Path(__file__).parent / "sol_img"

_REPAIR_PROMPTS = {
    "RF": "apply heavy reddish-brown sheet based elastomeric waterproof solution to the roof tiles, vacuum packing the roof tile, with a smooth semi-gloss finish and make roof tile to brand new condition.",
    "RT": "Repair the rooftop slab in [Base.png] completely. Apply a green urethane waterproofing coating. The surface should be glossy, smooth, and completely sealed against water. Apply the Roof vent Strainer shown in [RT.png].",
    "OF": "Apply a clean green urethane waterproofing coating to the floor area in [Base.png]. The surface should be glossy, smooth, and completely sealed. Replace the drain cover with the one shown in [OF.png].",
}

_REFERENCE_IMAGES = {
    "RT": _SOL_IMG_DIR / "RT.png",
    "OF": _SOL_IMG_DIR / "OF.png",
}


def generate_repaired_image(image_bytes: bytes, defect_result: dict) -> tuple:
    """
    Generate a photorealistic image of the building in brand-new repaired condition.
    Uses area-specific prompts and reference images for RF, RT, and OF.

    Args:
        image_bytes: Raw bytes of the original defect image.
        defect_result: The defect analysis dict returned by analyze_defect().

    Returns:
        (image_bytes, mime_type) of the generated image, or (None, None) on failure.
    """
    client = genai.Client(api_key=API_KEY)

    # Resize to closest supported ratio and capture ratio_label for ImageConfig
    image_bytes, _, ratio_label = resize_to_closest_ratio(image_bytes)

    area_code         = defect_result.get("area", {}).get("code", "")
    detailed_area_code = defect_result.get("detailed_area", {}).get("code", "")
    part_code         = defect_result.get("part", {}).get("code", "")

    # Priority: RF (area) → RT (area) → OF (detailed_area or part) → default
    if area_code == "RF":
        prompt_key = "RF"
    elif area_code == "RT":
        prompt_key = "RT"
    elif detailed_area_code == "OF" or part_code == "OF":
        prompt_key = "OF"
    else:
        prompt_key = None

    prompt = _REPAIR_PROMPTS.get(prompt_key, "Make the space brand new condition.")
    model = "gemini-3-pro-image-preview" if prompt_key == "RT" else "gemini-2.5-flash-image"

    # Build parts: original image sent as Base.png
    parts = [types.Part.from_bytes(data=image_bytes, mime_type="image/png")]

    # Attach reference image for RT and OF
    ref_path = _REFERENCE_IMAGES.get(prompt_key)
    if ref_path and ref_path.exists():
        ref_bytes = ref_path.read_bytes()
        parts.append(types.Part.from_bytes(data=ref_bytes, mime_type="image/png"))

    parts.append(types.Part.from_text(text=prompt))

    contents = [
        types.Content(
            role="user",
            parts=parts,
        ),
    ]

    generate_content_config = types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio=ratio_label),
        response_modalities=[
            "IMAGE",
            "TEXT",
        ],
    )

    result_data = None
    result_mime = None

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        if chunk.parts is None:
            continue
        if chunk.parts[0].inline_data and chunk.parts[0].inline_data.data:
            result_data = chunk.parts[0].inline_data.data
            result_mime = chunk.parts[0].inline_data.mime_type

    return result_data, result_mime


def print_result(result: dict) -> None:
    """Pretty-print the defect analysis result."""
    print("\n" + "=" * 50)
    print("건물 하자 분석 결과")
    print("=" * 50)
    print(f"  하자 코드   : {result.get('defect_code', 'N/A')}")
    print(f"  분야        : {result['field']['code']} - {result['field']['description']}")
    print(f"  영역        : {result['area']['code']} - {result['area']['description']}")
    print(f"  세부영역    : {result['detailed_area']['code']} - {result['detailed_area']['description']}")
    print(f"  부위        : {result['part']['code']} - {result['part']['description']}")
    print(f"  하자유형    : {result['defect_type']['code']} - {result['defect_type']['description']}")
    print(f"  요약        : {result.get('summary', '')}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python defect_analyzer.py <image_path> [description]")
        print('Example: python defect_analyzer.py photo.jpg "지붕에서 누수 발생"')
        sys.exit(1)

    image_path = sys.argv[1]
    description = sys.argv[2] if len(sys.argv) > 2 else ""

    print(f"Analyzing: {image_path}")
    result = analyze_defect(image_path, description)
    print_result(result)

    # Also save raw JSON
    output_file = Path(image_path).stem + "_defect.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Raw JSON saved to: {output_file}")
