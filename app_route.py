from flask import Flask, request, jsonify
import pandas as pd
import os

app = Flask(__name__)

# 질문-응답 템플릿 CSV 로드
CSV_PATH = "/mnt/data/산안법_질문응답_고정템플릿_20개.csv"
if os.path.exists(CSV_PATH):
    df = pd.read_csv(CSV_PATH)
else:
    df = pd.DataFrame(columns=["질문문장", "조문번호", "조문제목", "응답템플릿"])

@app.route("/route_answer", methods=["GET"])
def route_answer():
    user_q = request.args.get("question", "").strip()
    if not user_q:
        return jsonify({"error": "질문이 비어 있습니다."}), 400

    match = df[df["질문문장"] == user_q]

    if not match.empty:
        return jsonify({
            "answer": match.iloc[0]["응답템플릿"],
            "source": match.iloc[0]["조문번호"]
        })
    else:
        return jsonify({
            "route_to_gpt": True,
            "original_question": user_q
        })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10001)
