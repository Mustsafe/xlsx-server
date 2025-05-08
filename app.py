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
import re

# ── 엑셀 생성용 import ─────────────────────────────────────────────────────────
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

openai.api_key = os.getenv("OPENAI_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)


def build_alias_map(template_list: List[str]) -> dict:
    alias = {}
    SUFFIXES = [" 점검표", " 계획서", " 서식", " 표", "양식", " 양식", "_양식"]
    for tpl in template_list:
        base = tpl.replace("_", " ")
        low = tpl.lower()
        nospace = base.replace(" ", "").lower()
        # 기본 매핑
        alias[tpl] = tpl
        alias[base] = tpl
        alias[tpl.replace(" ", "_")] = tpl
        alias[low] = tpl
        alias[low.replace("_", " ")] = tpl
        alias[nospace] = tpl
        # 접미사 매핑
        for suf in SUFFIXES:
            alias[(base + suf).strip()] = tpl
            alias[(base + suf).strip().lower()] = tpl
            alias[(base + suf).replace(" ", "_")] = tpl
    # JSA/LOTO 강제
    for tpl in template_list:
        key = tpl.lower().replace(" ", "").replace("_", "")
        if "jsa" in key or "작업안전분석" in key:
            alias["__FORCE_JSA__"] = tpl
        if "loto" in key:
            alias["__FORCE_LOTO__"] = tpl
    # 공백/언더바 변환 추가
    extra = {}
    for k, v in alias.items():
        extra[k.replace(" ", "_")] = v
        extra[k.replace("_", " ")] = v
    alias.update(extra)
    return alias


def resolve_keyword(raw_keyword: str, template_list: List[str], alias_map: dict) -> str:
    raw = raw_keyword.strip()
    norm = raw.replace("_", " ").replace("-", " ").lower()
    cleaned = norm.replace(" ", "")
    # JSA / LOTO 우선
    if "__FORCE_JSA__" in alias_map and ("jsa" in cleaned or "작업안전분석" in cleaned):
        return alias_map["__FORCE_JSA__"]
    if "__FORCE_LOTO__" in alias_map and "loto" in cleaned:
        return alias_map["__FORCE_LOTO__"]
    # 정확 일치
    if raw in alias_map:
        return alias_map[raw]
    if norm in alias_map:
        return alias_map[norm]
    # 토큰 매칭 (모든 토큰)
    tokens = [t for t in norm.split() if t]
    all_cands = [tpl for tpl in template_list if all(tok in tpl.lower() for tok in tokens)]
    if len(all_cands) == 1:
        return all_cands[0]
    # 단일 토큰 기반 매칭: 특정 토큰으로만 필터링해 유일할 때
    for tok in tokens:
        c = [tpl for tpl in template_list if tok in tpl.lower()]
        if len(c) == 1:
            return c[0]
    # 부분 문자열 매칭
    substr = [tpl for tpl in template_list if cleaned in tpl.lower().replace(" ", "").replace("_", "")]
    if len(substr) == 1:
        return substr[0]
    # 퍼지 매칭
    norms = [t.replace(" ", "").replace("_", "").lower() for t in template_list]
    m = difflib.get_close_matches(cleaned, norms, n=1, cutoff=0.6)
    if m:
        return template_list[norms.index(m[0])]
    raise ValueError(f"템플릿 '{raw_keyword}'을(를) 찾을 수 없습니다.")


@app.route("/", methods=["GET"])
def index():
    return "📰 endpoints: /health, /list_templates, /create_xlsx, /daily_news, /render_news", 200


@app.route("/health", methods=["GET"])
def health_check():
    return "OK", 200


@app.route("/list_templates", methods=["GET"])
def list_templates():
    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404
    df = pd.read_csv(path, encoding="utf-8-sig")
    templates = sorted(df["템플릿명"].dropna().unique())
    return jsonify({
        "template_list": templates,
        "alias_keys": sorted(build_alias_map(templates).keys())
    })


@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    # 1) 전처리: “양식/서식/점검표/계획서/표 + (을|를)? + (주세요|줘)?” 제거
    raw = request.args.get("template", "").strip()
    raw = re.sub(
        r"\s*(?:양식|서식|점검표|계획서|표)(?:을|를)?\s*(?:주세요|줘)?$",
        "",
        raw,
        flags=re.IGNORECASE
    ).strip()

    path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(path):
        return jsonify(error="통합 CSV 파일이 없습니다."), 404

    df = pd.read_csv(path, encoding="utf-8-sig")
    if "템플릿명" not in df.columns:
        return jsonify(error="필요한 '템플릿명' 컬럼이 없습니다."), 500

    templates = sorted(df["템플릿명"].dropna().unique())
    alias_map = build_alias_map(templates)

    try:
        # 2) 고도화된 양식 매칭
        tpl = resolve_keyword(raw, templates, alias_map)
        out_df = df[df["템플릿명"] == tpl][
            ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
        ]
    except ValueError:
        # 3) 고도화되지 않은 양식 → GPT fallback
        system = {
            "role": "system",
            "content": (
                "당신은 산업안전 문서 템플릿 전문가입니다.\n"
                "아래 컬럼 구조에 맞춰, **5개 이상의 항목**을 가진 순수 JSON 배열만 출력해주세요.\n"
                "컬럼: 작업 항목, 작성 양식, 실무 예시 1, 실무 예시 2\n"
                f"템플릿명: {raw}\n"
                "각 항목마다 구체적이고 실무에 바로 적용 가능한 예시를 포함해주세요."
            )
        }
        user = {"role": "user", "content": f"템플릿명 '{raw}'의 고도화된 양식을 JSON 배열로 주세요."}
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[system, user],
            max_tokens=800,
            temperature=0.7
        )
        text = resp.choices[0].message.content
        try:
            data = json.loads(text)
            out_df = pd.DataFrame(data)
        except Exception:
            out_df = pd.DataFrame([{
                "작업 항목": raw,
                "작성 양식": text.replace("\n", " "),
                "실무 예시 1": "",
                "실무 예시 2": ""
            }])

    # 4) Excel 생성 & 포맷팅
    wb = Workbook()
    ws = wb.active
    headers = ["작업 항목", "작성 양식", "실무 예시 1", "실무 예시 2"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row in out_df.itertuples(index=False):
        ws.append(row)

    # 컬럼 너비 자동 조정 & 작성 양식 열 wrap
    for idx, col in enumerate(ws.columns, 1):
        max_len = max(len(str(c.value)) for c in col)
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = min(max_len + 2, 60)
        if headers[idx-1] == "작성 양식":
            for cell in col[1:]:
                cell.alignment = Alignment(wrap_text=True)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"{tpl if 'tpl' in locals() else raw}.xlsx"
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    resp_headers = {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": disposition,
        "Cache-Control": "public, max-age=3600"
    }
    return Response(buf.read(), headers=resp_headers)


# ── 뉴스 크롤링 / 렌더링 로직 ──────────────────────────────────────────────────
def fetch_safetynews_article_content(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("div#article-view-content-div")
        return node.get_text("\n").strip() if node else ""
    except:
        return ""


def crawl_naver_news():
    base = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        r = requests.get(base, headers=headers, params={"query":kw,"display":2,"sort":"date"}, timeout=10)
        if r.status_code != 200: continue
        for item in r.json().get("items", []):
            title = BeautifulSoup(item["title"], "html.parser").get_text()
            desc  = BeautifulSoup(item["description"], "html.parser").get_text()
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
    kws = ["건설 사고","추락 사고","끼임 사고","질식 사고","폭발 사고","산업재해","산업안전"]
    out = []
    for kw in kws:
        r = requests.get(f"{base}/search/news?searchword={kw}", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200: continue
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select(".article-list-content")[:2]:
            t = item.select_one(".list-titles")
            href = base + t["href"] if t and t.get("href") else None
            d = item.select_one(".list-dated")
            content = fetch_safetynews_article_content(href) if href else ""
            out.append({
                "출처": "안전신문",
                "제목": t.get_text(strip=True) if t else "",
                "링크": href or "",
                "날짜": d.get_text(strip=True) if d else "",
                "본문": content[:1000]
            })
    return out


@app.route("/daily_news", methods=["GET"])
def get_daily_news():
    news = crawl_naver_news() + crawl_safetynews()
    if not news:
        return jsonify(error="가져올 뉴스가 없습니다."), 200
    return jsonify(news)


@app.route("/render_news", methods=["GET"])
def render_news():
    news = crawl_naver_news() + crawl_safetynews()
    cutoff = datetime.utcnow() - timedelta(days=3)
    filtered = []
    for n in news:
        try:
            dt = parser.parse(n["날짜"])
        except:
            continue
        if dt >= cutoff:
            n["날짜"] = dt.strftime("%Y.%m.%d")
            filtered.append(n)
    items = sorted(filtered, key=lambda x: parser.parse(x["날짜"]), reverse=True)[:3]
    if not items:
        return jsonify(error="가져올 뉴스가 없습니다."), 200

    tpl = (
        "📌 산업 안전 및 보건 최신 뉴스\n"
        "📰 “{title}” ({date}, {출처})\n\n"
        "{본문}\n"
        "🔎 더 보려면 “뉴스 더 보여줘”를 입력하세요."
    )
    system = {"role":"system", "content":f"다음 JSON 형식의 뉴스 목록을 아래 템플릿에 맞춰 출력하세요.\n템플릿:\n{tpl}"}
    user = {"role":"user","content":str(items)}
    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system, user],
        max_tokens=800,
        temperature=0.7
    )
    return jsonify(formatted_news=resp.choices[0].message.content)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
