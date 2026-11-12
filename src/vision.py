import base64
import io
import json
import re
import uuid

from PIL import Image

import openrouter

MAX_IMAGE_DIMENSION = 1024
UNIT_OPTIONS = ["개", "병", "봉지", "팩", "단", "g", "kg", "ml", "L", "기타"]
RECOGNITION_PROMPT = (
    '이 냉장고 사진에서 보이는 식재료를 한국어로 인식해서 JSON 배열 형식으로만 답해줘. '
    '각 항목은 name(이름), quantity(수량, 숫자), unit(단위: 개/병/봉지/팩/단/g/kg/ml/L 중 하나)을 포함해줘.\n'
    '수량은 눈에 보이는 낱개를 하나씩 세어서 적어줘. '
    '달걀판처럼 여러 개가 담긴 것도 팩이 아니라 보이는 알의 개수로 세어줘. '
    '가려져서 셀 수 없을 때만 quantity를 1, unit을 "개"로 적어줘.\n'
    '음식이 아닌 물건(보관용기, 그릇, 포장재)은 목록에 넣지 마.\n'
    '예: [{"name": "계란", "quantity": 6, "unit": "개"}, {"name": "우유", "quantity": 1, "unit": "개"}]'
)

# 오픈라우터 무료 모델 목록 조회 자체가 실패할 경우를 위한 최소 안전망 (2026-10-31 동작 확인).
SAFETY_NET_VISION_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
]

# 2026-10-31 냉장고 사진으로 다시 실측한 우선순위. 앞쪽일수록 먼저 시도한다.
# minimax-m3는 2026-10-22 무료 목록에서 빠졌다.
# 무료 모델은 응답 자체가 거부되는 경우가 잦아, 성공률을 인식 정확도보다 앞에 둔다.
#   nemotron-3.5-super-vl  성공 3/3, 품목 7/7, 19~52초
#   dots-3-note-preview    성공 2/3, 품목 7/7, 52~71초 (실패 1회는 10분 뒤 빈 응답)
#   gemma-4-26b            성공 0/3, 429 (2026-08-27에는 성공 2/13, 개수 7.0/7)
#   gemma-4-31b            성공 0/3, 429 (2026-08-27에는 성공 1/13, 개수 6.0/7)
# 목록에 없는 모델은 이 뒤에 무작위 순서로 붙는다 (새로 등장한 무료 모델도 기회를 얻도록).
PREFERRED_VISION_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
]

def list_free_vision_models(allowed_models=None):
    """현재 사용 가능한 무료 비전 모델 id 목록. allowed_models가 주어지면 그 안에서만 고른다.

    allowed_models로 걸러낸 결과가 비어있으면(예: 사용자가 골라둔 모델이 전부 목록에서
    사라진 경우) 안전하게 전체 목록으로 되돌아간다.
    """
    entries = openrouter.fetch_free_model_entries(require_image=True)
    models = [m.get("id", "") for m in entries] or list(SAFETY_NET_VISION_MODELS)
    return openrouter.filter_allowed(models, allowed_models)


def list_free_vision_models_detailed():
    """프로필 화면의 모델 선택 UI에 쓸 상세 정보(id/name/context_length/created)를 반환."""
    return openrouter.model_details(openrouter.fetch_free_model_entries(require_image=True))


def to_resized_data_uri(file_like):
    """이미지를 긴 변 기준 MAX_IMAGE_DIMENSION 이하로 축소해 base64 data URI로 변환한다."""
    image = Image.open(file_like).convert("RGB")
    image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    resized_bytes = buffer.getvalue()
    b64 = base64.b64encode(resized_bytes).decode()
    return f"data:image/jpeg;base64,{b64}", len(resized_bytes)


def call_vision_model(model, data_uri, api_key):
    return openrouter.post_chat(
        model,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": RECOGNITION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        api_key,
    )


def recognize_ingredients(data_uri, api_key, on_attempt=None, attempts=3, allowed_models=None):
    """매 시도마다 오픈라우터의 현재 무료 비전 모델 중 서로 다른 모델을 순서대로 시도한다.

    실측 우선순위(PREFERRED_VISION_MODELS)가 앞에 오고, 아직 실측하지 않은 모델이
    무작위 순서로 뒤를 잇는다. 429(요청 폭주), 시간 초과, 빈 응답, JSON 파싱 실패 모두 실패로
    간주하고, 성공(재료 목록을 얻을 때)할 때까지 또는 attempts 횟수만큼 다음 모델로 넘어간다.
    고정된 "폴백 모델" 개념 대신, 매 시도 자체가 서로 다른 모델이라 폴백 역할을 겸한다.

    on_attempt: 선택적 콜백. (label, model) 을 인자로 호출되어 진행 상황을 UI에 전달할 수 있다.
    allowed_models: 사용자가 프로필에서 선택한 모델 id 목록. 없으면 전체 무료 모델을 대상으로 한다.
    """
    pool = openrouter.prioritize_models(
        list_free_vision_models(allowed_models=allowed_models), PREFERRED_VISION_MODELS
    )
    models = openrouter.plan_attempts(pool, SAFETY_NET_VISION_MODELS, attempts)
    return openrouter.call_with_fallback(
        models,
        lambda model: call_vision_model(model, data_uri, api_key),
        lambda response: parse_ingredients(openrouter.extract_content(response)) is not None,
        on_attempt=on_attempt,
    )


def merge_duplicate_ingredients(ingredients):
    """이름이 같은(공백 제거, 대소문자 무시) 재료는 수량을 합쳐 하나로 합친다."""
    merged = {}
    order = []
    for item in ingredients:
        key = item["name"].strip().lower()
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
        else:
            try:
                merged[key]["quantity"] = str(int(merged[key]["quantity"]) + int(item["quantity"]))
            except (TypeError, ValueError):
                pass
    return [merged[key] for key in order]


def parse_ingredients(content):
    """모델 응답을 [{"name", "quantity", "unit"}, ...] 형태로 파싱한다.

    모델이 예전 방식대로 문자열 배열(["계란", "우유"])로 답한 경우도
    수량 1, 단위 "개"로 채워 동일한 형태로 반환한다 (하위 호환).
    동일한 이름의 재료가 중복 인식된 경우 수량을 합쳐 하나로 정리한다.
    """
    if not isinstance(content, str):
        return None
    match = re.search(r"\[.*\]", content, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None

    ingredients = []
    for item in data:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            try:
                quantity = int(float(item.get("quantity", 1)))
            except (TypeError, ValueError):
                quantity = 1
            unit = str(item.get("unit") or "개").strip() or "개"
        else:
            name = str(item).strip()
            if not name:
                continue
            quantity = 1
            unit = "개"
        ingredients.append({"_id": uuid.uuid4().hex, "name": name, "quantity": str(quantity), "unit": unit})
    if not ingredients:
        return None
    return merge_duplicate_ingredients(ingredients)
