from flask import Flask, request, send_file, jsonify
import pandas as pd
import os

app = Flask(__name__)

# ✅ 유사 키워드 → 표준 키워드 전환
KEYWORD_ALIAS = {
    "고소작업 계획서": "고소작업대작업계획서", "고소 작업 계획서": "고소작업대작업계획서",
    "고소작업대 계획서": "고소작업대작업계획서", "고소작업": "고소작업대작업계획서",
    "밀폐공간 계획서": "밀폐공간작업계획서", "밀폐공간 작업 계획서": "밀폐공간작업계획서",
    "밀폐공간작업 계획서": "밀폐공간작업계획서", "밀폐공간": "밀폐공간작업계획서",
    "정전 작업 허가서": "정전작업허가서", "정전작업": "정전작업허가서",
    "해체 작업계획서": "해체작업계획서", "해체 계획서": "해체작업계획서",
    "구조물 해체 계획": "해체작업계획서", "해체작업": "해체작업계획서",
    "크레인 계획서": "크레인작업계획서", "크레인 작업 계획서": "크레인작업계획서",
    "양중기 작업계획서": "크레인작업계획서",
    "고온 작업 허가서": "고온작업허가서", "고온작업": "고온작업허가서",
    "화기작업 허가서": "화기작업허가서", "화기 작업계획서": "화기작업허가서", "화기작업": "화기작업허가서",
    "전기 작업계획서": "전기작업계획서", "전기 계획서": "전기작업계획서", "전기작업": "전기작업계획서",
    "굴착기 작업계획서": "굴착기작업계획서", "굴착기 계획서": "굴착기작업계획서", "굴삭기 작업계획서": "굴착기작업계획서",
    "용접작업 계획서": "용접용단작업허가서", "용접용단 계획서": "용접용단작업허가서", "용접작업": "용접용단작업허가서",
    "전기 작업 허가서": "전기작업허가서", "고압 전기작업 계획서": "전기작업허가서", "전기 허가서": "전기작업허가서",
    "비계 작업 계획서": "비계작업계획서", "비계 계획서": "비계작업계획서", "비계작업계획": "비계작업계획서",
    "협착 작업 계획서": "협착위험작업계획서", "협착 계획서": "협착위험작업계획서",
    "양중 작업 계획서": "양중작업계획서", "양중기 작업계획서": "양중작업계획서",
    "고압가스 작업 계획서": "고압가스작업계획서", "고압가스 계획서": "고압가스작업계획서"
}

# ✅ 템플릿 정의
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
    "고압가스작업계획서": {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []}
}

# ✅ 직무교육 링크 사전 (원형 URL 그대로)
LINKS = {
    "신규_안전관리자": "https://www.dutycenter.net/dutyedu/jfrt2200e?qryCourseDivCd=10&qryEduDiv=20&qryOrgCd=185E7D02A4CSTWOHBFPV",
    "신규_보건관리자": "https://www.dutycenter.net/dutyedu/jfrt2200e?qryCourseDivCd=20&qryEduDiv=20&qryOrgCd=185E7D02A4CSTWOHBFPV",
    "신규_책임자": "https://www.dutycenter.net/dutyedu/jfrt2200e?qryCourseDivCd=30&qryEduDiv=20&qryOrgCd=185E7D02A4CSTWOHBFPV",
    "보수_안전관리자": "https://www.dutycenter.net/dutyedu/jfrt2200e?qryCourseDivCd=10&qryEduDiv=30&qryOrgCd=185E7D02A4CSTWOHBFPV",
    "보수_보건관리자": "https://www.dutycenter.net/dutyedu/jfrt2200e?qryCourseDivCd=20&qryEduDiv=30&qryOrgCd=185E7D02A4CSTWOHBFPV",
    "관리감독자": "https://forms.gle/yAfFMBTTxJu2WNmr5"
}

@app.route("/get_training_link", methods=["GET"])
def get_training_link():
    code = request.args.get("code", "")
    url = LINKS.get(code)
    if not url:
        return jsonify({"error": f"'{code}'(으)로 등록된 링크가 없습니다."}), 404
    return jsonify({"url": url})

# ✅ NEW: GPT 응답 내 #링크_코드 → 실링크로 치환
@app.route("/replace_links", methods=["POST"])
def replace_links():
    data = request.json
    content = data.get("content", "")
    for code, url in LINKS.items():
        content = content.replace(f"#링크_{code}", url)
    return jsonify({"result": content})

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw_template = request.args.get("template", "")
    template_name = resolve_keyword(raw_template)

    if not template_name or template_name not in TEMPLATES:
        return {"error": f"'{raw_template}'(으)로는 양식을 찾을 수 없습니다."}, 400

    csv_path = f"/mnt/data/{template_name}.csv"
    if not os.path.exists(csv_path):
        return {"error": "CSV 원본 파일이 존재하지 않습니다."}, 404

    df = pd.read_csv(csv_path)
    drop_cols = TEMPLATES[template_name].get("drop_columns", [])
    df = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")
    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in final_cols if col in df.columns]]
