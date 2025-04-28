from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

app = Flask(__name__)

# 📂 데이터 디렉토리
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# 🔑 작업계획서 키워드 매핑
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

# 엑셀 템플릿 설정
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

# ▶️ 작업계획서 엑셀 생성
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    tmpl = resolve_keyword(raw)
    if tmpl not in TEMPLATES:
        return {"error": f"'{raw}'로는 양식을 찾을 수 없습니다."}, 400
    src = os.path.join(DATA_DIR, f"{tmpl}.csv")
    if not os.path.exists(src):
        return {"error": "CSV 파일 없음"}, 404
    df = pd.read_csv(src)
    drops = TEMPLATES[tmpl]["drop_columns"]
    df = df.drop(columns=[c for c in drops if c in df.columns], errors="ignore")
    cols = TEMPLATES[tmpl]["columns"]
    df = df[[c for c in cols if c in df.columns]]
    if tmpl in SOURCES:
        df.loc[len(df)] = [SOURCES[tmpl]] + [""]*(len(df.columns)-1)
    out = os.path.join(DATA_DIR, f"{tmpl}_final.xlsx")
    df.to_excel(out, index=False)
    return send_file(out, as_attachment=True, download_name=f"{tmpl}.xlsx")

# ▶️ 본문 가져오기
 def fetch_naver_article_content(url):
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=h, timeout=10)
        s = BeautifulSoup(r.text, "html.parser")
        if s.select_one("div#dic_area"):
            return s.select_one("div#dic_area").get_text(separator="\n").strip()
        if s.select_one("article"):
            return s.select_one("article").get_text(separator="\n").strip()
        return "(본문 수집 실패)"
    except:
        return "(본문 수집 실패)"

def fetch_safetynews_article_content(url):
    try:
        h = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=h, timeout=10)
        s = BeautifulSoup(r.text, "html.parser")
        div = s.select_one("div#article-view-content-div")
        return div.get_text(separator="\n").strip() if div else "(본문 수집 실패)"
    except:
        return "(본문 수집 실패)"

# ▶️ 네이버 뉴스 크롤링
@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    try:
        def crawl_naver():
            base = "https://search.naver.com/search.naver"
            kws = ["건설 사고","산업안전"]
            res = []
            for kw in kws:
                r = requests.get(base, params={"where":"news","query":kw}, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
                if r.status_code!=200: continue
                soup = BeautifulSoup(r.text, "html.parser")
                for li in soup.select(".list_news li")[:2]:
                    t = li.select_one(".news_tit")
                    if not t: continue
                    url = t["href"]
                    res.append({"출처":"네이버","제목":t.get("title",""),"링크":url,
                                "본문":fetch_naver_article_content(url)})
            return res
        def crawl_safe():
            base = "https://www.safetynews.co.kr"
            res=[]
            for kw in ["건설 사고","산업안전"]:
                r = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
                if r.status_code!=200: continue
                soup=BeautifulSoup(r.text,"html.parser")
                for it in soup.select(".article-list-content")[:2]:
                    a=it.select_one(".list-titles")
                    if not a: continue
                    url=base+a.get("href")
                    res.append({"출처":"안전신문","제목":a.text.strip(),"링크":url,
                                "본문":fetch_safetynews_article_content(url)})
            return res
        data = crawl_naver()+crawl_safe()
        return jsonify(data)
    except Exception as e:
        return {"error":str(e)},500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
