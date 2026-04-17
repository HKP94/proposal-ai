# Claude Code 작업 가이드 — proposal-ai

## 작업 규칙
- 모든 코드 수정은 `git commit` 후 `git push`까지 완료할 것
- `step1_extract.py`, `step2_structure.py`, `step4_build_module_db.py`는 로컬 전용 (`.gitignore` 처리됨) — 수정은 가능하나 git 추적 대상이 아님

## 핵심 파일 구조

| 파일/폴더 | 역할 | git 추적 |
|---|---|---|
| `app.py` | Streamlit 앱 메인 (배포용) | ✅ |
| `module_db/` | ChromaDB 벡터 DB (앱 실행 필수, 2,845개 모듈) | ✅ |
| `requirements.txt` | 패키지 의존성 | ✅ |
| `step1_extract.py` | raw_data JSON 추출 (로컬 전용) | ❌ |
| `step2_structure.py` | JSON 구조화 (로컬 전용) | ❌ |
| `step4_build_module_db.py` | module_db 구축 스크립트 (로컬 전용) | ❌ |
| `module_add/` | 신규 제안서 추가 파이프라인 (로컬 전용) | ❌ |
| `raw_data/` | 원본 JSON 데이터 (로컬 전용) | ❌ |
| `structured_data/` | 전처리된 JSON (로컬 전용) | ❌ |

## 앱 워크플로우 (6단계)

1. **Step 1** — 고객 정보 입력 (산업군/교육대상/교육시간) + 니즈 챗봇 대화
   - `analyze_needs()` 호출 시 대화 내용에서 target/industry를 우선 추론 (UI 기본값 무시)
2. **Step 2** — 니즈 분석 결과 확인 (`needs_json` 구조 확인)
3. **Step 3** — 모듈 검색 (`search_modules_detailed()` 멀티쿼리 + RRF)
   - 상단 "🔄 니즈 다시 고도화하기" 버튼 → Step 1로 복귀 (대화 유지)
   - 검색 결과에 `추천타겟` / `관련산업` 태그 표시 (Gemini 배치 분석)
   - 결과는 `similarity_percent` 내림차순 정렬
4. **Step 4** — A/B 커리큘럼 초안 생성 → 통합 제안서 생성
5. **Step 5** — AI 검수 (`review_proposal()`)
6. **Step 6** — 피드백 반영 재생성 (`improve_proposal()`)

## 주요 함수 위치

| 함수 | 역할 |
|---|---|
| `analyze_needs()` | 자연어 니즈 → JSON 구조화 (target/industry 텍스트 우선 추론) |
| `_generate_search_queries()` | target/industry 포함 검색 쿼리 3개 생성 |
| `search_modules_detailed()` | 멀티쿼리 임베딩 검색 + RRF 정렬 + 맥락 분석 |
| `group_modules_by_type()` | intro/core/apply 그룹핑 |
| `assemble_curriculum_ab()` | A/B 두 방향 커리큘럼 초안 생성 |
| `combine_ab_proposals()` | A/B 통합 최선 제안서 생성 |
| `review_proposal()` | AI 검수 (시니어 HRD 페르소나) |
| `improve_proposal()` | 피드백 반영 재작성 |

## module_db 현황

- **모듈 수**: 2,845개 (기존 2,399 + 2024 신규 454개 - 빈 콘텐츠 8개 제거)
- **임베딩 모델**: `gemini-embedding-001` (실제 차원: 3,072)
- **컬렉션 UUID**: `751bd8a9-2e00-43b9-bd4b-56601fdb19d8`
- **HNSW segment**: `ac52b33d-0b52-487a-9f22-b3e9c4623d19`

> **⚠️ Streamlit Cloud SQLite 캐싱 주의**
> Streamlit Cloud는 앱 실행 중 `chroma.sqlite3`에 쓰기 작업을 하여
> git push 이후에도 구버전 SQLite가 유지될 수 있음.
> module_db를 재구축할 때는 **기존 컬렉션 UUID를 그대로 유지**하며 모듈을 추가해야 함.
> UUID가 바뀌면 배포 서버에서 `Collection does not exist` 오류 발생.

## 신규 제안서 추가 파이프라인 (module_add/)

새 PPT를 추가할 때의 전체 흐름:

```
PPT 파일
  ↓ module_add/step1_extract.py     → module_add/raw_data/
  ↓ module_add/step2_structure.py   → module_add/structured_data/
  ↓ module_add/step3_deduplicate.py → module_add/deduped_modules.json
  ↓ module_add/step4_build_module_db.py → module_add/module_db/ (임시)
```

이후 `deduped_modules.json`을 기반으로 기존 `module_db`에 병합:
- 기존 컬렉션 UUID(751bd8a9) 유지 필수
- `내용_원문` 기반으로 임베딩 재생성 후 추가
- 병합 완료 후 `module_db/` commit & push

## .gitignore 주요 항목

```
raw_data/          # 원본 데이터
structured_data/   # 전처리 데이터
module_add/        # 신규 추가 작업 폴더
*_backup/          # 백업 폴더
PPT/               # 원본 PPTX
step1_extract.py
step2_structure.py
step4_build_module_db.py
```
