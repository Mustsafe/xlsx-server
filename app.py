from flask import Flask, request, jsonify, Response
import pandas as pd
import os
import re
import json
import difflib
import requests
from bs4 import BeautifulSoup
from io import BytesIO
from typing import List
from urllib.parse import quote
from datetime import datetime, timedelta
from dateutil import parser
import openai
import logging

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

# ── 로거 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 앱 설정 ───────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

openai.api_key      = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# ── 유틸: 소문자+한글+숫자만 남기기 ─────────────────────────────────────────────
def sanitize(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())

# ── alias_map 생성 ─────────────────────────────────────────────────────────────
def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = ["점검표","계획서","서식","표","양식"]
    for tpl in template_list:
        low = tpl.lower()
        alias[low] = tpl
        alias[low.replace(" ", "_")] = tpl
        alias[low.replace("_", " ")] = tpl
        alias[sanitize(low)] = tpl
        base = re.sub(r"(서식|양식|점검표|계획서|표)$", "", low).strip()
        for suf in SUFFIXES:
            key = sanitize(base + suf)
            alias[key] = tpl
    for tpl in template_list:
        s = sanitize(tpl)
        if "jsa" in s:
            alias["jsa"] = tpl
        if "loto" in s:
            alias["loto"] = tpl
    for tpl in template_list:
        words = re.findall(r"[0-9a-z가-힣]+", tpl.lower())
        for w in words:
            alias[sanitize(w)] = tpl
    return alias

# ── 키워드 → 템플릿 resolve ─────────────────────────────────────────────────────
def resolve_keyword(raw: str, templates: List[str], alias_map: dict, freq: dict) -> str:
    query = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘|달라|전달)?$", "",
        raw.strip(), flags=re.IGNORECASE
    ).lower()
    key = sanitize(query)
    if key in alias_map:
        return alias_map[key]
    # fuzzy and substring matching
    matches = difflib.get_close_matches(key, [sanitize(t) for t in templates], n=1, cutoff=0.6)
    if matches:
        return templates[[sanitize(t) for t in templates].index(matches[0])]
    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

# ── 템플릿 리스트 조회 ─────────────────────────────────────────────────────────
@app.route("/list_templates", methods=["GET"])
def list_templates():
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(path, encoding="utf-8-sig")
    templates = df["템플릿명"].dropna().unique().tolist()
    alias_map = build_alias_map(templates)
    return jsonify({"template_list": templates, "alias_keys": list(alias_map.keys())})

# ── 엑셀 생성 엔드포인트 ───────────────────────────────────────────────────────
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(path, encoding="utf-8-sig")
    templates = df["템플릿명"].dropna().unique().tolist()
    freq = df["템플릿명"].value_counts().to_dict()
    alias_map = build_alias_map(templates)
    try:
        tpl = resolve_keyword(raw, templates, alias_map, freq)
        out_df = df[df["템플릿명"] == tpl][["작업 항목","작성 양식","실무 예시 1","실무 예시 2"]].copy()
    except ValueError:
        system = {"role":"system","content":"산업안전 템플릿 전문가. JSON 배열 생성."}
        user = {"role":"user","content":f"템플릿명 '{raw}' 기본 양식 JSON 배열로 주세요."}
        resp = openai.ChatCompletion.create(model="gpt-4o-mini", messages=[system, user], max_tokens=800)
        try:
            out_df = pd.DataFrame(json.loads(resp.choices[0].message.content))
        except:
            out_df = pd.DataFrame([{"작업 항목": raw, "작성 양식": resp.choices[0].message.content, "실무 예시 1": "", "실무 예시 2": ""}])
    # ── AI 동적 고도화 ─────────────────────────────────────────────────────────
    for idx, row in out_df.iterrows():
        # 작성 양식 고도화
        base = row["작성 양식"]
        if isinstance(base,str) and len(base.splitlines())<3:
            sys_msg = {"role":"system","content":"5~8개 점검 리스트 JSON 배열로 생성."}
            usr_msg = {"role":"user","content":json.dumps({"base": base})}
            try:
                r = openai.ChatCompletion.create(model="gpt-4o-mini", messages=[sys_msg, usr_msg], max_tokens=300)
                items = json.loads(r.choices[0].message.content)
                out_df.at[idx, "작성 양식"] = "\n".join(items)
            except: pass
        # 실무 예시 고도화
        for ex in ["실무 예시 1","실무 예시 2"]:
            ex_base = row.get(ex,"")
            if ex_base:
                sysg = {"role":"system","content":"구체적 현장 사례 한 문장 설명."}
                usrg = {"role":"user","content":json.dumps({"base": ex_base})}
                try:
                    rr = openai.ChatCompletion.create(model="gpt-4o-mini", messages=[sysg, usrg], max_tokens=100)
                    out_df.at[idx, ex] = rr.choices[0].message.content.strip()
                except: pass
    # 순서 재정렬
    order = ["📋 작업 절차","💡 실무 가이드","✅ 체크리스트","🛠️ 유지보수 포인트","📎 출처"]
    out_df["_order"] = out_df["작업 항목"].apply(lambda x: order.index(x) if x in order else 99)
    out_df = out_df.sort_values("_order").drop(columns=["_order"])
    # 엑셀 생성 & 포맷
    wb = Workbook(); ws = wb.active
    headers = ["작업 항목","작성 양식","실무 예시 1","실무 예시 2"]
    ws.append(headers)
    for cell in ws[1]: cell.font=Font(bold=True); cell.alignment=Alignment(horizontal="center", vertical="center")
    for row in out_df.itertuples(index=False): ws.append(row)
    for i,col in enumerate(ws.columns,1):
        mx = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(mx+2,60)
        for cell in col[1:]: cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    disp = quote(f"{tpl}.xlsx")
    return Response(buf.read(), headers={
        "Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition":f"attachment; filename*=UTF-8''{disp}",
        "Cache-Control":"public, max-age=3600"
    })

