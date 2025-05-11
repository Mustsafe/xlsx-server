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

# 로거 설정
logging.basicConfig(level=logging.INFO, format="%((asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
openai.api_key = os.getenv("OPENAI_API_KEY")
DATA_DIR = "./data"
os.makedirs(DATA_DIR, exist_ok=True)

# 템플릿 매핑 유틸
def sanitize(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())

def build_alias_map(templates: List[str]) -> dict:
    alias = {}
    for tpl in templates:
        key = sanitize(tpl)
        alias[key] = tpl
    return alias

def resolve_keyword(raw: str, templates: List[str], alias_map: dict, freq: dict) -> str:
    key = sanitize(re.sub(r"\s*(?:양식|서식)(?:을|를)?$", "", raw))
    if key in alias_map:
        return alias_map[key]
    raise ValueError(f"템플릿 '{raw}'을(를) 찾을 수 없습니다.")

@app.route("/create_xlsx", methods=["GET"])
def create_xlsx():
    try:
        raw = request.args.get("template", "")
        path = os.path.join(DATA_DIR, "통합_노지파일.csv")
        df = pd.read_csv(path, encoding="utf-8-sig")
        templates = df["템플릿명"].dropna().unique().tolist()
        alias_map = build_alias_map(templates)
        freq = df["템플릿명"].value_counts().to_dict()
        try:
            tpl = resolve_keyword(raw, templates, alias_map, freq)
            out_df = df[df["템플릿명"]==tpl][["작업 항목","작성 양식","실무 예시 1","실무 예시 2"]].copy()
        except ValueError:
            tpl = raw
            system = {"role":"system","content":"...
