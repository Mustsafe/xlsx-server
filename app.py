from flask import Flask, request, send_file
import pandas as pd
import os

app = Flask(__name__)

@app.route('/convert', methods=['POST'])
def convert_csv_to_xlsx():
    if 'file' not in request.files:
        return "파일이 필요합니다", 400

    file = request.files['file']
    filename = file.filename
    if not filename.endswith('.csv'):
        return "CSV 파일만 허용됩니다", 400

    df = pd.read_csv(file)
    xlsx_path = f"/tmp/{os.path.splitext(filename)[0]}.xlsx"
    df.to_excel(xlsx_path, index=False)

    return send_file(xlsx_path, as_attachment=True)

@app.route('/')
def home():
    return "CSV → XLSX 변환 서버 정상 작동 중입니다."

if __name__ == '__main__':
    app.run(debug=True)
