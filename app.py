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
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 앱 설정 ───────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

openai.api_key      = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# ── 유틸리티: 소문자+한글+숫자만 남기기 ────────────────────────────────────────
def sanitize(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())

# ── alias_map 생성 ──────────────────────────────────────────────────────────────
def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = ["점검표","계획서","서식","표","양식"]
    for tpl in template_list:
        low = tpl.lower()
        # 1) 원본 소문자
        alias[low] = tpl
        # 2) 공백<->언더바
        alias[low.replace(" ", "_")] = tpl
        alias[low.replace("_", " ")] = tpl
        # 3) 특수문자 제거
        key3 = sanitize(low)
        alias[key3] = tpl
        # 4) 접미사 변형
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

# ── 키워드 → 템플릿 resolve (최다 사용 빈도 우선) ─────────────────────────────
def resolve_keyword(raw: str, templates: List[str], alias_map: dict, freq: dict) -> str:
    # 1) 접미사형 동사 제거
    r = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘|달라|해주세요|전달)?$",
        "",
        raw.strip(),
        flags=re.IGNORECASE
    ).lower()
    cleaned = sanitize(r)

    # helper to pick highest frequency
    def pick_max(cands):
        return max(cands, key=lambda t: freq.get(t, 0))

    # 2) alias_map 직접 조회
    if cleaned in alias_map:
        return alias_map[cleaned]

    # 3) FORCE JSA/LOTO
    if "jsa" in cleaned and "jsa" in alias_map:
        return alias_map["jsa"]
    if "loto" in cleaned and "loto" in alias_map:
        return alias_map["loto"]

    # 4) 토큰 매칭
    tokens = [t for t in r.split() if t]
    tok_cands = [tpl for tpl in templates if all(tok in tpl.lower() for tok in tokens)]
    if tok_cands:
        return pick_max(tok_cands)

    # 5) 접두사 매칭
    prefix_cands = [tpl for tpl in templates if sanitize(tpl).startswith(cleaned)]
    if prefix_cands:
        return pick_max(prefix_cands)

    # 6) 부분문자열 매칭
    substr_cands = [tpl for tpl in templates if cleaned in sanitize(tpl)]
    if substr_cands:
        return pick_max(substr_cands)

    # 7) 퍼지 매칭
    norms = [sanitize(t) for t in templates]
    matches = difflib.get_close_matches(cleaned, norms, n=3, cutoff=0.6)
    if matches:
        cands = [templates[norms.index(m)] for m in matches]
        return pick_max(cands)

    # 8) 매칭 실패
    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

# ── 템플릿 리스트 조회 ───────────────────────────────────────────────────────
@app.route("/list_templates", methods=["GET"])
def list_templates():
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(path, encoding="utf-8-sig")
    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(alias_map.keys())
    })

# ── 엑셀 생성 ─────────────────────────────────────────────────────────────────
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404

    df = pd.read_csv(path, encoding="utf-8-sig")
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)
    freq = df["템플릿명"].value_counts().to_dict()

    try:
        tpl = resolve_keyword(raw, templates, alias_map, freq)
        logger.info(f"Matched template: {tpl}")
        out_df = df[df["템플릿명"] == tpl][
            ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
        ]
    except ValueError as e:
        logger.warning(str(e))
        # fallback: GPT에게 JSON 요청
        system = {
            "role": "system",
            "content": (
                "당신은 산업안전 문서 템플릿 전문가입니다.\n"
                "다음 컬럼(작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2)을 가진 JSON 배열을 5개 이상 생성해주세요.\n"
                f"템플릿명: {raw}"
            )
        }
        user = {"role": "user", "content": f"템플릿명 '{raw}'의 기본 양식을 JSON 배열로 주세요."}
        resp = openai.chat.completions.create(
            model="gpt-4o-mini", messages=[system, user],
            max_tokens=800, temperature=0.7
        )
        try:
            data = json.loads(resp.choices[0].message.content)
            out_df = pd.DataFrame(data)
        except:
            out_df = pd.DataFrame([{
                "작업 항목": raw,
                "작성 양식": resp.choices[0].message.content.replace("\n", " "),
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    # 엑셀 생성 & 포맷
    wb = Workbook()
    ws = wb.active
    headers = ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for row in out_df.itertuples(index=False):
        ws.append(row)
    for i, col in enumerate(ws.columns, 1):
        mx = max(len(str(c.value)) for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(mx + 2, 60)
        if headers[i-1] == "작성 양식":
            for c in col[1:]:
                c.alignment = Alignment(wrap_text=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    disp = quote(f"{tpl}.xlsx" if 'tpl' in locals() else f"{raw}.xlsx")
    return Response(
        buf.read(),
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f"attachment; filename*=UTF-8''{disp}",
            "Cache-Control": "public, max-age=3600"
        }
    )

# ── 뉴스 크롤링 & 렌더링 로직 ─────────────────────────────────────────────────
def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else ""
    except:
        return ""

def crawl_naver_news():
    base = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        r = requests.get(base, headers=headers,
                         params={"query": kw, "display": 2, "sort": "date"},
                         timeout=10)
        if r.status_code != 200:
            continue
        for item in r.json().get("items", []):
            title = BeautifulSoup(item["title"], "html.parser").get_text()
            desc  = BeautifulSoup(item["description"], "html.parser").get_text()
            out.append({
                "출처": item.get("originallink", "네이버"),
                "제목": title,
                "링크": item.get("link", ""),
                "날짜": item.get("pubDate", ""),
                "본문": desc
            })
    return out

def crawl_safetynews():
    base = "https://www.safetynews.co.kr"
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        r = requests.get(f"{base}/search/news?searchword={kw}",
                         headers={"User-Agent": "Mozilla/5.0"},
                         timeout=10)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select(".article-list-content")[:2]:
            t = item.select_one(".list-titles")
            href = base + t["href"] if t and t.get("href") else ""
            d    = item.select_one(".list-dated")
            content = fetch_safetynews_article_content(href) if href else ""
            out.append({
                "출처": "안전신문",
                "제목": t.get_text(strip=True) if t else "",
                "링크": href,
                "날짜": d.get_text(strip=True) if d else "",
                "본문": content[:1000]
            })
    return out

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news = crawl_naver_news() + crawl_safetynews()
    if not news:
        return jsonify(error="가져올 뉴스가 없습니다."), 200
    return jsonify(news)

@app.route("/render_news", methods=["GET"])
def render_news():
    news = crawl_naver_news() + crawl_safetynews()
    cutoff = datetime.utcnow() - timedelta(days=3)
    filtered = []
    for n in news:
        try:
            dt = parser.parse(n["날짜"])
        except:
            continue
        if dt >= cutoff:
            n["날짜"] = dt.strftime("%Y.%m.%d")
            filtered.append(n)
    items = sorted(filtered, key=lambda x: parser.parse(x["날짜"]), reverse=True)[:3]
    if not items:
        return jsonify(error="가져올 뉴스가 없습니다."), 200

    template = (
        "📌 산업 안전 및 보건 최신 뉴스\n"
        "📰 “{title}” ({date}, {출처})\n\n"
        "{본문}\n"
        "🔎 더 보려면 “뉴스 더 보여줘”를 입력하세요."
    )
    system_msg = {
        "role": "system",
        "content": f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{template}"
    }
    user_msg = {"role": "user", "content": str(items)}
    resp = openai.chat.completions.create(
        model="gpt-4o-mini", messages=[system_msg, user_msg],
        max_tokens=800, temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
