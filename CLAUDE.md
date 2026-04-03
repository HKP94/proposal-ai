# 프로젝트: 제안서 AI 어시스턴트

기업교육(HRD) 제안서 PPT에서 커리큘럼을 추출해 ChromaDB에 저장하고,
Streamlit 챗봇(app.py)으로 새 제안서를 자동 생성하는 파이프라인.

---

## 파이프라인 순서

```
step1_extract.py       PPT 폴더 → raw_data/          (PPT → 슬라이드 텍스트+표 JSON)
step2_structure.py     raw_data/ → structured_data/   (Gemini API로 프로그램/커리큘럼 구조화)
step3_deduplicate.py   structured_data/ → deduped_modules.json  (임베딩 기반 중복 제거)
step4_build_module_db.py  deduped_modules.json → module_db/     (ChromaDB 구축)
app.py                 Streamlit 챗봇 (module_db 기반 RAG)
```

---

## 현재 상태 (2026-04-02 기준)

| 단계 | 상태 |
|------|------|
| step1 | 완료 — raw_data/ 696개 파일 |
| step2 | 완료 — structured_data/ 686개 파일 (3개 커리큘럼 없음, 1개 파싱오류는 정상) |
| step3 | **미완료** — embed_cache.json 필요 (별도 전송), 완료 후 deduped_modules.json 생성됨 |
| step4 | 미실행 — step3 완료 후 실행 |

### step3 재개 방법
1. `embed_cache.json` (117MB) 을 프로젝트 루트에 복사
2. `python step3_deduplicate.py` 실행 → 캐시 자동 이어받아 진행

---

## 핵심 설계 원칙

- **내용 축약 금지**: 학습내용·실습내용은 원문 그대로 JSON/DB에 저장
- **중복 제거**: 코사인 유사도 ≥ 0.92 이면 중복, 더 상세한 버전을 보존 (step3)
- **step4는 step3 결과만 읽음**: structured_data/ 폴더를 직접 읽지 않음

---

## 주요 데이터 구조

### structured_data JSON 형식
```json
{
  "원본파일명": "파일명.pptx",
  "프로그램수": 1,
  "프로그램목록": [
    {
      "고객사": "회사명",
      "과정명": "교육과정명",
      "교육대상": "대상",
      "교육기간": "8H",
      "교육목표": ["목표1", "목표2"],
      "카테고리": ["리더십"],
      "커리큘럼": [
        {
          "모듈명": "모듈명",
          "세부내용": [
            {
              "주제": "주제명",
              "학습내용": ["항목1", "항목2"],
              "실습내용": ["실습1"],
              "시간": "2H",
              "교수방법": ["강의", "실습"]
            }
          ]
        }
      ]
    }
  ]
}
```

### deduped_modules.json 형식 (step3 출력)
```json
{
  "총모듈수": N,
  "원본모듈수": 3459,
  "제거된중복": K,
  "모듈목록": [
    {
      "원본파일": "파일명.json",
      "과정명": "...",
      "고객사": "...",
      "카테고리": [...],
      "교육대상": "...",
      "교육기간": "...",
      "교육목표": [...],
      "모듈": { "모듈명": "...", "세부내용": [...] }
    }
  ]
}
```

---

## API 키 (Gemini)

코드에 하드코딩되어 있음 (step2, step3, step4 모두 동일):
- `GEMINI_API_KEY` 환경변수 우선, 없으면 코드 내 기본값 사용
- 3개 키 순환으로 429 rate limit 대응

## 의존성 설치

```bash
pip install -r requirements.txt
# 또는
pip install google-genai chromadb streamlit python-pptx python-docx numpy
```
