"""
Step 4: 모듈 단위 ChromaDB 구축 (v2 - Gemini API 배치 분류)
- 파일명 정제 (날짜/버전/특수문자 제거)
- Gemini API로 10개씩 배치 분류 → intro / core / apply 태깅
- 임베딩 생성 후 ChromaDB 저장
"""

import os
import json
import re
import time

from google import genai
import chromadb

# ============ 설정 ============
API_KEYS = [k for k in [
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("GEMINI_API_KEY_2", ""),
    os.getenv("GEMINI_API_KEY_3", ""),
] if k]
_key_idx = 0
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STRUCTURED_FOLDER = os.path.join(SCRIPT_DIR, "structured_data")
MODULE_DB_PATH = os.path.join(SCRIPT_DIR, "module_db")

def get_client():
    return genai.Client(api_key=API_KEYS[_key_idx])

def rotate_key():
    global _key_idx, client
    _key_idx = (_key_idx + 1) % len(API_KEYS)
    client = get_client()
    print(f"    🔑 API 키 교체 → 키 {_key_idx + 1}/{len(API_KEYS)}")

client = get_client()

chroma_client = chromadb.PersistentClient(path=MODULE_DB_PATH)
try:
    chroma_client.delete_collection("modules")
    print("기존 modules 컬렉션 삭제 완료")
except:
    pass

collection = chroma_client.create_collection(
    name="modules",
    metadata={"hnsw:space": "cosine"}
)


# ============ [답변 3] 파일명 정제 함수 ============
def clean_course_name(filename):
    """
    파일명에서 과정명 추출 + 정제
    제거 대상: 날짜(YYMMDD/YYYYMMDD), 버전(v1/v2/최종/픽스), 특수기호
    """
    name = filename.replace('.json', '')
    parts = name.split('_')

    # 4번째 파트가 과정명 (001_교육_1-10a_[과정명]_표준제안서★)
    if len(parts) >= 4:
        course = parts[3]
    else:
        course = name

    # 불필요한 접미사 제거
    noise_patterns = [
        r'표준제안서\s*★?',
        r'표준\s*제안서',
        r'[\[\]]',                           # 대괄호 기호만 제거 (내용 보존)
        r'\b\d{6,8}\b',                      # 날짜 (YYMMDD ~ YYYYMMDD)
        r'\bv\d+(\.\d+)?\b',                 # 버전: v1, v2, v1.2
        r'\b(최종|픽스|fix|final|수정|확인)\b', # 버전 텍스트
        r'[★☆\*]',                           # 특수 기호
    ]
    for pat in noise_patterns:
        course = re.sub(pat, '', course, flags=re.IGNORECASE)

    return course.strip()


# ============ [답변 1] Gemini 배치 분류 함수 ============
def classify_modules_batch(module_list):
    """
    모듈 10개씩 Gemini에 보내 배치 분류
    반환: {"0": "intro", "1": "core", ...} 형태의 dict
    """
    items = ""
    for i, m in enumerate(module_list):
        name = m.get('모듈명', '') or m.get('주제', '')
        topics = m.get('세부내용', [])
        topic_str = ""
        if topics and isinstance(topics[0], dict):
            topic_str = ", ".join([t.get('주제', '') for t in topics[:3] if t.get('주제')])
        elif topics:
            topic_str = ", ".join([str(t)[:30] for t in topics[:3]])
        items += f"{i}. 모듈명: {name} | 주제: {topic_str}\n"

    prompt = f"""당신은 HRD 전문가입니다. 아래 교육 모듈들을 분류하세요.

분류 기준:
- intro: 도입, 마인드셋, 필요성 인식, 오리엔테이션, 아이스브레이킹
- core:  핵심 이론 강의, 스킬 학습, 실습, 롤플레잉, 케이스 스터디
- apply: 현업 적용, 실행 계획, Wrap-up, 액션 플래닝, 마무리 정리

모듈 목록:
{items}

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.
{{"0": "intro or core or apply", "1": "...", ...}}"""

    for attempt in range(len(API_KEYS) * 4):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite-preview",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            result = json.loads(response.text)
            return result
        except Exception as e:
            if "429" in str(e):
                rotate_key()
                time.sleep(5)
            else:
                print(f"    ⚠️ 분류 오류 (시도 {attempt+1}): {e}, 재시도 중...")
                time.sleep(2)
    print(f"    ❌ 최대 재시도 초과, 기본값(core) 사용")
    return {str(i): "core" for i in range(len(module_list))}


def embed_with_retry(text, max_retries=None):
    """임베딩 생성 (429 오류 시 키 교체 후 재시도)"""
    if max_retries is None:
        max_retries = len(API_KEYS) * 4
    for attempt in range(max_retries):
        try:
            result = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text
            )
            return result.embeddings[0].values
        except Exception as e:
            if "429" in str(e):
                rotate_key()
                time.sleep(5)
            else:
                raise e
    raise Exception("최대 재시도 횟수 초과")


def make_embed_text(course_name, module_name, topics):
    lines = [f"과정: {course_name}", f"모듈: {module_name}"]
    for t in topics:
        if isinstance(t, dict):
            subj = t.get('주제', '')
            cont = t.get('내용', '')[:120]
            if subj:
                lines.append(f"  주제: {subj}")
            if cont:
                lines.append(f"  내용: {cont}")
        else:
            lines.append(f"  - {str(t)[:80]}")
    return "\n".join(lines)


