from flask import Flask, request, send_file
import pandas as pd
import os

app = Flask(__name__)

TEMPLATES = {
    "고소작업대 작업계획서": {
        "columns": ["작업명", "작업일시", "작업장소", "고소작업대 종류", "작업인원", "착용 보호구", "위험요인", "안전조치사항", "담당자 서명"],
        "drop_columns": ["분류", "키워드", "비고"]
    },
    # 이후 추가 등록 가능
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
    df.columns = df.columns.str.strip()  # ✅ 컬럼명 앞뒤 공백 제거

    # 필요 없는 열 제거
    drop_cols = TEMPLATES[template_name].get("drop_columns", [])
    df = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")

    # 지정된 컬럼 순서로 정렬
    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in final_cols if col in df.columns]]

    xlsx_path = f"/mnt/data/{template_name}_최종양식.xlsx"
    df.to_excel(xlsx_path, index=False)
    return send_file(xlsx_path, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
