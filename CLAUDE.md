# Claude Code 작업 가이드 — proposal-ai

## 작업 규칙
- 모든 코드 수정은 `git commit` 후 `git push`까지 완료할 것
- `step1_extract.py`, `step2_structure.py`, `step4_build_module_db.py`는 로컬 전용 (`.gitignore` 처리됨) — 수정은 가능하나 git 추적 대상이 아님

## 핵심 파일 구조

| 파일/폴더 | 역할 | git 추적 |
|---|---|---|
| `app.py` | Streamlit 앱 메인 (배포용) | ✅ |
| `module_db/` | ChromaDB 벡터 DB (앱 실행 필수) | ✅ |
| `requirements.txt` | 패키지 의존성 | ✅ |
| `step1_extract.py` | raw_data JSON 추출 (로컬 전용) | ❌ |
| `step2_structure.py` | JSON 구조화 (로컬 전용) | ❌ |
| `step4_build_module_db.py` | module_db 구축 스크립트 (로컬 전용) | ❌ |
| `raw_data/` | 원본 JSON 데이터 (로컬 전용) | ❌ |
| `structured_data/` | 전처리된 JSON (로컬 전용) | ❌ |

## 앱 흐름 요약
1. **Step 1** — 고객 정보 입력 (고객사명/산업군/교육대상/교육시간) + 니즈 챗봇
2. **Step 2** — 니즈 분석 결과 확인 (`analyze_needs()` → JSON)
3. **Step 3** — 모듈 검색 (`search_modules_detailed()` 멀티쿼리) + 사용자 선택
4. **Step 4** — 제안서 생성 (`assemble_curriculum()`)
5. **Step 5** — AI 검수 (`review_proposal()`)
6. **Step 6** — 피드백 반영 재생성 (`improve_proposal()`)

---

## [TODO] 임베딩 모델 업그레이드 및 DB 재구축

### 배경 및 이유
현재 `module_db/`는 `gemini-embedding-001` 모델로 구축됨.
더 나은 임베딩 모델로 교체하면 모듈 검색 품질이 향상됨.

> **핵심 제약**: 쿼리 임베딩 모델과 DB 구축 모델이 반드시 동일해야 함.
> 모델을 바꾸면 DB 전체를 새 모델로 재구축해야 하며, 기존 `module_db/`는 폐기.

### 추천 모델 옵션

| 모델명 | 벡터 차원 | 특징 |
|---|---|---|
| `gemini-embedding-001` | 768 | 현재 사용 중 |
| `text-embedding-004` | 768 | 안정적, 한국어 품질 향상, 권장 1순위 |
| `gemini-embedding-exp-03-07` | 3072 | 최고 품질, 실험적, 비용 높음 |

**권장**: `text-embedding-004` — 차원 수 동일(768)하여 ChromaDB 설정 변경 불필요, 성능 향상 확인됨.

### 변경 작업 목록

#### 1. `app.py` 상단 상수 변경
```python
# 현재
EMBED_MODEL_NAME = "gemini-embedding-001"

# 변경 후
EMBED_MODEL_NAME = "text-embedding-004"
```

#### 2. `step4_build_module_db.py` (로컬 파일) 변경
`embed_with_retry()` 함수 내:
```python
# 현재
result = client.models.embed_content(
    model="gemini-embedding-001",
    contents=text
)

# 변경 후
result = client.models.embed_content(
    model="text-embedding-004",
    contents=text
)
```

#### 3. DB 재구축 절차
```bash
# 1. 두 파일의 모델명 변경 후
python step4_build_module_db.py

# 2. 완료 확인 후 module_db/ 커밋 & 푸시
git add module_db/
git commit -m "feat: 임베딩 모델 text-embedding-004로 업그레이드, DB 재구축"
git push
```

> `step4_build_module_db.py`는 로컬 전용이므로 push 대상이 아님.
> `module_db/`만 push하면 됨.

#### 4. 주의사항
- 재구축 전 기존 `module_db/` 백업 권장
- `gemini-embedding-exp-03-07` 선택 시 차원이 3072로 변경 → ChromaDB 컬렉션 생성 시 별도 설정 불필요 (자동 감지)
- 재구축 완료 전까지 앱은 기존 DB로 정상 동작함
