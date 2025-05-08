from flask import Flask, request, jsonify, send_from_directory, Response
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import openai
import difflib
from dateutil import parser
from datetime import datetime, timedelta
from io import BytesIO
from typing import List
from urllib.parse import quote
import json
import logging

# 로거 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

# 환경 변수 로드
openai.api_key = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 데이터 디렉토리
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# 헬스체크
@app.route("/health", methods=["GET"])
def health_check():
    logger.info("Health check endpoint called")
    return "OK", 200

# 플러그인 매니페스트
@app.route("/.well-known/<path:filename>")
def serve_well_known(filename):
    logger.info(f"Serving well-known file: {filename}")
    return send_from_directory(
        os.path.join(app.root_path, "static", ".well-known"),
        filename, mimetype="application/json"
    )

@app.route("/openapi.json")
def serve_openapi():
    logger.info("Serving openapi.json")
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "openapi.json", mimetype="application/json"
    )

@app.route("/logo.png")
def serve_logo():
    logger.info("Serving logo.png")
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.png", mimetype="image/png"
    )

# alias map 생성
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

# 키워드 매핑
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
    substr_cands = [tpl for tpl in template_list if cleaned_key in tpl.lower().replace(" ", "").replace("_", "")]
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
    raise ValueError(f"템플릿 '{raw_keyword}'을(를) 찾을 수 없습니다.")

@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /daily_news, /render_news, /create_xlsx, /list_templates", 200

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    logger.info(f"create_xlsx called with template={raw}")
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        logger.error("통합 CSV 파일이 없습니다.")
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        logger.error("필요한 '템플릿명' 컬럼이 없습니다.")
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500
    template_list = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(template_list)
    try:
        tpl = resolve_keyword(raw, template_list, alias_map)
        logger.info(f"Template matched: {tpl}")
        filtered = df[df["템플릿명"] == tpl]
        out_df = filtered[["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]
    except ValueError as e:
        logger.warning(f"Template resolve failed for {raw}: {e}")
        system_prompt = {
            "role": "system",
            "content": f"""당신은 산업안전 문서 전문가입니다. 요청된 템플릿이 등록되어 있지 않을 때,
다음 JSON 배열 형태로 기본 양식을 생성해 주세요:
[
  {{"작업 항목": "...", "작성 양식": "...", "실무 예시 1": "...", "실무 예시 2": "..."}},
  {{...}}
]
템플릿명: {raw}
""" 
        }
        user_prompt = {
            "role": "user",
            "content": f"템플릿명 '{raw}' 기본 양식 JSON으로 주세요."
        }
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[system_prompt, user_prompt],
            max_tokens=500,
            temperature=0.5,
        )
        data = json.loads(resp.choices[0].message.content)
        out_df = pd.DataFrame(data)

    buffer = BytesIO()
    out_df.to_excel(buffer, index=False)
    buffer.seek(0)
    logger.info(f"Response ready for template={raw}")
    filename = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control": "public, max-age=3600"
    }
    return Response(buffer.read(), headers=headers)

@app.route("/list_templates", methods=["GET"])
def list_templates():
    logger.info("list_templates called")
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        logger.error("통합 CSV 파일이 없습니다.")
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path)
    return jsonify({
        "template_list": sorted(df["템플릿명"].dropna().unique()),
        "alias_keys": sorted(build_alias_map(sorted(df["템플릿명"].dropna().unique())).keys())
    })

# 뉴스 크롤링 및 엔드포인트 (생략)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
