from flask import Flask, request, jsonify, send_from_directory, Response
import pandas as pd
import os
import re
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

# ── 로거 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── Flask & 환경변수 ───────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
openai.api_key      = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# ── 데이터 디렉토리 ───────────────────────────────────────────────────────────
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# ── 엑셀 생성용 import ─────────────────────────────────────────────────────────
from openpyxl import Workbook
from openpyxl.styles import Font

# ── app (1).py에서 백업해온 매핑 로직 ──────────────────────────────────────────
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

    # 1) JSA/LOTO 우선 매핑
    if "__FORCE_JSA__" in alias_map and ("jsa" in cleaned_key or "작업안전분석" in cleaned_key):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in cleaned_key:
        return alias_map["__FORCE_LOTO__"]

    # 2) 완전 일치
    for tpl in template_list:
        tpl_norm = tpl.lower().replace(" ", "").replace("_", "")
        if key_lower == tpl.lower() or cleaned_key == tpl_norm:
            return tpl

    # 3) 토큰 기반 매칭
    tokens = [t for t in key_lower.split(" ") if t]
    candidates = [tpl for tpl in template_list if all(tok in tpl.lower() for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]

    # 4) 부분 문자열 매칭
    substr_cands = [
        tpl for tpl in template_list
        if cleaned_key in tpl.lower().replace(" ", "").replace("_", "")
    ]
    if len(substr_cands) == 1:
        return substr_cands[0]

    # 5) **새로 추가된** prefix 매칭
    prefix_cands = [
        tpl for tpl in template_list
        if tpl.lower().replace(" ", "").replace("_", "").startswith(cleaned_key)
    ]
    if len(prefix_cands) == 1:
        return prefix_cands[0]

    # 6) alias_map 직접 조회
    if raw in alias_map:
        return alias_map[raw]
    if key_lower in alias_map:
        return alias_map[key_lower]

    # 7) 퍼지 매칭
    candidates_norm = [
        t.replace(" ", "").replace("_", "").lower()
        for t in template_list
    ]
    matches = difflib.get_close_matches(cleaned_key, candidates_norm, n=1, cutoff=0.6)
    if matches:
        return template_list[candidates_norm.index(matches[0])]

    raise ValueError(f"템플릿 '{raw_keyword}'을(를) 찾을 수 없습니다. 정확한 이름을 입력해주세요.")
# ────────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /list_templates, /create_xlsx, /daily_news, /render_news", 200

@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(alias_map.keys())
    })

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "").strip()
    # “양식/서식/점검표/계획서/표 + (을|를)? + (주세요|줘|달라|해주세요)?$” 제거
    raw = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘|달라|해주세요)?$",
        "",
        raw,
        flags=re.IGNORECASE
    ).strip()

    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)

    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        logger.info(f"Matched template: {tpl}")
        out_df = df[df["템플릿명"] == tpl][[
            "작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"
        ]]
    except ValueError as e:
        logger.warning(f"Template resolve failed for '{raw}': {e}")
        out_df = pd.DataFrame([{
            "작업 항목": raw,
            "작성 양식": "[여기에 양식 항목을 입력하세요]",
            "실무 예시 1": "",
            "실무 예시 2": ""
        }])

    wb = Workbook()
    ws = wb.active
    headers = ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in out_df.itertuples(index=False):
        ws.append(row)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{tpl}.xlsx" if 'tpl' in locals() else f"{raw}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    resp_headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control": "public, max-age=3600"
    }
    return Response(buf.read(), headers=resp_headers)

# ── 이하 뉴스 크롤링 / render_news 로직은 원본 그대로 유지 ──────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
