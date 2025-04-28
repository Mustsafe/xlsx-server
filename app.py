from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)

# ─── 데이터 디렉토리 설정 ───────────────────────────────────────────────────────
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ─── 기존 작업계획서 키워드 매핑 ─────────────────────────────────────────────────
KEYWORD_ALIAS = {
    "고소작업 계획서": "고소작업대작업계획서",
    "고소 작업 계획서": "고소작업대작업계획서",
    "고소작업대 계획서": "고소작업대작업계획서",
    "고소작업": "고소작업대작업계획서",
    "밀폐공간 계획서": "밀폐공간작업계획서",
    "밀폐공간 작업 계획서": "밀폐공간작업계획서",
    "밀폐공간작업 계획서": "밀폐공간작업계획서",
    "밀폐공간": "밀폐공간작업계획서",
    "정전 작업 허가서": "정전작업허가서",
    "정전작업": "정전작업허가서",
    "해체 작업계획서": "해체작업계획서",
    "해체 계획서": "해체작업계획서",
    "구조물 해체 계획": "해체작업계획서",
    "해체작업": "해체작업계획서",
    "크레인 계획서": "크레인작업계획서",
    "크레인 작업 계획서": "크레인작업계획서",
    "양중기 작업계획서": "크레인작업계획서",
    "고온 작업 허가서": "고온작업허가서",
    "고온작업": "고온작업허가서",
    "화기작업 허가서": "화기작업허가서",
    "화기 작업계획서": "화기작업허가서",
    "화기작업": "화기작업허가서",
    "전기 작업계획서": "전기작업계획서",
    "전기 계획서": "전기작업계획서",
    "전기작업": "전기작업계획서",
    "굴착기 작업계획서": "굴착기작업계획서",
    "굴착기 계획서": "굴착기작업계획서",
    "굴삭기 작업계획서": "굴착기작업계획서",
    "용접작업 계획서": "용접용단작업허가서",
    "용접용단 계획서": "용접용단작업허가서",
    "용접작업": "용접용단작업허가서",
    "전기 작업 허가서": "전기작업허가서",
    "고압 전기작업 계획서": "전기작업허가서",
    "전기 허가서": "전기작업허가서",
    "비계 작업 계획서": "비계작업계획서",
    "비계 계획서": "비계작업계획서",
    "비계작업계획": "비계작업계획서",
    "협착 작업 계획서": "협착위험작업계획서",
    "협착 계획서": "협착위험작업계획서",
    "양중 작업 계획서": "양중작업계획서",
    "양중기 작업계획서": "양중작업계획서",
    "고압가스 작업 계획서": "고압가스작업계획서",
    "고압가스 계획서": "고압가스작업계획서"
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

# ─── 작업계획서 엑셀 생성 엔드포인트 ───────────────────────────────────────────────
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw_template = request.args.get("template", "")
    template_name = resolve_keyword(raw_template)

    if not template_name or template_name not in TEMPLATES:
        return {"error": f"'{raw_template}'(으)로는 양식을 찾을 수 없습니다."}, 400

    csv_path = os.path.join(DATA_DIR, f"{template_name}.csv")
    if not os.path.exists(csv_path):
        return {"error": "CSV 원본 파일이 존재하지 않습니다."}, 404

    df = pd.read_csv(csv_path)
    drop_cols = TEMPLATES[template_name]["drop_columns"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore")

    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[c for c in final_cols if c in df.columns]]

    # 소스 문구 추가
    source_text = SOURCES.get(template_name)
    if source_text:
        df.loc[len(df)] = [source_text] + [""] * (len(df.columns) - 1)

    xlsx_path = os.path.join(DATA_DIR, f"{template_name}_최종양식.xlsx")
    df.to_excel(xlsx_path, index=False)
    return send_file(xlsx_path, as_attachment=True, download_name=f"{template_name}.xlsx")

# ─── 본문 수집 함수 ─────────────────────────────────────────────────────────────
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

# ─── 네이버 뉴스 크롤링 (최신 2개) ──────────────────────────────────────────────
def crawl_naver_news():
    base = "https://search.naver.com/search.naver"
    keywords = [
        "건설 사고","건설 사망사고","추락 사고","끼임 사고",
        "질식 사고","폭발 사고","산업재해","산업안전"
    ]
    out = []
    for kw in keywords:
        params = {"where":"news","query":kw}
        resp = requests.get(base, params=params, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
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

# ─── 안전신문 뉴스 크롤링 (최신 2개) ────────────────────────────────────────────
def crawl_safetynews():
    base = "https://www.safetynews.co.kr"
    keywords = [
        "건설 사고","건설 사망사고","추락 사고","끼임 사고",
        "질식 사고","폭발 사고","산업재해","산업안전"
    ]
    out = []
    for kw in keywords:
        url = f"{base}/search/news?searchword={kw}"
        resp = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
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

# ─── 통합 뉴스 엔드포인트 ───────────────────────────────────────────────────────
@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    try:
        news = crawl_naver_news() + crawl_safetynews()
        if not news:
            return {"error": "가져올 뉴스가 없습니다."}, 200
        return jsonify(news)
    except Exception as e:
        return {"error": f"Internal Server Error: {e}"}, 500

# ─── 서버 실행 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
