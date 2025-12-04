import streamlit as st
import pandas as pd
import io
import uuid
from datetime import datetime
from xml.sax.saxutils import escape
import csv

st.set_page_config(page_title="BofA to QFX Converter", page_icon="🏦", layout="centered")
st.title("Convert Bank of America CSV/Excel to QFX")

st.markdown("""
Upload a BofA file (CSV or Excel) to convert it to a QFX format for import into MoneyGrit or Quicken.
""")

uploaded_file = st.file_uploader("Upload a Bank of America CSV or Excel file", type=["csv", "xls", "xlsx"])

def parse_bofa_csv(file):
    raw = file.read()

    # Try multiple encodings
    for enc in ["utf-8-sig", "utf-8", "latin1", "windows-1252"]:
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue

    # Strip BOM, blank lines, junk
    cleaned = "\n".join([line for line in text.splitlines() if line.strip()])

    # Sniff delimiter
    dialect = csv.Sniffer().sniff(cleaned, delimiters=[",", ";", "\t", "|"])

    df = pd.read_csv(io.StringIO(cleaned), delimiter=dialect.delimiter)
    return df

def detect_account_type(df):
    memo_text = " ".join(df.get("Description", "").astype(str).tolist()).lower()
    if "payment" in memo_text or "purchase" in memo_text:
        return "CREDIT"
    else:
        return "CHECKING"

def convert_to_qfx(df, account_type):
    qfx_template = """
<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>{uid}</TRNUID>
<STATUS>
<CODE>0</CODE>
<SEVERITY>INFO</SEVERITY>
</STATUS>
<STMTRS>
<CURDEF>USD</CURDEF>
<BANKACCTFROM>
<BANKID>000000000</BANKID>
<ACCTID>000000000000</ACCTID>
<ACCTTYPE>{account_type}</ACCTTYPE>
</BANKACCTFROM>
<BANKTRANLIST>
{transactions}
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    txn_template = """
<STMTTRN>
<TRNTYPE>{type}</TRNTYPE>
<DTPOSTED>{date}</DTPOSTED>
<TRNAMT>{amount}</TRNAMT>
<FITID>{fitid}</FITID>
<NAME>{name}</NAME>
<MEMO>{memo}</MEMO>
</STMTTRN>
"""

    transactions = ""
    for i, row in df.iterrows():
        amount = float(row["Amount"])
        txn_type = "CREDIT" if amount > 0 else "DEBIT"
        date = pd.to_datetime(row["Date"]).strftime("%Y%m%d")
        memo = escape(str(row.get("Description", "")))
        fitid = str(uuid.uuid4().int)[:10]
        name = memo[:32]

        transactions += txn_template.format(
            type=txn_type,
            date=date,
            amount=amount,
            fitid=fitid,
            name=name,
            memo=memo
        )

    qfx_data = qfx_template.format(
        uid=str(uuid.uuid4()),
        account_type=account_type,
        transactions=transactions
    )
    return qfx_data

if uploaded_file:
    try:
        if uploaded_file.name.lower().endswith((".xls", ".xlsx")):
            df = pd.read_excel(uploaded_file)
        else:
            df = parse_bofa_csv(uploaded_file)

        required_columns = ["Date", "Description", "Amount"]
        if not all(col in df.columns for col in required_columns):
            st.error(f"Missing required columns: {', '.join([c for c in required_columns if c not in df.columns])}")
        else:
            df = df[required_columns].dropna()
            account_type = detect_account_type(df)
            qfx_data = convert_to_qfx(df, account_type)
            st.success("File converted successfully!")

            st.download_button(
                label="Download QFX File",
                data=qfx_data,
                file_name="converted.qfx",
                mime="application/vnd.intu.qfx"
            )
    except Exception as e:
        st.error("Error processing file: " + str(e))
