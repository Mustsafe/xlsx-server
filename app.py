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

# ---------- Logger 설정 ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

# ---------- 환경 변수 로드 ----------
openai.api_key = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# ---------- 데이터 디렉토리 ----------
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# ---------- Alias 맵 생성 ----------
def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = [" 점검표", " 계획서", " 서식", " 표", "양식", " 양식", "_양식"]
    for tpl in template_list:
        base = tpl.replace('_', ' ')
        low = base.lower()
        keys = {tpl, base, tpl.replace(' ', '_'), low, low.replace(' ', ''), low.replace(' ', '_')}
        keys |= {k + suf for k in {base, low} for suf in SUFFIXES}
        for k in keys:
            alias[k] = tpl
    # JSA/LOTO 강제 매핑
    for tpl in template_list:
        norm = tpl.lower().replace(" ", "").replace("_", "")
        if any(x in norm for x in ['jsa', '작업안전분석']):
            alias['__FORCE_JSA__'] = tpl
        if 'loto' in norm:
            alias['__FORCE_LOTO__'] = tpl
    return alias

# ---------- 키워드 해석 ----------
def resolve_keyword(raw: str, templates: List[str], alias_map: dict) -> str:
    key = raw.strip()
    norm = key.replace('_', ' ').replace('-', ' ').lower()
    compact = norm.replace(' ', '')
    # 1) 정확 일치
    for tpl in templates:
        if key == tpl or key.replace(' ', '_') == tpl or key.replace('_', ' ') == tpl:
            return tpl
    # 2) 강제 JSA/LOTO
    if '__FORCE_JSA__' in alias_map and any(x in compact for x in ['jsa','작업안전분석']):
        return alias_map['__FORCE_JSA__']
    if '__FORCE_LOTO__' in alias_map and 'loto' in compact:
        return alias_map['__FORCE_LOTO__']
    # 3) 소문자+언더바 제거 후 일치
    for tpl in templates:
        if compact == tpl.lower().replace(' ','').replace('_',''):
            return tpl
    # 4) 토큰 매칭
    tokens = norm.split()
    candidates = [t for t in templates if all(tok in t.lower() for tok in tokens)]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        for c in candidates:
            if c.endswith('점검표'):
                return c
        return candidates[0]
    # 5) alias 맵
    if key in alias_map:
        return alias_map[key]
    if norm in alias_map:
        return alias_map[norm]
    # 6) 퍼지 매치
    keys = [t.replace(' ', '').replace('_','').lower() for t in templates]
    m = difflib.get_close_matches(compact, keys, n=1, cutoff=0.7)
    if m:
        return templates[keys.index(m[0])]
    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

# ---------- 엔드포인트: 기본, 헬스 ----------
@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /daily_news, /render_news, /create_xlsx, /list_templates", 200

@app.route("/health", methods=["GET"])
def health_check():
    logger.info("Health check called")
    return "OK", 200

# ---------- 템플릿 목록 반환 ----------
@app.route("/list_templates", methods=["GET"])
def list_templates():
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(path)
    templates = sorted(df['템플릿명'].dropna().unique())
    return jsonify({
        'template_list': templates,
        'alias_keys': sorted(build_alias_map(templates).keys())
    })

