from flask import Flask, request, jsonify, send_from_directory, Response
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
from urllib.parse import quote
import json
import logging
from openpyxl import Workbook
from openpyxl.styles import Font

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

openai.api_key = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

@app.route("/health", methods=["GET"])
def health_check():
    logger.info("Health check endpoint called")
    return "OK", 200

@app.route("/.well-known/<path:filename>")
def serve_well_known(filename):
    return send_from_directory(
        os.path.join(app.root_path, "static", ".well-known"),
        filename, mimetype="application/json"
    )

@app.route("/openapi.json")
def serve_openapi():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "openapi.json", mimetype="application/json"
    )

@app.route("/logo.png")
def serve_logo():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "logo.png", mimetype="image/png"
    )

def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = [" 점검표", " 계획서", " 서식", " 표", "양식", " 양식", "_양식"]
    for tpl in template_list:
        alias[tpl] = tpl
        alias[tpl.replace("_", " ")] = tpl
        alias[tpl.replace(" ", "_")] = tpl
        low = tpl.lower()
        alias[low] = tpl
        alias[low.replace("_", " ")] = tpl
        base_space = tpl.replace("_", " ")
        nospace = base_space.replace(" ", "").lower()
        alias[nospace] = tpl
        for suf in SUFFIXES:
            combo = base_space + suf
            alias[combo] = tpl
            alias[combo.replace(" ", "_")] = tpl
            alias[combo.lower()] = tpl
    for tpl in template_list:
        norm = tpl.lower().replace(" ", "").replace("_", "")
        if "jsa" in norm or "작업안전분석" in norm:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in norm:
            alias["__FORCE_LOTO__"] = tpl
    temp = {}
    for k, v in alias.items():
        temp[k.replace(" ", "_")] = v
        temp[k.replace("_", " ")] = v
    alias.update(temp)
    return alias

def resolve_keyword(raw_keyword: str, template_list: List[str], alias_map: dict) -> str:
    raw = raw_keyword.strip()
    norm = raw.replace("_", " ").replace("-", " ")
    key_lower = norm.lower()
    cleaned_key = key_lower.replace(" ", "")
    if "__FORCE_JSA__" in alias_map and ("jsa" in cleaned_key or "작업안전분석" in cleaned_key):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in cleaned_key:
        return alias_map["__FORCE_LOTO__"]
    for tpl in template_list:
        if key_lower == tpl.lower() or cleaned_key == tpl.replace(" ", "").replace("_", "").lower():
            return tpl
    tokens = [t for t in key_lower.split(" ") if t]
    candidates = [tpl for tpl in template_list if all(tok in tpl.lower() for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]
    if raw in alias_map:
        return alias_map[raw]
    if key_lower in alias_map:
        return alias_map[key_lower]
    candidates_norm = [t.replace(" ", "").replace("_", "").lower() for t in template_list]
    matches = difflib.get_close_matches(cleaned_key, candidates_norm, n=1, cutoff=0.8)
    if matches:
        return template_list[candidates_norm.index(matches[0])]
    raise ValueError(f"템플릿 '{raw_keyword}'을(를) 찾을 수 없습니다.")

@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /daily_news, /render_news, /create_xlsx, /list_templates", 200

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    logger.info(f"create_xlsx called with template={raw}")

    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404

    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    template_list = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map      = build_alias_map(template_list)

    try:
        tpl = resolve_keyword(raw, template_list, alias_map)
        filtered = df[df["템플릿명"] == tpl]
        out_df   = filtered[["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]]
    except ValueError:
        system_prompt = {
            "role":"system",
            "content":(
                "당신은 산업안전 분야 문서 템플릿 전문가입니다.\n"
                "아래 컬럼 구조에 맞춰 5개 이상의 항목을 가진 JSON 배열을 생성해 주세요.\n"
                "컬럼: 작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2\n"
                f"템플릿명: {raw}"
            )
        }
        user_prompt = {
            "role":"user",
            "content":f"템플릿명 '{raw}'의 기본 양식을 JSON으로 주세요."
        }
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system_prompt, user_prompt],
            max_tokens=800,
            temperature=0.5
        )
        text = resp.choices[0].message.content
        try:
            data = json.loads(text)
            out_df = pd.DataFrame(data)
        except:
            out_df = pd.DataFrame([{
                "작업 항목": raw,
                "작성 양식": text,
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    wb = Workbook()
    ws = wb.active
    ws.append(["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in out_df.itertuples(index=False):
        ws.append(row)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    headers = {
        "Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control":"public, max-age=3600"
    }
    return Response(buf.read(), headers=headers)

@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path)
    templates = sorted(df["템플릿명"].dropna().unique())
    return jsonify({
        "template_list":templates,
        "alias_keys":sorted(build_alias_map(templates).keys())
    })

def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else "(본문 수집 실패)"
    except:
        return "(본문 수집 실패)"

def crawl_naver_news():
    base = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id":NAVER_CLIENT_ID,
        "X-Naver-Client-Secret":NAVER_CLIENT_SECRET
    }
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        params = {"query":kw,"display":2,"sort":"date"}
        r = requests.get(base, headers=headers, params=params, timeout=10)
        if r.status_code!=200: continue
        for item in r.json().get("items",[]):
            title = BeautifulSoup(item["title"],"html.parser").get_text()
            desc  = BeautifulSoup(item["description"],"html.parser").get_text()
            out.append({
                "출처":item.get("originallink","네이버"),
                "제목":title,
                "링크":item.get("link",""),
                "날짜":item.get("pubDate",""),
                "본문":desc
            })
    return out

def crawl_safetynews():
    base="https://www.safetynews.co.kr"
    kws=["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out=[]
    for kw in kws:
        r = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if r.status_code!=200: continue
        soup = BeautifulSoup(r.text,"html.parser")
        for item in soup.select(".article-list-content")[:2]:
            t = item.select_one(".list-titles")
            href = base + t["href"] if t and t.get("href") else None
            d = item.select_one(".list-dated")
            content = fetch_safetynews_article_content(href) if href else ""
            out.append({
                "출처":"안전신문",
                "제목":t.get_text(strip=True) if t else "",
                "링크":href,
                "날짜":d.get_text(strip=True) if d else "",
                "본문":content[:1000]
            })
    return out

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news = crawl_naver_news() + crawl_safetynews()
    if not news: return jsonify(error="가져올 뉴스가 없습니다."),200
    return jsonify(news)

@app.route("/render_news", methods=["GET"])
def render_news():
    news = crawl_naver_news() + crawl_safetynews()
    if not news: return jsonify(error="가져올 뉴스가 없습니다."),200
    cutoff = datetime.utcnow()-timedelta(days=3)
    filtered=[]
    for n in news:
        try:
            dt = parser.parse(n["날짜"])
        except:
            continue
        if dt>=cutoff:
            n["날짜"]=dt.strftime("%Y.%m.%d")
            filtered.append(n)
    items = sorted(filtered, key=lambda x: parser.parse(x["날짜"]), reverse=True)[:3]
    if not items: return jsonify(error="가져올 뉴스가 없습니다."),200
    template_text = (
        "📌 산업 안전 및 보건 최신 뉴스\n"
        "📰 “{title}” ({date}, {출처})\n\n"
        "{본문}\n"
        "🔎 더 보려면 “뉴스 더 보여줘”를 입력하세요."
    )
    system_message = {
        "role":"system",
        "content":f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{template_text}"
    }
    user_message={"role":"user","content":str(items)}
    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message,user_message],
        max_tokens=800,
        temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)))
