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
from openpyxl import Workbook
from openpyxl.styles import Font

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
        base = tpl.replace("_", " ")
        nospace = base.replace(" ", "").lower()
        alias[nospace] = tpl
        for suf in SUFFIXES:
            combo = base + suf
            alias[combo] = tpl
            alias[combo.replace(" ", "_")] = tpl
            alias[combo.lower()] = tpl
    for tpl in template_list:
        norm = tpl.lower().replace(" ", "").replace("_", "")
        if "jsa" in norm or "작업안전분석" in norm:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in norm:
            alias["__FORCE_LOTO__"] = tpl
    # 공백/언더바 버전 추가
    extra = {}
    for k, v in alias.items():
        extra[k.replace(" ", "_")] = v
        extra[k.replace("_", " ")] = v
    alias.update(extra)
    return alias

def resolve_keyword(raw: str, templates: List[str], alias_map: dict) -> str:
    key = raw.strip()
    norm = key.replace("_", " ").replace("-", " ").lower()
    compact = norm.replace(" ", "")

    # 0) 정확 일치 우선
    for tpl in templates:
        if key == tpl or key.replace("_", " ") == tpl or key.replace(" ", "_") == tpl:
            return tpl

    # 1) JSA/LOTO 예외
    if "__FORCE_JSA__" in alias_map and ("jsa" in compact or "작업안전분석" in compact):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in compact:
        return alias_map["__FORCE_LOTO__"]

    # 2) 소문자·공백·언더바 제거 후 완전 일치
    for tpl in templates:
        if compact == tpl.lower().replace(" ", "").replace("_", ""):
            return tpl

    # 3) 토큰 매칭
    tokens = norm.split()
    candidates = [t for t in templates if all(tok in t.lower() for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # 점검표 우선
        for c in candidates:
            if c.endswith("점검표"):
                return c
        return candidates[0]

    # 4) alias 맵
    if key in alias_map:
        return alias_map[key]
    if norm in alias_map:
        return alias_map[norm]

    # 5) 퍼지 매치
    keys = [t.replace(" ", "").replace("_", "").lower() for t in templates]
    match = difflib.get_close_matches(compact, keys, n=1, cutoff=0.75)
    if match:
        return templates[keys.index(match[0])]

    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /daily_news, /render_news, /create_xlsx, /list_templates", 200

@app.route("/health", methods=["GET"])
def health_check():
    logger.info("Health check endpoint called")
    return "OK", 200

@app.route("/.well-known/<path:filename>")
def serve_well_known(filename):
    return send_from_directory(
        os.path.join(app.root_path, "static", ".well-known"),
        filename, mimetype="application/json"
    )

@app.route("/openapi.json")
def serve_openapi():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "openapi.json", mimetype="application/json"
    )

@app.route("/logo.png")
def serve_logo():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.png", mimetype="image/png"
    )

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    df = pd.read_csv(csv_path)
    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)

    # 1) 먼저 기존 매핑 로직(백업된 버전) 그대로 적용
    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        out_df = df[df["템플릿명"] == tpl][["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]
    except ValueError:
        # 2) GPT fallback: JSON → DataFrame → 네 컬럼 강제 분리
        system = {
            "role": "system",
            "content": (
                "당신은 산업안전 문서 전문가입니다. 등록되지 않은 템플릿 요청 시 "
                "4개 컬럼(작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2)으로 구성된 JSON 배열을 "
                "리턴해주세요. 추가 설명 없이 순수 JSON만."
            )
        }
        user = {"role": "user", "content": f"템플릿명 '{raw}' 양식을 JSON으로 주세요."}
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system, user],
            max_tokens=600,
            temperature=0.5,
        )
        data = json.loads(resp.choices[0].message.content)
        # 강제 4컬럼 분리
        out_df = pd.DataFrame(data)
        out_df = out_df.reindex(columns=["작업 항목","작성 양식","실무 예시 1","실무 예시 2"])

    # 엑셀 생성 (컬럼별로 제대로 들어갑니다)
    wb = Workbook()
    ws = wb.active
    ws.append(out_df.columns.tolist())
    for row in out_df.itertuples(index=False):
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return Response(
        buffer.read(),
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f"attachment; filename*=UTF-8''{tpl if 'tpl' in locals() else raw}.xlsx"
        }
    )

@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path)
    templates = sorted(df["템플릿명"].dropna().unique())
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(build_alias_map(templates).keys())
    })

def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else "(본문 수집 실패)"
    except:
        return "(본문 수집 실패)"

def crawl_naver_news():
    base = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        r = requests.get(base, headers=headers, params={"query":kw,"display":2,"sort":"date"}, timeout=10)
        if r.status_code != 200: continue
        for item in r.json().get("items", []):
            title = BeautifulSoup(item["title"], "html.parser").get_text()
            desc  = BeautifulSoup(item["description"], "html.parser").get_text()
            out.append({
                "출처": item.get("originallink","네이버"),
                "제목": title,
                "링크": item.get("link",""),
                "날짜": item.get("pubDate",""),
                "본문": desc
            })
    return out

def crawl_safetynews():
    base = "https://www.safetynews.co.kr"
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        r = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if r.status_code != 200: continue
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select(".article-list-content")[:2]:
            t = item.select_one(".list-titles")
            href = base + t["href"] if t and t.get("href") else None
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
    items = []
    for n in news:
        try:
            dt = parser.parse(n["날짜"])
        except:
            continue
        if dt >= cutoff:
            n["날짜"] = dt.strftime("%Y.%m.%d")
            items.append(n)
    items = sorted(items, key=lambda x: parser.parse(x["날짜"]), reverse=True)[:3]
    if not items:
        return jsonify(error="가져올 뉴스가 없습니다."), 200

    template = (
        "📌 산업 안전 및 보건 최신 뉴스\n"
        "📰 “{title}” ({날짜}, {출처})\n\n"
        "{본문}\n"
        "🔎 더 보려면 “뉴스 더 보여줘”를 입력하세요."
    )
    system_message = {
        "role": "system",
        "content": (
            "다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n"
            f"템플릿:\n{template}"
        )
    }
    user_message = {"role": "user", "content": json.dumps(items, ensure_ascii=False)}
    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message, user_message],
        max_tokens=800,
        temperature=0.7,
    )
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
