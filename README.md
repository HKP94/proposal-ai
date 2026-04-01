# 📋 제안서 AI 어시스턴트 v3.0

> **시니어 컨설턴트의 기획을 돕는 브레인스토밍 파트너 & 초안 작성 비서**
> 202개 기존 제안서 기반 RAG + Gemini API로 HR 교육 제안서 자동 생성

---

## ⚡ 빠른 실행

```bash
# 1. 환경 설정 (최초 1회)
conda create -n proposal-ai python=3.10 -y
conda activate proposal-ai
pip install -r requirements.txt

# 2. 웹앱 실행
cd /Users/kyeongpilheo/Desktop/Python/proposal-ai
streamlit run app.py

# 3. 브라우저 → http://localhost:8501
```

---

## 📁 파일 구조

```
proposal-ai/
├── app.py                    # ✅ 메인 앱 (v3.0)
├── requirements.txt          # 패키지 목록
├── README.md                 # 이 파일
│
├── module_db/                # ✅ 현재 사용 중인 벡터 DB (22MB, 모듈 단위)
├── raw_data/                 # ✅ 202개 원본 추출 JSON (DB 재구축용)
├── structured_data/          # ✅ 202개 구조화 JSON (DB 재구축용)
├── PPT/                      # 원본 PPTX 참고용
│
├── step1_extract.py          # DB 재구축 1단계: PPT → raw_data/
├── step2_structure.py        # DB 재구축 2단계: raw_data/ → structured_data/
├── step4_build_module_db.py  # DB 재구축 3단계: structured_data/ → module_db/
│
├── gemini_prompts.md         # 프롬프트 참고 메모
└── QA_TESTING_GUIDE.md       # QA 체크리스트
```

---

## 🏗️ v3.0 앱 워크플로우 (6단계)

```
Step 1          Step 2         Step 3         Step 4         Step 5       Step 6
니즈 입력   →  니즈 분석   →  모듈 선택  →  제안서 생성  →  AI 검수  →  개선 재생성
(챗봇 대화)    결과 확인      (체크박스)     (Self-Correction)
```

**핵심 특징:**
- **Self-Correction**: 제안서 생성 시 품질 검증 자동 실행 (모듈 3개↑, bullet 3개↑, 상호작용 활동 포함) → 미충족 시 최대 2회 자동 재생성
- **시간 제약 없음**: AI가 시간을 맞추기 위해 내용을 삭제하지 않음. 시간은 `60~90분 (제안)` 형태로 표기
- **상호작용 강제**: 모든 모듈에 `[토의]` / `[실습]` / `[롤플레잉]` / `[워크샵]` / `[사례분석]` 중 최소 1개 필수 포함
- **출력 분리**: 고객 제출용(깔끔) / 내부 QA용(시간검증·placeholder·Self-Correction 결과) 분리

---

## 🔧 기술 스택

| 항목 | 내용 |
|---|---|
| AI 모델 | `gemini-3.1-flash-lite-preview` (생성), `gemini-embedding-001` (임베딩) |
| 벡터 DB | ChromaDB — `module_db/` (모듈 단위, 22MB) |
| UI | Streamlit |
| 문서 변환 | python-docx (마크다운 → .docx) |
| API Key | `REDACTED_API_KEY` |

---

## 🗄️ DB 재구축 방법 (필요 시만 실행)

```bash
# 새 PPT 추가 시 전체 파이프라인 재실행
python step1_extract.py       # PPT → raw_data/ (202개 JSON)
python step2_structure.py     # raw_data/ → structured_data/ (202개 JSON)
python step4_build_module_db.py  # structured_data/ → module_db/ (벡터 DB)
```

> **참고:** `module_db/`가 존재하면 앱은 자동으로 모듈 DB를 사용합니다.

---

## 🐛 문제 해결

| 문제 | 해결 |
|---|---|
| `ModuleNotFoundError` | `conda activate proposal-ai` 후 `pip install -r requirements.txt` |
| 포트 8501 사용 중 | `streamlit run app.py --server.port 8502` |
| API 429 오류 | 자동 재시도 내장 (60초 대기 후 최대 3회). 잠시 후 재실행 |
| module_db 오류 | `ls module_db/` 확인 — `chroma.sqlite3` 파일 존재 여부 확인 |
| 제안서 내용 빈약 | Self-Correction이 자동 재생성하나, 3회 후에도 빈약하면 모듈 선택 수 조정 |
