"""
제안서 AI 어시스턴트 v2.0
3-Step RAG: 니즈 분석 → 모듈 검색 → 커리큘럼 조합 → 양식 출력
"""

import streamlit as st
import json
import os
import re
import time
import io
from google import genai
import chromadb
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ============ 설정 ============
API_KEY = st.secrets.get("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_DB_PATH = os.path.join(SCRIPT_DIR, "module_db")
LEGACY_DB_PATH = os.path.join(SCRIPT_DIR, "chroma_db")  # 구버전 폴백용

client_genai = genai.Client(api_key=API_KEY)
MODEL_NAME = "gemini-3.1-flash-lite-preview"

# 교육 시간 프레임워크
DURATION_FRAMEWORK = {
    "4H (반일)":  {"도입": 1, "핵심": 2, "마무리": 1, "total_h": 4},
    "8H (1일)":   {"도입": 1, "핵심": 5, "실습": 1, "마무리": 1, "total_h": 8},
    "16H (2일)":  {"도입": 1, "핵심": 6, "실습": 6, "마무리": 3, "total_h": 16},
    "24H (3일)":  {"도입": 2, "핵심": 8, "실습": 10, "마무리": 4, "total_h": 24},
}

# [P1-1] 활동별 소요 시간 데이터셋 (CoT Time Validator용)
ACTIVITY_TIME_ESTIMATE = {
    "강의": 1.0,            # 1분/분량
    "진단": 5,              # 진단 활동 5분
    "진단지": 5,
    "롤플레잉": 10,         # 롤플레잉 1회 10분
    "페어": 10,             # 페어 활동
    "토의": 8,              # 팀/전체 토의
    "실습": 12,             # 실습 활동 12분
    "사례": 8,              # 사례 분석/공유
    "케이스": 12,
    "시뮬레이션": 15,       # 시뮬레이션 15분
    "피드백": 5,            # 피드백 세션
    "워크숍": 2.0,          # 워크숍 형식 2분/분량
    "발표": 8,              # 발표/공유
    "Q&A": 5,               # 질의응답
    "마무리": 3,            # 정리/마무리
}

# ============ [P0-A] Interactive Needs Gathering ============
# 필수 정보 체크리스트
REQUIRED_INFO = {
    "교육인원": {
        "keywords": ["명", "명정도", "人", "인원", "참석자", "people", "persons"],
        "pattern": r'(\d+)\s*명',
        "required": True,
        "description": "몇 명을 대상으로 하는가?"
    },
    "pain_point": {
        "keywords": ["어려움", "문제", "과제", "부족", "고민", "이슈", "어려워", "힘들", "도전"],
        "pattern": None,
        "required": True,
        "description": "가장 시급한 문제 1가지는?"
    },
    "실습비중": {
        "keywords": ["강의", "실습", "혼합", "비중", "실제", "연습", "실행", "워크숍", "토의"],
        "pattern": r'(강의|실습|혼합|워크숍|토의)',
        "required": True,
        "description": "강의형 vs 혼합형 vs 실습형?"
    },
    "기존시도": {
        "keywords": ["해봤", "시도", "경험", "했어", "작년", "지난해", "전에", "이전"],
        "pattern": None,
        "required": False,
        "description": "기존에 해본 교육이 있나?"
    }
}

def check_info_completeness(full_text: str) -> dict:
    """
    누적된 대화에서 각 필수 정보가 포함되었는지 확인
    Returns: {정보명: bool}
    """
    results = {}

    for info_key, info_spec in REQUIRED_INFO.items():
        # 키워드 기반 검사 (대소문자 무시)
        text_lower = full_text.lower()
        found_by_keyword = any(kw in text_lower for kw in info_spec["keywords"])

        # 정규식 기반 검사
        found_by_pattern = False
        if info_spec["pattern"]:
            found_by_pattern = bool(re.search(info_spec["pattern"], full_text))

        # 둘 중 하나라도 매치되면 포함된 것으로 간주
        results[info_key] = found_by_keyword or found_by_pattern

    return results

def is_needs_complete(full_text: str) -> bool:
    """
    필수 정보 3개 이상이 확보되었는지 판단
    Returns: bool
    """
    completeness = check_info_completeness(full_text)
    required_items = [k for k, v in REQUIRED_INFO.items() if v["required"]]
    found_count = sum(1 for k in required_items if completeness.get(k, False))

    # 필수 정보의 75% 이상 확보되면 검색 진행 가능
    threshold = int(len(required_items) * 0.75)
    return found_count >= threshold

def generate_follow_up_questions(initial_query: str, industry: str, target: str,
                                 conversation_history: list = None) -> str:
    """
    사용자의 초기 입력 + 현재까지 대화 내용을 분석하여
    부족한 정보를 파악하고 3~4개의 구체적인 질문 생성
    """

    # 누적된 대화 텍스트화
    accumulated_text = initial_query
    if conversation_history:
        accumulated_text += " " + " ".join([msg.get("content", "") for msg in conversation_history if msg.get("role") == "user"])

    # 현재 정보 완성도 확인
    completeness = check_info_completeness(accumulated_text)

    # 부족한 정보 식별
    missing_info = [key for key, complete in completeness.items()
                    if not complete and REQUIRED_INFO[key]["required"]]

    if not missing_info:
        # 모든 필수 정보가 확보된 경우
        return "완벽합니다! 충분한 정보가 수집되었습니다. '검색 진행' 버튼을 클릭해주세요."

    # 부족한 정보에 대한 질문 생성 (최대 3개)
    missing_questions_text = "\n".join([f"- {REQUIRED_INFO[key]['description']}" for key in missing_info[:3]])

    prompt = f"""당신은 경험 많은 HRD 컨설턴트입니다.

[초기 요청]
{initial_query}

[산업군]: {industry}
[교육 대상]: {target}

[이전 대화]
{chr(10).join([f"- 고객: {msg['content']}" for msg in conversation_history if msg.get('role') == 'user']) if conversation_history else "없음"}

[여전히 부족한 정보]
{missing_questions_text}

위 부족한 정보를 자연스럽게 얻기 위해 3~4개의 간단하고 구체적인 질문을 던져주세요.

예시 포맷:
"좋습니다! 몇 가지 더 확인하고 싶습니다:
1. [질문1]
2. [질문2]
3. [질문3]"

주의:
- 이미 언급된 정보는 다시 묻지 마세요
- 개방형 질문으로 자연스럽게
- 너무 길지 않게 (3~4개 질문만)
"""

    for attempt in range(3):
        try:
            response = client_genai.models.generate_content(
                model=MODEL_NAME,
                contents=prompt
            )
            return response.text
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(30)
            else:
                return f"질문 생성 중 오류가 발생했습니다. 직접 입력해주세요.\n\n부족한 정보: {missing_questions_text}"


# ============ [답변 2] 마크다운 → DOCX 변환 ============
def markdown_to_docx(markdown_text: str) -> bytes:
    """
    마크다운을 Word(.docx) 파일로 변환
    H1(#) → 제목1, H2(##) → 제목2, H3(###) → 제목3
    **bold** → 굵은 글씨, 표(|) → 워드 테이블, * 항목 → 글머리 기호
    """
    doc = Document()

    # 기본 폰트 설정
    style = doc.styles['Normal']
    style.font.name = '맑은 고딕'
    style.font.size = Pt(10)

    def add_run_with_bold(para, text):
        """**text** 패턴을 파싱해서 굵은 글씨 처리"""
        parts = re.split(r'(\*\*.*?\*\*)', text)
        for part in parts:
            if part.startswith('**') and part.endswith('**'):
                run = para.add_run(part[2:-2])
                run.bold = True
            else:
                para.add_run(part)

    lines = markdown_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]

        # 헤딩
        if line.startswith('### '):
            p = doc.add_heading(line[4:].strip(), level=3)
        elif line.startswith('## '):
            p = doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith('# '):
            p = doc.add_heading(line[2:].strip(), level=1)

        # 구분선
        elif line.strip() in ('---', '***', '___'):
            doc.add_paragraph('─' * 40)

        # 표 감지 (|로 시작하는 줄)
        elif line.strip().startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1

            # 구분선 행 제거 (|---|---|)
            data_lines = [l for l in table_lines if not re.match(r'^\s*\|[\s\-:]+\|', l)]
            if not data_lines:
                continue

            # 셀 파싱
            rows = []
            for tl in data_lines:
                cells = [c.strip() for c in tl.strip().strip('|').split('|')]
                rows.append(cells)

            if rows:
                n_cols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=n_cols)
                table.style = 'Table Grid'
                for r_idx, row_data in enumerate(rows):
                    for c_idx, cell_text in enumerate(row_data):
                        if c_idx < n_cols:
                            cell = table.rows[r_idx].cells[c_idx]
                            cell.text = re.sub(r'\*\*(.*?)\*\*', r'\1', cell_text)
                            if r_idx == 0:
                                for run in cell.paragraphs[0].runs:
                                    run.bold = True
            continue

        # 글머리 기호 (* 또는 -)
        elif re.match(r'^[\*\-] ', line):
            text = line[2:].strip()
            p = doc.add_paragraph(style='List Bullet')
            add_run_with_bold(p, text)

        # 들여쓰기 글머리 (  * 또는   -)
        elif re.match(r'^\s{2,}[\*\-] ', line):
            text = re.sub(r'^\s+[\*\-] ', '', line).strip()
            p = doc.add_paragraph(style='List Bullet 2')
            add_run_with_bold(p, text)

        # 빈 줄
        elif line.strip() == '':
            doc.add_paragraph('')

        # 일반 텍스트
        else:
            p = doc.add_paragraph()
            add_run_with_bold(p, line.strip())

        i += 1

    # bytes로 반환
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ============ DB 로드 ============
@st.cache_resource
def load_module_db():
    """모듈 단위 ChromaDB 로드"""
    if os.path.exists(MODULE_DB_PATH):
        try:
            c = chromadb.PersistentClient(path=MODULE_DB_PATH)
            col = c.get_collection("modules")
            return col, "module"
        except:
            pass
    # 폴백: 구버전 제안서 단위 DB
    c = chromadb.PersistentClient(path=LEGACY_DB_PATH)
    col = c.get_collection("proposals")
    return col, "legacy"


