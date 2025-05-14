# analytics_view.py

import pandas as pd
from datetime import datetime

# 1) 로그 CSV 경로
LOG_CSV = "data/analytics_log.csv"

# 2) 파일 읽기 (한글 깨짐 방지)
df = pd.read_csv(LOG_CSV, encoding="utf-8-sig", parse_dates=["timestamp"])

# 3) 오늘 날짜 필터
today = datetime.utcnow().date()
today_df = df[df["timestamp"].dt.date == today]

# 4) 콘솔에 출력
pd.set_option("display.max_rows", None)   # 전체 행 보이도록
print(f"=== {today.isoformat()} 요청 로그 ({len(today_df)}건) ===")
print(today_df)

# 5) 엑셀로도 저장
OUT_XLSX = "analytics_log_today.xlsx"
today_df.to_excel(OUT_XLSX, index=False)
print(f"{OUT_XLSX} 파일이 생성되었습니다.")
