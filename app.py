from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)

# ✅ 수정된 부분: ./data 디렉토리 사용
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ✅ 기존 작업계획서 키워드 매핑 유지
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
SOURCES = {name: f"※ 본 양식은 {name} 관련 법령 또는 지침을 기반으로 작성되어있습니다." for name in KEYWORD_ALIAS.values()}

def resolve_keyword(raw_keyword: str) -> str:
    for alias, standard in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return standard
    return raw_keyword

def crawl_news_content(url, source):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")

        if source == "네이버":
            body = soup.select_one("#dic_area") or soup.select_one(".newsct_article")
        elif source == "안전신문":
            body = soup.select_one(".view-article") or soup.select_one(".article-view-content")
        else:
            body = None

        if body:
            return body.get_text(strip=True)
        else:
            return ""
    except:
        return ""

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
                link = title_tag['href']
                content = crawl_news_content(link, "네이버")
                collected.append({
                    "출처": "네이버",
                    "제목": title_tag["title"],
                    "링크": link,
                    "날짜": date_tag.text.strip() if date_tag else "",
                    "본문": content
                })
    return collected

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
                content = crawl_news_content(link, "안전신문")

                collected.append({
                    "출처": "안전신문",
                    "제목": title,
                    "링크": link,
                    "날짜": date,
                    "본문": content
                })
    return collected

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    try:
        naver_news = crawl_naver_news()
        safety_news = crawl_safetynews()

        all_news = naver_news + safety_news

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

        return jsonify(filtered_news)

    except Exception as e:
        return {"error": f"Internal Server Error: {str(e)}"}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
