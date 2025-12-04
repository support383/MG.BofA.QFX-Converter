import streamlit as st
import pandas as pd
import io
import uuid
from datetime import datetime

# --- Helper Functions ---
def is_credit_card_file(df):
    memo_col = df.columns.str.contains("memo", case=False).argmax()
    if df.shape[0] == 0:
        return False
    sample_memo = str(df.iloc[:, memo_col].astype(str).str.lower().head(10).tolist())
    return any(keyword in sample_memo for keyword in ["payment", "purchase", "authorization"])

def parse_bofa_csv(file):
    try:
        content = file.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        df = pd.read_csv(io.StringIO(content))
    except Exception:
        file.seek(0)
        df = pd.read_excel(file)
    return df

def normalize_columns(df):
    df.columns = df.columns.str.strip().str.lower()
    return df

def create_qfx(df, account_id, is_credit):
    df = normalize_columns(df)

    date_col = df.columns[df.columns.str.contains("date")].tolist()[0]
    amount_col = df.columns[df.columns.str.contains("amount")].tolist()[0]
    memo_col = df.columns[df.columns.str.contains("memo")].tolist()[0]
    payee_col = df.columns[df.columns.str.contains("description|payee|name")].tolist()[0]

    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(by=date_col)

    qfx = ["<OFX>", "<BANKMSGSRSV1>", "<STMTTRNRS>", "<TRNUID>1", "<STATUS>", "<CODE>0", "<SEVERITY>INFO", "</STATUS>", "<STMTRS>"]
    qfx.append("<CURDEF>USD")
    qfx.append(f"<BANKACCTFROM><BANKID>012345678<ACCTID>{account_id}<ACCTTYPE>CHECKING</BANKACCTFROM>")
    qfx.append("<BANKTRANLIST>")
    qfx.append(f"<DTSTART>{df[date_col].iloc[0].strftime('%Y%m%d')}\n<DTEND>{df[date_col].iloc[-1].strftime('%Y%m%d')}")

    for _, row in df.iterrows():
        dtposted = row[date_col].strftime('%Y%m%d')
        amount = row[amount_col]
        if is_credit:
            amount = -amount  # reverse for CC
        trntype = "CREDIT" if amount > 0 else "DEBIT"
        memo = str(row[memo_col])
        name = str(row[payee_col])
        fitid = str(uuid.uuid4().int)[:12]

        qfx.append("<STMTTRN>")
        qfx.append(f"<TRNTYPE>{trntype}")
        qfx.append(f"<DTPOSTED>{dtposted}")
        qfx.append(f"<TRNAMT>{amount:.2f}")
        qfx.append(f"<FITID>{fitid}")
        qfx.append(f"<NAME>{name[:32]}")
        qfx.append(f"<MEMO>{memo[:255]}")
        qfx.append("</STMTTRN>")

    qfx.append("</BANKTRANLIST>")
    qfx.append("</STMTRS>")
    qfx.append("</STMTTRNRS>")
    qfx.append("</BANKMSGSRSV1>")
    qfx.append("</OFX>")

    return "\n".join(qfx)

# --- Streamlit App ---
st.set_page_config(page_title="BofA to QFX Converter", layout="centered")
st.title("Convert Bank of America CSV/Excel to QFX")

st.markdown("Upload a BofA file (CSV or Excel) to convert it to a QFX format for import into MoneyGrit or Quicken.")

uploaded_file = st.file_uploader("Upload a Bank of America CSV or Excel file", type=["csv", "xls", "xlsx"])

if uploaded_file:
    try:
        df = parse_bofa_csv(uploaded_file)
        if df.shape[0] == 0:
            st.error("The file appears to be empty.")
        else:
            is_credit = is_credit_card_file(df)
            account_id = str(uuid.uuid4().int)[:10]
            qfx_data = create_qfx(df, account_id, is_credit)

            st.success("File converted successfully!")
            st.download_button(
                label="Download QFX File",
                data=qfx_data,
                file_name="converted.qfx",
                mime="application/qfx"
            )
    except Exception as e:
        st.error(f"Error processing file: {e}\nPlease make sure it's a valid CSV or Excel file from Bank of America.")
