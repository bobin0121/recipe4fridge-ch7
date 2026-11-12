<!-- 2026-11-12 11:55 KST -->
# 코드 리뷰 결과

높음 4건, 중간 9건, 낮음 10건

## 코드 리뷰 결과: `src/` 전체 (app.py, auth.py, auth_ui.py, db.py, recipe.py, vision.py, test_api.py, pages/*) + requirements.txt, CLAUDE.md, docs/PRD_step2~3.md

검토 방식: code-reviewer 에이전트(읽기 도구만, 모델 sonnet)가 위 파일을 모두 읽고 확인했습니다. `.env`와 `.streamlit/secrets.toml`은 열지 않았고, 명령도 실행하지 않았습니다(정적 검토만). 실행해 보지 않은 판단은 "확인 필요"로 표시했습니다. 코드에 하드코딩된 비밀 키는 발견하지 못했습니다. `get_api_key()`와 `_database_url()` 패턴은 지켜지고 있고, 레시피 삭제의 `user_id` 검사(`src/db.py:261`)와 로그인 실패 메시지 통일(`src/auth.py:9`)도 정상입니다.

### 높음 (앱이 멈추거나 틀린 결과를 냄, 먼저 고칠 것)

1. `src/vision.py:178-195`, `src/recipe.py:177-196` 모델 호출 중 예외가 나면 다음 모델로 넘어가지 않음
   - 상황: 재시도 루프 안의 `call_vision_model()`과 `call_recipe_model()`이 `requests.post(..., timeout=60)`을 부릅니다. 첫 모델이 시간 초과나 연결 오류를 내면 예외가 루프 밖으로 나가 `src/app.py:318-325`, `src/app.py:483-494`로 곧바로 갑니다. 남은 2개 모델은 시도되지 않습니다. 코드 주석에도 무료 모델이 5분 넘게 응답하지 않은 적이 있다고 적혀 있어 실제로 자주 생길 수 있는 경로입니다. "3차 시도 모두 실패" 문구와도 맞지 않습니다.
   - 제안: 루프 안에서 `requests.exceptions.RequestException`을 잡아 로그나 마지막 오류로 기록하고 다음 모델로 넘어갑니다. 모든 시도가 예외로 끝났을 때만 마지막 예외를 다시 올립니다.

2. `src/vision.py:188`, `src/recipe.py:188`, `src/app.py:345`, `src/app.py:525` 응답의 `content`가 null이거나 `message` 키가 없으면 앱이 죽음
   - 상황: 무료 모델은 `content: null`이나 빈 응답을 자주 줍니다(`src/vision.py:39`의 주석에도 "빈 응답"이 적혀 있습니다). 이때 `parse_ingredients(None)`과 `parse_recipes(None)`에서 `re.search(..., None)`이 `TypeError`를 냅니다. `choices[0]["message"]`에 키가 없으면 `KeyError`입니다. 재시도 루프는 `ValueError`만 잡으므로 다음 모델로 넘어가지 못하고, 앱 화면에는 처리되지 않은 예외가 그대로 뜹니다.
   - 제안: `content = (choices[0].get("message") or {}).get("content")`로 읽고, 문자열이 아니면 실패로 보고 다음 모델로 넘어갑니다. `parse_ingredients`와 `parse_recipes` 맨 앞에 `if not isinstance(content, str): return None`을 둡니다.

3. `src/recipe.py:210-221`, `src/recipe.py:204`, `src/app.py:549-577` 레시피 JSON의 내부 구조를 검증하지 않음
   - 상황: `parse_recipes`는 `recipes`가 리스트인지만 확인합니다. 원소가 dict가 아닐 때(`r.get` 호출 시 `AttributeError`), `used_ingredients`나 `missing_ingredients`에 문자열이 아닌 값이 있을 때(`", ".join(...)`에서 `TypeError`), `steps`가 리스트가 아니라 문자열일 때(글자 단위로 번호가 매겨짐)가 모두 문제입니다. `_is_korean_enough`도 `r.get`을 쓰므로 `AttributeError`는 재시도 루프에서 잡히지 않습니다. `recipes`가 10개 이상이면 `st.columns(len(recipes))`(`src/app.py:545`)도 깨집니다.
   - 제안: `parse_recipes`에서 레시피마다 정규화합니다. dict가 아니면 버리고, 문자열 필드는 `str()`로, 리스트 필드는 문자열 리스트로 바꿉니다. 이름이 없는 레시피는 제외하고 최대 3개로 자릅니다.

