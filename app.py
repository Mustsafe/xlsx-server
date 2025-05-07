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

# --- 헬스체크 엔드포인트 ---
@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

# 플러그인 매니페스트, OpenAPI, 로고 서빙
@app.route("/.well-known/<path:filename>")
def serve_well_known(filename):
    return send_from_directory(os.path.join(app.root_path, "static", ".well-known"), filename, mimetype="application/json")

@app.route("/openapi.json")
def serve_openapi():
    return send_from_directory(os.path.join(app.root_path, "static"), "openapi.json", mimetype="application/json")

@app.route("/logo.png")
def serve_logo():
    return send_from_directory(os.path.join(app.root_path, "static"), "logo.png", mimetype="image/png")

# --- 네이버 뉴스 크롤링용 자격증명 ---
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# --- 별칭 맵 빌드 ---
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
    # JSA/LOTO 우선
    for tpl in template_list:
        norm = tpl.lower().replace(" ", "").replace("_", "")
        if "jsa" in norm or "작업안전분석" in norm:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in norm:
            alias["__FORCE_LOTO__"] = tpl
    # 공백<->_ 추가
    temp = {k.replace(" ", "_"): v for k,v in alias.items()}
    temp.update({k.replace("_", " "): v for k,v in alias.items()})
    alias.update(temp)
    return alias

# --- 키워드 → 템플릿명 해석 ---
def resolve_keyword(raw_keyword: str, template_list: List[str], alias_map: dict) -> str:
    raw = raw_keyword.strip()
    norm = raw.replace("_", " ").replace("-", " ")
    key_lower = norm.lower()
    cleaned_key = key_lower.replace(" ", "")
    # JSA/LOTO 예외
    if "__FORCE_JSA__" in alias_map and ("jsa" in cleaned_key or "작업안전분석" in cleaned_key):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in cleaned_key:
        return alias_map["__FORCE_LOTO__"]
    # 완전일치
    for tpl in template_list:
        tpl_norm = tpl.lower().replace(" ", "").replace("_", "")
        if key_lower == tpl.lower() or cleaned_key == tpl_norm:
            return tpl
    # 토큰매칭
    tokens = [t for t in key_lower.split() if t]
    cands = [tpl for tpl in template_list if all(tok in tpl.lower() for tok in tokens)]
    if len(cands) == 1:
        return cands[0]
    # 부분문자열
    substr = [tpl for tpl in template_list if cleaned_key in tpl.lower().replace(" ", "").replace("_", "")]
    if len(substr) == 1:
        return substr[0]
    # alias_map
    if raw in alias_map:
        return alias_map[raw]
    if key_lower in alias_map:
        return alias_map[key_lower]
    # fuzzy
    norms = [t.replace(" ", "").replace("_", "").lower() for t in template_list]
    m = difflib.get_close_matches(cleaned_key, norms, n=1, cutoff=0.6)
    if m:
        return template_list[norms.index(m[0])]
    raise ValueError(f"템플릿 ‘{raw_keyword}’을(를) 찾을 수 없습니다.")

# --- 기본 템플릿 생성 (GPT) ---
def ask_gpt_for_default(template_name: str) -> pd.DataFrame:
    system = {
        "role": "system",
        "content": (
            "당신은 산업안전보건 문서 템플릿 전문가입니다."
            " 없는 템플릿명 요청 시, 반드시 아래 형식으로 생성해주세요:\n"
            "- 문서명, 법적 근거, 제출방법 명시\n"
            "- '항목'/'기입 내용' 표 제공"
        )
    }
    user = {"role":"user","content":f"템플릿명: {template_name}"}
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[system, user],
        temperature=0.5, max_tokens=600
    )
    md = resp.choices[0].message.content
    lines = [l for l in md.splitlines() if l.strip().startswith("|")]
    table_md = "\n".join(lines)
    txt = table_md.replace("|", "\t").strip()
    return pd.read_csv(StringIO(txt), sep="\t", engine="python")

# --- 엔드포인트 정의 ---
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "").strip()
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df_all = pd.read_csv(path)
    if "템플릿명" not in df_all.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    tpl_list = sorted(df_all["템플릿명"].dropna().unique())
    alias_map = build_alias_map(tpl_list)

    try:
        tpl = resolve_keyword(raw, tpl_list, alias_map)
        # 고도화된 70종 양식: 해당 템플릿에 맞는 모든 컬럼 전달
        df_filtered = df_all[df_all["템플릿명"] == tpl]
        out_df = df_filtered.drop(columns=["템플릿명"])
    except ValueError:
        # 없는 양식 요청 시, GPT 기본 예시 제공
        out_df = ask_gpt_for_default(raw)

    def gen_xlsx():
        buf = BytesIO()
        out_df.to_excel(buf, index=False)
        buf.seek(0)
        while True:
            chunk = buf.read(8192)
            if not chunk:
                break
            yield chunk

    fname = f"{raw or tpl}.xlsx"
    disp = "attachment; filename*=UTF-8''" + quote(fname)
    headers = {"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               "Content-Disposition":disp,
               "Cache-Control":"public, max-age=3600"}
    return Response(gen_xlsx(), headers=headers)

@app.route("/list_templates", methods=["GET"])
def list_templates():
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(path)
    tpl_list = sorted(df["템플릿명"].dropna().unique())
    return jsonify({"template_list":tpl_list, "alias_keys":sorted(build_alias_map(tpl_list).keys())})

# (이하 /daily_news, /render_news 등 기존 크롤링 엔드포인트 동일)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)))
