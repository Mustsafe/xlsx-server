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

# 로거 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 환경 변수 로드
openai.api_key        = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID       = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET   = os.getenv("NAVER_CLIENT_SECRET")

# 데이터 디렉토리 및 fallback JSON
DATA_DIR             = "./data"
FALLBACK_JSON_PATH   = os.path.join(DATA_DIR, "fallback_templates.json")
os.makedirs(DATA_DIR, exist_ok=True)
# fallback_templates.json 예시 구조:
# {
#   "밀폐공간작업": [
#     {"작업 항목":"점검 대상 선정", "작성 양식":"대상 설비 및 환경 확인", "실무 예시 1":"탱크 내부 산소농도 측정", "실무 예시 2":"밀폐구역 접근 허가서 작성"},
#     ...
#   ],
#   ...
# }

def load_fallback_templates():
    if os.path.exists(FALLBACK_JSON_PATH):
        with open(FALLBACK_JSON_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}

FALLBACK_STORE = load_fallback_templates()

def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = [" 점검표"," 계획서"," 서식"," 표","양식"," 양식","_양식"]
    for tpl in template_list:
        alias[tpl] = tpl
        alias[tpl.replace("_"," ")] = tpl
        alias[tpl.replace(" ","_")] = tpl
        low = tpl.lower()
        alias[low] = tpl
        base = tpl.replace("_"," ")
        no_sp = base.replace(" ","").lower()
        alias[no_sp] = tpl
        for suf in SUFFIXES:
            alias[base + suf] = tpl
            alias[(base + suf).replace(" ","_")] = tpl
            alias[(base + suf).lower()] = tpl
    # JSA/LOTO 강제 매핑
    for tpl in template_list:
        key = tpl.lower().replace(" ","").replace("_","")
        if "jsa" in key or "작업안전분석" in key:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in key:
            alias["__FORCE_LOTO__"] = tpl
    # 공백/언더바 버전 추가
    extra = {}
    for k,v in alias.items():
        extra[k.replace(" ","_")] = v
        extra[k.replace("_"," ")] = v
    alias.update(extra)
    return alias

def resolve_keyword(raw: str, templates: List[str], alias_map: dict) -> str:
    key = raw.strip()
    norm = key.replace("_"," ").lower()
    compact = norm.replace(" ","")
    # 정확 일치
    for tpl in templates:
        if key==tpl or key.replace("_"," ")==tpl or key.replace(" ","_")==tpl:
            return tpl
    # JSA/LOTO
    if "__FORCE_JSA__" in alias_map and ("jsa" in compact or "작업안전분석" in compact):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in compact:
        return alias_map["__FORCE_LOTO__"]
    # 소문자+compact 일치
    for tpl in templates:
        if compact == tpl.lower().replace(" ","").replace("_",""):
            return tpl
    # 토큰 매칭
    toks = norm.split()
    cands = [t for t in templates if all(tok in t.lower() for tok in toks)]
    if len(cands)==1:
        return cands[0]
    if len(cands)>1:
        for c in cands:
            if c.endswith("점검표"):
                return c
        return cands[0]
    # alias_map
    if key in alias_map:
        return alias_map[key]
    if norm in alias_map:
        return alias_map[norm]
    # fuzzy
    keys = [t.replace(" ","").replace("_","").lower() for t in templates]
    m = difflib.get_close_matches(compact, keys, n=1, cutoff=0.75)
    if m:
        return templates[keys.index(m[0])]
    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /daily_news, /render_news, /create_xlsx, /list_templates", 200

