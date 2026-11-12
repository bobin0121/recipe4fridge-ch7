# 2026-11-12 12:00 KST
"""vision.py와 recipe.py가 함께 쓰는 오픈라우터 호출 코드 (모델 목록 조회, 요청, 모델 순차 시도)."""
import random
import time

import requests
import streamlit as st

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
_EXCLUDE_KEYWORDS = ["safety", "rerank", "guard"]

CONNECT_TIMEOUT_SECONDS = 10
# 한 모델에 주는 전체 응답 시간. 오픈라우터는 응답이 끝나기 전까지 공백 문자를 계속 보내므로
# requests의 timeout(바이트 사이 간격)만으로는 끊기지 않는다. 그래서 전체 시간을 따로 잰다.
# 2026-10-31 실측에서 성공한 응답은 가장 느린 것이 71초였다.
MODEL_TOTAL_TIMEOUT_SECONDS = 90
# 어느 모델로 바꿔도 똑같이 실패하는 상태 코드 (키 오류, 크레딧 부족, 권한 없음). 다음 모델로 넘어가지 않는다.
PERMANENT_ERROR_STATUSES = {401, 402, 403}


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_models_payload():
    """오픈라우터 모델 목록 원본을 5분간 캐시한다.

    실패하면 예외를 그대로 올려서 실패 결과가 캐시에 남지 않게 한다
    (일시적인 네트워크 오류가 5분 동안 굳어지지 않도록).
    """
    response = requests.get(MODELS_URL, timeout=10)
    response.raise_for_status()
    return response.json().get("data", [])


def fetch_free_model_entries(require_image=False):
    """현재 사용 가능한 무료 모델의 원본 정보. require_image면 이미지 입력 모델만 고른다.

    조회 자체가 실패하면 빈 리스트를 반환한다 (호출부에서 안전망으로 대체).
    """
    try:
        data = _fetch_models_payload()
    except (requests.exceptions.RequestException, ValueError):
        return []

    entries = []
    for m in data:
        model_id = m.get("id", "")
        if not model_id.endswith(":free"):
            continue
        if any(keyword in model_id.lower() for keyword in _EXCLUDE_KEYWORDS):
            continue
        if require_image and "image" not in m.get("architecture", {}).get("input_modalities", []):
            continue
        entries.append(m)
    return entries


def filter_allowed(models, allowed_models):
    """allowed_models 안의 모델만 남긴다. 걸러낸 결과가 비면 전체 목록으로 되돌아간다."""
    if allowed_models:
        filtered = [m for m in models if m in allowed_models]
        if filtered:
            return filtered
    return models


def prioritize_models(pool, preferred):
    """실측 우선순위(preferred) 순서로 후보를 정렬한다.

    우선순위 목록에 없는 모델은 뒤쪽에 무작위 순서로 배치한다. 무료 모델 라인업은
    수시로 바뀌므로, 아직 실측하지 않은 새 모델도 뒤에서는 시도될 수 있게 남겨둔다.
    """
    ranked = [m for m in preferred if m in pool]
    rest = [m for m in pool if m not in ranked]
    random.shuffle(rest)
    return ranked + rest


def plan_attempts(pool, safety_net, attempts):
    """시도할 모델 순서를 attempts개 만든다.

    풀이 attempts보다 적으면(예: 사용자가 모델을 1~2개만 선택한 경우) 안전망 모델로
    채우지 않고 풀 안에서 순환한다 - 사용자가 고르지 않은 모델을 몰래 끼워넣지 않기 위함.
    """
    if pool:
        return [pool[i % len(pool)] for i in range(attempts)]
    return [random.choice(safety_net) for _ in range(attempts)]


def model_details(entries):
    """프로필 화면의 모델 선택 UI에 쓸 상세 정보(id/name/context_length/created)를 반환."""
    return [
        {
            "id": m.get("id", ""),
            "name": m.get("name") or m.get("id", ""),
            "context_length": m.get("context_length"),
            "created": m.get("created"),
        }
        for m in entries
    ]


def post_chat(model, messages, api_key, total_timeout=MODEL_TOTAL_TIMEOUT_SECONDS):
    """채팅 요청을 보내고, total_timeout초 안에 응답이 끝나지 않으면 Timeout을 올린다."""
    deadline = time.monotonic() + total_timeout
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": messages},
        timeout=(CONNECT_TIMEOUT_SECONDS, total_timeout),
        stream=True,
    )
    chunks = []
    try:
        for chunk in response.iter_content(chunk_size=None):
            chunks.append(chunk)
            if time.monotonic() > deadline:
                raise requests.exceptions.Timeout(f"{model}: {total_timeout}초 안에 응답이 끝나지 않았습니다.")
    finally:
        response.close()
    # 다 받은 본문을 넣어 두어 호출부가 평소처럼 response.json(), response.text를 쓸 수 있게 한다.
    response._content = b"".join(chunks)
    return response


def extract_content(response):
    """200 응답에서 choices[0].message.content 문자열을 꺼낸다. 없거나 형식이 다르면 None."""
    if response is None or response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    content = (choices[0].get("message") or {}).get("content")
    return content if isinstance(content, str) else None


def call_with_fallback(models, send, is_success, on_attempt=None):
    """models를 순서대로 send(model)로 호출해, is_success(response)가 참이면 멈춘다.

    시간 초과나 연결 오류는 실패로 기록하고 다음 모델로 넘어간다. 키나 크레딧 문제
    (PERMANENT_ERROR_STATUSES)는 어느 모델이든 똑같이 실패하므로 바로 멈춘다.
    반환값은 마지막으로 받은 응답과 그 모델이다. 모든 시도가 예외로 끝났을 때만 마지막 예외를 올린다.

    on_attempt: 선택적 콜백. (label, model) 을 인자로 호출되어 진행 상황을 UI에 전달할 수 있다.
    """
    response = None
    used_model = models[0]
    last_error = None
    for i, model in enumerate(models):
        if on_attempt is not None:
            on_attempt(f"{i + 1}차 시도", model)
        try:
            current = send(model)
        except requests.exceptions.RequestException as e:
            last_error = e
            continue
        response, used_model = current, model
        if current.status_code in PERMANENT_ERROR_STATUSES or is_success(current):
            break
        if current.status_code == 429 and i < len(models) - 1:
            # 429는 모델별 순간 혼잡이라 1초로는 잘 풀리지 않는다. 시도마다 대기를 늘린다.
            time.sleep(2 * (i + 1))
    if response is None:
        raise last_error
    return response, used_model