# ============ 전체 모듈 수집 ============
files = sorted(os.listdir(STRUCTURED_FOLDER))
print(f"총 {len(files)}개 파일에서 모듈 수집 중...\n")

all_modules = []  # {course_name, filename, category, target, duration, module, mod_idx, total_mods}

for filename in files:
    filepath = os.path.join(STRUCTURED_FOLDER, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    course_name = clean_course_name(filename)
    category  = data.get('카테고리', '')
    target    = data.get('교육대상', '')
    duration  = data.get('교육기간', '')
    modules   = data.get('커리큘럼', [])

    for mod_idx, module in enumerate(modules):
        all_modules.append({
            "course_name": course_name,
            "filename":    filename,
            "category":    category,
            "target":      target,
            "duration":    duration,
            "module":      module,
            "mod_idx":     mod_idx,
            "total_mods":  len(modules),
        })

print(f"총 {len(all_modules)}개 모듈 수집 완료\n")


# ============ [답변 1] 배치 분류 (10개씩) ============
BATCH_SIZE = 10
type_map = {}  # 전체 인덱스 → "intro"/"core"/"apply"

TEST_LIMIT = 0  # 테스트: 30개만 처리 (0으로 바꾸면 전체 처리)
test_modules = all_modules[:TEST_LIMIT] if TEST_LIMIT else all_modules

print("=== STEP 1: Gemini 배치 분류 시작 ===")
for batch_start in range(0, len(test_modules), BATCH_SIZE):
    batch = test_modules[batch_start: batch_start + BATCH_SIZE]
    batch_modules = [item["module"] for item in batch]

    batch_result = classify_modules_batch(batch_modules)
    time.sleep(1)  # 배치 간 딜레이

    for local_idx, item in enumerate(batch):
        global_idx = batch_start + local_idx
        module_type = batch_result.get(str(local_idx), "core")
        if isinstance(module_type, list):
            module_type = module_type[0] if module_type else "core"
        if module_type not in ("intro", "core", "apply"):
            module_type = "core"
        type_map[global_idx] = module_type

    done = min(batch_start + BATCH_SIZE, len(all_modules))
    print(f"  분류 완료: {done}/{len(all_modules)}")

type_counts = {"intro": 0, "core": 0, "apply": 0}
for t in type_map.values():
    type_counts[t] = type_counts.get(t, 0) + 1
print(f"\n분류 결과: 도입={type_counts['intro']} / 핵심={type_counts['core']} / 현업적용={type_counts['apply']}\n")


# ============ STEP 2: 임베딩 생성 & ChromaDB 저장 ============
print("=== STEP 2: 임베딩 생성 & DB 저장 시작 ===")
for global_idx, item in enumerate(test_modules):
    module      = item["module"]
    course_name = item["course_name"]
    module_name = module.get('모듈명', '') or module.get('주제', f'모듈{item["mod_idx"]+1}')
    topics      = module.get('세부내용', [])
    module_type = type_map.get(global_idx, "core")

    # 세부 주제 목록 (표시용)
    topic_subjects = []
    topic_contents = []
    for t in topics:
        if isinstance(t, dict):
            subj = t.get('주제', '')
            cont = t.get('내용', '')[:200]
            hour = t.get('시간', '')
            methods = t.get('교수방법', [])
            if subj:
                topic_subjects.append(f"{subj}({hour})" if hour else subj)
            if cont:
                topic_contents.append(cont)
        else:
            topic_subjects.append(str(t)[:40])

    embed_text = make_embed_text(course_name, module_name, topics)

    try:
        embedding = embed_with_retry(embed_text)
        time.sleep(0.3)
    except Exception as e:
        print(f"  ❌ [{global_idx+1}] 임베딩 실패: {e}")
        continue

    collection.add(
        ids=[f"module_{global_idx:04d}"],
        embeddings=[embedding],
        documents=[embed_text],
        metadatas=[{
            "파일명":       item["filename"],
            "과정명":       course_name,
            "카테고리":     item["category"],
            "교육대상":     item["target"],
            "교육기간":     item["duration"],
            "모듈명":       module_name,
            "모듈성격":     module_type,
            "모듈순서":     item["mod_idx"],
            "총모듈수":     item["total_mods"],
            "세부주제목록": " | ".join(topic_subjects[:6]),
            "세부내용요약": " ".join(topic_contents),
        }]
    )

    icon = {"intro": "🔵", "core": "🟢", "apply": "🟡"}.get(module_type, "⚪")
    print(f"  {icon} [{global_idx+1:03d}/{len(all_modules)}] [{module_type.upper()}] {course_name[:20]} | {module_name[:30]}")

print(f"\n{'='*55}")
print(f"✅ 완료! {collection.count()}개 모듈 저장 → {MODULE_DB_PATH}")
print(f"   🔵 도입(intro):    {type_counts['intro']}개")
print(f"   🟢 핵심(core):     {type_counts['core']}개")
print(f"   🟡 현업적용(apply):{type_counts['apply']}개")