@app.route("/health", methods=["GET"])
def health_check():
    logger.info("Health check endpoint called")
    return "OK",200

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template","")
    logger.info(f"create_xlsx called with template={raw}")
    csv_path = os.path.join(DATA_DIR,"통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."),404
    df = pd.read_csv(csv_path)
    if "템플릿명" not in df.columns:
        return jsonify(error="템플릿명 컬럼이 없습니다."),500

    templates = sorted(df["템플릿명"].dropna().unique().tolist())
    alias_map = build_alias_map(templates)

    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        out_df = df[df["템플릿명"]==tpl][["작업 항목","작성 양식","실무 예시 1","실무 예시 2"]]
    except ValueError:
        # fallback store 우선
        if raw in FALLBACK_STORE:
            out_df = pd.DataFrame(FALLBACK_STORE[raw])
        else:
            # GPT fallback: 최소 skeleton
            system = {
                "role":"system",
                "content":(
                    "당신은 산업안전 문서 전문가입니다.\n"
                    "아래 컬럼에 맞춰 5개 이상의 JSON 배열만 출력하세요.\n"
                    "컬럼: 작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2\n"
                    f"템플릿명:{raw}"
                )
            }
            user   = {"role":"user","content":f"템플릿명 '{raw}' 기본 양식을 JSON 배열로 주세요."}
            resp   = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[system,user],
                max_tokens=800,temperature=0.5
            )
            try:
                arr = json.loads(resp.choices[0].message.content)
                out_df = pd.DataFrame(arr)
            except:
                out_df = pd.DataFrame([{
                    "작업 항목":raw,"작성 양식":resp.choices[0].message.content,
                    "실무 예시 1":"","실무 예시 2":""
                }])

    # 엑셀 생성
    wb = Workbook(); ws = wb.active
    ws.append(["작업 항목","작성 양식","실무 예시 1","실무 예시 2"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in out_df.itertuples(index=False):
        ws.append(r)

    buf = BytesIO(); wb.save(buf); buf.seek(0)
    filename = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disp     = "attachment; filename*=UTF-8''"+quote(filename)
    headers  = {
        "Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition":disp,"Cache-Control":"public, max-age=3600"
    }
    return Response(buf.read(), headers=headers)

@app.route("/list_templates", methods=["GET"])
def list_templates():
    csv_path = os.path.join(DATA_DIR,"통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."),404
    df = pd.read_csv(csv_path)
    templates = sorted(df["템플릿명"].dropna().unique())
    return jsonify(template_list=templates, alias_keys=sorted(build_alias_map(templates).keys()))

# — 뉴스 크롤링 & 렌더링 (원본 복원) —
def fetch_naver():
    base="https://openapi.naver.com/v1/search/news.json"
    hdr={"X-Naver-Client-Id":NAVER_CLIENT_ID,"X-Naver-Client-Secret":NAVER_CLIENT_SECRET}
    kws=["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out=[]
    for kw in kws:
        r=requests.get(base,headers=hdr,params={"query":kw,"display":2,"sort":"date"},timeout=10)
        if r.status_code!=200: continue
        for it in r.json().get("items",[]):
            t=BeautifulSoup(it["title"],"html.parser").get_text()
            d=BeautifulSoup(it["description"],"html.parser").get_text()
            out.append({"출처":it.get("originallink","네이버"),"제목":t,"링크":it.get("link",""),"날짜":it.get("pubDate",""),"본문":d})
    return out

def fetch_safetynews():
    base="https://www.safetynews.co.kr"
    kws=["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out=[]
    for kw in kws:
        r=requests.get(f"{base}/search/news?searchword={kw}",headers={"User-Agent":"Mozilla/5.0"},timeout=10)
        if r.status_code!=200: continue
        sp=BeautifulSoup(r.text,"html.parser")
        for it in sp.select(".article-list-content")[:2]:
            t=it.select_one(".list-titles"); href=base+t["href"] if t and t.get("href") else ""
            d=it.select_one(".list-dated"); bd=fetch_naver() if href else ""
            out.append({"출처":"안전신문","제목":t.get_text(strip=True),"링크":href,"날짜":d.get_text(strip=True) if d else "", "본문":fetch_naver()})
    return out

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news = fetch_naver()+fetch_safetynews()
    return jsonify(news) if news else jsonify(error="가져올 뉴스가 없습니다."),200

@app.route("/render_news", methods=["GET"])
def render_news():
    raw = fetch_naver()+fetch_safetynews()
    cutoff = datetime.utcnow()-timedelta(days=3)
    flt=[]
    for n in raw:
        try: dt=parser.parse(n["날짜"])
        except: continue
        if dt>=cutoff:
            n["날짜"]=dt.strftime("%Y.%m.%d"); flt.append(n)
    items=sorted(flt,key=lambda x:parser.parse(x["날짜"]),reverse=True)[:3]
    if not items: return jsonify(error="가져올 뉴스가 없습니다."),200
    tpl_txt=("📌 산업 안전·보건 최신 뉴스\n📰 “{제목}” ({날짜}, {출처})\n\n{본문}\n")
    sys_msg={"role":"system","content":f"JSON 형식으로 뉴스 3건만 출력하세요.\n템플릿:\n{tpl_txt}"}
    user_msg={"role":"user","content":str(items)}
    resp=openai.chat.completions.create(model="gpt-4o-mini",messages=[sys_msg,user_msg],max_tokens=500,temperature=0.7)
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)))
