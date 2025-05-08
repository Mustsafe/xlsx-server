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

# ── 로거 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ── Flask & 환경변수 ───────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
openai.api_key = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# ── 데이터 디렉토리 ───────────────────────────────────────────────────────────
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# ── 템플릿별 다양한 별칭 구축 ───────────────────────────────────────────────────
def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = [" 점검표", " 계획서", " 서식", " 표", " 양식", "_양식"]
    for tpl in template_list:
        base = tpl.strip()
        variants = {base, base.lower(), base.replace(" ", "_"), base.replace("_", " ")}
        for v in list(variants):
            alias[v.strip()] = tpl
            alias[v.strip().lower()] = tpl
            for suf in SUFFIXES:
                alias[(v + suf).strip()] = tpl
                alias[(v + suf).strip().lower()] = tpl
    # JSA/LOTO 강제 맵핑
    for tpl in template_list:
        key = tpl.lower().replace(" ", "")
        if "jsa" in key or "작업안전분석" in key:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in key:
            alias["__FORCE_LOTO__"] = tpl
    return alias

# ── 키워드 → 정식 템플릿명 변환 (100% 매칭 보장) ────────────────────────────────
def resolve_keyword(raw: str, templates: List[str], alias_map: dict) -> str:
    key = raw.strip()
    norm = key.replace("_", " ").replace("-", " ").lower()

    # 1) 정확 일치
    if key in templates or key in alias_map:
        return alias_map.get(key, key)
    if norm in alias_map:
        return alias_map[norm]

    # 2) JSA/LOTO 우선
    compact = norm.replace(" ", "")
    if "__FORCE_JSA__" in alias_map and ("jsa" in compact or "작업안전분석" in compact):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in compact:
        return alias_map["__FORCE_LOTO__"]

    # 3) 소문자·공백·언더바 제거 후 완전 일치
    for tpl in templates:
        if compact == tpl.lower().replace(" ", "").replace("_", ""):
            return tpl

    # 4) 토큰 매칭
    tokens = norm.split()
    candidates = [t for t in templates if all(tok in t.lower() for tok in tokens)]
    if candidates:
        # 점검표 우선
        for c in candidates:
            if c.endswith("점검표"):
                return c
        return candidates[0]

    # 5) 퍼지 매치
    keys = [t.replace(" ", "").replace("_", "").lower() for t in templates]
    match = difflib.get_close_matches(compact, keys, n=1, cutoff=0.75)
    if match:
        return templates[keys.index(match[0])]

    # 찾을 수 없으면 예외
    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

# ── 엔드포인트 ────────────────────────────────────────────────────────────────
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
    df = pd.read_csv(csv_path)
    templates = sorted(df["템플릿명"].dropna().unique())
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(build_alias_map(templates).keys())
    })

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "").strip()
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404

    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)

    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        out_df = df[df["템플릿명"] == tpl][["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]
    except ValueError:
        # GPT fallback: 풍성한 5개 이상의 항목 강제
        system_prompt = {
            "role": "system",
            "content": (
                "당신은 산업안전 문서 템플릿 전문가입니다.\n"
                "아래 컬럼 구조에 맞춰, **5개 이상의 항목**을 가진 순수 JSON 배열만 출력해주세요.\n"
                "컬럼: 작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2\n"
                f"템플릿명: {raw}\n"
                "각 항목마다 구체적이고 실무에 바로 적용 가능한 예시를 포함해주세요."
            )
        }
        user_prompt = {
            "role": "user",
            "content": f"템플릿명 '{raw}'의 고도화된 양식을 JSON 배열로 주세요."
        }
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[system_prompt, user_prompt],
            max_tokens=800,
            temperature=0.7
        )
        text = resp.choices[0].message.content
        try:
            data = json.loads(text)
            out_df = pd.DataFrame(data)
        except Exception as e:
            logger.error(f"JSON 파싱 실패: {e}")
            # 최소한 하나의 행이라도 반환
            out_df = pd.DataFrame([{
                "작업 항목": raw,
                "작성 양식": text,
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    # 엑셀 생성
    wb = Workbook()
    ws = wb.active
    ws.append(["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in out_df.itertuples(index=False):
        ws.append(row)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(fname)
    headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control": "public, max-age=3600"
    }
    return Response(buf.read(), headers=headers)

# ── 뉴스 크롤링 / 렌더링 로직은 기존대로 유지합니다 ─────────────────────────────
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

def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else ""
    except:
        return ""

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
                "출처":"안전신문",
                "제목": t.get_text(strip=True) if t else "",
                "링크": href or "",
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
    system_message = {
        "role":"system",
        "content":f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{template}"
    }
    user_message = {"role":"user","content":str(items)}
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[system_message, user_message],
        max_tokens=800,
        temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
