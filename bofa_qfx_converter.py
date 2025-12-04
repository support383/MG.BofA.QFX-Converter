import streamlit as st
import pandas as pd
import io
import uuid
from datetime import datetime

# ----------------------------
# QFX Formatting Helpers
# ----------------------------

def sanitize_amount(value):
    value = str(value).replace(",", "").replace("$", "").replace("NZ$", "").replace("€", "").replace("£", "").strip()
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    try:
        return float(value)
    except ValueError:
        return 0.0

def format_date(date_str):
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y%m%d")
        except Exception:
            continue
    return "00000000"  # fallback invalid date

# ----------------------------
# Parse CSV or Excel
# ----------------------------

def parse_bofa_csv(file):
    try:
        df = pd.read_csv(file)
    except pd.errors.ParserError:
        file.seek(0)
        df = pd.read_excel(file)
    return df

# ----------------------------
# Extract Transactions
# ----------------------------

def extract_transactions(df):
    col_map = {
        "date": ["date", "transaction date", "posted date"],
        "desc": ["description", "details", "name (payee/r)"],
        "amount": ["amount"],
        "memo": ["memo", "note"]
    }

    df.columns = [col.strip().lower() for col in df.columns]

    def find_col(possible_names):
        for name in possible_names:
            for col in df.columns:
                if name in col:
                    return col
        return None

    date_col = find_col(col_map["date"])
    desc_col = find_col(col_map["desc"])
    amount_col = find_col(col_map["amount"])
    memo_col = find_col(col_map["memo"])

    if not (date_col and desc_col and amount_col):
        st.error("Missing required columns in uploaded file.")
        return []

    transactions = []
    for _, row in df.iterrows():
        date = format_date(str(row[date_col]))
        desc = str(row[desc_col])[:80]
        amount = sanitize_amount(row[amount_col])
        memo = str(row[memo_col])[:200] if memo_col else desc

        transactions.append({
            "date": date,
            "desc": desc,
            "amount": amount,
            "memo": memo,
            "fitid": str(uuid.uuid4()).replace("-", "")[:10]
        })

    return transactions

# ----------------------------
# Build QFX File
# ----------------------------

def build_qfx(transactions):
    header = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
<DTSERVER>{date}</DTSERVER>
<LANGUAGE>ENG</LANGUAGE>
<FI><ORG>BOFA</ORG><FID>123456789</FID></FI>
</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>0</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
<STMTRS><CURDEF>USD</CURDEF><BANKACCTFROM><BANKID>123456789</BANKID><ACCTID>000000000</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM><BANKTRANLIST>"""

    footer = "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
    body = ""
    today = datetime.now().strftime("%Y%m%d")

    for txn in transactions:
        body += f"""
<STMTTRN>
<TRNTYPE>{'DEBIT' if txn['amount'] < 0 else 'CREDIT'}</TRNTYPE>
<DTPOSTED>{txn['date']}</DTPOSTED>
<TRNAMT>{txn['amount']}</TRNAMT>
<FITID>{txn['fitid']}</FITID>
<NAME>{txn['desc']}</NAME>
<MEMO>{txn['memo']}</MEMO>
</STMTTRN>"""

    return header.format(date=today) + body + footer

# ----------------------------
# Streamlit App
# ----------------------------

st.set_page_config(page_title="BofA QFX Converter", layout="centered")
st.title("🔁 Bank of America to QFX Converter")
st.caption("Convert your BofA Excel or CSV transaction files into .QFX format for import into Quicken or MoneyGrit.")

uploaded_file = st.file_uploader("Upload a Bank of America CSV or Excel file", type=["csv", "xls", "xlsx"])

if uploaded_file:
    try:
        df = parse_bofa_csv(uploaded_file)
        transactions = extract_transactions(df)

        if transactions:
            qfx_data = build_qfx(transactions)
            qfx_bytes = io.BytesIO(qfx_data.encode("utf-8"))

            st.success(f"{len(transactions)} transactions processed.")
            st.download_button(
                label="📥 Download QFX File",
                data=qfx_bytes,
                file_name="transactions.qfx",
                mime="application/x-qfx"
            )
    except Exception as e:
        st.error(f"Error processing file: {e}")