# ============ Step 1: 니즈 분석 ============
def analyze_needs(query, industry, target, duration):
    """
    Gemini API로 자연어 니즈 → 구조화된 JSON 변환
    """
    duration_h = DURATION_FRAMEWORK.get(duration, {}).get("total_h", 8)

    prompt = f"""당신은 10년 경력의 시니어 HRD 컨설턴트입니다.
고객이 입력한 교육 니즈를 분석하여 아래 JSON 형식으로 정확하게 변환하세요.

[고객 입력]
니즈: {query}
산업군: {industry}
교육 대상: {target}
교육 시간: {duration} ({duration_h}H)

[출력 규칙]
- core_keywords: 교육에서 반드시 다뤄야 할 핵심 역량/주제 키워드 3~5개
- pain_point: 현재 조직/구성원의 핵심 문제점 1~2문장
- expected_behavior: 교육 후 기대되는 구체적 행동 변화 1~2문장
- learning_level: beginner / intermediate / advanced 중 선택

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

{{
  "target": "{target}",
  "industry": "{industry}",
  "duration_hours": {duration_h},
  "core_keywords": ["키워드1", "키워드2", "키워드3"],
  "pain_point": "현재 문제점",
  "expected_behavior": "기대 행동 변화",
  "learning_level": "intermediate",
  "preferred_style": "실습형"
}}"""

    try:
        response = client_genai.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
    except Exception as e:
        # JSON 파싱 실패 시 기본값 반환
        return {
            "target": target,
            "industry": industry,
            "duration_hours": duration_h,
            "core_keywords": query.split()[:5],
            "pain_point": query,
            "expected_behavior": "역량 향상",
            "learning_level": "intermediate",
            "preferred_style": "실습형"
        }


# ============ Step 2: 모듈 검색 ============
def search_modules(collection, needs_json, db_type, top_k=20):
    """
    구조화된 니즈를 기반으로 ChromaDB에서 관련 모듈 검색
    """
    keywords = needs_json.get("core_keywords", [])
    search_text = " ".join(keywords) + " " + needs_json.get("pain_point", "")

    # 임베딩 생성
    result = client_genai.models.embed_content(
        model="gemini-embedding-001",
        contents=search_text
    )
    embedding = result.embeddings[0].values

    # ChromaDB 검색
    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(top_k, collection.count())
    )
    return results


# ============ [P0-B] Hard-Binding RAG Context ============
def search_modules_detailed(collection, needs_json, db_type, top_k=8):
    """
    [P0-B] 검색 결과를 상세하게 JSON으로 구조화하여 Gemini 프롬프트에 주입할 수 있는 형태로 반환
    기존의 search_modules()와 동일한 검색 과정이지만, 결과를 상세 JSON으로 포장
    """
    keywords = needs_json.get("core_keywords", [])
    search_text = " ".join(keywords) + " " + needs_json.get("pain_point", "")

    # 임베딩 생성
    result = client_genai.models.embed_content(
        model="gemini-embedding-001",
        contents=search_text
    )
    embedding = result.embeddings[0].values

    # ChromaDB 검색
    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(top_k, collection.count())
    )

    # [P0-B] 검색 결과를 상세하게 구조화
    retrieved_modules = []

    if db_type == "module":
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        for idx, (meta, dist) in enumerate(zip(metas, distances)):
            similarity = round((1 - dist) * 100, 1)

            # 핵심: 모듈의 세부 내용을 모두 포함
            module_detail = {
                "rank": idx + 1,
                "similarity_percent": similarity,
                "모듈명": meta.get("모듈명", ""),
                "과정명": meta.get("과정명", ""),
                "세부주제목록": meta.get("세부주제목록", ""),
                "세부내용요약": meta.get("세부내용요약", ""),  # ← 핵심 콘텐츠
                "권장시간": meta.get("권장시간", ""),
                "교육방식": meta.get("교육방식", ""),
                "모듈성격": meta.get("모듈성격", "core"),
            }
            retrieved_modules.append(module_detail)
    else:
        # 구버전 폴백
        metas = results["metadatas"][0]
        distances = results["distances"][0]
        for idx, (meta, dist) in enumerate(zip(metas, distances)):
            similarity = round((1 - dist) * 100, 1)
            module_detail = {
                "rank": idx + 1,
                "similarity_percent": similarity,
                "과정명": meta.get("과정명", ""),
                "세부내용요약": meta.get("세부내용요약", ""),
                "권장시간": meta.get("권장시간", ""),
            }
            retrieved_modules.append(module_detail)

    # JSON 형식으로 정렬하여 반환
    retrieved_json = json.dumps(retrieved_modules, ensure_ascii=False, indent=2)
    return results, retrieved_modules, retrieved_json


def group_modules_by_type(search_results, db_type):
    """
    검색된 모듈을 성격별로 그룹핑: intro / core / apply
    """
    groups = {"intro": [], "core": [], "apply": []}

    if db_type == "module":
        metas = search_results["metadatas"][0]
        docs = search_results["documents"][0]
        distances = search_results["distances"][0]

        for meta, doc, dist in zip(metas, docs, distances):
            module_type = meta.get("모듈성격", "core")
            similarity = round((1 - dist) * 100, 1)
            groups[module_type].append({
                "meta": meta,
                "similarity": similarity,
            })
    else:
        # 구버전 폴백
        metas = search_results["metadatas"][0]
        distances = search_results["distances"][0]
        for meta, dist in zip(metas, distances):
            similarity = round((1 - dist) * 100, 1)
            groups["core"].append({"meta": meta, "similarity": similarity})

    return groups


