from flask import Flask, request, jsonify, send_from_directory, Response
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import openai
import difflib
from dateutil import parser
from datetime import datetime, timedelta
from io import BytesIO, StringIO
from typing import List
from urllib.parse import quote

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

# 환경 변수에서 API 키 불러오기
openai.api_key = os.getenv("OPENAI_API_KEY")

# ./data 디렉토리 사용
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# --- 1. 헬스체크 엔드포인트 ---
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

# 플러그인 매니페스트 서빙
@app.route("/.well-known/<path:filename>")
def serve_well_known(filename):
    return send_from_directory(
        os.path.join(app.root_path, "static", ".well-known"),
        filename,
        mimetype="application/json"
    )

# OpenAPI 및 로고 파일 서빙
@app.route("/openapi.json")
def serve_openapi():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "openapi.json",
        mimetype="application/json"
    )

@app.route("/logo.png")
def serve_logo():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.png",
        mimetype="image/png"
    )

# 네이버 오픈 API 자격증명 (뉴스 크롤링용)
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = [" 점검표", " 계획서", " 서식", " 표", "양식", " 양식", "_양식"]
    for tpl in template_list:
        alias[tpl] = tpl
        alias[tpl.replace("_", " ")] = tpl
        alias[tpl.replace(" ", "_")] = tpl
        low = tpl.lower()
        alias[low] = tpl
        alias[low.replace("_", " ")] = tpl
        base_space = tpl.replace("_", " ")
        nospace = base_space.replace(" ", "").lower()
        alias[nospace] = tpl
        for suf in SUFFIXES:
            combo = base_space + suf
            alias[combo] = tpl
            alias[combo.replace(" ", "_")] = tpl
            alias[combo.lower()] = tpl
    for tpl in template_list:
        norm = tpl.lower().replace(" ", "").replace("_", "")
        if "jsa" in norm or "작업안전분석" in norm:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in norm:
            alias["__FORCE_LOTO__"] = tpl
    temp = {}
    for k, v in alias.items():
        temp[k.replace(" ", "_")] = v
        temp[k.replace("_", " ")] = v
    alias.update(temp)
    return alias

def resolve_keyword(raw_keyword: str, template_list: List[str], alias_map: dict) -> str:
    raw = raw_keyword.strip()
    norm = raw.replace("_", " ").replace("-", " ")
    key_lower = norm.lower()
    cleaned_key = key_lower.replace(" ", "")
    if "__FORCE_JSA__" in alias_map and ("jsa" in cleaned_key or "작업안전분석" in cleaned_key):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in cleaned_key:
        return alias_map["__FORCE_LOTO__"]
    for tpl in template_list:
        tpl_norm = tpl.lower().replace(" ", "").replace("_", "")
        if key_lower == tpl.lower() or cleaned_key == tpl_norm:
            return tpl
    tokens = [t for t in key_lower.split(" ") if t]
    candidates = [tpl for tpl in template_list if all(tok in tpl.lower() for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]
    substr_cands = [
        tpl for tpl in template_list
        if cleaned_key in tpl.lower().replace(" ", "").replace("_", "")
    ]
    if len(substr_cands) == 1:
        return substr_cands[0]
    if raw in alias_map:
        return alias_map[raw]
    if key_lower in alias_map:
        return alias_map[key_lower]
    candidates_norm = [t.replace(" ", "").replace("_", "").lower() for t in template_list]
    matches = difflib.get_close_matches(cleaned_key, candidates_norm, n=1, cutoff=0.6)
    if matches:
        return template_list[candidates_norm.index(matches[0])]
    raise ValueError(f"템플릿 ‘{raw_keyword}’을(를) 찾을 수 없습니다. 정확한 이름을 입력해주세요.")

def ask_gpt_for_default(template_name: str) -> pd.DataFrame:
    """
    고도화 목록에 없는 템플릿일 때, GPT에게 기본 양식을 생성해 달라고 요청하고
    그 결과의 마크다운 테이블을 DataFrame으로 파싱해서 반환합니다.
    """
    prompt_system = {
        "role": "system",
        "content": (
            "당신은 산업안전보건 관련 문서 템플릿 생성 전문가입니다.\n"
            "사용자가 요청한 양식명이 CSV에 없을 때, 아래과 같은 형식으로 자세히 기본 템플릿을 만들어주세요.\n\n"
            "- 문서명: 요청된 제목\n"
            "- 법적 근거: 관련 법령명·조문 번호 및 출처\n"
            "- 제출방법 또는 비고\n\n"
            "그리고 두 칼럼(‘항목’, ‘기입 내용’)으로 구성된 마크다운 표를 출력해주세요."
        )
    }
    prompt_user = {"role": "user", "content": f"템플릿명: {template_name}"}
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[prompt_system, prompt_user],
        temperature=0.5,
        max_tokens=600
    )
    md = resp.choices[0].message.content

    # 마크다운 표만 추출해서 DataFrame으로 변환
    # ```markdown
    # |항목|기입 내용|
    # |---|---|
    # |사업장 명|...|
    # ...
    # ```
    # 간단히 |로 시작하는 줄만 모아서 파싱
    lines = [l for l in md.splitlines() if l.strip().startswith("|")]
    table_md = "\n".join(lines)
    # 판다스가 마크다운 읽기는 지원 안 하므로, 탭 구분으로 변환
    table_txt = table_md.replace("|", "\t").strip()
    df = pd.read_csv(StringIO(table_txt), sep="\t", engine="python")
    return df

@app.route("/", methods=["GET"])
def index():
    return "📰 사용 가능한 엔드포인트: /health, /daily_news, /render_news, /create_xlsx, /list_templates", 200

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404

    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    template_list = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map     = build_alias_map(template_list)

    try:
        # 고도화 목록에서 찾아서
        tpl = resolve_keyword(raw, template_list, alias_map)
        filtered = df[df["템플릿명"] == tpl]
        out_df   = filtered[["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]
    except ValueError:
        # 없으면 GPT에게 기본 양식 생성 요청
        out_df = ask_gpt_for_default(raw)

    # 엑셀 스트림 생성
    def generate_xlsx():
        buf = BytesIO()
        out_df.to_excel(buf, index=False)
        buf.seek(0)
        while True:
            chunk = buf.read(8192)
            if not chunk:
                break
            yield chunk

    filename    = f"{raw or 'default'}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    headers     = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control": "public, max-age=3600"
    }
    return Response(generate_xlsx(), headers=headers)

@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df            = pd.read_csv(csv_path)
    template_list = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map     = build_alias_map(template_list)
    return jsonify({
        "template_list": template_list,
        "alias_keys":    sorted(alias_map.keys())
    })

# 이하 뉴스 크롤링 및 /daily_news, /render_news 엔드포인트는 기존과 동일
# ...

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
