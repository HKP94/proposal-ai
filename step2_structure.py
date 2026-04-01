"""
2단계: raw JSON → 표준 JSON 구조화 스크립트
- raw_data 폴더의 JSON 파일들을 읽어서
- 커리큘럼 표, 기본정보, 교육목표를 자동 탐지하여
- structured_data 폴더에 표준 JSON으로 저장
"""

import os
import json
import re

# Get paths relative to script location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_FOLDER = os.path.join(SCRIPT_DIR, "raw_data")
OUTPUT_FOLDER = os.path.join(SCRIPT_DIR, "structured_data")

os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def is_curriculum_table(table):
    """커리큘럼 표 여부 판단 (Module/Contents/Activity/Time 헤더 포함)"""
    for row in table[:2]:
        row_text = " ".join(row).lower()
        if "module" in row_text and ("contents" in row_text or "time" in row_text):
            return True
    return False


def is_basic_info_table(table):
    """기본정보 표 여부 판단 (프로젝트명/교육대상/기간 포함)"""
    for row in table:
        row_text = " ".join(row)
        if any(k in row_text for k in ["프로젝트 명", "과정명", "교육 대상", "교육대상", "프로젝트 기간", "교육 기간"]):
            return True
    return False


def extract_basic_info(table):
    """기본정보 표에서 값 추출"""
    info = {}
    for row in table:
        if len(row) < 2:
            continue
        key = row[0].strip()
        value = row[1].strip() if row[1].strip() else (row[2].strip() if len(row) > 2 else "")
        if "프로젝트 명" in key or "과정명" in key:
            info["과정명"] = value
        elif "교육 대상" in key or "교육대상" in key:
            info["교육대상"] = value
        elif "기간" in key:
            info["교육기간"] = value
        elif "인원" in key:
            info["교육인원"] = value
        elif "장소" in key:
            info["교육장소"] = value
    return info


def extract_curriculum(table):
    """커리큘럼 표에서 모듈 정보 추출 (전문형 - 전체 내용 보존)"""
    modules = []
    current_module = None
    current_topic = None

    for row in table[1:]:  # 헤더 행 스킵
        if not any(cell.strip() for cell in row):
            continue

        module_cell = row[0].strip() if len(row) > 0 else ""
        topic_cell = row[1].strip() if len(row) > 1 else ""
        content_cell = row[2].strip() if len(row) > 2 else ""
        activity_cell = row[3].strip() if len(row) > 3 else ""
        time_cell = row[4].strip() if len(row) > 4 else ""

        # 새 모듈 시작
        if module_cell and module_cell not in ["Module", ""]:
            if current_module:
                modules.append(current_module)
            current_module = {
                "모듈명": module_cell,
                "세부내용": []
            }
            current_topic = None

        if not current_module:
            continue

        # 새 주제 시작
        if topic_cell and topic_cell != "상황 예시":
            current_topic = {
                "주제": topic_cell,
                "내용": content_cell,
                "교수방법": [m.strip() for m in activity_cell.split("\n") if m.strip()],
                "시간": time_cell
            }
            current_module["세부내용"].append(current_topic)
        elif content_cell and current_topic:
            # 같은 주제의 추가 내용
            current_topic["내용"] += "\n" + content_cell
            if time_cell and not current_topic["시간"]:
                current_topic["시간"] = time_cell

    if current_module:
        modules.append(current_module)

    return modules


def extract_learning_objectives(slides):
    """교육목표 추출"""
    objectives = []
    for slide in slides:
        for table in slide.get("표", []):
            for row in table:
                if row and "교육목표" in row[0]:
                    obj = row[1].strip() if len(row) > 1 else ""
                    if obj:
                        objectives.append(obj)
    return objectives


def infer_industry(filename, texts):
    """고객사명/텍스트에서 산업군 추론"""
    all_text = filename + " " + " ".join(texts)
    if any(k in all_text for k in ["은행", "금융", "보험", "증권", "카드"]):
        return "금융"
    elif any(k in all_text for k in ["병원", "의료", "제약", "헬스"]):
        return "의료/제약"
    elif any(k in all_text for k in ["롯데", "신세계", "현대백화점", "유통", "리테일"]):
        return "유통"
    elif any(k in all_text for k in ["제조", "자동차", "반도체", "전자", "화학"]):
        return "제조"
    elif any(k in all_text for k in ["건설", "부동산", "건축"]):
        return "건설"
    elif any(k in all_text for k in ["공기업", "공공", "공단", "정부"]):
        return "공공"
    elif any(k in all_text for k in ["IT", "소프트웨어", "플랫폼", "게임"]):
        return "IT"
    elif any(k in all_text for k in ["대학", "교육기관", "학교"]):
        return "교육"
    else:
        return "전산업"


