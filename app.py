from flask import Flask, request, send_file
import pandas as pd
import os

app = Flask(__name__)

TEMPLATES = {
    "고소작업대 작업계획서": {
        "columns": ["작업 항목", "작성 양식", "실무 예시"],
        "drop_columns": []
    },
    "밀폐공간작업계획서": {
        "columns": ["작업 항목", "작성 양식", "실무 예시"],
        "drop_columns": []
    },
    # 여기에 추가 등록 가능
}

# 각 템플릿별 출처 문구
SOURCES = {
    "고소작업대 작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제34조를 기반으로 작성되었습니다.",
    "밀폐공간작업계획서": "※ 본 양식은 산업안전보건기준에 관한 규칙 제619~626조 및 밀폐공간 질식재해 예방 가이드를 기반으로 작성되었습니다."
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

    # 필요 없는 열 제거
    drop_cols = TEMPLATES[template_name].get("drop_columns", [])
    df = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")

    # 지정된 컬럼 순서로 정렬
    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in final_cols if col in df.columns]]

    # 출처 문구 삽입 (마지막 행)
    if template_name in SOURCES:
        source_text = SOURCES[template_name]
        df.loc[len(df)] = [source_text] + [""] * (len(df.columns) - 1)

    # 엑셀 파일로 저장
    xlsx_path = f"/mnt/data/{template_name}_최종양식.xlsx"
    df.to_excel(xlsx_path, index=False)

    # 사용자 친화적 파일명 지정
    return send_file(
        xlsx_path,
        as_attachment=True,
        download_name=f"{template_name}.xlsx"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