# ============ Step 3: 커리큘럼 조합 ============
# ============ [P1-1] 활동 소요 시간 검증 ============
def validate_curriculum_timing(curriculum_text: str, total_hours: int) -> dict:
    """
    생성된 커리큘럼에서 활동별 소요 시간을 추출하고 검증
    Returns: {valid: bool, total_minutes: int, warnings: list, details: str}
    """
    total_minutes = total_hours * 60
    extracted_activities = []

    # 표 형식의 커리큘럼에서 시간 추출 (정규식)
    table_pattern = r'\|\s*(.+?)\s*\|\s*(\d+(?:\.\d+)?)\s*[HhWw]?\s*\|'
    matches = re.findall(table_pattern, curriculum_text)

    total_allocated = 0
    for module_name, hours_str in matches:
        try:
            hours = float(hours_str)
            minutes = hours * 60
            total_allocated += minutes
            extracted_activities.append({
                "모듈": module_name.strip()[:40],
                "시간": hours,
                "분": minutes
            })
        except:
            pass

    # 검증
    is_valid = total_allocated <= total_minutes
    variance = total_minutes - total_allocated

    details = f"배분된 시간: {total_allocated}분 / 전체: {total_minutes}분 (여유: {variance}분)"

    warnings = []
    if variance < 0:
        warnings.append(f"⚠️ 교육 시간 초과: {abs(variance)}분 과다")
    elif variance < 30:
        warnings.append(f"⚠️ 여유 시간 부족: {variance}분만 남음 (피드백/휴식 시간 추가 권장)")

    return {
        "valid": is_valid,
        "total_minutes_allocated": total_allocated,
        "total_minutes_available": total_minutes,
        "variance_minutes": variance,
        "activities_count": len(extracted_activities),
        "warnings": warnings,
        "details": details,
        "activities": extracted_activities
    }


def validate_proposal_quality(text: str) -> dict:
    """
    [P1] AI Self-Correction Validator
    생성된 제안서가 최소 품질 기준을 충족하는지 검증
    Returns: {passed: bool, failures: list, module_count: int, interactions_found: list}
    """
    failures = []

    # 검증 1: 모듈 헤더(### N.) 개수 (3개 이상)
    module_count = len(re.findall(r'^###\s+\d+[\.\s]', text, re.MULTILINE))
    if module_count < 3:
        failures.append(f"커리큘럼 모듈이 {module_count}개로 3개 미만입니다. (최소 3개 필요)")

    # 검증 2: 세부 항목 bullet 개수 (전체 모듈 합산 기준 — 모듈당 평균 3개 이상)
    bullet_count = len(re.findall(r'(?:^|\n)\s*[·\-\*]\s+\S', text))
    if module_count > 0 and bullet_count < module_count * 3:
        failures.append(
            f"모듈당 세부 항목이 부족합니다. "
            f"(현재 전체 {bullet_count}개 / 최소 {module_count * 3}개 필요)"
        )

    # 검증 3: 상호작용 활동 키워드 포함 여부
    interaction_keywords = ['토의', '실습', '롤플레잉', '롤플레이', '워크샵', '사례분석', '케이스스터디']
    found_interactions = [kw for kw in interaction_keywords if kw in text]
    if not found_interactions:
        failures.append(
            "상호작용형 활동 키워드(토의/실습/롤플레잉/워크샵/사례분석)가 전혀 포함되지 않았습니다."
        )

    return {
        "passed": len(failures) == 0,
        "failures": failures,
        "module_count": module_count,
        "interactions_found": found_interactions,
    }


