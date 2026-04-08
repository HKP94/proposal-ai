# 📋 제안서 AI 어시스턴트

> **시니어 컨설턴트의 기획을 돕는 브레인스토밍 파트너 & 초안 작성 비서**
> 2,474개 교육 모듈 DB 기반 RAG + Gemini API로 HR 교육 제안서 자동 생성

---

## ⚡ 빠른 실행

```bash
# 1. 환경 설정 (최초 1회)
pip install -r requirements.txt

# 2. 웹앱 실행
streamlit run app.py

# 3. 브라우저 → http://localhost:8501
```

---

## 📁 파일 구조

```
proposal-ai/
├── app.py                    # 메인 앱
├── requirements.txt          # 패키지 목록
├── README.md
├── CLAUDE.md                 # Claude Code 작업 가이드
│
├── module_db/                # ChromaDB 벡터 DB (2,474개 모듈)
│
├── module_add/               # 신규 제안서 추가 파이프라인 (로컬 전용)
│   ├── step1_extract.py
│   ├── step2_structure.py
│   ├── step3_deduplicate.py
│   ├── step4_build_module_db.py
│   ├── raw_data/
│   ├── structured_data/
│   └── deduped_modules.json
│
└── QA_TESTING_GUIDE.md       # QA 체크리스트
```

---

## 🏗️ 앱 워크플로우 (6단계)

```
Step 1       Step 2        Step 3         Step 4          Step 5      Step 6
니즈 입력  →  니즈 분석  →  모듈 검색   →  제안서 생성  →  AI 검수  →  개선 재생성
(챗봇 대화)   결과 확인    (체크박스 선택)  (A/B 초안 → 통합)
                          ↑
                     니즈 재고도화 버튼
```

**핵심 특징:**
- **맥락 기반 검색**: 산업군·직급·키워드를 모두 반영한 멀티쿼리 + RRF 정렬
- **맥락 태그**: 검색된 모듈마다 추천 타겟(👤) / 관련 산업(🏢) 태그 자동 표시
- **니즈 재고도화**: Step 3에서 이전 대화를 유지한 채 Step 1로 돌아가 추가 대화 가능
- **A/B 초안**: AI가 두 가지 방향의 커리큘럼 초안을 제시하고 통합 제안서 생성
- **AI 검수**: 시니어 HRD 컨설턴트 페르소나로 제안서 품질 점수 및 개선 지시 제공
- **출력 분리**: 고객 제출용(.docx) / 내부 QA용(.md) 분리 다운로드

---

## 🔧 기술 스택

| 항목 | 내용 |
|---|---|
| AI 모델 | `gemini-3.1-flash-lite-preview` (생성), `gemini-embedding-001` (임베딩) |
| 벡터 DB | ChromaDB — `module_db/` (2,474개 모듈) |
| UI | Streamlit |
| 문서 변환 | python-docx (마크다운 → .docx) |

---

## 🗄️ 신규 제안서 추가 방법

```bash
# module_add/ 폴더에서 순서대로 실행
cd module_add/
python step1_extract.py       # PPT → raw_data/
python step2_structure.py     # raw_data/ → structured_data/
python step3_deduplicate.py   # 중복 제거 → deduped_modules.json
# step4는 임시 DB 구축용 — 실제 병합은 별도 스크립트 사용
```

> 병합 후 `module_db/` commit & push 필요. UUID 유지 필수 (CLAUDE.md 참고).

---

## 🐛 문제 해결

| 문제 | 해결 |
|---|---|
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| API 429 오류 | 자동 재시도 내장 (최대 3회). 잠시 후 재실행 |
| `Collection does not exist` | module_db UUID 불일치 — CLAUDE.md의 Streamlit Cloud 주의사항 참고 |
| 포트 8501 사용 중 | `streamlit run app.py --server.port 8502` |