# ---------- 엑셀 생성 로직 ----------
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get('template','')
    logger.info(f"create_xlsx with template={raw}")
    csv_path = os.path.join(DATA_DIR, '통합_노지파일.csv')
    if not os.path.exists(csv_path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(csv_path)
    if '템플릿명' not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500
    templates = sorted(df['템플릿명'].dropna().unique())
    alias_map = build_alias_map(templates)
    try:
        tpl = resolve_keyword(raw, templates, alias_map)
        out_df = df[df['템플릿명']==tpl][['작업 항목','작성 양식','실무 예시 1','실무 예시 2']]
    except ValueError:
        # GPT fallback: 항상 JSON array
        system = {
            'role':'system', 'content':(
                '당신은 산업안전 전문가입니다. 요청 양식에 맞춰 JSON 배열만 출력.'
                '컬럼: 작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2. 추가 설명 금지.'
                f"템플릿명: {raw}" )}
        user = {'role':'user','content':f"템플릿명 '{raw}' 양식을 JSON 배열로 주세요."}
        resp = openai.chat.completions.create(
            model='gpt-4o-mini', messages=[system,user], max_tokens=800, temperature=0.5
        )
        try:
            data = json.loads(resp.choices[0].message.content)
            out_df = pd.DataFrame(data)
        except:
            out_df = pd.DataFrame([{ '작업 항목':raw, '작성 양식':resp.choices[0].message.content, '실무 예시 1':'', '실무 예시 2':'' }])
    # 엑셀 작성
    wb = Workbook()
    ws = wb.active
    ws.append(['작업 항목','작성 양식','실무 예시 1','실무 예시 2'])
    for c in ws[1]: c.font = Font(bold=True)
    for row in out_df.itertuples(index=False): ws.append(row)
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    fname = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disp = "attachment; filename*=UTF-8''" + quote(fname)
    return Response(buf.read(), headers={
        'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition':disp, 'Cache-Control':'public, max-age=3600'
    })

# ---------- 뉴스 크롤링 & 렌더링 ----------
# 아래 함수들은 기존 고도화 전 로직과 동일하게 유지합니다.
def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=10)
        soup = BeautifulSoup(r.text,'html.parser')
        node = soup.select_one('div#article-view-content-div')
        return node.get_text('\n').strip() if node else '(본문 수집 실패)'
    except:
        return '(본문 수집 실패)'

def crawl_naver_news():
    base='https://openapi.naver.com/v1/search/news.json'
    headers={'X-Naver-Client-Id':NAVER_CLIENT_ID,'X-Naver-Client-Secret':NAVER_CLIENT_SECRET}
    kws=['건설 사고','추락 사고','끼임 사고','질식 사고','폭발 사고','산업재해','산업안전']
    out=[]
    for kw in kws:
        r=requests.get(base, headers=headers, params={'query':kw,'display':2,'sort':'date'})
        if r.status_code!=200: continue
        for it in r.json().get('items',[]):
            title=BeautifulSoup(it['title'],'html.parser').get_text()
            desc = BeautifulSoup(it['description'],'html.parser').get_text()
            out.append({'출처':it.get('originallink','네이버'),'제목':title,'링크':it.get('link',''),
                        '날짜':it.get('pubDate',''),'본문':desc})
    return out

def crawl_safetynews():
    base='https://www.safetynews.co.kr'
    kws=['건설 사고','추락 사고','끼임 사고','질식 사고','폭발 사고','산업재해','산업안전']
    out=[]
    for kw in kws:
        r=requests.get(f"{base}/search/news?searchword={kw}", headers={'User-Agent':'Mozilla/5.0'})
        if r.status_code!=200: continue
        soup=BeautifulSoup(r.text,'html.parser')
        for item in soup.select('.article-list-content')[:2]:
            t=item.select_one('.list-titles'); href=base+t['href'] if t else None
            d=item.select_one('.list-dated'); content=fetch_safetynews_article_content(href)
            out.append({'출처':'안전신문','제목':t.get_text(strip=True),'링크':href,'날짜':d.get_text(strip=True),'본문':content[:1000]})
    return out

@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news = crawl_naver_news()+crawl_safetynews()
    return jsonify(news or {'error':'가져올 뉴스가 없습니다.'})

@app.route("/render_news", methods=["GET"])
def render_news():
    news = crawl_naver_news()+crawl_safetynews()
    if not news: return jsonify(error='가져올 뉴스가 없습니다.'),200
    cutoff = datetime.utcnow()-timedelta(days=3)
    filt=[n for n in news if parser.parse(n['날짜'])>=cutoff]
    items=sorted(filt, key=lambda x: parser.parse(x['날짜']), reverse=True)[:3]
    if not items: return jsonify(error='가져올 뉴스가 없습니다.'),200
    tpl = ("📌 산업 안전 및 보건 최신 뉴스\n"
           "📰 “{title}” ({date}, {출처})\n\n{본문}\n"
           "🔎 더 보려면 “뉴스 더 보여줘”를 입력하세요.")
    system={'role':'system','content':f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{tpl}"}
    user={'role':'user','content':str(items)}
    resp=openai.chat.completions.create(model='gpt-4o-mini',messages=[system,user],max_tokens=800,temperature=0.7)
    return jsonify(formatted_news=resp.choices[0].message.content)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT',5000)))
