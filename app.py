from flask import Flask, request, send_file
import pandas as pd
import os

app = Flask(__name__)

TEMPLATES = {
    "고소작업대 작업계획서": {
        "columns": ["작업 항목", "작성 양식", "실무 예시"]
    },
    "밀폐공간작업계획서": {
        "columns": ["작업 항목", "작성 양식", "실무 예시"]
    },
    # 이후 양식은 여기 추가
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

    # 등록된 columns 기준 정제
    expected_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in expected_cols if col in df.columns]]

    # 자동 파일명 지정
    xlsx_path = f"/mnt/data/{template_name}_양식.xlsx"
    df.to_excel(xlsx_path, index=False)

    return send_file(
        xlsx_path,
        as_attachment=True,
        download_name=f"{template_name}_양식.xlsx"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
