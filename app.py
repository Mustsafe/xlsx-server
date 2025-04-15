from flask import Flask, request, send_file
import pandas as pd
import os

app = Flask(__name__)

# ✅ 유사 키워드 → 표준 키워드 전환 맵
KEYWORD_ALIAS = {
    "고소작업 계획서": "고소작업대작업계획서",
    "고소작업대 계획서": "고소작업대작업계획서",
    "밀폐공간 계획서": "밀폐공간작업계획서",
    "굴착기 계획서": "굴착기작업계획서",
    "굴삭기 계획서": "굴착기작업계획서",
    "정전 작업 계획서": "정전작업허가서",
    "화기 작업 계획서": "화기작업허가서",
    "전기 계획서": "전기작업계획서",
    "전기 허가서": "전기작업허가서",
    "용접 계획서": "용접용단작업허가서",
    "해체 작업 계획서": "해체작업계획서",
    "비계 작업 계획서": "비계작업계획서",
    "양중 작업 계획서": "양중작업계획서",
    "협착 계획서": "협착위험작업계획서",
    "고압가스 계획서": "고압가스작업계획서"
}

# ✅ 표준 템플릿 및 출처 정의
TEMPLATES = {
    "고소작업대작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "밀폐공간작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "굴착기작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "정전작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "화기작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "전기작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "전기작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "해체작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "용접용단작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "고온작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "크레인작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "비계작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "양중작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "협착위험작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "고압가스작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []}
}

SOURCES = {
    "고소작업대작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제34조를 기반으로 작성되었습니다.",
    "밀폐공간작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제619~626조 및 밀폐공간 질식재해 예방 가이드를 기반으로 작성되었습니다.",
    "굴착기작업계획서": "※ 본 양식은 굴착기 작업 시 지중매설물 손상, 붕괴 예방 기준에 따라 구성되었습니다.",
    "정전작업허가서": "※ 본 양식은 전기설비 정전 작업 안전지침을 기반으로 작성되었습니다.",
    "화기작업허가서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제280조(화재·폭발위험작업) 기준을 따릅니다.",
    "전기작업계획서": "※ 본 양식은 전기설비 작업 안전수칙과 절연 보호구 착용 기준을 반영하였습니다.",
    "전기작업허가서": "※ 본 양식은 감전 및 아크플래시 예방을 위한 고압 전기작업 허가 절차를 포함합니다.",
    "해체작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제526~529조에 따라 구성되었습니다.",
    "용접용단작업허가서": "※ 본 양식은 용접·용단 작업 시 화재 및 유해가스 위험 예방 기준을 반영하였습니다.",
    "고온작업허가서": "※ 본 양식은 고온 환경작업 시 열사병 예방 3대 수칙에 따라 작성되었습니다.",
    "크레인작업계획서": "※ 본 양식은 산업안전보건기준에 따른 양중기 작업 안전지침을 기반으로 합니다.",
    "비계작업계획서": "※ 본 양식은 비계 설치 및 해체 작업의 안전관리지침을 기반으로 작성되었습니다.",
    "양중작업계획서": "※ 본 양식은 양중작업 사전계획 및 신호수 배치 기준에 따라 구성되었습니다.",
    "협착위험작업계획서": "※ 본 양식은 협착위험 작업 시 위험기계 점검 및 보호덮개 기준을 기반으로 합니다.",
    "고압가스작업계획서": "※ 본 양식은 고압가스 안전관리법 시행규칙을 기반으로 작성되었습니다."
}

# ✅ 키워드 정제
def resolve_keyword(raw_keyword: str) -> str:
    for alias, standard in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return standard
    return raw_keyword  # 못 찾으면 원본 그대로

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw_template = request.args.get("template", "")
    template_name = resolve_keyword(raw_template)

    if not template_name or template_name not in TEMPLATES:
        return {"error": f"'{raw_template}'에 해당하는 작업계획서 양식을 찾을 수 없습니다."}, 400

    csv_path = f"/mnt/data/{template_name}.csv"
    if not os.path.exists(csv_path):
        return {"error": f"CSV 원본 파일이 존재하지 않습니다: {template_name}.csv"}, 404

    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        return {"error": f"CSV 읽기 실패: {str(e)}"}, 500

    drop_cols = TEMPLATES[template_name].get("drop_columns", [])
    df = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")

    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in final_cols if col in df.columns]]

    if template_name in SOURCES:
        source_text = SOURCES[template_name]
        df.loc[len(df)] = [source_text] + [""] * (len(df.columns) - 1)

    xlsx_path = f"/mnt/data/{template_name}_최종양식.xlsx"
    df.to_excel(xlsx_path, index=False)

    return send_file(
        xlsx_path,
        as_attachment=True,
        download_name=f"{template_name}.xlsx"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
