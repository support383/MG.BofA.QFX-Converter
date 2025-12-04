import streamlit as st
import pandas as pd
import io
from datetime import datetime

def detect_delimiter(sample):
    if '\t' in sample:
        return '\t'
    elif ';' in sample:
        return ';'
    else:
        return ','

def find_transaction_header(df):
    for i, row in df.iterrows():
        row_lower = row.astype(str).str.lower().tolist()
        if any("date" in col for col in row_lower) and any("amount" in col for col in row_lower):
            return i
    return None

def parse_bofa_csv(file):
    content = file.read().decode("utf-8", errors="ignore")
    delimiter = detect_delimiter(content[:500])
    buffer = io.StringIO(content)

    try:
        raw_df = pd.read_csv(buffer, delimiter=delimiter, header=None, skip_blank_lines=False)
    except Exception as e:
        raise ValueError("Unable to read file. Ensure it is a valid CSV from BofA.") from e

    header_row_index = find_transaction_header(raw_df)
    if header_row_index is None:
        raise ValueError("Could not find the transaction header row in the file.")

    df = pd.read_csv(io.StringIO(content), delimiter=delimiter, skiprows=header_row_index)
    df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
    return df

def convert_to_qfx(df, account_type='CHECKING'):
    now = datetime.now().strftime('%Y%m%d%H%M%S')
    fid = "10898"
    org = "BofA"
    account_id = "000000000000"
    bankid = "000000000"

    qfx = [
        "OFXHEADER:100",
        "DATA:OFXSGML",
        "VERSION:102",
        "SECURITY:NONE",
        "ENCODING:USASCII",
        "CHARSET:1252",
        "COMPRESSION:NONE",
        "OLDFILEUID:NONE",
        "NEWFILEUID:NONE",
        "",
        "<OFX>",
        f"  <SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>{now}<LANGUAGE>ENG<FI><ORG>{org}<FID>{fid}</FI></SONRS></SIGNONMSGSRSV1>",
        f"  <BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS><STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>{bankid}<ACCTID>{account_id}<ACCTTYPE>{account_type}</BANKACCTFROM><BANKTRANLIST><DTSTART>{now}<DTEND>{now}"
    ]

    for i, row in df.iterrows():
        try:
            date = pd.to_datetime(row.get("date") or row.get("posted_date"), errors='coerce')
            if pd.isna(date):
                continue
            date_str = date.strftime('%Y%m%d')
            amount = float(str(row['amount']).replace(",", "").strip())
            name = str(row.get("description") or row.get("name") or "N/A").strip()
            memo = str(row.get("memo") or name)

            qfx.append("<STMTTRN>")
            qfx.append("<TRNTYPE>OTHER")
            qfx.append(f"<DTPOSTED>{date_str}")
            qfx.append(f"<TRNAMT>{amount:.2f}")
            qfx.append(f"<FITID>{i+1}")
            qfx.append(f"<NAME>{name[:32]}")
            qfx.append(f"<MEMO>{memo[:255]}")
            qfx.append("</STMTTRN>")
        except Exception:
            continue

    qfx.append("</BANKTRANLIST><LEDGERBAL><BALAMT>0.00<DTASOF>{}</LEDGERBAL></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>".format(now))
    return "\n".join(qfx)

st.set_page_config(page_title="BofA to QFX Converter", layout="centered")
st.title("Convert Bank of America CSV/Excel to QFX")
st.markdown("Upload a BofA file (CSV or Excel) to convert it to a QFX format for import into MoneyGrit or Quicken.")

uploaded_file = st.file_uploader("Upload a Bank of America CSV or Excel file", type=["csv", "xls", "xlsx"])

if uploaded_file:
    try:
        df = parse_bofa_csv(uploaded_file)
        st.success("File parsed successfully. Preview below:")
        st.dataframe(df.head())

        qfx_content = convert_to_qfx(df)
        st.download_button("Download QFX File", data=qfx_content, file_name="transactions.qfx", mime="application/qfx")

    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
