from flask import Flask, request, jsonify, Response
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

from openpyxl import Workbook
from openpyxl.styles import Font

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


def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = ["점검표", "계획서", "서식", "표", "양식"]
    for tpl in template_list:
        low = tpl.lower()
        # 1) 원본·소문자
        alias[low] = tpl
        # 2) 공백<->언더바
        alias[low.replace(" ", "_")] = tpl
        alias[low.replace("_", " ")] = tpl
        # 3) 괄호·특수문자 제거
        cleaned = re.sub(r"[^0-9a-z가-힣]", "", low)
        alias[cleaned] = tpl
        # 4) 접미사 변형
        base = re.sub(r"(서식|양식|점검표|계획서|표)$", "", low).strip()
        for suf in SUFFIXES:
            key = base + suf
            alias[key] = tpl
            alias[key.replace(" ", "_")] = tpl
            alias[re.sub(r"[^0-9a-z가-힣]", "", key)] = tpl

    # JSA/LOTO 강제 키
    for tpl in template_list:
        norm = re.sub(r"[^0-9a-z]", "", tpl.lower())
        if "jsa" in norm or "작업안전분석" in norm:
            alias["jsa"] = tpl
            alias["작업안전분석서"] = tpl
        if "loto" in norm:
            alias["loto"] = tpl

    return alias


@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(alias_map.keys())
    })


@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    # “양식/서식/점검표/계획서/표 + (을|를)? + (주세요|줘|달라|해주세요|전달)?” 제거
    raw_norm = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘|달라|해주세요|전달)?$",
        "",
        raw,
        flags=re.IGNORECASE
    ).strip()

    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)

    # 매칭 키 생성
    norm = raw_norm.lower().replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"[^0-9a-z가-힣]", "", norm)

    # 등록된 템플릿이면 무조건 매칭
    if cleaned in alias_map:
        tpl = alias_map[cleaned]
        out_df = df[df["템플릿명"] == tpl][
            ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
        ]
    else:
        # GPT fallback
        system = {
            "role": "system",
            "content": (
                "당신은 산업안전 문서 템플릿 전문가입니다.\n"
                "다음 컬럼(작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2)을 가진 JSON 배열을 5개 이상 출력해주세요.\n"
                f"템플릿명: {raw_norm}"
            )
        }
        user = {
            "role": "user",
            "content": f"템플릿명 '{raw_norm}'의 기본 양식을 JSON 배열로 주세요."
        }
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system, user],
            max_tokens=800,
            temperature=0.7
        )
        try:
            data = json.loads(resp.choices[0].message.content)
            out_df = pd.DataFrame(data)
        except:
            out_df = pd.DataFrame([{
                "작업 항목": raw_norm,
                "작성 양식": resp.choices[0].message.content.replace("\n", " "),
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    # Excel 생성
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

    filename = f"{tpl}.xlsx" if 'tpl' in locals() else f"{raw_norm}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    return Response(
        buf.read(),
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": disposition,
            "Cache-Control": "public, max-age=3600"
        }
    )


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
        r = requests.get(base, headers=headers, params={"query": kw, "display": 2, "sort": "date"}, timeout=10)
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
        r = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select(".article-list-content")[:2]:
            t = item.select_one(".list-titles")
            href = base + t["href"] if t and t.get("href") else ""
            d = item.select_one(".list-dated")
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
        model="gpt-4o-mini", messages=[system_msg, user_msg], max_tokens=800, temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
