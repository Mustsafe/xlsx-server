from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# ./data 디렉토리 사용
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 네이버 오픈 API 자격증명 (실제론 환경변수로 관리하세요)
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "QK5pGnOogpbtXc2_AQAQ")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "xjH5Nn5auL")

# 작업계획서 키워드 매핑
KEYWORD_ALIAS = {
    "고소작업 계획서": "고소작업대작업계획서", "고소 작업 계획서": "고소작업대작업계획서",
    "고소작업대 계획서": "고소작업대작업계획서", "고소작업": "고소작업대작업계획서",
    "밀폐공간 계획서": "밀폐공간작업계획서", "밀폐공간 작업 계획서": "밀폐공간작업계획서",
    "밀폐공간작업 계획서": "밀폐공간작업계획서", "밀폐공간": "밀폐공간작업계획서",
    "정전 작업 허가서": "정전작업허가서", "정전작업": "정전작업허가서",
    "해체 작업계획서": "해체작업계획서", "해체 계획서": "해체작업계획서",
    "구조물 해체 계획": "해체작업계획서", "해체작업": "해체작업계획서",
    "크레인 계획서": "크레인작업계획서", "크레인 작업 계획서": "크레인작업계획서",
    "양중기 작업계획서": "크레인작업계획서",
    "고온 작업 허가서": "고온작업허가서", "고온작업": "고온작업허가서",
    "화기작업 허가서": "화기작업허가서", "화기 작업계획서": "화기작업허가서", "화기작업": "화기작업허가서",
    "전기 작업계획서": "전기작업계획서", "전기 계획서": "전기작업계획서", "전기작업": "전기작업계획서",
    "굴착기 작업계획서": "굴착기작업계획서", "굴착기 계획서": "굴착기작업계획서", "굴삭기 작업계획서": "굴착기작업계획서",
    "용접작업 계획서": "용접용단작업허가서", "용접용단 계획서": "용접용단작업허가서", "용접작업": "용접용단작업허가서",
    "전기 작업 허가서": "전기작업허가서", "고압 전기작업 계획서": "전기작업허가서", "전기 허가서": "전기작업허가서",
    "비계 작업 계획서": "비계작업계획서", "비계 계획서": "비계작업계획서", "비계작업계획": "비계작업계획서",
    "협착 작업 계획서": "협착위험작업계획서", "협착 계획서": "협착위험작업계획서",
    "양중 작업 계획서": "양중작업계획서", "양중기 작업계획서": "양중작업계획서",
    "고압가스 작업 계획서": "고압가스작업계획서", "고압가스 계획서": "고압가스작업계획서"
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
    for alias, std in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return std
    return raw_keyword

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    tpl = resolve_keyword(raw)
    if tpl not in TEMPLATES:
        return {"error": f"'{raw}' 양식을 찾을 수 없습니다."}, 400

    csv_path = os.path.join(DATA_DIR, f"{tpl}.csv")
    if not os.path.exists(csv_path):
        return {"error": "CSV 원본 파일이 없습니다."}, 404

    df = pd.read_csv(csv_path)
    df = df.drop(columns=TEMPLATES[tpl]["drop_columns"], errors="ignore")
    df = df[[c for c in TEMPLATES[tpl]["columns"] if c in df.columns]]

    source = SOURCES.get(tpl)
    if source:
        df.loc[len(df)] = [source] + [""] * (len(df.columns) - 1)

    xlsx_path = os.path.join(DATA_DIR, f"{tpl}_최종양식.xlsx")
    df.to_excel(xlsx_path, index=False)
    return send_file(xlsx_path, as_attachment=True, download_name=f"{tpl}.xlsx")

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
    base_url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    keywords = [
        "건설 사고", "건설 사망사고", "추락 사고", "끼임 사고",
        "질식 사고", "폭발 사고", "산업재해", "산업안전"
    ]
    out = []
    for kw in keywords:
        params = {"query": kw, "display": 2, "sort": "date"}
        resp = requests.get(base_url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            continue
        for item in resp.json().get("items", []):
            title = BeautifulSoup(item.get("title", ""), "html.parser").get_text()
            desc  = BeautifulSoup(item.get("description", ""), "html.parser").get_text()
            link  = item.get("link", "")
            out.append({
                "출처": item.get("originallink", "네이버"),
                "제목": title,
                "링크": link,
                "날짜": "",
                "본문": desc
            })
    return out

def crawl_safetynews():
    base = "https://www.safetynews.co.kr"
    keywords = [
        "건설 사고", "건설 사망사고", "추락 사고", "끼임 사고",
        "질식 사고", "폭발 사고", "산업재해", "산업안전"
    ]
    out = []
    for kw in keywords:
        resp = requests.get(
            f"{base}/search/news?searchword={kw}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
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
    try:
        news = crawl_naver_news() + crawl_safetynews()
        if not news:
            return {"error": "가져올 뉴스가 없습니다."}, 200
        return jsonify(news)
    except Exception as e:
        return {"error": f"Internal Server Error: {e}"}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