# ── 뉴스 크롤링 & 렌더링 로직 ──────────────────────────────────────────────────
def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else ""
    except:
        return ""

def crawl_naver_news():
    base = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out=[]
    for kw in kws:
        r = requests.get(base, headers=headers, params={"query":kw,"display":2,"sort":"date"}, timeout=10)
        if r.status_code==200:
            for item in r.json().get("items",[]):
                title=BeautifulSoup(item["title"],"html.parser").get_text()
                desc=BeautifulSoup(item["description"],"html.parser").get_text()
                out.append({"출처":item.get("originallink","네이버"),"제목":title,"링크":item.get("link",""),"날짜":item.get("pubDate",""),"본문":desc})
    return out

def crawl_safetynews():
    base="https://www.safetynews.co.kr"
    kws=["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out=[]
    for kw in kws:
        r=requests.get(f"{base}/search/news?searchword={kw}",headers={"User-Agent":"Mozilla/5.0"},timeout=10)
        if r.status_code==200:
            soup=BeautifulSoup(r.text,"html.parser")
            for item in soup.select(".article-list-content")[:2]:
                t=item.select_one(".list-titles")
                href=base+t["href"] if t and t.get("href") else ""
                d=item.select_one(".list-dated")
                content=fetch_safetynews_article_content(href) if href else ""
                out.append({"출처":"안전신문","제목":t.get_text(strip=True) if t else "","링크":href,"날짜":d.get_text(strip=True) if d else "","본문":content[:1000]})
    return out

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news=crawl_naver_news()+crawl_safetynews()
    return jsonify(news if news else {"error":"가져올 뉴스가 없습니다."})

@app.route("/render_news", methods=["GET"])
def render_news():
    news=crawl_naver_news()+crawl_safetynews()
    cutoff=datetime.utcnow()-timedelta(days=3)
    filtered=[{**n, "날짜":parser.parse(n["날짜"]).strftime("%Y.%m.%d")} for n in news if parser.parse(n["날짜"])>=cutoff]
    items=sorted(filtered, key=lambda x: parser.parse(x["날짜"]), reverse=True)[:3]
    if not items: return jsonify(error="가져올 뉴스가 없습니다."),200
    template="📌 산업 안전 및 보건 최신 뉴스\n📰 “{title}” ({date}, {출처})\n\n{본문}\n🔎 더 보려면 “뉴스 더 보여줘”를 입력하세요."
    system_msg={"role":"system","content":f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{template}"}
    user_msg={"role":"user","content":str(items)}
    resp=openai.chat.completions.create(model="gpt-4o-mini",messages=[system_msg,user_msg],max_tokens=800,temperature=0.7)
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)))
