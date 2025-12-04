
import streamlit as st
import pandas as pd
from datetime import datetime
import uuid
import html
import io

# UI Setup
st.set_page_config(page_title="BoA to QFX Converter", layout="centered")
st.title("Bank of America CSV to QFX Converter")
st.write("Upload your Bank of America CSV file below. This tool will convert it into a QFX file compatible with Quicken and MoneyGrit.")

# File upload
uploaded_file = st.file_uploader("Upload Bank of America CSV", type=["csv"])

# Utility functions
def normalize_amount(amount):
    cleaned = str(amount).replace("$", "").replace(",", "").replace("(", "-").replace(")", "").strip()
    return float(cleaned)

def detect_account_type(filename):
    if "cc" in filename.lower():
        return "CREDIT"
    return "CHECKING"

def parse_bofa_csv(file) -> pd.DataFrame:
    df = pd.read_csv(file)
    df = df.rename(columns=lambda x: x.strip())

    # Identify likely column names
    date_col = next((col for col in df.columns if "date" in col.lower()), None)
    desc_col = next((col for col in df.columns if "payee" in col.lower() or "description" in col.lower()), None)
    amount_col = next((col for col in df.columns if "amount" in col.lower()), None)

    if not all([date_col, desc_col, amount_col]):
        st.error("Couldn't identify necessary columns (Date, Description, Amount)")
        return None

    df = df[[date_col, desc_col, amount_col]]
    df.columns = ["Date", "Description", "Amount"]

    df["Amount"] = df["Amount"].apply(normalize_amount)
    df["Date"] = pd.to_datetime(df["Date"], errors='coerce')
    df = df.dropna(subset=["Date"])
    df["Date"] = df["Date"].dt.strftime('%Y%m%d')
    return df

def generate_qfx(df, account_type):
    timezone = "[-8]"
    now = datetime.now()
    dtserver = now.strftime('%Y%m%d%H%M%S') + ".000" + timezone

    header = f"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0
<SEVERITY>INFO
<MESSAGE>OK
</STATUS>
<DTSERVER>{dtserver}
<LANGUAGE>ENG
<INTU.BID>69487
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>0
<STATUS>
<CODE>0
<SEVERITY>INFO
<MESSAGE>OK
</STATUS>
<STMTRS>
<CURDEF>USD
<BANKACCTFROM>
<BANKID>026005092
<ACCTID>10000001
<ACCTTYPE>{account_type}
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>{df['Date'].min()}130000.000{timezone}
<DTEND>{df['Date'].max()}130000.000{timezone}
"""

    transactions = ""
    for _, row in df.iterrows():
        trntype = "DEBIT" if row['Amount'] < 0 else "CREDIT"
        date_posted = row['Date'] + "130000.000" + timezone
        fitid = "R" + uuid.uuid4().hex[:16]
        name = html.escape(str(row['Description'])[:100])
        amount = f"{row['Amount']:.2f}"

        transactions += f"""<STMTTRN>
<TRNTYPE>{trntype}
<DTPOSTED>{date_posted}
<TRNAMT>{amount}
<FITID>{fitid}
<NAME>{name}
</STMTTRN>
"""

    footer = f"""</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>0.00
<DTASOF>{now.strftime('%Y%m%d')}130000.000{timezone}
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""

    return header + transactions + footer

# Main logic
if uploaded_file:
    account_type = detect_account_type(uploaded_file.name)
    df = parse_bofa_csv(uploaded_file)
    if df is not None:
        qfx_data = generate_qfx(df, account_type)
        qfx_filename = uploaded_file.name.replace('.csv', '.qfx')
        st.download_button(
            label="Download QFX File",
            data=qfx_data,
            file_name=qfx_filename,
            mime="application/x-qfx"
        )