def assemble_curriculum(needs_json, grouped_modules, duration, retrieved_modules_json=None, selected_modules=None, track="standard", advanced_context=None):
    """
    Gemini가 검색된 모듈을 조합하여 최적 커리큘럼 구성
    [P0-B] Hard-Binding RAG: Retrieved modules를 JSON으로 강제 주입
    [P1-1] CoT Time Validator: AI가 활동별 소요 시간을 계산하고 자동 조정
    [PM] selected_modules: 사용자가 선택한 모듈 (우선 포함 필수)
    """
    framework = DURATION_FRAMEWORK.get(duration, DURATION_FRAMEWORK["8H (1일)"])
    total_h = framework["total_h"]
    total_minutes = total_h * 60

    # 각 그룹에서 상위 모듈 선별 (화면 표시용 - 프롬프트에는 사용 안 함)
    def format_modules(modules, limit=5):
        out = []
        for m in modules[:limit]:
            meta = m["meta"]
            name = meta.get("모듈명", "")
            topics = meta.get("세부주제목록", "")
            content = meta.get("세부내용요약", "")[:150]
            course = meta.get("과정명", "")
            sim = m["similarity"]
            out.append(f"- [{course}] {name}\n  주제: {topics}\n  내용: {content}\n  유사도: {sim}%")
        return "\n".join(out) if out else "없음"

    intro_list = format_modules(grouped_modules.get("intro", []))
    core_list = format_modules(grouped_modules.get("core", []), 8)
    apply_list = format_modules(grouped_modules.get("apply", []))

    # [P0-B] retrieved_modules_json이 없으면 이전 방식으로 폴백
    if not retrieved_modules_json:
        retrieved_modules_json = json.dumps([
            {"모듈명": intro_list, "세부내용요약": "도입 모듈"},
            {"모듈명": core_list, "세부내용요약": "핵심 모듈"},
            {"모듈명": apply_list, "세부내용요약": "현업 적용 모듈"}
        ], ensure_ascii=False, indent=2)

    # [PM] 사용자 선택 모듈 섹션 생성
    if selected_modules:
        selected_json_str = json.dumps(selected_modules, ensure_ascii=False, indent=2)
        selected_section = f"""
## 🎯 [사용자 선택 모듈] 반드시 우선 포함 (필수)

사용자가 직접 선택한 모듈입니다. 아래 모듈들은 커리큘럼에 **반드시** 포함되어야 합니다.

```json
{selected_json_str}
```

### 선택 모듈 우선 순위 규칙
1. 위 선택된 모듈들은 100% 커리큘럼에 포함합니다 (생략 불가).
2. 선택된 모듈의 "세부내용요약"에서 구체적 활동을 추출하여 그대로 사용합니다.
3. 남은 시간은 아래 Retrieved Modules에서 가장 적합한 모듈로 채웁니다.
"""
    else:
        selected_section = """
## 🎯 모듈 선택 안내
사용자가 특별히 선택한 모듈이 없습니다. 아래 Retrieved Modules 목록에서 AI가 가장 적합한 모듈을 자유롭게 선택하세요.
"""

    prompt = f"""당신은 10년 경력의 시니어 HRD 컨설턴트입니다.
아래 고객 니즈와 검색된 교육 모듈을 바탕으로, 완성도 높은 교육 제안서를 작성하세요.

## 고객 니즈 분석 결과
- 교육 대상: {needs_json.get('target')}
- 산업군: {needs_json.get('industry')}
- 핵심 키워드: {', '.join(needs_json.get('core_keywords', []))}
- 현재 문제점: {needs_json.get('pain_point')}
- 기대 행동 변화: {needs_json.get('expected_behavior')}
- 교육 시간: {total_h}H (= {total_minutes}분)
{selected_section}

## ⭐ [P0-B] Retrieved Modules (검색된 실제 교육 모듈들 - 반드시 이것만 사용)

다음은 고객의 니즈와 가장 유사한 실제 교육 모듈들입니다.
당신은 이 모듈들의 세부 활동과 시간을 "레고 블록처럼" 조립하여 새로운 커리큘럼을 구성해야 합니다.
이 목록에 없는 새로운 활동을 창작하면 안 됩니다.

```json
{retrieved_modules_json}
```

## 검색된 교육 모듈 풀 (참고용 - 위 JSON이 정확함)

### [도입 모듈 후보]
{intro_list}

### [핵심 스킬 모듈 후보]
{core_list}

### [현업 적용 모듈 후보]
{apply_list}

## 활동별 소요 시간 참고 데이터 (Chain-of-Thought 계산용)
- 강의/설명: 1.0분/분량
- 진단/사전평가: 5분
- 롤플레잉: 10분
- 페어 활동: 10분
- 팀/전체 토의: 8분
- 실습 활동: 12분
- 사례 분석: 8분
- 케이스 스터디: 12분
- 시뮬레이션: 15분
- 피드백 세션: 5분
- 발표/공유: 8분
- Q&A: 5분

## 작성 지침 (필수)

### [포맷팅 절대 규칙]
- 마크다운 표 내부 '세부 내용' 컬럼에 **절대로 `<br>` 태그나 HTML을 사용하지 마십시오.**
- 줄바꿈이 필요하면 반드시 `\n- ` 형식의 마크다운 리스트로 작성하십시오.

### [P0-B] Hard-Binding RAG 규칙 (100% 강제)
1. **절대 금지**: 위 "Retrieved Modules" 목록에 없는 활동을 추가하지 마세요.
2. **레고 블록 조립**: 각 모듈의 "세부내용요약"에서 구체적인 활동(진단, 롤플레이, 토의 등)을 추출하여 그대로 사용하세요.
3. **시간 참고**: 각 모듈의 "권장시간"은 참고 기준입니다. 풍부한 내용 제공을 위해 시간이 다소 초과되더라도 내용을 축소하거나 활동을 삭제하지 마세요.
4. **충분한 내용**: 각 모듈당 최소 5~8개의 풍부한 세부 항목을 원본 JSON의 "세부내용요약"에서 가져와 표 형식으로 구성하세요.

### [P0-2] RAG 강제 인용 규칙 (80% 이상)
1. **"검색된 교육 모듈 풀"의 모듈들의 구체적 활동을 반드시 80% 이상 커리큘럼에 인용하세요.**
   - 모듈 제목만 차용하지 말 것 (❌ "피드백 스킬")
   - 구체적 활동을 직접 명시할 것 (✅ "피어 피드백 진단지 작성 (30분)")
2. AI 창작은 최소화하고, 검색된 모듈의 검증된 콘텐츠를 우선 활용하세요.

### [핵심 품질 지침 — 반드시 준수]

**[지침 1] 내용 풍부성 (절대 축소 금지)**
- 절대 내용을 축소하거나 요약하지 마십시오.
- 컨설턴트가 취사선택할 수 있도록 각 모듈당 최소 5개 이상의 구체적인 세부 내용을 제안하십시오.
- 시간이 초과된다는 이유로 활동을 삭제하거나 모듈 수를 줄이는 것을 엄격히 금지합니다.

**[지침 2] 상호작용형 활동 필수 포함**
- 각 커리큘럼 모듈에는 이론/강의뿐만 아니라, 반드시 최소 1개 이상의 상호작용형 활동을 포함하십시오.
- 활동 유형: `[토의]`, `[실습]`, `[롤플레잉]`, `[워크샵]`, `[사례분석]` 중 선택
- 구체적인 실행 방법과 사례명을 명시하십시오.
  - 예: `[롤플레잉] 고객 클레임 3단계 대응 시나리오 — 3인 1조 역할 분담 후 피드백`
  - 예: `[토의] 우리 팀의 소통 장벽 Top 3 도출 — 포스트잇 브레인스토밍`

**[지침 3] 시간 표기 방식**
- 시간 컬럼에는 고정값 대신 **범위 형태**로 표기하세요. (예: `60~90분 (제안)`, `90~120분 (제안)`)
- 총 교육 시간 {duration} ({total_h}H)은 컨설턴트가 최종 조율하는 **참고 기준**입니다.
- 시간을 맞추기 위해 내용을 삭제하는 행위는 엄격히 금지합니다.

4. 반드시 아래 양식을 정확히 따라 작성하세요

---

# [고객사명] 맞춤 교육 제안서

## 📋 과정 개요
* **과정명:** (창의적이고 전문적인 과정명)
* **교육 대상:** {needs_json.get('target')}
* **교육 목적:** (1~2문장)
* **교육 시간:** {duration} ({total_h}H)
* **교육 방식:** (강의형/실습형/워크샵형 중 선택)

---

## 🎯 교육 목표
교육 종료 후 참가자는 다음을 할 수 있습니다:
1. (블룸의 분류학: 인식/이해 수준) ~의 중요성을 인식하고 설명할 수 있다
2. (블룸의 분류학: 적용 수준) ~스킬을 현업 상황에 적용할 수 있다
3. (블룸의 분류학: 분석/평가 수준) ~을 기준으로 분석하고 개선안을 도출할 수 있다

---

## 📚 상세 커리큘럼

(아래 양식을 모듈마다 반복. 마크다운 표(table)는 절대 사용 금지)

### 1. [모듈 주제명] (60~90분 제안)
- 주요 학습 내용 요약 (1~2문장)
  - 세부 상세 내용 1
  - 세부 상세 내용 2
  - 세부 상세 내용 3
  - 세부 상세 내용 4
  - 세부 상세 내용 5
  - [실습] 구체적 활동명 및 실행 방법 (대괄호 필수)

### 2. [모듈 주제명] (60~90분 제안)
- 주요 학습 내용 요약
  - 세부 상세 내용 1~5
  - [토의] 구체적 토의 주제 및 진행 방식 (대괄호 필수)

### 3. [모듈 주제명] (60~90분 제안)
- 주요 학습 내용 요약
  - 세부 상세 내용 1~5
  - [롤플레잉] 시나리오명 및 역할 구성 (대괄호 필수)

(모듈은 교육 시간에 따라 자유롭게 추가. 표 사용 절대 금지)

---

## 💡 기대 효과

### 조직 관점
(조직 차원에서 기대되는 변화 2~3문장)

### 개인 관점
(개인 구성원 차원에서 기대되는 성장 2~3문장)

---
*본 제안서는 [고객사명]의 요구사항을 반영하여 작성된 맞춤형 초안입니다.*
*담당: [담당 컨설턴트명] | 문의: [연락처]*
"""

    # [Sprint 2-2] 고도화 모드: 조직 맥락 컨텍스트 + 추가 섹션 강제
    if track == "advanced" and advanced_context:
        adv_filled = {k: v for k, v in advanced_context.items() if v and v.strip()}
        if adv_filled:
            ctx_lines = "\n".join(f"- {k}: {v}" for k, v in adv_filled.items())
            prompt = f"""[조직 맞춤화 컨텍스트 — 반드시 커리큘럼에 반영하세요]
{ctx_lines}

""" + prompt

        prompt += """

---

## 📌 고도화 전용 추가 섹션 (반드시 아래 3개 섹션을 제안서 하단에 작성)

### 6. 조직 맥락 반영
(위 [조직 맞춤화 컨텍스트]의 기업 문화·현황을 커리큘럼 설계에 반영한 근거를 2~3문장으로 서술)

### 7. 학습 전이(Learning Transfer) 플랜
| 단계 | 시점 | 활동 내용 |
|------|------|-----------|
| 사전 진단 | 교육 2주 전 | (진단 도구 및 방법) |
| 현업 적용 과제 | 교육 직후 | (Action Learning 과제) |
| 사후 Follow-up | 교육 4~8주 후 | (코칭·점검 방식) |

### 8. ROI 및 평가 방안
| 평가 단계 | Kirkpatrick 수준 | 측정 지표 | 측정 방법 |
|-----------|------------------|-----------|-----------|
| 반응 | Level 1 | 만족도 점수 | 교육 종료 후 설문 |
| 학습 | Level 2 | 사전·사후 역량 변화 | 진단지 점수 비교 |
| 행동 | Level 3 | 현업 적용률 | 상사 관찰 / 자기 보고 |
| 결과 | Level 4 | 팀 성과 지표 변화 | (구체적 KPI 명시) |
"""

    # [P1] Self-Correction 루프: 최대 3회 시도 (첫 생성 + 최대 2회 재생성)
    MAX_QUALITY_ATTEMPTS = 3
    last_curriculum = None

    for quality_attempt in range(MAX_QUALITY_ATTEMPTS):
        # API 호출 (Rate-limit 재시도 포함)
        curriculum = None
        for api_attempt in range(3):
            try:
                response = client_genai.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt
                )
                curriculum = response.text
                break
            except Exception as e:
                if "429" in str(e) and api_attempt < 2:
                    wait = 60 * (api_attempt + 1)
                    st.warning(f"⏳ API 제한. {wait}초 후 재시도... ({api_attempt+1}/3)")
                    time.sleep(wait)
                else:
                    raise e

        if curriculum is None:
            raise Exception("API 호출에 실패했습니다.")

        last_curriculum = curriculum

        # [P1] Self-Correction Validator — 품질 검증
        quality_result = validate_proposal_quality(curriculum)

        if quality_result["passed"]:
            # 품질 기준 통과 → 바로 반환
            break

        if quality_attempt < MAX_QUALITY_ATTEMPTS - 1:
            # 품질 기준 미달 → 백그라운드 재생성 (에러 노출 없음)
            failures_text = " | ".join(quality_result["failures"])
            # 프롬프트 끝에 실패 원인을 추가하여 재시도
            prompt = prompt + f"""

---
[자동 재생성 — 이전 결과 품질 기준 미달]
이전 생성 결과가 아래 기준을 충족하지 못했습니다. 반드시 수정하여 재생성하세요:
{chr(10).join(f'- {f}' for f in quality_result['failures'])}
특히 각 모듈에 [토의]/[실습]/[롤플레잉]/[워크샵]/[사례분석] 중 하나 이상을 반드시 포함하고,
모듈당 세부 항목을 5개 이상 작성하세요.
"""
        # 마지막 시도이거나 통과했으면 루프 종료

    # 시간 검증 (참고용 — 내부 QA용)
    timing_result = validate_curriculum_timing(last_curriculum, total_h)
    st.session_state.curriculum_timing = timing_result

    return last_curriculum, timing_result


