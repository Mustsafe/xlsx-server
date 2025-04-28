from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)

# ✅ '/mnt/data' 디렉토리 없으면 생성
DATA_DIR = "/mnt/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ✅ 기존 작업계획서 키워드 매핑
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

TEMPLATES = {name: {"columns": ["작업 항목", "작성 양식", "실무 예시"], "drop_columns": []} for name in KEYWORD_ALIAS.values()}
SOURCES = {name: f"※ 본 양식은 {name} 관련 법령 또는 지침을 기반으로 작성되었습니다." for name in KEYWORD_ALIAS.values()}

def resolve_keyword(raw_keyword: str) -> str:
    for alias, standard in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return standard
    return raw_keyword

# ✅ 작업계획서 xlsx 생성
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
    drop_cols = TEMPLATES[template_name].get("drop_columns", [])
    df = df.drop(columns=[col for col in drop_cols if col in df.columns], errors="ignore")

    final_cols = TEMPLATES[template_name]["columns"]
    df = df[[col for col in final_cols if col in df.columns]]

    if template_name in SOURCES:
        source_text = SOURCES[template_name]
        df.loc[len(df)] = [source_text] + [""] * (len(df.columns) - 1)

    xlsx_path = os.path.join(DATA_DIR, f"{template_name}_최종양식.xlsx")
    df.to_excel(xlsx_path, index=False)

    return send_file(xlsx_path, as_attachment=True, download_name=f"{template_name}.xlsx")

# ✅ 네이버 뉴스 크롤링
def crawl_naver_news():
    base_url = "https://search.naver.com/search.naver"
    keywords = ["건설 사고", "건설 사망사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]

    headers = {"User-Agent": "Mozilla/5.0"}
    collected = []

    for keyword in keywords:
        params = {"where": "news", "query": keyword}
        response = requests.get(base_url, params=params, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        news_items = soup.select(".list_news > li")

        for item in news_items:
            title_tag = item.select_one(".news_tit")
            date_tag = item.select_one(".info_group span.date")

            if title_tag and title_tag.get("title") and title_tag.get("href"):
                collected.append({
                    "출처": "네이버",
                    "제목": title_tag["title"],
                    "링크": title_tag["href"],
                    "날짜": date_tag.text.strip() if date_tag else ""
                })
    return collected

# ✅ 안전신문 크롤링
def crawl_safetynews():
    base_url = "https://www.safetynews.co.kr"
    keywords = ["건설 사고", "건설 사망사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]

    headers = {"User-Agent": "Mozilla/5.0"}
    collected = []

    for keyword in keywords:
        search_url = f"{base_url}/search/news?searchword={keyword}"
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        news_items = soup.select(".article-list-content")

        for item in news_items:
            title_element = item.select_one(".list-titles")
            date_element = item.select_one(".list-dated")

            if title_element:
                title = title_element.text.strip()
                link = base_url + title_element.get("href")
                date = date_element.text.strip() if date_element else ""

                collected.append({
                    "출처": "안전신문",
                    "제목": title,
                    "링크": link,
                    "날짜": date
                })
    return collected

# ✅ 통합 뉴스 크롤링
@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    try:
        # 🛠 여기 추가 (핵심)
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

        naver_news = crawl_naver_news()
        safety_news = crawl_safetynews()

        all_news = naver_news + safety_news

        # 최근 7일 필터
        today = datetime.now()
        one_week_ago = today - timedelta(days=7)

        filtered_news = []
        for news in all_news:
            try:
                date = datetime.strptime(news["날짜"], "%Y.%m.%d.")
                if one_week_ago <= date <= today:
                    filtered_news.append(news)
            except:
                continue

        if not filtered_news:
            return {"error": "최근 7일 내 가져올 수 있는 뉴스가 없습니다."}, 200

        df = pd.DataFrame(filtered_news)
        filename = os.path.join(DATA_DIR, f"daily_safety_news_{today.strftime('%Y%m%d')}.csv")
        df.to_csv(filename, index=False, encoding="utf-8-sig")

        return send_file(filename, as_attachment=True)

    except Exception as e:
        return {"error": f"Internal Server Error: {str(e)}"}, 500

# ✅ 서버 실행
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
