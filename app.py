from flask import Flask, request, send_file, jsonify, send_from_directory, Response
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
from itertools import product

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

# 환경 변수에서 API 키 불러오기
openai.api_key = os.getenv("OPENAI_API_KEY")

# ./data 디렉토리 사용
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# --- 1. 헬스체크 엔드포인트 추가 ---
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

# 네이버 오픈 API 자격증명
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

def build_alias_map(template_list: List[str]) -> dict:
    """
    template_list 에 있는 각 템플릿명에 대해
    다양한 변형(alias)을 자동 생성하여 매핑 dict 를 반환합니다.
    """
    alias = {}
    for tpl in template_list:
        # 1) 원래 이름
        alias[tpl] = tpl
        # 2) 언더스코어 ↔ 공백
        alias[tpl.replace("_", " ")] = tpl
        alias[tpl.replace(" ", "_")] = tpl
        # 3) 소문자 버전
        low = tpl.lower()
        alias[low] = tpl
        alias[low.replace("_", " ")] = tpl
        # 4) 주요 접미사 추가
        base_space = tpl.replace("_", " ")
        for suf in [" 점검표", " 계획서", " 서식", " 표"]:
            combo = base_space + suf
            alias[combo] = tpl
            alias[combo.replace(" ", "_")] = tpl
            alias[combo.lower()] = tpl
    return alias

def resolve_keyword(raw_keyword: str, template_list: List[str], alias_map: dict) -> str:
    """
    1) 토큰 기반 매칭: raw_keyword를 분리한 토큰이 tpl에 모두 포함되면 바로 매치
    2) alias_map 매핑 우선 적용
    3) difflib로 fuzzy 매칭 (언더스코어·공백 모두 제거)
    4) 못 찾으면 원본 반환(이후 fallback 처리)
    """
    key = raw_keyword.strip()
    # 1) 토큰 기반 매칭
    tokens = [t for t in key.replace("_", " ").split(" ") if t]
    candidates = [tpl for tpl in template_list
                  if all(tok in tpl for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]

    # 2) alias 맵
    if key in alias_map:
        return alias_map[key]

    # 3) fuzzy match (언더스코어·공백 모두 제거)
    cleaned = key.replace(" ", "").replace("_", "").lower()
    candidates_norm = [t.replace(" ", "").replace("_", "").lower() for t in template_list]
    matches = difflib.get_close_matches(cleaned, candidates_norm, n=1, cutoff=0.6)
    if matches:
        idx = candidates_norm.index(matches[0])
        return template_list[idx]

    # 4) no match
    return key

@app.route("/", methods=["GET"])
def index():
    return "📰 사용 가능한 엔드포인트: /health, /daily_news, /render_news, /create_xlsx", 200

# XLSX 생성 엔드포인트 (스트리밍 및 캐싱 헤더 추가)
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return {"error": "통합 CSV 파일이 없습니다."}, 404

    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        return {"error": "필요한 '템플릿명' 컬럼이 없습니다."}, 500

    # 템플릿 목록 및 alias_map 생성
    template_list = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(template_list)

    # 키워드 해석
    tpl = resolve_keyword(raw, template_list, alias_map)

    # 필터링 및 fallback 처리
    filtered = df[df["템플릿명"].astype(str) == tpl]
    if filtered.empty:
        filtered = df[df["템플릿명"] == template_list[0]]
        used_tpl = template_list[0]
    else:
        used_tpl = tpl

    out_df = filtered[["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]

    # 스트리밍 Response
    def generate_xlsx():
        buffer = BytesIO()
        out_df.to_excel(buffer, index=False)
        buffer.seek(0)
        while True:
            chunk = buffer.read(8192)
            if not chunk:
                break
            yield chunk

    headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": f'attachment; filename="{used_tpl}.xlsx"',
        "Cache-Control": "public, max-age=3600"
    }
    return Response(generate_xlsx(), headers=headers)

# 이하 뉴스 크롤링 및 렌더 함수 (변경 없음)
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
            desc  = BeautifulSoup(item.get("description",""), "html.parser").get_text()
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
        resp = requests.get(f"{base}/search/news?searchword={kw}",
                            headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".article-list-content")[:2]:
            t    = item.select_one(".list-titles")
            href = base + t["href"] if t and t.get("href") else None
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
        return jsonify({"error":"가져올 뉴스가 없습니다."}), 200
    return jsonify(news)

@app.route("/render_news", methods=["GET"])
def render_news():
    raw    = crawl_naver_news() + crawl_safetynews()
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
        return jsonify({"error":"가져올 뉴스가 없습니다."}), 200

    template_text = (
        "📌 산업 안전 및 보건 최신 뉴스\n"
        "📰 “{title}” ({date}, {source})\n\n"
        "{headline}\n"
        "🔎 {recommendation}\n"
        "👉 요약 제공됨 · “뉴스 더 보여줘” 입력 시 유사 사례 추가 확인 가능"
    )
    system_message = {
        "role":"system",
        "content":f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{template_text}"
    }
    user_message = {"role":"user","content":str(news_items)}

    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[system_message, user_message],
        max_tokens=800,
        temperature=0.7
    )
    return jsonify({"formatted_news": resp.choices[0].message.content})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
