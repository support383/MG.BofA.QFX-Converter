import streamlit as st
import pandas as pd
from io import StringIO
import uuid
from datetime import datetime

def extract_transactions(file):
    # Read the entire file content as text
    content = file.read().decode('utf-8')
    lines = content.splitlines()

    # Find the header line
    header_line_index = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Date,Description,Amount"):
            header_line_index = i
            break

    if header_line_index is None:
        st.error("Could not find the transaction header row in the file.")
        return None

    # Re-parse the CSV from the header line onward
    trimmed_csv = "\n".join(lines[header_line_index:])
    df = pd.read_csv(StringIO(trimmed_csv))

    # Clean column names and return
    df.columns = [col.strip() for col in df.columns]
    return df

def convert_to_qfx(df):
    now = datetime.now()
    dtserver = now.strftime('%Y%m%d%H%M%S')
    fid = 10898
    org = "BofA"
    bank_id = "123456789"
    account_id = str(uuid.uuid4().int)[:10]
    
    qfx = ["<OFX>", "<BANKMSGSRSV1>", "<STMTTRNRS>", "<TRNUID>1", "<STATUS><CODE>0<SEVERITY>INFO</STATUS>"]
    qfx += [f"<STMTRS><CURDEF>USD<BANKACCTFROM><BANKID>{bank_id}<ACCTID>{account_id}<ACCTTYPE>CHECKING</BANKACCTFROM>"]
    qfx += [f"<BANKTRANLIST><DTSTART>{dtserver}<DTEND>{dtserver}"]

    for _, row in df.iterrows():
        date_str = pd.to_datetime(row['Date']).strftime('%Y%m%d')
        amount = row['Amount']
        memo = row['Description']
        trntype = 'CREDIT' if amount > 0 else 'DEBIT'
        qfx.append(f"<STMTTRN><TRNTYPE>{trntype}<DTPOSTED>{date_str}<TRNAMT>{amount}<FITID>{uuid.uuid4().hex[:10]}<NAME>{memo[:32]}</STMTTRN>")

    qfx += ["</BANKTRANLIST>", f"<LEDGERBAL><BALAMT>{df['Running Bal.'].iloc[-1]}<DTASOF>{dtserver}</LEDGERBAL>", "</STMTRS>", "</STMTTRNRS>", "</BANKMSGSRSV1>", "</OFX>"]
    return "".join(qfx)

# Streamlit app
st.title("Convert Bank of America CSV/Excel to QFX")
st.markdown("Upload a BofA file (CSV or Excel) to convert it to a QFX format for import into MoneyGrit or Quicken.")

uploaded_file = st.file_uploader("Upload a Bank of America CSV or Excel file", type=['csv', 'xls', 'xlsx'])
if uploaded_file:
    try:
        df = extract_transactions(uploaded_file)
        if df is not None:
            qfx_data = convert_to_qfx(df)
            st.download_button("Download QFX", data=qfx_data, file_name="transactions.qfx", mime="application/qfx")
    except Exception as e:
        st.error(f"Error processing file: {e}")
