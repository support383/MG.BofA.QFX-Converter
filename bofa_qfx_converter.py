import streamlit as st
import pandas as pd
import io
from datetime import datetime

st.set_page_config(page_title="BofA to QFX Converter", layout="centered")
st.title("Converter")
st.markdown("""
Convert your BofA Excel or CSV transaction files into .QFX format for import into Quicken or MoneyGrit.
""")

uploaded_file = st.file_uploader("Upload a Bank of America CSV or Excel file", type=["csv", "xls", "xlsx"])

@st.cache_data

def parse_bofa_csv(file):
    try:
        df = pd.read_csv(file)
    except Exception:
        try:
            df = pd.read_csv(file, engine='python')
        except Exception:
            try:
                df = pd.read_excel(file)
            except Exception as e:
                raise ValueError("Unable to read file. Please make sure it's a valid CSV or Excel file.") from e

    # Normalize headers
    df.columns = [str(c).strip().lower() for c in df.columns]

    if 'amount' not in df.columns:
        raise ValueError("Missing 'Amount' column in the file.")

    # Determine direction from amount or separate columns
    if 'withdrawal' in df.columns or 'deposit' in df.columns:
        df['amount'] = df.get('deposit', 0).fillna(0) - df.get('withdrawal', 0).fillna(0)
    
    # Required columns
    date_col = next((c for c in df.columns if 'date' in c), None)
    name_col = next((c for c in df.columns if 'payee' in c or 'description' in c or 'name' in c), None)
    memo_col = next((c for c in df.columns if 'memo' in c), name_col)

    if not all([date_col, name_col]):
        raise ValueError("Missing required columns. Ensure your file includes date and payee/description columns.")

    df['date'] = pd.to_datetime(df[date_col], errors='coerce')
    df = df.dropna(subset=['date'])

    df['name'] = df[name_col].astype(str).str.strip()
    df['memo'] = df[memo_col].astype(str).str.strip() if memo_col else df['name']

    df = df[['date', 'name', 'memo', 'amount']]
    df = df.sort_values(by='date')
    return df

def convert_to_qfx(df):
    qfx_output = io.StringIO()
    fid = 10898  # example: BofA ID for Quicken
    bank_id = "123456789"
    acct_id = "000000000000"
    dtstart = df['date'].min().strftime('%Y%m%d')
    dtend = df['date'].max().strftime('%Y%m%d')

    header = f"""
<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<DTSERVER>{datetime.now().strftime('%Y%m%d%H%M%S')}<LANGUAGE>ENG
<FI><ORG>Bank of America<FID>{fid}</FI>
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS>
<CURDEF>USD
<BANKACCTFROM><BANKID>{bank_id}<ACCTID>{acct_id}<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>{dtstart}<DTEND>{dtend}
"""
    qfx_output.write(header)

    for idx, row in df.iterrows():
        dtposted = row['date'].strftime('%Y%m%d')
        amount = f"{row['amount']:.2f}"
        name = row['name'][:32]
        memo = row['memo'][:255]
        fitid = f"{dtposted}{idx}"
        qfx_output.write(f"<STMTTRN>\n<TRNTYPE>OTHER\n<DTPOSTED>{dtposted}\n<TRNAMT>{amount}\n<FITID>{fitid}\n<NAME>{name}\n<MEMO>{memo}\n</STMTTRN>\n")

    footer = """
</BANKTRANLIST>
<LEDGERBAL><BALAMT>0.00<DTASOF>{dtend}</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""
    qfx_output.write(footer)
    return qfx_output.getvalue()

if uploaded_file:
    try:
        df = parse_bofa_csv(uploaded_file)
        qfx_data = convert_to_qfx(df)
        st.success("File converted successfully!")
        st.download_button("Download QFX File", qfx_data, file_name="converted.qfx", mime="application/qfx")
    except Exception as e:
        st.error(f"Error processing file: {str(e)}")
