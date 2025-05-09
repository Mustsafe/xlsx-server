from flask import Flask, request, jsonify, Response
import pandas as pd
import os
import re
import json
import difflib
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from typing import List
from urllib.parse import quote
from datetime import datetime, timedelta
from dateutil import parser
import openai
import logging

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

# ── 로거 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── 앱 설정 ───────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

openai.api_key      = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# ── 유틸: 입력 및 템플릿명 정규화 ─────────────────────────────────────────────
def sanitize(text: str) -> str:
    """소문자+한글+숫자만 남기고 특수문자 제거"""
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())

# ── alias_map 생성: 다양한 변형까지 모두 키로 등록 ───────────────────────────────
def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = ["점검표","계획서","서식","표","양식"]
    for tpl in template_list:
        low = tpl.lower()
        # 1) 원본 소문자
        alias[low] = tpl
        # 2) 공백<->언더바 변형
        alias[low.replace(" ", "_")] = tpl
        alias[low.replace("_", " ")] = tpl
        # 3) 괄호·특수문자 제거
        key3 = sanitize(low)
        alias[key3] = tpl
        # 4) 접미사 변형: 접미사 제거 후 재조합
        base = re.sub(r"(서식|양식|점검표|계획서|표)$", "", low).strip()
        for suf in SUFFIXES:
            k = base + suf
            alias[k] = tpl
            alias[k.replace(" ", "_")] = tpl
            alias[sanitize(k)] = tpl
    # 5) FORCE JSA/LOTO
    for tpl in template_list:
        s = sanitize(tpl)
        if "jsa" in s or "작업안전분석" in s:
            alias["jsa"] = tpl
            alias["작업안전분석"] = tpl
        if "loto" in s:
            alias["loto"] = tpl
    return alias

# ── 키워드 → 정식 템플릿명 resolve (범용적 매칭 순서) ───────────────────────────
def resolve_keyword(raw: str, templates: List[str], alias_map: dict) -> str:
    # 1) 전처리: 접미어형 동사 및 조사 제거
    r = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘|달라|해주세요|전달)?$",
        "",
        raw,
        flags=re.IGNORECASE
    ).strip().lower()
    cleaned = sanitize(r)

    # 2) alias_map 직접 조회
    if cleaned in alias_map:
        return alias_map[cleaned]

    # 3) FORCE_JSA / FORCE_LOTO (부분 포함)
    if "jsa" in cleaned and "__FORCE_JSA__" in alias_map:
        return alias_map["__FORCE_JSA__"]
    if "loto" in cleaned and "__FORCE_LOTO__" in alias_map:
        return alias_map["__FORCE_LOTO__"]

    # 4) 토큰 매칭: 모든 토큰이 tpl에 포함되는 경우
    tokens = [t for t in r.split() if t]
    tok_cands = [tpl for tpl in templates if all(tok in tpl.lower() for tok in tokens)]
    if len(tok_cands) == 1:
        return tok_cands[0]

    # 5) 접두사 매칭: tpl.normalize().startswith(cleaned)
    prefix_cands = [
        tpl for tpl in templates
        if sanitize(tpl).startswith(cleaned)
    ]
    if len(prefix_cands) == 1:
        return prefix_cands[0]

    # 6) 부분 문자열 매칭: cleaned in sanitize(tpl)
    substr_cands = [
        tpl for tpl in templates
        if cleaned in sanitize(tpl)
    ]
    if len(substr_cands) == 1:
        return substr_cands[0]

    # 7) 퍼지 매칭
    norms = [sanitize(t) for t in templates]
    matches = difflib.get_close_matches(cleaned, norms, n=1, cutoff=0.6)
    if matches:
        return templates[norms.index(matches[0])]

    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

# ── 템플릿 목록 조회 ────────────────────────────────────────────────────────
@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_p = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_p):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_p, encoding="utf-8-sig")
    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(build_alias_map(templates).keys())
    })

# ── 엑셀 생성 엔드포인트 ───────────────────────────────────────────────────────
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    csv_p = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_p):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_p, encoding="utf-8-sig")
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)

    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        logger.info(f"Matched template: {tpl}")
        out_df = df[df["템플릿명"] == tpl][
            ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
        ]
    except ValueError as e:
        logger.warning(str(e))
        # GPT fallback
        system = {
            "role":"system",
            "content":(
                "당신은 산업안전 문서 템플릿 전문가입니다.\n"
                "작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2 컬럼을 가진 JSON 배열을 5개 이상 생성해주세요.\n"
                f"템플릿명: {raw}"
            )
        }
        user = {"role":"user","content":f"템플릿명 '{raw}'의 기본 양식을 JSON 배열로 주세요."}
        resp = openai.chat.completions.create(
            model="gpt-4o-mini", messages=[system,user],
            max_tokens=800, temperature=0.7
        )
        try:
            data = json.loads(resp.choices[0].message.content)
            out_df = pd.DataFrame(data)
        except:
            out_df = pd.DataFrame([{
                "작업 항목": raw,
                "작성 양식": resp.choices[0].message.content.replace("\n"," "),
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    # Excel 생성 & 포맷
    wb = Workbook()
    ws = wb.active
    headers = ["작업 항목","작성 양식","실무 예시 1","실무 예시 2"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for row in out_df.itertuples(index=False):
        ws.append(row)
    for i, col in enumerate(ws.columns,1):
        max_len = max(len(str(c.value)) for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max_len+2,60)
        if headers[i-1]=="작성 양식":
            for c in col[1:]:
                c.alignment = Alignment(wrap_text=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"{tpl}.xlsx" if 'tpl' in locals() else f"{raw}.xlsx"
    disp = "attachment; filename*=UTF-8''"+quote(fname)
    return Response(buf.read(), headers={
        "Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition":disp,
        "Cache-Control":"public, max-age=3600"
    })

# ── 뉴스 크롤링 / 렌더링 로직 (이전 그대로 유지) ────────────────────────────────
def fetch_safetynews_article_content(url): ...
def crawl_naver_news(): ...
def crawl_safetynews(): ...

@app.route("/daily_news", methods=["GET"])
def get_daily_news(): ...
@app.route("/render_news", methods=["GET"])
def render_news(): ...

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
