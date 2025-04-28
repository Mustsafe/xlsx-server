from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import time

app = Flask(__name__)

# ✅ ./data 디렉토리 사용
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# ✅ 작업계획서 키워드 매핑
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

# 뉴스 크롤링 함수
def crawl_naver_news():
    base_url = "https://search.naver.com/search.naver"
    keywords = ["건설 사고", "건설 사망사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]

    headers = {"User-Agent": "Mozilla/5.0"}
    collected = []
    
    for keyword in keywords:
        params = {"where": "news", "query": keyword}
        try:
            response = requests.get(base_url, params=params, headers=headers, timeout=10)
            print(f"Fetched URL: {base_url} - Status Code: {response.status_code}")  # 상태 코드 로그
            if response.status_code != 200:
                print(f"Failed to fetch news for {keyword}. Skipping.")  # 실패 로그
                continue
            
            soup = BeautifulSoup(response.text, "html.parser")
            news_items = soup.select(".list_news > li")[:2]  # 키워드당 2개 제한
            print(f"Found {len(news_items)} news items for {keyword}")  # 뉴스 아이템 수 출력

            for item in news_items:
                title_tag = item.select_one(".news_tit")
                link = title_tag.get("href") if title_tag else None
                date_tag = item.select_one(".info_group span.date")
                content = ""
                if link:
                    article_res = requests.get(link, headers=headers, timeout=10)
                    article_soup = BeautifulSoup(article_res.text, "html.parser")
                    paragraphs = article_soup.select("p")
                    content = " ".join([p.text.strip() for p in paragraphs if p.text.strip()])
                print(f"Title: {title_tag['title']} | Link: {link} | Date: {date_tag.text.strip() if date_tag else ''}")  # 뉴스 항목 로그
                collected.append({
                    "출처": "네이버",
                    "제목": title_tag["title"] if title_tag else "",
                    "링크": link,
                    "날짜": date_tag.text.strip() if date_tag else "",
                    "본문": content[:1000]
                })

            # 크롤링 후 3초 대기 (다음 요청으로 차단 방지)
            time.sleep(3)  # 3초 대기

        except requests.exceptions.RequestException as e:
            print(f"Error during request: {e}")  # 요청 오류 로그
            continue
    return collected

def crawl_safetynews():
    base_url = "https://www.safetynews.co.kr"
    keywords = ["건설 사고", "건설 사망사고", "추락 사고", "끼임 사고", "질식 사고", "폭발 사고", "산업재해", "산업안전"]

    headers = {"User-Agent": "Mozilla/5.0"}
    collected = []

    for keyword in keywords:
        search_url = f"{base_url}/search/news?searchword={keyword}"
        response = requests.get(search_url, headers=headers, timeout=10)
        print(f"Fetched URL: {search_url} - Status Code: {response.status_code}")  # 상태 코드 로그
        if response.status_code != 200:
            print(f"Failed to fetch news for {keyword}. Skipping.")  # 실패 로그
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        news_items = soup.select(".article-list-content")[:2]

        for item in news_items:
            title_element = item.select_one(".list-titles")
            link = base_url + title_element.get("href") if title_element else None
            date_element = item.select_one(".list-dated")
            content = ""
            if link:
                article_res = requests.get(link, headers=headers, timeout=10)
                article_soup = BeautifulSoup(article_res.text, "html.parser")
                paragraphs = article_soup.select("p")
                content = " ".join([p.text.strip() for p in paragraphs if p.text.strip()])
            print(f"Title: {title_element.text.strip()} | Link: {link} | Date: {date_element.text.strip() if date_element else ''}")  # 뉴스 항목 로그
            collected.append({
                "출처": "안전신문",
                "제목": title_element.text.strip() if title_element else "",
                "링크": link,
                "날짜": date_element.text.strip() if date_element else "",
                "본문": content[:1000]
            })
    return collected

# ✅ 통합 뉴스 엔드포인트
@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    try:
        naver_news = crawl_naver_news()
        safety_news = crawl_safetynews()

        all_news = naver_news + safety_news

        if not all_news:
            return {"error": "최근 7일 내 가져올 수 있는 뉴스가 없습니다."}, 200

        print(f"All News Collected: {len(all_news)} items")  # 전체 뉴스 수 출력
        return jsonify(all_news)

    except Exception as e:
        print(f"Error: {str(e)}")  # 오류 로그
        return {"error": f"Internal Server Error: {str(e)}"}, 500

# ✅ 서버 실행
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
