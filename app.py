from flask import Flask, request, send_file, jsonify
import pandas as pd
import os
import requests
from bs4 import BeautifulSoup
import openai
from dateutil import parser
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 깨짐 방지

openai.api_key = os.getenv("OPENAI_API_KEY")

DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

from flask import send_from_directory

# 플러그인 매니페스트 서빙
@app.route("/.well-known/<path:filename>")
def serve_well_known(filename):
    return send_from_directory(
        os.path.join(app.root_path, "static", ".well-known"),
        filename,
        mimetype="application/json"
    )

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

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# 🔥 키워드 통합 매핑 (표준화)
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

def resolve_keyword(raw_keyword: str) -> str:
    for alias, std in KEYWORD_ALIAS.items():
        if alias in raw_keyword:
            return std
    return raw_keyword

@app.route("/", methods=["GET"])
def index():
    return "📰 사용 가능한 엔드포인트: /daily_news, /render_news, /create_xlsx", 200

# 🔥 XLSX 생성 (통합 CSV 기반으로)
@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    raw = request.args.get("template", "")
    tpl = resolve_keyword(raw)

    csv_path = os.path.join(DATA_DIR, "통합_노지파일.csv")
    if not os.path.exists(csv_path):
        return {"error": "통합 노지 파일이 존재하지 않습니다."}, 404

    df = pd.read_csv(csv_path)

    # '양식명' 컬럼에서 tpl과 일치하는 항목만 필터
    target = df[df["양식명"] == tpl]

    if target.empty:
        return {"error": f"요청한 양식 '{tpl}'을(를) 찾을 수 없습니다."}, 400

    # 필요한 열만 남김
    columns_to_keep = ["작업 항목", "작성 양식", "실무 예시"]
    target = target[columns_to_keep]

    # 하단에 출처 추가
    target.loc[len(target)] = [f"※ 본 양식은 {tpl} 관련 법령 또는 지침을 기반으로 작성되었습니다.", "", ""]

    # 엑셀 파일 저장
    xlsx_path = os.path.join(DATA_DIR, f"{tpl}_최종양식.xlsx")
    target.to_excel(xlsx_path, index=False)

    return send_file(xlsx_path, as_attachment=True, download_name=f"{tpl}.xlsx")

# 여기부터 뉴스 관련 기존 함수 그대로 (생략 가능, 필요하면 이어서 붙여줄게)