# ============ 검수자 AI ============
def review_proposal(proposal_text: str, needs_json: dict) -> dict:
    """
    검수자 페르소나(시니어 HRD 컨설턴트)가 제안서를 평가하고
    점수·피드백·개선지시를 JSON으로 반환
    """
    prompt = f"""당신은 HR 교육 컨설팅 분야의 20년 경력 시니어 컨설턴트이자 제안서 품질 검수 전문가입니다.
아래 AI가 생성한 교육 제안서를 냉정하고 전문적으로 평가하세요.

## 고객 니즈 (기준점)
- 대상: {needs_json.get('target')}
- 핵심 키워드: {', '.join(needs_json.get('core_keywords', []))}
- 문제점: {needs_json.get('pain_point')}
- 기대 행동 변화: {needs_json.get('expected_behavior')}

## 평가 대상 제안서
{proposal_text}

## 평가 기준 (100점 만점)
- 니즈_적합성 (25점): 고객 문제점과 기대 행동 변화가 커리큘럼에 반영되었는가
- 커리큘럼_완성도 (35점): 모듈 흐름(도입→핵심→적용)이 논리적이고 시간 배분이 현실적인가
- 전문성_표현 (25점): HRD 교수법(롤플레잉·진단지·케이스 스터디 등)이 구체적으로 명시되었는가
- 제출_가능성 (15점): 고객사에 바로 이메일로 보낼 수 있는 수준의 문장과 형식인가

반드시 아래 JSON 형식으로만 응답하세요:
{{
  "총점": 85,
  "항목별_점수": {{
    "니즈_적합성": 22,
    "커리큘럼_완성도": 30,
    "전문성_표현": 20,
    "제출_가능성": 13
  }},
  "잘된_점": ["구체적인 강점 1", "강점 2"],
  "개선_필요": ["우선순위 1번 개선사항", "2번", "3번"],
  "즉시_수정_필요": ["고객 제출 전 반드시 고쳐야 할 사항"],
  "제출_가능_여부": "즉시 가능 or 수정 후 가능 or 전면 재작성 필요",
  "개선_지시문": "재생성 AI에게 전달할 구체적인 개선 지시 (2~4문장, 한국어)"
}}"""

    for attempt in range(3):
        try:
            response = client_genai.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(60 * (attempt + 1))
            else:
                return {"총점": 0, "개선_지시문": str(e), "제출_가능_여부": "오류"}


def improve_proposal(original_proposal: str, review_result: dict,
                     needs_json: dict, grouped_modules: dict, duration: str) -> str:
    """검수 피드백을 반영해 제안서 재생성"""
    framework = DURATION_FRAMEWORK.get(duration, DURATION_FRAMEWORK["8H (1일)"])
    total_h = framework["total_h"]

    improvement_prompt = review_result.get("개선_지시문", "전반적으로 개선하세요.")
    issues = "\n".join([f"- {i}" for i in review_result.get("개선_필요", [])])
    critical = "\n".join([f"- {i}" for i in review_result.get("즉시_수정_필요", [])])

    prompt = f"""당신은 10년 경력의 시니어 HRD 컨설턴트입니다.
아래 1차 제안서를 검수자의 피드백을 반영하여 완성도 높게 개선하세요.

## 검수자 피드백 (반드시 모두 반영)
{improvement_prompt}

### 개선 필요 사항
{issues}

### 즉시 수정 필수
{critical}

## 고객 니즈
- 대상: {needs_json.get('target')} | 산업: {needs_json.get('industry')} | {total_h}H
- 핵심 키워드: {', '.join(needs_json.get('core_keywords', []))}
- 기대 행동 변화: {needs_json.get('expected_behavior')}

## 1차 제안서 (개선 기준)
{original_proposal[:3000]}

---
위 피드백을 100% 반영하여, 동일한 섹션 구조(과정 개요 → 교육 목표 → 상세 커리큘럼 → 기대 효과)로
마크다운 형식의 개선된 제안서를 작성하세요. [고객사명], [담당자명] 플레이스홀더는 유지하세요.

커리큘럼 작성 시 반드시 아래 양식을 사용하세요 (표 사용 절대 금지):
### N. [모듈 주제명] (XX~XX분 제안)
- 주요 학습 내용 요약
  - 세부 내용 1~5개 이상
  - [실습/토의/롤플레잉/워크샵/사례분석] 활동명 (대괄호 필수)

## 개선 시 반드시 지켜야 할 품질 기준
- **내용 축소 절대 금지**: 각 모듈당 세부 항목 5개 이상, 내용을 요약하거나 줄이지 마세요.
- **상호작용 활동 필수**: 각 모듈에 `[토의]`, `[실습]`, `[롤플레잉]`, `[워크샵]`, `[사례분석]` 중 최소 1개 이상 포함하고 구체적 실행 방법을 명시하세요.
- **시간 표기**: 시간 컬럼은 `60~90분 (제안)` 형태의 범위로 표기하세요."""

    for attempt in range(3):
        try:
            response = client_genai.models.generate_content(
                model=MODEL_NAME,
                contents=prompt
            )
            return response.text
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(60 * (attempt + 1))
            else:
                raise e



# ============ [P0-1] Placeholder 치환 함수 ============
def replace_placeholders(text: str, company_name: str) -> tuple:
    """
    생성된 텍스트에서 placeholder를 실제 정보로 치환
    Returns: (치환된 텍스트, 미처리된 placeholder 목록)
    """
    result = text
    result = result.replace("[고객사명]", company_name)
    result = result.replace("[고객 사명]", company_name)

    # 미처리된 placeholder 찾기
    remaining = re.findall(r'\[([^\]]+)\]', result)
    remaining = list(set(remaining))  # 중복 제거
    return result, remaining


# ============ Streamlit UI v3.0 ============
st.set_page_config(page_title="제안서 AI 어시스턴트", page_icon="📋", layout="wide")

st.markdown("""
<style>
.needs-box {
    background: #f0f4ff; border-left: 4px solid #4a6cf7;
    padding: 12px; border-radius: 6px; margin: 8px 0;
}
.module-card {
    background: #f9fafb; border: 1px solid #e5e7eb;
    padding: 10px; border-radius: 6px; margin: 4px 0; font-size: 0.9em;
}
.step-done {
    background: #f0fdf4; border: 1px solid #86efac;
    padding: 8px 14px; border-radius: 6px;
    color: #15803d; font-size: 0.9em; margin: 4px 0;
}
</style>
""", unsafe_allow_html=True)

st.title("📋 제안서 AI 어시스턴트 v3.0")
st.caption("고객 니즈 수집 → 니즈 확인 → 모듈 선택 → 맞춤 제안서 생성")

