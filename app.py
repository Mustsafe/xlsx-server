from flask import Flask, request, send_file
import pandas as pd
import os

app = Flask(__name__)

# 템플릿 정의 (공백 없는 파일명 기준)
TEMPLATES = {
    "고소작업대작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "밀폐공간작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "정전작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "해체작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "크레인작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "고온작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "화기작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "전기작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "굴착기작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "용접용단작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "전기작업허가서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "비계작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "협착위험작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "양중작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
    "고압가스작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []},
}

# 출처 정의
SOURCES = {
    "고소작업대작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제34조를 기반으로 작성되었습니다.",
    "밀폐공간작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제619~626조 및 밀폐공간 질식재해 예방 가이드를 기반으로 작성되었습니다.",
    "정전작업허가서": "※ 본 양식은 전기설비 정전 작업 안전지침에 따라 구성되었습니다.",
    "해체작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제526~529조에 따라 구성되었습니다.",
    "크레인작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제99조 및 양중기 안전기준에 따라 구성되었습니다.",
    "고온작업허가서": "※ 본 양식은 고온 환경작업 시 열사병 예방 3대 수칙에 따라 작성되었습니다.",
    "화기작업허가서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제280조(화재·폭발위험작업) 기준을 따릅니다.",
    "전기작업계획서": "※ 본 양식은 전기설비 작업 안전수칙과 절연 보호구 착용 기준을 반영하였습니다.",
    "굴착기작업계획서": "※ 본 양식은 건설기계관리법 및 굴착기 안전 작업 기준을 기반으로 구성되었습니다.",
    "용접용단작업허가서": "※ 본 양식은 용접·용단 작업 시 화재 및 유해가스 위험 예방 기준을 반영하였습니다.",
    "전기작업허가서": "※ 본 양식은 감전 및 아크플래시 예방을 위한 고압 전기작업 허가 절차를 포함합니다.",
    "비계작업계획서": "※ 본 양식은 비계 설치 및 해체 작업의 안전관리지침을 기반으로 작성되었습니다.",
    "협착위험작업계획서": "※ 본 양식은 끼임·협착 위험이 있는 기계 작업 전 사전조치 기준을 따릅니다.",
    "양중작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제99조 및 양중기 작업 안전수칙을 기반으로 합니다.",
    "고압가스작업계획서": "※ 본 양식은 고압가스 안전관리법 시행규칙을 기반으로 작성되었습니다.",
}

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    template_name = request.args.get("template")
    if not template_name or template_name not in TEMPLATES:
        return {"error": "올바른 template 파라미터가 필요합니다."}, 400

    csv_path = f"/mnt/data/{template_name}.csv"
    if not os.path.exists(csv_path):
        return {"error": "CSV 원본 파일이 존재하지 않습니다."}, 404

    df = pd.read_csv(csv_path)

    # 불필요한 열 제거
    drop_cols = TEMPLATES[template_name].get("drop_columns", [])
    df = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")

    # 컬럼 순서 정렬
    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in final_cols if col in df.columns]]

    # 출처 삽입
    if template_name in SOURCES:
        source_text = SOURCES[template_name]
        df.loc[len(df)] = [source_text] + [""] * (len(df.columns) - 1)

    # 엑셀 저장
    xlsx_path = f"/mnt/data/{template_name}_최종양식.xlsx"
    df.to_excel(xlsx_path, index=False)

    return send_file(
        xlsx_path,
        as_attachment=True,
        download_name=f"{template_name}.xlsx"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
