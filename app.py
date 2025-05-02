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

app = Flask(__name__)
app.config['JSON_ASII'] = False  # 한글 깨짐 방지

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

        for suf in [" 점검표", " 계획서", " 서식", " 표", "양식", " 양식", "_양식"]:
            combo = base_space + suf
            alias[combo] = tpl
            alias[combo.replace(" ", "_")] = tpl
            alias[combo.lower()] = tpl

    # General acronym‐based aliases
    for tpl in template_list:
        norm = tpl.lower()
        if "jsa" in norm:
            alias["jsa"] = tpl
            alias["jsa양식"] = tpl
            alias["jsa 양식"] = tpl
            alias["작업안전분석(jsa)"] = tpl
        if "loto" in norm:
            alias["loto"] = tpl
            alias["loto양식"] = tpl
            alias["loto 양식"] = tpl
            alias["loto 실행 기록부"] = tpl

    return alias

def resolve_keyword(raw_keyword: str, template_list: List[str], alias_map: dict) -> str:
    key = raw_keyword.strip()

    # 0) 완전 일치 우선
    for tpl in template_list:
        if key.lower() == tpl.lower() or key.replace(" ", "").lower() == tpl.replace(" ", "").lower():
            return tpl

    # 1) 토큰 기반 매칭 (소문자 비교)
    tokens = [t.lower() for t in key.replace("_", " ").split(" ") if t]
    candidates = [tpl for tpl in template_list if all(tok in tpl.lower() for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]

    # 2) alias 맵
    if key in alias_map:
        return alias_map[key]

    # 3) fuzzy match
    cleaned = key.replace(" ", "").replace("_", "").lower()
    candidates_norm = [t.replace(" ", "").replace("_", "").lower() for t in template_list]
    matches = difflib.get_close_matches(cleaned, candidates_norm, n=1, cutoff=0.6)
    if matches:
        return template_list[candidates_norm.index(matches[0])]

    # 4) no match → 에러 유도
    raise ValueError(f"템플릿 ‘{raw_keyword}’을(를) 찾을 수 없습니다. 정확한 이름을 입력해주세요.")

@app.route("/", methods=["GET"])
def index():
    return "📰 사용 가능한 엔드포인트: /health, /daily_news, /render_news, /create_xlsx", 200

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
    alias_map = build_alias_map(template_list)

    try:
        tpl = resolve_keyword(raw, template_list, alias_map)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    filtered = df[df["템플릿명"] == tpl]
    out_df = filtered[["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]

    def generate_xlsx():
        buffer = BytesIO()
        out_df.to_excel(buffer, index=False)
        buffer.seek(0)
        while True:
            chunk = buffer.read(8192)
            if not chunk:
                break
            yield chunk

    filename = f"{tpl}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control": "public, max-age=3600"
    }
    return Response(generate_xlsx(), headers=headers)

# --- 이하 뉴스 크롤링 유틸 및 엔드포인트 ---

def fetch_safetynews_article_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else "(본문 수집 실패)"
    except:
        return "(본문 수집 실패)"

def crawl_naver_news():
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    keywords = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in keywords:
        params = {"query": kw, "display": 2, "sort": "date"}
        resp = requests.get(base_url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            continue
        for item in resp.json().get("items", []):
            title = BeautifulSoup(item.get("title",""), "html.parser").get_text()
            desc = BeautifulSoup(item.get("description",""), "html.parser").get_text()
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
    keywords = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in keywords:
        resp = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
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
    raw = crawl_naver_news() + crawl_safetynews()
    cutoff = datetime.utcnow() - timedelta(days=3)
    filtered = []
    for n in raw:
        try:
            dt = parser.parse(n["날짜"])
        except:
            continue
        if dt >= cutoff:
            n["날짜"] = dt.strftime("%Y.%m.%d")
            filtered.append(n)

    news_items = sorted(filtered, key=lambda x: parser.parse(x["날짜"]), reverse=True)[:3]
    if not news_items:
        return jsonify(error="가져올 뉴스가 없습니다."), 200

    # ← 여기를 원래의 풍부한 템플릿으로 복원
    template_text = (
        "📌 산업 안전 및 보건 최신 뉴스\n"
        "📰 “{title}” ({date}, {source})\n\n"
        "{headline}\n"
        "🔎 {recommendation}\n"
        "👉 요약 제공됨 · “뉴스 더 보여줘” 입력 시 유사 사례 추가 확인 가능"
    )
    system_message = {
        "role": "system",
        "content": f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{template_text}"
    }
    user_message = {"role": "user", "content": str(news_items)}

    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[system_message, user_message],
        max_tokens=800,
        temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