# ── session_state 초기화 ──
_DEFAULTS = {
    "workflow_step": 1,
    "initial_query": None,
    "chatbot_started": False,        # [Sprint 1-1] 명시적 버튼 클릭 전까지 챗봇 비활성
    "needs_conversation": [],
    "needs_complete": False,
    "needs_json": None,
    "retrieved_modules": [],
    "retrieved_modules_json": None,
    "grouped": None,
    "selected_modules": [],
    "generation_track": "standard",  # [Sprint 2] "standard" | "advanced"
    "advanced_context": {},          # [Sprint 2] 고도화 모드 추가 입력값
    "proposal": None,
    "remaining_placeholders": [],
    "curriculum_timing": None,
    "review": None,
    "improved_proposal": None,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

current_step = st.session_state.workflow_step

# ── 사이드바 ──
with st.sidebar:
    st.header("📝 고객 정보")

    company_name = st.text_input(
        "🏢 고객사명",
        placeholder="예: 삼성전자, 현대자동차, SK하이닉스",
        help="생성된 제안서에서 [고객사명]을 자동으로 치환합니다."
    )
    industry = st.selectbox("산업군", ["전산업", "유통", "금융", "제조", "IT", "건설", "공공", "의료/제약", "교육", "통신/미디어"])
    target   = st.selectbox("교육 대상", ["팀장/리더급", "신입사원", "중간관리자", "임원", "전직급", "영업직", "기타"])
    duration = st.selectbox("교육 기간", ["4H (반일)", "8H (1일)", "16H (2일)", "24H (3일)"])

    st.divider()
    st.header("⚙️ 검색 설정")
    top_k = st.slider("검색할 모듈 수 (내부)", 8, 20, 12)

    st.divider()
    if os.path.exists(MODULE_DB_PATH):
        st.success("✅ 모듈 DB 사용 중")
    else:
        st.warning("⚠️ 모듈 DB 없음")
    st.caption(f"🤖 모델: `{MODEL_NAME}`")

    st.divider()
    if st.button("🔄 처음부터 다시 시작", use_container_width=True):
        for _k in list(_DEFAULTS.keys()):
            if _k in st.session_state:
                del st.session_state[_k]
        st.rerun()

st.divider()

# ─────────────────────────────────────────────────────────
# STEP 1 : 고객 니즈 입력 & 추가 정보 수집 (챗봇)
# ─────────────────────────────────────────────────────────
st.subheader("1️⃣ 고객 니즈 입력")

if current_step == 1:
    initial_query_input = st.text_area(
        "고객의 교육 니즈를 자유롭게 입력하세요",
        height=120,
        placeholder=(
            "예: 우리 회사 신임 팀장들이 MZ세대 팀원들과 소통하는 데 어려움을 겪고 있어요. "
            "특히 면담이나 피드백 상황에서 어떻게 대화해야 할지 몰라 회피하는 경향이 있습니다. "
            "실습 위주로 실제 현장에서 바로 쓸 수 있는 스킬을 익히길 원합니다."
        ),
        key="initial_query_input"
    )

    # 텍스트 변경 시 chatbot 상태 초기화 (자동 진행 없음)
    if initial_query_input and initial_query_input != st.session_state.initial_query:
        st.session_state.initial_query = initial_query_input
        st.session_state.needs_conversation = []
        st.session_state.needs_complete = False
        st.session_state.chatbot_started = False

    # [Sprint 1-1] 명시적 트리거 버튼 — 클릭 전까지 챗봇 비활성
    if initial_query_input and not st.session_state.chatbot_started:
        if st.button("🚀 니즈 분석 시작", type="primary", use_container_width=True, key="start_chatbot"):
            st.session_state.initial_query = initial_query_input
            st.session_state.chatbot_started = True
            st.rerun()

    if st.session_state.initial_query and st.session_state.chatbot_started:
        st.divider()
        st.subheader("🤖 추가 정보 수집")

        full_conv_text = st.session_state.initial_query + " " + " ".join(
            m.get("content", "") for m in st.session_state.needs_conversation if m.get("role") == "user"
        )

        # AI 첫 번째 질문 생성 (대화가 없을 때만)
        if len(st.session_state.needs_conversation) == 0:
            with st.spinner("🤖 AI가 질문을 준비 중..."):
                ai_q = generate_follow_up_questions(
                    st.session_state.initial_query, industry, target, []
                )
            st.session_state.needs_conversation.append({"role": "assistant", "content": ai_q})
            st.rerun()

        # 대화 기록 표시
        for msg in st.session_state.needs_conversation:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        # 정보 완성도 표시
        completeness = check_info_completeness(full_conv_text)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("📍 교육 인원", "✅" if completeness.get("교육인원") else "⏳")
        with c2:
            st.metric("🎯 Pain Point", "✅" if completeness.get("pain_point") else "⏳")
        with c3:
            st.metric("📚 실습 비중", "✅" if completeness.get("실습비중") else "⏳")
        with c4:
            st.metric("📖 기존 시도", "✅" if completeness.get("기존시도") else "⏳ (선택)")

        # 사용자 추가 답변 입력
        user_chat_in = st.chat_input("답변을 입력하세요...", key="needs_chat_input")
        if user_chat_in:
            st.session_state.needs_conversation.append({"role": "user", "content": user_chat_in})
            new_full = full_conv_text + " " + user_chat_in
            if not is_needs_complete(new_full):
                with st.spinner("🤖 다음 질문 준비 중..."):
                    nq = generate_follow_up_questions(
                        st.session_state.initial_query, industry, target,
                        st.session_state.needs_conversation
                    )
                st.session_state.needs_conversation.append({"role": "assistant", "content": nq})
            st.rerun()

        st.divider()

        needs_ok = is_needs_complete(full_conv_text)
        if not needs_ok:
            st.info("💡 필수 정보(교육인원, Pain Point, 실습비중) 중 최소 2가지 이상 입력하면 다음 단계로 진행할 수 있습니다.")

        if st.button(
            "✅ 충분합니다 — 니즈 분석으로",
            type="primary",
            use_container_width=True,
            disabled=not needs_ok,
            key="step1_next"
        ):
            full_needs_text = st.session_state.initial_query + " " + " ".join(
                m.get("content", "") for m in st.session_state.needs_conversation if m.get("role") == "user"
            )
            with st.spinner("🔍 니즈를 구조화하는 중..."):
                nj = analyze_needs(full_needs_text, industry, target, duration)
            st.session_state.needs_json = nj
            st.session_state.workflow_step = 2
            st.rerun()

else:
    # Step 1 완료 요약
    st.markdown(
        f'<div class="step-done">✅ 니즈 수집 완료: {str(st.session_state.initial_query or "")[:80]}…</div>',
        unsafe_allow_html=True
    )

# ─────────────────────────────────────────────────────────
# STEP 2 : 니즈 분석 결과 확인
# ─────────────────────────────────────────────────────────
if current_step >= 2 and st.session_state.needs_json:
    st.divider()
    st.subheader("2️⃣ 니즈 분석 결과 확인")

    nj = st.session_state.needs_json
    st.markdown(f"""
<div class="needs-box">
<b>핵심 키워드:</b> {', '.join(nj.get('core_keywords', []))}<br>
<b>문제점 (Pain Point):</b> {nj.get('pain_point', '')}<br>
<b>기대 행동 변화:</b> {nj.get('expected_behavior', '')}<br>
<b>학습 수준:</b> {nj.get('learning_level', '')} &nbsp;|&nbsp; <b>선호 방식:</b> {nj.get('preferred_style', '')}
</div>
""", unsafe_allow_html=True)

    if current_step == 2:
        st.caption("위 분석 결과가 맞으면 다음 단계로 진행하세요. 다르면 처음으로 돌아가 수정하세요.")

        col_ok, col_back = st.columns([3, 1])
        with col_ok:
            if st.button(
                "✅ 분석 결과가 맞습니다 — 모듈 검색으로",
                type="primary",
                use_container_width=True,
                key="step2_confirm"
            ):
                collection, db_type = load_module_db()
                with st.spinner("📚 관련 모듈 검색 중..."):
                    try:
                        s_results, r_modules, r_modules_json = search_modules_detailed(
                            collection, nj, db_type, top_k=top_k
                        )
                        grouped = group_modules_by_type(s_results, db_type)
                        st.session_state.retrieved_modules = r_modules
                        st.session_state.retrieved_modules_json = r_modules_json
                        st.session_state.grouped = grouped
                        st.session_state.workflow_step = 3
                    except Exception as e:
                        st.error(f"모듈 검색 오류: {e}")
                st.rerun()

        with col_back:
            if st.button("✏️ 다시 입력", use_container_width=True, key="step2_back"):
                st.session_state.workflow_step = 1
                st.session_state.needs_json = None
                st.rerun()

    else:
        st.markdown(
            f'<div class="step-done">✅ 분석 확인 완료 | 키워드: {", ".join(nj.get("core_keywords", []))}</div>',
            unsafe_allow_html=True
        )

# ─────────────────────────────────────────────────────────
# STEP 3 : 모듈 선택
# ─────────────────────────────────────────────────────────
if current_step >= 3 and st.session_state.retrieved_modules:
    st.divider()
    st.subheader("3️⃣ 교육 모듈 선택")

    r_mods = st.session_state.retrieved_modules

    if current_step == 3:
        st.caption(
            "검색된 모듈 중 커리큘럼에 포함할 모듈을 선택하세요. "
            "선택하지 않으면 AI가 자동으로 최적 모듈을 선택합니다."
        )

        type_label = {"intro": "🔵 도입", "core": "🟢 핵심", "apply": "🟡 현업적용"}

        # [로드맵] 유사도 → 별점 변환
        def sim_to_stars(s):
            if s >= 90: return "⭐⭐⭐⭐⭐"
            elif s >= 80: return "⭐⭐⭐⭐"
            elif s >= 70: return "⭐⭐⭐"
            elif s >= 60: return "⭐⭐"
            return "⭐"

        for i, mod in enumerate(r_mods):
            mod_name   = mod.get("모듈명", f"모듈 {i+1}")
            course     = mod.get("과정명", "")
            sim        = mod.get("similarity_percent", 0)
            topics     = mod.get("세부주제목록", "")
            content    = mod.get("세부내용요약", "")
            rec_time   = mod.get("권장시간", "")
            edu_type   = mod.get("교육방식", "")
            char       = mod.get("모듈성격", "core")
            tag        = type_label.get(char, "🟢 핵심")
            stars      = sim_to_stars(sim)

            col_chk, col_info = st.columns([1, 11])
            with col_chk:
                st.checkbox("선택", key=f"mod_sel_{i}", label_visibility="collapsed")
            with col_info:
                with st.expander(
                    f"{tag} **{mod_name}** | {course} | {rec_time} | {stars} ({sim}%)"
                ):
                    st.markdown(f"**세부 주제:** {topics}")
                    st.markdown(f"**교육 방식:** {edu_type}")
                    if content:
                        st.markdown("**세부 내용:**")
                        for ln in content.split("\n"):
                            if ln.strip():
                                st.markdown(f"- {ln.strip()}")
            st.markdown("---")

        # 선택 현황 요약
        sel_idx = [i for i in range(len(r_mods)) if st.session_state.get(f"mod_sel_{i}", False)]
        if len(sel_idx) == 0:
            st.info("💡 모듈을 선택하지 않으면 AI가 자동으로 선택합니다.")
        else:
            sel_names = [r_mods[i].get("모듈명", f"모듈 {i+1}") for i in sel_idx]
            st.success(f"✅ {len(sel_idx)}개 선택됨: {', '.join(sel_names)}")

        st.divider()

        # ── [Sprint 2-1] 투트랙 선택 UI ──
        st.markdown("#### 🎯 제안서 생성 방식 선택")
        col_std, col_adv = st.columns(2)
        with col_std:
            std_selected = st.session_state.generation_track == "standard"
            if st.button(
                "📄 표준 (Standard)\n빠른 뼈대 제안서 (~1분)",
                use_container_width=True,
                type="primary" if std_selected else "secondary",
                key="track_standard"
            ):
                st.session_state.generation_track = "standard"
                st.rerun()
        with col_adv:
            adv_selected = st.session_state.generation_track == "advanced"
            if st.button(
                "🔬 고도화 (Advanced)\n맞춤형 심층 제안서 (~3분)",
                use_container_width=True,
                type="primary" if adv_selected else "secondary",
                key="track_advanced"
            ):
                st.session_state.generation_track = "advanced"
                st.rerun()

        # 현재 선택 트랙 안내
        if st.session_state.generation_track == "standard":
            st.caption("✅ **표준 모드**: RAG 기반 커리큘럼을 빠르게 생성합니다.")
        else:
            st.caption("✅ **고도화 모드**: 조직 맥락 + Learning Transfer + ROI 평가 섹션이 추가됩니다.")

        # [Sprint 2-1] 고도화 모드 추가 입력 필드
        if st.session_state.generation_track == "advanced":
            with st.expander("📋 고도화 추가 정보 입력 (선택)", expanded=True):
                adv_status   = st.text_area("1️⃣ 교육 대상 현황", placeholder="예: 팀장 승진 후 평균 6개월 이내, 실무자 출신으로 리더십 경험 부족", height=80, key="adv_status")
                adv_culture  = st.text_area("2️⃣ 조직 문화 / 특이사항", placeholder="예: 수평적 문화 지향, 최근 조직 개편으로 팀 갈등 높음", height=80, key="adv_culture")
                adv_change   = st.text_area("3️⃣ 기대하는 변화 (구체적)", placeholder="예: 교육 3개월 후 팀원 만족도 10% 향상, 이직률 감소", height=80, key="adv_change")
                adv_special  = st.text_area("4️⃣ 특별 요청 사항", placeholder="예: 경쟁사 사례 배제, 특정 강사 선호, 외부 강사 불가", height=80, key="adv_special")
                st.session_state.advanced_context = {
                    "교육대상현황": adv_status,
                    "조직문화": adv_culture,
                    "기대변화": adv_change,
                    "특별요청": adv_special,
                }

        st.divider()

        if not company_name.strip():
            st.warning("⚠️ 사이드바에서 고객사명을 입력해야 제안서를 생성할 수 있습니다.")

        if st.button(
            "🚀 제안서 생성",
            type="primary",
            use_container_width=True,
            disabled=not company_name.strip(),
            key="step3_generate"
        ):
            final_sel_idx = [i for i in range(len(r_mods)) if st.session_state.get(f"mod_sel_{i}", False)]
            st.session_state.selected_modules = final_sel_idx
            sel_details = [r_mods[i] for i in final_sel_idx]
            track = st.session_state.generation_track
            adv_ctx = st.session_state.advanced_context if track == "advanced" else None

            # [로드맵] 모크 프로그레스 바
            _prog = st.progress(0, text="🔍 고객 니즈 확인 중...")
            _prog.progress(15, text="📚 관련 모듈 검색 중...")
            _prog.progress(35, text="✍️ 커리큘럼 조합 중..." + (" (고도화 모드)" if track == "advanced" else ""))

            proposal, timing_result = assemble_curriculum(
                st.session_state.needs_json,
                st.session_state.grouped,
                duration,
                st.session_state.retrieved_modules_json,
                sel_details if sel_details else None,
                track=track,
                advanced_context=adv_ctx,
            )

            _prog.progress(90, text="🔍 품질 검증 중...")
            _prog.progress(100, text="✅ 완료!")
            time.sleep(0.4)
            _prog.empty()

            # HTML 태그 정규화 클렌징
            proposal = re.sub(r'<br\s*/?>', '\n- ', proposal)
            proposal = re.sub(r'<[^>]+>', '', proposal)

            # Placeholder 치환
            proposal, rem_ph = replace_placeholders(proposal, company_name)

            st.session_state.proposal = proposal
            st.session_state.curriculum_timing = timing_result
            st.session_state.remaining_placeholders = rem_ph
            st.session_state.workflow_step = 4
            st.rerun()

    else:
        sel_idx_done = st.session_state.selected_modules
        if not sel_idx_done:
            st.markdown('<div class="step-done">✅ 모듈 선택: AI 자동 선택 모드로 진행</div>', unsafe_allow_html=True)
        else:
            done_names = [
                r_mods[i].get("모듈명", f"모듈 {i+1}")
                for i in sel_idx_done if i < len(r_mods)
            ]
            preview = ", ".join(done_names[:3]) + ("…" if len(done_names) > 3 else "")
            st.markdown(
                f'<div class="step-done">✅ {len(sel_idx_done)}개 모듈 선택됨: {preview}</div>',
                unsafe_allow_html=True
            )

# ─────────────────────────────────────────────────────────
# STEP 4 : 생성된 제안서
# ─────────────────────────────────────────────────────────
if current_step >= 4 and st.session_state.proposal:
    st.divider()
    st.subheader("4️⃣ 생성된 제안서")

    proposal      = st.session_state.proposal
    nj            = st.session_state.needs_json or {}
    timing_result = st.session_state.curriculum_timing
    rem_ph        = st.session_state.remaining_placeholders or []
    keyword       = nj.get("core_keywords", ["제안서"])[0]

    # ── [P2] 클라이언트 제출용 (깔끔한 버전 — 내부 QA 정보 미포함) ──
    st.markdown(proposal)
    st.divider()

    st.markdown("**📥 제출용 버전 다운로드**")
    try:
        docx_bytes = markdown_to_docx(proposal)
        docx_ok = True
    except Exception:
        docx_ok = False

    dl_c1, dl_c2 = st.columns(2)
    with dl_c1:
        if docx_ok:
            st.download_button(
                "📄 Word 다운로드 (.docx)",
                data=docx_bytes,
                file_name=f"제안서_{keyword}_제출용.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                type="primary"
            )
    with dl_c2:
        st.download_button(
            "📥 Markdown (.md)",
            data=proposal,
            file_name=f"제안서_{keyword}.md",
            mime="text/markdown",
            use_container_width=True
        )

    # ── [P2] 내부 QA 버전 (expander — 제출용 파일에는 미포함) ──
    with st.expander("🔧 내부 검토용 (QA 버전)", expanded=False):

        # [P1] Self-Correction 품질 검증 결과 표시
        st.markdown("### 🤖 Self-Correction 품질 검증")
        quality_check = validate_proposal_quality(proposal)
        if quality_check["passed"]:
            st.success(
                f"✅ 품질 기준 통과 | "
                f"모듈 {quality_check['module_count']}개 | "
                f"상호작용: {', '.join(quality_check['interactions_found']) or '없음'}"
            )
        else:
            st.warning(
                "⚠️ 최종 품질 체크 (참고용)\n" +
                "\n".join(f"- {f}" for f in quality_check["failures"])
            )

        st.markdown("### 📊 시간 검증 결과 (참고용)")
        if timing_result:
            tc1, tc2 = st.columns([1, 2])
            with tc1:
                alloc_m = timing_result["total_minutes_allocated"]
                st.metric("배분된 교육 시간 (참고)", f"{alloc_m}분 ({alloc_m/60:.1f}H)")
            with tc2:
                if timing_result["valid"]:
                    st.success(f"✅ 시간 범위 내 | 여유: {timing_result['variance_minutes']}분")
                else:
                    st.info(
                        f"ℹ️ 시간 초과 {abs(timing_result['variance_minutes'])}분 — "
                        "풍부한 내용 제공 목적이므로 컨설턴트가 현장에서 조율하세요."
                    )
            # 시간 초과 경고는 오류가 아닌 참고 정보로 표시
            for w in timing_result.get("warnings", []):
                st.caption(f"📌 참고: {w}")

        st.markdown("### 🔍 Placeholder 검증")
        if rem_ph:
            st.warning(f"⚠️ 미처리 항목: {', '.join(f'[{p}]' for p in rem_ph)}\n수동으로 입력해주세요.")
        else:
            st.success("✅ 모든 placeholder 처리 완료")

        st.markdown("### 📋 모듈 사용 정보")
        sel_cnt = len(st.session_state.selected_modules)
        if sel_cnt:
            st.info(f"사용자 선택 모듈 {sel_cnt}개 우선 포함")
        else:
            st.info("AI 자동 선택 모드")
        for m in st.session_state.retrieved_modules[:5]:
            st.markdown(f"- {m.get('모듈명', '')} ({m.get('similarity_percent', 0)}%)")

        # QA 버전 다운로드
        qa_md = (
            f"# QA 검토 버전\n\n{proposal}\n\n---\n\n## 내부 검토 정보\n"
        )
        if timing_result:
            qa_md += f"\n### 시간 검증\n{timing_result['details']}\n"
        if rem_ph:
            qa_md += f"\n### 미처리 항목\n{', '.join(rem_ph)}\n"
        st.download_button(
            "📥 내부 검토용 다운로드 (.md)",
            data=qa_md,
            file_name=f"제안서_{keyword}_QA버전.md",
            mime="text/markdown",
            use_container_width=True
        )

    # ── STEP 5 : AI 검수 ──
    st.divider()
    st.subheader("5️⃣ AI 검수 (검수자 페르소나)")

    if st.button("🔍 AI 검수 시작", use_container_width=True, key="review_btn"):
        with st.spinner("📋 시니어 HRD 컨설턴트가 제안서를 검토 중..."):
            review = review_proposal(
                st.session_state.improved_proposal or proposal,
                nj
            )
            st.session_state.review = review

    if st.session_state.review:
        review = st.session_state.review
        total  = review.get("총점", 0)

        score_color  = "#16a34a" if total >= 80 else "#d97706" if total >= 60 else "#dc2626"
        verdict      = review.get("제출_가능_여부", "")
        verdict_icon = "✅" if "즉시" in verdict else "⚠️" if "수정" in verdict else "❌"

        col_sc, col_detail = st.columns([1, 2])
        with col_sc:
            st.markdown(f"""
<div style="text-align:center; padding:20px; background:#f9fafb;
            border-radius:12px; border: 2px solid {score_color}">
<div style="font-size:3em; font-weight:bold; color:{score_color}">{total}</div>
<div style="color:#6b7280">/ 100점</div>
</div>""", unsafe_allow_html=True)

        with col_detail:
            scores  = review.get("항목별_점수", {})
            max_map = {"니즈_적합성": 25, "커리큘럼_완성도": 35, "전문성_표현": 25, "제출_가능성": 15}
            for k, v in scores.items():
                ms = max_map.get(k, 25)
                st.markdown(f"**{k}** {v}/{ms}점")
                st.progress(int(v / ms * 100) / 100)

        st.markdown(f"**{verdict_icon} 제출 가능 여부:** {verdict}")
        st.divider()

        col_good, col_bad = st.columns(2)
        with col_good:
            st.markdown("**✅ 잘된 점**")
            for item in review.get("잘된_점", []):
                st.markdown(f"- {item}")
        with col_bad:
            st.markdown("**🔧 개선 필요**")
            for item in review.get("개선_필요", []):
                st.markdown(f"- {item}")

        if review.get("즉시_수정_필요"):
            st.warning("**⚠️ 즉시 수정 필수**\n" +
                       "\n".join(f"- {i}" for i in review["즉시_수정_필요"]))

        st.info(f"**📌 검수자 개선 지시문:**\n{review.get('개선_지시문', '')}")

        # ── STEP 6 : 피드백 반영 재생성 ──
        st.divider()
        st.subheader("6️⃣ 피드백 반영 재생성")

        if st.button(
            "🔄 검수 피드백 반영하여 제안서 개선",
            use_container_width=True,
            disabled=(total >= 90),
            key="improve_btn"
        ):
            with st.spinner("✍️ 검수 피드백을 반영하여 제안서 개선 중..."):
                improved = improve_proposal(
                    proposal,
                    review,
                    nj,
                    st.session_state.grouped,
                    duration
                )
            # [P2] HTML 태그 정규화 클렌징
            improved = re.sub(r'<br\s*/?>', '\n- ', improved)   # <br> → 리스트 항목 형식
            improved = re.sub(r'<[^>]+>', '', improved)          # 나머지 HTML 태그 제거
            improved, _ = replace_placeholders(improved, company_name)
            st.session_state.improved_proposal = improved

        if total >= 90:
            st.caption("✅ 90점 이상 — 즉시 제출 가능한 수준입니다!")

        if st.session_state.improved_proposal:
            st.success("✅ 개선된 제안서가 생성되었습니다.")
            st.markdown(st.session_state.improved_proposal)
            st.divider()

            imp_timing = validate_curriculum_timing(
                st.session_state.improved_proposal,
                DURATION_FRAMEWORK.get(duration, DURATION_FRAMEWORK["8H (1일)"])["total_h"]
            )
            if imp_timing:
                itc1, itc2 = st.columns([1, 2])
                with itc1:
                    im = imp_timing["total_minutes_allocated"]
                    st.metric("개선본 배분 시간", f"{im}분 ({im/60:.1f}H)")
                with itc2:
                    if imp_timing["valid"]:
                        st.success(f"✅ 시간 정합성 OK | 여유: {imp_timing['variance_minutes']}분")
                    else:
                        st.error(f"❌ 시간 초과: {abs(imp_timing['variance_minutes'])}분 과다")

            try:
                imp_docx = markdown_to_docx(st.session_state.improved_proposal)
                st.download_button(
                    "📄 개선본 Word 다운로드 (.docx)",
                    data=imp_docx,
                    file_name=f"제안서_{keyword}_개선본.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                    type="primary"
                )
            except Exception:
                pass

            st.caption("💡 '🔍 AI 검수 시작' 버튼을 다시 누르면 개선본을 재검수합니다.")

st.divider()
st.caption("💡 Powered by Gemini AI + ChromaDB | 티엔에프컨설팅 제안서 202개 기반")