4. `src/db.py:164-170`, `src/auth_ui.py:33-51`, `src/auth.py:20-35` Google 로그인이 이메일만으로 기존 계정에 합쳐짐 (확인 필요)
   - 상황: 이메일 회원가입에는 이메일 인증이 없습니다. 공격자가 피해자 이메일로 비밀번호 계정을 미리 만들어 두면, 나중에 피해자가 Google로 로그인할 때 `get_or_create_google_user`가 그 계정을 그대로 돌려줍니다. 공격자는 자신의 비밀번호로 계속 로그인할 수 있습니다. 이때 피해자의 저장 레시피와 프로필도 볼 수 있습니다. 또 `st.user`의 `email_verified` 값을 확인하지 않습니다(Google 쪽에서 항상 true인지는 설정에 따라 다르므로 확인 필요).
   - 제안: Google 로그인 시 `email_verified`가 true인지 확인합니다. 비밀번호 계정과 합치려면 별도 확인 절차를 거치거나, 이미 비밀번호가 있는 계정이면 합치지 않고 안내만 합니다. 가능하면 이메일 인증을 도입합니다.

### 중간 (고치면 좋은 것)

1. `src/app.py:339`, `src/app.py:516` 상태 코드 200이어도 본문이 JSON이 아니면 `response.json()`이 `ValueError`를 냄
   - 상황: 프록시나 제공자가 HTML 오류 페이지를 200으로 주면, 이 호출 때문에 처리되지 않은 예외가 납니다. `src/vision.py:186`은 같은 경우를 잡고 있어 처리 방식이 서로 다릅니다.
   - 제안: `try/except ValueError`로 감싸고 "응답 형식이 올바르지 않습니다" 오류와 기술 정보 expander를 보여 줍니다.

2. `src/app.py:306` (`src/vision.py:126-134`) 손상된 이미지나 지원하지 않는 이미지에서 예외 처리 없음
   - 상황: 확장자만 png이고 내용이 깨진 파일이면 `Image.open`이 `PIL.UnidentifiedImageError`를 냅니다. `st.status` 블록 안에서 터지고 화면에 스택 트레이스가 나옵니다. 용량 검사(`src/app.py:286`)만 있고 디코딩 실패 검사는 없습니다. 아주 큰 해상도의 이미지(압축 폭탄)에 대한 방어도 없습니다.
   - 제안: `OSError`와 `Image.DecompressionBombError`를 잡아 한국어 오류 문구를 보여 줍니다.

3. `src/app.py:419` 재료 이름을 이스케이프하지 않고 `unsafe_allow_html=True`로 출력
   - 상황: 재료 이름은 AI 응답이나 사용자 입력입니다. `<`나 `<img ...>` 같은 문자가 들어 있으면 HTML로 해석되어 화면이 깨지거나 임의 태그가 삽입됩니다. 이 값은 자기 화면에만 그려지는 경로이지만, AI가 만든 값을 그대로 쓰는 점은 고쳐야 합니다.
   - 제안: `html.escape(name)`을 적용합니다.

4. `src/auth.py:31-34` 가입 경쟁 상태와 bcrypt 72바이트 제한
   - 상황: `email_exists` 확인과 `create_user` 사이에 같은 이메일이 먼저 들어오면 `UNIQUE` 위반(`psycopg2.errors.UniqueViolation`)이 처리되지 않고 화면에 노출됩니다. 또 비밀번호가 72바이트를 넘으면 최신 bcrypt는 `hashpw`에서 `ValueError`를 낼 수 있습니다(버전에 따라 다름, 확인 필요). `requirements.txt`는 버전을 고정하지 않았습니다. `src/auth.py:17`의 `checkpw`도 같습니다. 비밀번호 상한 검사가 없습니다.
   - 제안: `create_user`에서 `UniqueViolation`을 잡아 "이미 가입된 이메일입니다"로 돌려줍니다. 비밀번호 최대 길이(예: 72바이트)를 검사하고, 로그인 쪽도 예외를 잡습니다.

5. `src/db.py:59` 커넥션 풀이 가득 차면 예외 처리 없이 앱이 죽음
   - 상황: 풀은 최대 5개(`src/db.py:38-40`)입니다. `pool.getconn()`이 `try` 밖에 있어서, 동시 접속자가 5명을 넘으면 `PoolError`가 그대로 올라가 페이지가 오류 화면이 됩니다. 한 번의 화면 갱신마다 `get_user_by_id` 등 여러 번 연결을 꺼내므로 동시 사용자가 적어도 가능성이 있습니다(확인 필요).
   - 제안: `getconn`을 `try` 안에 넣고 `PoolError`는 잠시 기다렸다 재시도하거나 안내 문구를 보여 줍니다. 최대 연결 수가 DB 한도(Supabase 풀러)와 맞는지도 확인합니다.

