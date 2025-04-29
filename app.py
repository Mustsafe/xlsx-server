from flask import Flask, request, send_file, jsonify, send_from_directory
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import openai
from dateutil import parser
from datetime import datetime, timedelta
from io import BytesIO

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

# 환경 변수에서 API 키 불러오기
openai.api_key = os.getenv("OPENAI_API_KEY")

# ./data 디렉토리 사용
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

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

# 키워드 매핑
KEYWORD_ALIAS = {
    "고소작업 계획서": "고소작업대작업계획서",
    "고소 작업 계획서": "고소작업대작업계획서",
    "고소작업": "고소작업대작업계획서",
    "밀폐공간 계획서": "밀폐공간작업계획서",
    "밀폐공간": "밀폐공간작업계획서",
    "해체 작업계획서": "해체작업계획서",
    "크레인 계획서": "크레인작업계획서",
    "비계 작업 계획서": "비계작업계획서",
    "협착 작업 계획서": "협착위험작업계획서",
    "양중기 작업계획서": "크레인작업계획서",
    "고압가스 작업 계획서": "고압가스작업계획서"
}

def resolve_keyword(raw_keyword: str) -> str:
    for alias, std in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return std
    return raw_keyword

@app.route("/", methods=["GET"])
def index():
    return "📰 사용 가능한 엔드포인트: /daily_news, /render_news, /create_xlsx", 200

# ════ XLSX 생성 엔드포인트 ════
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    tpl = resolve_keyword(raw)

    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return {"error": "통합 CSV 파일이 없습니다."}, 404

    df = pd.read_csv(csv_path)

    # '작업 항목' 컬럼을 사용한 필터링 로직
    if "작업 항목" in df.columns:
        mask = df["작업 항목"].astype(str).str.contains(tpl)
        filtered = df[mask]
    else:
        return {"error": "필요한 '작업 항목' 컬럼이 없습니다."}, 500

    if filtered.empty:
        return {"error": f"'{tpl}' 양식을 찾을 수 없습니다."}, 404

    # 실제로 존재하는 컬럼만 추출
    columns_to_use = ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
    out_df = filtered[columns_to_use]

    # 메모리 상에서 엑셀 파일 생성
    output = BytesIO()
    out_df.to_excel(output, index=False)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"{tpl}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# SafetyNews 본문 추출
def fetch_safetynews_article_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else "(본문 수집 실패)"
    except Exception:
        return "(본문 수집 실패)"

# 네이버 뉴스 크롤링
def crawl_naver_news():
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    keywords = ["건설 사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]
    out = []
    for kw in keywords:
        params = {"query": kw, "display": 2, "sort": "date"}
        resp = requests.get(base_url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            continue
        for item in resp.json().get("items", []):
            title = BeautifulSoup(item.get("title", ""), "html.parser").get_text()
            desc = BeautifulSoup(item.get("description", ""), "html.parser").get_text()
            out.append({
                "출처": item.get("originallink", "네이버"),
                "제목": title,
                "링크": item.get("link", ""),
                "날짜": item.get("pubDate", ""),
                "본문": desc
            })
    return out

# SafetyNews 크롤링
def crawl_safetynews():
    base = "https://www.safetynews.co.kr"
    keywords = ["건설 사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]
    out = []
    for kw in keywords:
        resp = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
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

# 원본 뉴스 JSON 반환
@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news = crawl_naver_news() + crawl_safetynews()
    if not news:
        return jsonify({"error": "가져올 뉴스가 없습니다."}), 200
    return jsonify(news)

# GPT 포맷 뉴스 반환
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
        return jsonify({"error": "가져올 뉴스가 없습니다."}), 200

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
    return jsonify({"formatted_news": resp.choices[0].message.content})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
