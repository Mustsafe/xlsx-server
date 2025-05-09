from flask import Flask, request, jsonify, Response, send_from_directory
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

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

openai.api_key      = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)


def sanitize(text: str) -> str:
    """소문자·숫자·한글만 남기고 모두 제거"""
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())


def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = ["점검표", "계획서", "서식", "표", "양식"]
    for tpl in template_list:
        low = tpl.lower()
        base = re.sub(r"[\(\)\-]", "", low)  # 괄호/대시 제거
        key = sanitize(base)
        alias[key] = tpl
        alias[low] = tpl
        for suf in SUFFIXES:
            alias[sanitize(base + suf)] = tpl
    # JSA/LOTO 강제
    for tpl in template_list:
        key = sanitize(tpl)
        if "jsa" in key or "작업안전분석" in key:
            alias["jsa"] = tpl
            alias["작업안전분석"] = tpl
        if "loto" in key:
            alias["loto"] = tpl
    return alias


def resolve_keyword(raw: str, templates: List[str], alias_map: dict) -> str:
    # 1) 전처리: 어미 제거
    r = raw.strip().lower()
    r = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘|달라|해주세요)?$",
        "",
        r,
        flags=re.IGNORECASE
    ).strip()
    key = sanitize(r)

    # 2) FORCE 매핑
    if key in ("loto", "jsa", "작업안전분석"):
        return alias_map.get(key)

    # 3) alias_map 직접 조회
    if key in alias_map:
        return alias_map[key]

    # 4) prefix 매칭: sanitize(tpl) 이 key 로 시작하면
    prefix_cands = [
        tpl for tpl in templates
        if sanitize(tpl).startswith(key)
    ]
    if prefix_cands:
        return prefix_cands[0]

    # 5) 부분 문자열 매칭
    substr_cands = [
        tpl for tpl in templates
        if key in sanitize(tpl)
    ]
    if len(substr_cands) == 1:
        return substr_cands[0]

    # 6) fuzzy 매칭
    norms = [sanitize(t) for t in templates]
    m = difflib.get_close_matches(key, norms, n=1, cutoff=0.6)
    if m:
        return templates[norms.index(m[0])]

    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")


@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /list_templates, /create_xlsx, /daily_news, /render_news", 200


@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200


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

    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        out_df = df[df["템플릿명"] == tpl][
            ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
        ]
    except ValueError:
        # fallback: GPT에게 JSON 생성 요청
        system = {
            "role": "system",
            "content": (
                "당신은 산업안전 문서 템플릿 전문가입니다.\n"
                "아래 컬럼(작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2)을 갖춘 JSON 배열을 5개 이상 출력해주세요.\n"
                f"템플릿명: {raw}"
            )
        }
        user = {
            "role": "user",
            "content": f"템플릿명 '{raw}'의 고도화된 양식을 JSON 배열로 주세요."
        }
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system, user],
            max_tokens=800,
            temperature=0.7
        )
        content = resp.choices[0].message.content
        try:
            data = json.loads(content)
            out_df = pd.DataFrame(data)
        except:
            out_df = pd.DataFrame([{
                "작업 항목": raw,
                "작성 양식": content.replace("\n", " "),
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    # Excel 생성 및 포맷팅
    wb = Workbook()
    ws = wb.active
    headers = ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for row in out_df.itertuples(index=False):
        ws.append(row)

    # 자동 너비 조정 + wrap_text
    for idx, col in enumerate(ws.columns, 1):
        max_len = max(len(str(c.value)) for c in col)
        width = min(max_len + 2, 60)
        ws.column_dimensions[get_column_letter(idx)].width = width
        if headers[idx-1] == "작성 양식":
            for c in col[1:]:
                c.alignment = Alignment(wrap_text=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disp = "attachment; filename*=UTF-8''" + quote(fname)
    return Response(
        buf.read(),
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": disp,
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
    kws = ["건설 사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]
    out = []
    for kw in kws:
        r = requests.get(base, headers=headers,
                         params={"query": kw, "display": 2, "sort": "date"},
                         timeout=10)
        if r.status_code != 200:
            continue
        for item in r.json().get("items", []):
            title = BeautifulSoup(item["title"], "html.parser").get_text()
            desc = BeautifulSoup(item["description"], "html.parser").get_text()
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
    kws = ["건설 사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]
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
    items = sorted(filtered,
                   key=lambda x: parser.parse(x["날짜"]),
                   reverse=True)[:3]
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
        model="gpt-4o-mini",
        messages=[system_msg, user_msg],
        max_tokens=800,
        temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
