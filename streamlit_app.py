import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import base64
import requests
import json
from datetime import datetime
import re

# ── 页面配置 ──────────────────────────────────────────────
st.set_page_config(
    page_title="黄金记账",
    page_icon="🥇",
    layout="centered"
)

# ── 密码保护 ──────────────────────────────────────────────
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🥇 黄金记账")
        pwd = st.text_input("请输入访问密码", type="password")
        if st.button("登录"):
            if pwd == st.secrets["APP_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密码错误")
        return False
    return True

if not check_password():
    st.stop()

# ── 连接 Google Sheets ────────────────────────────────────
@st.cache_resource
def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    client = gspread.authorize(creds)
    sheet = client.open("黄金交易记录").sheet1
    return sheet

# ── 用 Gemini 识别截图 ────────────────────────────────────
def extract_from_image(image_bytes):
    api_key = st.secrets["GEMINI_API_KEY"]
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """请从这张基金交易截图中提取以下字段，严格按JSON格式返回，不要任何多余文字：
{
  "订单号": "",
  "申请时间": "",
  "标的名称": "",
  "实际支付_元": 0,
  "确认份额_份": 0,
  "手续费_元": 0,
  "折算克数_克": 0,
  "成交金价_元每克": 0
}
注意：
- 申请时间格式：YYYY-MM-DD HH:MM:SS
- 成交金价只取数字部分，去掉日期括号
- 手续费如果是0.00则填0
- 所有数值字段返回数字类型，不要带单位"""

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}}
            ]
        }]
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()

    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    raw = re.sub(r"```json|```", "", raw).strip()
    return json.loads(raw)

# ── 计算累计字段 ──────────────────────────────────────────
def calc_cumulative(sheet, new_payment, new_grams, realtime_price):
    records = sheet.get_all_values()
    data_rows = [r for r in records[1:] if r[0]]

    prev_total_payment = 0.0
    prev_total_grams = 0.0

    for row in data_rows:
        try:
            prev_total_payment += float(row[3]) if row[3] else 0
        except:
            pass
        try:
            prev_total_grams += float(row[6]) if row[6] else 0
        except:
            pass

    cum_payment = prev_total_payment + new_payment
    cum_grams = prev_total_grams + new_grams
    avg_price = round(cum_payment / cum_grams, 4) if cum_grams > 0 else 0
    market_value = round(cum_grams * realtime_price, 2) if realtime_price else ""

    return cum_payment, cum_grams, avg_price, market_value

# ── 主界面 ────────────────────────────────────────────────
st.title("🥇 黄金记账")
st.caption("上传交易截图，自动识别并写入 Google 表格")

uploaded = st.file_uploader("上传交易截图", type=["jpg", "jpeg", "png"])

if uploaded:
    st.image(uploaded, caption="已上传截图", use_container_width=True)

    with st.spinner("Gemini 识别中..."):
        try:
            data = extract_from_image(uploaded.read())
            st.success("识别成功，请核对以下数据：")
        except Exception as e:
            st.error(f"识别失败：{e}")
            st.stop()

    with st.form("confirm_form"):
        st.subheader("核对识别结果")

        order_id    = st.text_input("订单号",       value=str(data.get("订单号", "")))
        apply_time  = st.text_input("申请时间",     value=str(data.get("申请时间", "")))
        fund_name   = st.text_input("标的名称",     value=str(data.get("标的名称", "")))
        payment     = st.number_input("实际支付_元",    value=float(data.get("实际支付_元", 0)), format="%.2f")
        shares      = st.number_input("确认份额_份",    value=float(data.get("确认份额_份", 0)), format="%.4f")
        fee         = st.number_input("手续费_元",      value=float(data.get("手续费_元", 0)), format="%.2f")
        grams       = st.number_input("折算克数_克",    value=float(data.get("折算克数_克", 0)), format="%.4f")
        trade_price = st.number_input("成交金价_元/克", value=float(data.get("成交金价_元每克", 0)), format="%.4f")
        realtime_price = st.number_input(
            "记账时实时金价_元/克（手动输入当前市价）",
            value=float(data.get("成交金价_元每克", 0)),
            format="%.4f"
        )

        submitted = st.form_submit_button("✅ 确认并写入表格")

    if submitted:
        with st.spinner("写入 Google 表格..."):
            try:
                sheet = get_sheet()
                cum_payment, cum_grams, avg_price, market_value = calc_cumulative(
                    sheet, payment, grams, realtime_price
                )

                row = [
                    order_id,
                    apply_time,
                    fund_name,
                    payment,
                    shares,
                    fee,
                    grams,
                    trade_price,
                    realtime_price,
                    round(cum_payment, 2),
                    round(cum_grams, 4),
                    avg_price,
                    market_value
                ]
                sheet.append_row(row, value_input_option="USER_ENTERED")
                st.success("🎉 已成功写入表格！")
                st.balloons()

            except Exception as e:
                st.error(f"写入失败：{e}")
