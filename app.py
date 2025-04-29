from flask import Flask, request, send_file, jsonify, send_from_directory
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

# 키워드 매핑 (구체 키 우선, 길이 내림차순으로 매칭)
KEYWORD_ALIAS = {
    # 기존 11종
    "고소작업 사전점검표":         "고소작업_사전점검표",
    "고소작업 계획서":            "고소작업대작업계획서",
    "고소 작업 계획서":           "고소작업대작업계획서",
    "고소작업":                  "고소작업대작업계획서",
    "밀폐공간 계획서":            "밀폐공간작업계획서",
    "밀폐공간":                  "밀폐공간작업계획서",
    "해체 작업계획서":            "해체작업계획서",
    "크레인 계획서":              "크레인작업계획서",
    "비계 작업 계획서":           "비계작업계획서",
    "협착 작업 계획서":           "협착위험작업계획서",
    "양중기 작업계획서":          "양중기_작업계획서",
    "고압가스 작업 계획서":        "고압가스작업계획서",

    # 추가된 39종
    "가시설점검표":               "가시설점검표",
    "고압가스작업계획서":         "고압가스작업계획서",
    "교대근무계획표":             "교대근무계획표",
    "구내버스운행관리대장":       "구내버스운행관리대장",
    "굴삭기운전계획서":           "굴삭기운전계획서",
    "위험기계잠금·격리절차서":     "위험기계잠금·격리절차서",
    "방폭설비유지보수계획서":     "방폭설비유지보수계획서",
    "보건관리순회일지":           "보건관리순회일지",
    "비상대응훈련계획서":         "비상대응훈련계획서",
    "사무실안전점검표":           "사무실안전점검표",
    "생산설비정비계획서":         "생산설비정비계획서",
    "선박·해양구조물점검표":       "선박·해양구조물점검표",
    "소음진동측정계획서":         "소음진동측정계획서",
    "소방설비점검표":             "소방설비점검표",
    "안전교육계획서":             "안전교육계획서",
    "안전보건관리체계구축계획서":   "안전보건관리체계구축계획서",
    "안전작업허가절차서":         "안전작업허가절차서",
    "안전작업일지":               "안전작업일지",
    "산업안전보건위원회회의록":     "산업안전보건위원회회의록",
    "산업재해예방계획서":         "산업재해예방계획서",
    "시설물유지관리계획서":       "시설물유지관리계획서",
    "승강기정기검사계획서":       "승강기정기검사계획서",
    "아이소가스측정계획서":       "아이소가스측정계획서",
    "작업허가서":                 "작업허가서",
    "작업환경측정계획서":         "작업환경측정계획서",
    "위험성평가매뉴얼":           "위험성평가매뉴얼",
    "위험성평가보고서":           "위험성평가보고서",
    "위험위해방지계획서":         "위험위해방지계획서",
    "응급처치훈련기록표":         "응급처치훈련기록표",
    "장비검사기록표":             "장비검사기록표",
    "점검표작성가이드라인":       "점검표작성가이드라인",
    "중대사고조사보고서":         "중대사고조사보고서",
    "출입통제관리대장":           "출입통제관리대장",
    "품질안전보증계획서":         "품질안전보증계획서",
    "환경영향평가계획서":         "환경영향평가계획서",
    "현장안전점검표":             "현장안전점검표",
    "회전기계점검계획서":         "회전기계점검계획서",
    "회의록서식(안전보건)":       "회의록서식(안전보건)",
    "화학물질관리계획서":         "화학물질관리계획서",
}

def resolve_keyword(raw_keyword: str, template_list: List[str]) -> str:
    """
    1) KEYWORD_ALIAS 매핑 우선 적용 (길이 순)
    2) difflib로 fuzzy 매칭
    3) 못 찾으면 원본 반환(이후 404 처리)
    """
    # 1) alias (길이 순으로 구체 매핑 먼저)
    for alias in sorted(KEYWORD_ALIAS.keys(), key=len, reverse=True):
        if alias in raw_keyword:
            return KEYWORD_ALIAS[alias]

    # 2) fuzzy match
    cleaned = raw_keyword.replace(" ", "").lower()
    candidates = [t.replace(" ", "").lower() for t in template_list]
    matches = difflib.get_close_matches(cleaned, candidates, n=1, cutoff=0.6)
    if matches:
        idx = candidates.index(matches[0])
        return template_list[idx]

    # 3) no match
    return raw_keyword

@app.route("/", methods=["GET"])
def index():
    return "📰 사용 가능한 엔드포인트: /daily_news, /render_news, /create_xlsx", 200

# XLSX 생성 엔드포인트
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return {"error": "통합 CSV 파일이 없습니다."}, 404

    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        return {"error": "필요한 '템플릿명' 컬럼이 없습니다."}, 500

    # fuzzy 매칭 적용
    template_list = sorted(df["템플릿명"].dropna().unique().tolist())
    tpl = resolve_keyword(raw, template_list)

    filtered = df[df["템플릿명"].astype(str) == tpl]
    if filtered.empty:
        return {"error": f"'{tpl}' 양식을 찾을 수 없습니다."}, 404

    out_df = filtered[["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]
    output = BytesIO()
    out_df.to_excel(output, index=False)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name=f"{tpl}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# 이하 뉴스 크롤링 및 렌더 함수 (수정 없음)

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