6. `src/vision.py:182-194`, `src/recipe.py:181-195`, `src/app.py:335-337` 401, 402 같은 영구 오류까지 3번 재시도하고 안내도 부족함
   - 상황: API 키가 틀렸거나(401) 크레딧이 없으면(402) 어느 모델로 해도 실패인데, 2초와 4초를 기다리며 3번 호출합니다. 최종 화면에는 "status 401" 같은 문구와 원본 응답이 나옵니다(`src/app.py:336`).
   - 제안: 401, 402, 403은 즉시 루프를 끝내고 "API 키나 크레딧을 확인하세요" 같은 한국어 안내를 보여 줍니다. 원본 응답에 비밀 정보가 섞여 있지 않은지도 확인합니다.

7. `src/auth_ui.py:10-26` 로그아웃 시 지워야 할 상태 키 목록이 `src/app.py:37-53`과 어긋남
   - 상황: `APP_FLOW_STATE_KEYS`에 `used_recipe_model`이 없습니다. 로그아웃해도 "실제 응답 모델" 표시가 남을 수 있습니다. 같은 목록이 두 곳에 있어 한쪽만 고치기 쉽습니다(주석에도 "여기도 갱신해야 한다"고 적혀 있음). 재료별 위젯 key(`ingredient_name_<id>` 등)는 지워지지 않지만 id가 새로 생기므로 영향은 작습니다.
   - 제안: 키 목록을 한 모듈(예: `auth_ui.py`나 새 상수 모듈)에 두고 양쪽에서 가져다 씁니다.

8. `src/app.py:525-533`, `src/recipe.py:189` 한국어 검사를 통과하지 못한 응답도 화면에 그대로 보여 줌
   - 상황: `generate_recipes`는 영어로만 온 응답이면 다음 모델로 넘어가지만, 3번 모두 영어면 마지막 응답을 그대로 반환합니다. `app.py`는 한국어 검사를 다시 하지 않으므로 영어 레시피가 그대로 표시됩니다. 프롬프트가 요구한 "한국어만"과 다릅니다.
   - 제안: 마지막까지 실패한 경우 `generate_recipes`가 실패 상태를 알려 주도록 하고(예: 별도 반환값), 화면에서는 "한국어 응답을 받지 못했습니다" 오류로 처리합니다.

9. `src/app.py:93` Windows 환경에서 `ZoneInfo("Asia/Seoul")` 실패 가능 (확인 필요)
   - 상황: Windows에는 시간대 데이터가 기본으로 없어 `tzdata`가 필요합니다. `requirements.txt`에는 `tzdata`가 없습니다. 이때 `ZoneInfoNotFoundError`가 나는데, 이는 `KeyError`의 하위 클래스라 `except (TypeError, ValueError, OSError, OverflowError)`에 잡히지 않습니다. 429 안내를 만드는 도중에 예외로 죽습니다. 배포 환경(Linux)에서는 대체로 문제없습니다.
   - 제안: `requirements.txt`에 `tzdata`를 추가하고, 예외 목록에 `KeyError`를 넣거나 `ZoneInfo` 생성을 `try` 안에 둡니다. 패키지 버전 고정도 권장합니다(`requirements.txt:1-9`).

### 낮음 (작은 개선)

1. `src/vision.py:13-32`, `src/recipe.py:9-22`, `src/vision.py:49-58`, `src/recipe.py:38-47` 두 모듈이 거의 같은 코드를 따로 가짐
   - 상황: `API_URL`, `MODELS_URL`, `_EXCLUDE_KEYWORDS`, `_fetch_models_payload`(캐시 함수가 둘이라 같은 URL을 두 번 조회함), `prioritize_models`, 재시도 루프, 모델 상세 정보 함수가 중복입니다. 한쪽만 고치면 어긋납니다(위 높음 1번, 2번이 두 곳에 똑같이 있는 이유).
   - 제안: 공통 모듈(예: `openrouter.py`)로 모으고 모델 호출과 재시도 루프를 하나로 만듭니다.

2. `src/app.py:272-570` 및 전체 `src/app.py`(586줄)가 길고 오류 처리 코드가 반복됨
   - 상황: `st.session_state.pop("recipes")`와 `pop("selected_recipe")`가 6곳에서 반복됩니다(`src/app.py:485-529`). `_do_restart`는 파일 중간(`src/app.py:235`)에 정의되어 있고 `_default`는 블록 안에 중첩 정의입니다(`src/app.py:428`).
   - 제안: 단계별 함수(`render_step1/2/3`)와 `clear_recipe_state()` 도우미로 나눕니다.

3. `src/app.py:550`, `src/pages/1_내_레시피.py:28` 조리 시간이 `None`이면 "약 None분"으로 표시됨
   - 상황: `dict.get(키, '?')`는 키가 있고 값이 `None`이면 기본값을 쓰지 않습니다. 모델이 `cook_time_minutes: null`을 주거나 DB 컬럼이 NULL이면 발생합니다.
   - 제안: `value = r.get(...)`로 읽고 `value if value is not None else "?"`처럼 처리합니다.