def infer_categories(filename, texts):
    """과정명/내용에서 카테고리 추론"""
    all_text = filename + " " + " ".join(texts[:5])
    categories = []
    if any(k in all_text for k in ["리더십", "리더", "코칭", "팀장"]):
        categories.append("리더십")
    if any(k in all_text for k in ["성과", "KPI", "목표", "평가"]):
        categories.append("성과관리")
    if any(k in all_text for k in ["소통", "커뮤니케이션", "협업", "갈등"]):
        categories.append("커뮤니케이션")
    if any(k in all_text for k in ["문제해결", "사고", "혁신", "창의"]):
        categories.append("문제해결/혁신")
    if any(k in all_text for k in ["조직", "문화", "변화관리", "몰입"]):
        categories.append("조직문화")
    if any(k in all_text for k in ["신입", "온보딩", "입문"]):
        categories.append("신입/온보딩")
    if any(k in all_text for k in ["워크샵", "팀빌딩"]):
        categories.append("워크샵")
    if any(k in all_text for k in ["영업", "마케팅", "세일즈"]):
        categories.append("영업/마케팅")
    if any(k in all_text for k in ["강사", "HRD", "퍼실리테이터"]):
        categories.append("HRD역량")
    return categories if categories else ["기타"]


def infer_core_competencies(filename, texts):
    """핵심역량 추출"""
    all_text = filename + " " + " ".join(texts[:5])
    competencies = []
    keywords = [
        "KPI", "목표수립", "피드백", "코칭", "리더십", "커뮤니케이션",
        "문제해결", "혁신", "변화관리", "조직활성화", "협업", "갈등관리",
        "성과관리", "전략적사고", "동기부여", "팀빌딩", "보고", "발표"
    ]
    for kw in keywords:
        if kw.lower() in all_text.lower():
            competencies.append(kw)
    return competencies[:5] if competencies else []


def extract_course_name(slides, filename):
    """과정명 추출 - 슬라이드 1에서 주로 발견"""
    # 슬라이드 1 텍스트에서 추출 시도
    for slide in slides[:3]:
        for text in slide.get("텍스트", []):
            if "워크샵" in text or "과정" in text or "프로그램" in text:
                # 너무 긴 텍스트는 제외
                if len(text) < 50:
                    return text
    # 파일명에서 추출
    name = os.path.splitext(filename)[0]
    name = re.sub(r'^교육_[\d\-]+[a-z]?_', '', name)
    name = name.replace("_표준제안서", "").replace("★", "").replace("  ", " ").strip()
    return name


def extract_proposal_date(slides):
    """제안일 추출 시도"""
    for slide in slides:
        for text in slide.get("텍스트", []):
            match = re.search(r'20\d{2}[-.]?\d{2}[-.]?\d{2}', text)
            if match:
                return match.group()
    return ""


def structure_proposal(raw_data, filename):
    """raw JSON → 표준 JSON 변환"""
    slides = raw_data.get("슬라이드", [])
    all_texts = [t for s in slides for t in s.get("텍스트", [])]

    # 기본정보 추출
    basic_info = {}
    curriculum_list = []
    learning_objectives = extract_learning_objectives(slides)

    for slide in slides:
        for table in slide.get("표", []):
            if is_basic_info_table(table):
                info = extract_basic_info(table)
                basic_info.update(info)
            if is_curriculum_table(table):
                modules = extract_curriculum(table)
                curriculum_list.extend(modules)

    course_name = basic_info.get("과정명") or extract_course_name(slides, filename)

    return {
        "기본정보": {
            "고객사": basic_info.get("고객사", "표준제안서"),
            "과정명": course_name,
            "제안일": extract_proposal_date(slides),
            "교육대상": basic_info.get("교육대상", ""),
            "교육인원": basic_info.get("교육인원", ""),
            "교육기간": basic_info.get("교육기간", "")
        },
        "과정분류": {
            "카테고리": infer_categories(filename, all_texts),
            "핵심역량": infer_core_competencies(filename, all_texts),
            "산업군": infer_industry(filename, all_texts)
        },
        "교육목표": learning_objectives,
        "커리큘럼": curriculum_list,
        "원본파일명": filename
    }


def run():
    raw_files = [f for f in os.listdir(RAW_FOLDER) if f.endswith(".json")]
    raw_files.sort()

    print(f"📂 총 {len(raw_files)}개 파일 처리 시작\n")
    success, fail = 0, 0

    for i, filename in enumerate(raw_files, 1):
        raw_path = os.path.join(RAW_FOLDER, filename)
        output_path = os.path.join(OUTPUT_FOLDER, filename)

        if os.path.exists(output_path):
            print(f"  ⏭️  [{i}/{len(raw_files)}] 스킵: {filename}")
            success += 1
            continue

        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            original_filename = raw_data.get("원본파일명", filename)
            structured = structure_proposal(raw_data, original_filename)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(structured, f, ensure_ascii=False, indent=2)

            print(f"  ✅ [{i}/{len(raw_files)}] 완료: {original_filename}")
            print(f"       📚 커리큘럼 모듈: {len(structured['커리큘럼'])}개 | 카테고리: {structured['과정분류']['카테고리']}")
            success += 1

        except Exception as e:
            print(f"  ❌ [{i}/{len(raw_files)}] 오류: {filename} → {e}")
            fail += 1

    print(f"\n{'='*50}")
    print(f"완료: {success}개 성공 / {fail}개 실패")
    print(f"저장 위치: {OUTPUT_FOLDER}")


if __name__ == "__main__":
    run()
