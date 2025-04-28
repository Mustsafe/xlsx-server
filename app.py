from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)

# 데이터 디렉토리 설정
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 키워드 매핑 (기존 코드 유지)
KEYWORD_ALIAS = {
    "고소작업 계획서": "고소작업대작업계획서",
    "고소 작업 계획서": "고소작업대작업계획서",
    # ... 이하 생략 (기존 전체 매핑 복사)
}

TEMPLATES = {
    name: {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []}
    for name in KEYWORD_ALIAS.values()
}
SOURCES = {
    name: f"※ 본 양식은 {name} 관련 법령 또는 지침을 기반으로 작성되었습니다."
    for name in KEYWORD_ALIAS.values()
}

def resolve_keyword(raw_keyword: str) -> str:
    for alias, standard in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return standard
    return raw_keyword

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw_template = request.args.get("template", "")
    template_name = resolve_keyword(raw_template)

    if not template_name or template_name not in TEMPLATES:
        return {"error": f"'{raw_template}' 양식을 찾을 수 없습니다."}, 400

    csv_path = os.path.join(DATA_DIR, f"{template_name}.csv")
    if not os.path.exists(csv_path):
        return {"error": "CSV 원본 파일이 없습니다."}, 404

    df = pd.read_csv(csv_path)
    df = df.drop(columns=TEMPLATES[template_name]["drop_columns"], errors="ignore")
    df = df[[c for c in TEMPLATES[template_name]["columns"] if c in df.columns]]

    source_text = SOURCES.get(template_name)
    if source_text:
        df.loc[len(df)] = [source_text] + [""] * (len(df.columns) - 1)

    xlsx_path = os.path.join(DATA_DIR, f"{template_name}_최종양식.xlsx")
    df.to_excel(xlsx_path, index=False)
    return send_file(xlsx_path, as_attachment=True, download_name=f"{template_name}.xlsx")

# 본문 수집 함수 (들여쓰기 스페이스 4칸)
def fetch_naver_article_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.select_one("div#dic_area"):
            return soup.select_one("div#dic_area").get_text("\n").strip()
        if soup.select_one("article"):
            return soup.select_one("article").get_text("\n").strip()
        return "(본문 수집 실패)"
    except Exception:
        return "(본문 수집 실패)"

def fetch_safetynews_article_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else "(본문 수집 실패)"
    except Exception:
        return "(본문 수집 실패)"

def crawl_naver_news():
    base = "https://search.naver.com/search.naver"
    keywords = [
        "건설 사고","건설 사망사고","추락 사고","끼임 사고",
        "질식 사고","폭발 사고","산업재해","산업안전"
    ]
    out = []
    for kw in keywords:
        resp = requests.get(base, params={"where":"news","query":kw},
                            headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".list_news > li")[:2]:
            title = item.select_one(".news_tit")
            href = title["href"] if title else None
            date = item.select_one(".info_group span.date")
            content = fetch_naver_article_content(href) if href else ""
            out.append({
                "출처": "네이버",
                "제목": title["title"] if title else "",
                "링크": href,
                "날짜": date.text.strip() if date else "",
                "본문": content[:1000]
            })
    return out

def crawl_safetynews():
    base = "https://www.safetynews.co.kr"
    keywords = [
        "건설 사고","건설 사망사고","추락 사고","끼임 사고",
        "질식 사고","폭발 사고","산업재해","산업안전"
    ]
    out = []
    for kw in keywords:
        resp = requests.get(f"{base}/search/news?searchword={kw}",
                            headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if resp.status_code != 200:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".article-list-content")[:2]:
            title_node = item.select_one(".list-titles")
            href = base + title_node["href"] if title_node else None
            date = item.select_one(".list-dated")
            content = fetch_safetynews_article_content(href) if href else ""
            out.append({
                "출처": "안전신문",
                "제목": title_node.text.strip() if title_node else "",
                "링크": href,
                "날짜": date.text.strip() if date else "",
                "본문": content[:1000]
            })
    return out

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    try:
        news = crawl_naver_news() + crawl_safetynews()
        if not news:
            return {"error": "가져올 뉴스가 없습니다."}, 200
        return jsonify(news)
    except Exception as e:
        return {"error": f"Internal Server Error: {e}"}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