4. `src/pages/1_내_레시피.py:19` 안내 문구가 실제 화면과 다름
   - 상황: "2단계 레시피 상세 화면"이라고 쓰였지만 저장 버튼은 3단계 화면(`src/app.py:579`)에 있습니다. PRD의 단계 번호와 앱의 마법사 단계 번호가 서로 다른 것에서 온 혼동으로 보입니다.
   - 제안: "③ 레시피 추천 화면의 '레시피 저장'"처럼 실제 화면 이름으로 고칩니다.

5. `src/app.py:73` 429 안내의 "무료 모델 3곳" 숫자가 고정되어 있음
   - 상황: 사용자가 모델을 1~2개만 골랐으면 같은 모델을 순환 호출하므로 "3곳"이 사실과 다릅니다(`src/vision.py:172`, `src/recipe.py:171`). `src/app.py:348`, `src/app.py:530`의 "서로 다른 모델 3차 시도"도 같습니다.
   - 제안: 실제 시도한 모델 수를 반환값에 담아 문구에 쓰거나, 숫자를 빼고 일반 문구로 바꿉니다.

6. `src/pages/2_프로필.py:46-66` 사용자 레코드가 없을 때 처리 없음
   - 상황: `saved`가 `None`이면(계정이 DB에서 지워진 경우 등) `saved.get(...)`에서 `AttributeError`가 납니다. `app.py`의 `get_allowed_models`는 `None`을 처리하고 있어 서로 다릅니다.
   - 제안: `if not saved:`이면 세션을 비우고 로그인 안내를 보여 줍니다.

7. `src/pages/2_프로필.py:112-118`, `src/pages/2_프로필.py:98-109` 모델 선택이 표시 이름 기준이고, 처음 저장하면 전체 선택이 고정됨
   - 상황: `name_to_id`는 모델 이름으로 id를 찾기 때문에 이름이 같은 모델이 둘이면 하나가 덮어씁니다. 또 저장 이력이 없을 때는 모든 행이 선택된 채로 보이는데, 그 상태에서 저장하면 "전체 사용(NULL)" 대신 현재 모델 id 전체가 저장되어 이후 새로 생긴 무료 모델이 후보에서 빠집니다(확인 필요).
   - 제안: 데이터프레임에 id 컬럼(숨김)을 두고 그 값으로 매핑합니다. 전체가 선택된 경우는 빈 리스트로 저장합니다.

8. `src/test_api.py:1-58` 이름이 `test_`로 시작하는 스크립트라 pytest가 자동 수집할 수 있음
   - 상황: import 시점에 `os.environ["OPENROUTER_API_KEY"]`를 읽어 키가 없으면 `KeyError`로 수집 단계에서 실패하고, 키가 있으면 실제 네트워크 호출이 테스트로 실행됩니다. 응답 전체를 출력하므로(`src/test_api.py:34`) 오류 본문에 민감한 정보가 있으면 로그에 남습니다. 또 PRD의 모델(`openai/gpt-oss-20b:free`)과 본 앱 코드의 모델 선택 방식이 다릅니다(PRD F2-2는 고정 모델이고 앱은 동적 목록).
   - 제안: 파일 이름을 `check_api.py`처럼 바꾸거나 `if __name__` 안으로 키 읽기를 옮깁니다. PRD F2-2도 현재 구현에 맞게 갱신합니다.

9. `CLAUDE.md:12`, `CLAUDE.md:22-25`, `requirements.txt:8` 문서와 의존성이 현재 코드와 어긋남
   - 상황: CLAUDE.md는 "src/에는 test_api.py만 있다", "빌드 시스템, 패키지 매니페스트가 없다"고 하지만 실제로는 Streamlit 앱 전체와 `requirements.txt`가 있습니다. 실행 명령(`streamlit run src/app.py`)과 PRD 단계와 모듈 대응표도 없습니다. `httpx`는 읽은 코드 어디에서도 쓰이지 않습니다(`Authlib`의 의존성으로 필요할 수 있어 확인 필요).
   - 제안: CLAUDE.md의 "폴더 구조"와 "실행" 절을 갱신하고, 쓰지 않는 의존성은 확인 후 정리합니다.

10. `src/auth_ui.py:96-99` Google 로그인 실패 시 예외 문자열을 화면에 그대로 표시 (확인 필요)
    - 상황: `st.code(str(e))`로 예외 메시지를 보여 줍니다. 설정 오류 메시지에 내부 설정 이름이나 URL이 섞일 수 있습니다. 키 값이 들어간다고 확인한 것은 아니지만 공개 화면에 내부 정보가 나가는 경로입니다.
    - 제안: 사용자에게는 일반 문구만 보이고, 상세 내용은 서버 로그에만 남깁니다.
