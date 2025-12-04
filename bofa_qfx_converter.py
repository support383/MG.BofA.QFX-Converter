import streamlit as st
import pandas as pd
import io
from datetime import datetime

def detect_delimiter(sample):
    """Detect the delimiter used in CSV file"""
    if '\t' in sample:
        return '\t'
    elif ';' in sample:
        return ';'
    else:
        return ','

def find_transaction_header(df):
    """Find the row containing transaction column headers"""
    for i, row in df.iterrows():
        row_lower = row.astype(str).str.lower().tolist()
        if any("date" in col for col in row_lower) and any("amount" in col for col in row_lower):
            return i
    return None

def parse_bofa_file(file):
    """Parse BofA CSV or Excel file"""
    file_type = file.name.split('.')[-1].lower()
    
    # Handle Excel files
    if file_type in ['xls', 'xlsx']:
        try:
            # Try reading Excel file
            raw_df = pd.read_excel(file, header=None)
            header_row_index = find_transaction_header(raw_df)
            
            if header_row_index is None:
                raise ValueError("Could not find the transaction header row in the Excel file.")
            
            # Re-read with proper header
            file.seek(0)
            df = pd.read_excel(file, skiprows=header_row_index)
            df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
            return df
            
        except Exception as e:
            raise ValueError(f"Unable to read Excel file: {str(e)}")
    
    # Handle CSV files
    else:
        content = file.read().decode("utf-8", errors="ignore")
        delimiter = detect_delimiter(content[:500])
        buffer = io.StringIO(content)
        
        try:
            raw_df = pd.read_csv(buffer, delimiter=delimiter, header=None, skip_blank_lines=False)
        except Exception as e:
            raise ValueError(f"Unable to read CSV file: {str(e)}")
        
        header_row_index = find_transaction_header(raw_df)
        if header_row_index is None:
            raise ValueError("Could not find the transaction header row in the CSV file.")
        
        df = pd.read_csv(io.StringIO(content), delimiter=delimiter, skiprows=header_row_index)
        df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
        return df

def convert_to_qfx(df, account_type='CHECKING'):
    """Convert DataFrame to QFX format"""
    now = datetime.now().strftime('%Y%m%d%H%M%S')
    fid = "10898"
    org = "BofA"
    account_id = "000000000000"
    bankid = "000000000"
    
    # QFX header
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
        "  <SIGNONMSGSRSV1>",
        "    <SONRS>",
        "      <STATUS>",
        "        <CODE>0</CODE>",
        "        <SEVERITY>INFO</SEVERITY>",
        "      </STATUS>",
        f"      <DTSERVER>{now}</DTSERVER>",
        "      <LANGUAGE>ENG</LANGUAGE>",
        "      <FI>",
        f"        <ORG>{org}</ORG>",
        f"        <FID>{fid}</FID>",
        "      </FI>",
        "    </SONRS>",
        "  </SIGNONMSGSRSV1>",
        "  <BANKMSGSRSV1>",
        "    <STMTTRNRS>",
        "      <TRNUID>1</TRNUID>",
        "      <STATUS>",
        "        <CODE>0</CODE>",
        "        <SEVERITY>INFO</SEVERITY>",
        "      </STATUS>",
        "      <STMTRS>",
        "        <CURDEF>USD</CURDEF>",
        "        <BANKACCTFROM>",
        f"          <BANKID>{bankid}</BANKID>",
        f"          <ACCTID>{account_id}</ACCTID>",
        f"          <ACCTTYPE>{account_type}</ACCTTYPE>",
        "        </BANKACCTFROM>",
        "        <BANKTRANLIST>",
        f"          <DTSTART>{now}</DTSTART>",
        f"          <DTEND>{now}</DTEND>",
    ]
    
    # Process transactions
    transaction_count = 0
    for i, row in df.iterrows():
        try:
            # Try different possible date column names
            date_value = row.get("date") or row.get("posted_date") or row.get("transaction_date")
            if pd.isna(date_value):
                continue
                
            date = pd.to_datetime(date_value, errors='coerce')
            if pd.isna(date):
                continue
            
            date_str = date.strftime('%Y%m%d')
            
            # Get amount (try different possible column names)
            amount_value = row.get("amount") or row.get("transaction_amount")
            if pd.isna(amount_value):
                continue
                
            amount = float(str(amount_value).replace(",", "").replace("$", "").strip())
            
            # Get description
            name = str(row.get("description") or row.get("name") or row.get("payee") or "N/A").strip()
            memo = str(row.get("memo") or row.get("description") or name).strip()
            
            # Determine transaction type
            trntype = "DEBIT" if amount < 0 else "CREDIT"
            
            qfx.append("          <STMTTRN>")
            qfx.append(f"            <TRNTYPE>{trntype}</TRNTYPE>")
            qfx.append(f"            <DTPOSTED>{date_str}</DTPOSTED>")
            qfx.append(f"            <TRNAMT>{amount:.2f}</TRNAMT>")
            qfx.append(f"            <FITID>{i+1}</FITID>")
            qfx.append(f"            <NAME>{name[:32]}</NAME>")
            qfx.append(f"            <MEMO>{memo[:255]}</MEMO>")
            qfx.append("          </STMTTRN>")
            
            transaction_count += 1
            
        except Exception as e:
            st.warning(f"Skipped row {i+1}: {str(e)}")
            continue
    
    # Close QFX structure
    qfx.extend([
        "        </BANKTRANLIST>",
        "        <LEDGERBAL>",
        "          <BALAMT>0.00</BALAMT>",
        f"          <DTASOF>{now}</DTASOF>",
        "        </LEDGERBAL>",
        "      </STMTRS>",
        "    </STMTTRNRS>",
        "  </BANKMSGSRSV1>",
        "</OFX>"
    ])
    
    return "\n".join(qfx), transaction_count

# Streamlit UI
st.set_page_config(page_title="BofA to QFX Converter", layout="centered")
st.title("🏦 Convert Bank of America to QFX")
st.markdown("Upload a BofA file (CSV or Excel) to convert it to QFX format for import into MoneyGrit or Quicken.")

uploaded_file = st.file_uploader(
    "Upload a Bank of America CSV or Excel file", 
    type=["csv", "xls", "xlsx"],
    help="Select a CSV or Excel file exported from Bank of America"
)

if uploaded_file:
    try:
        with st.spinner("Parsing file..."):
            df = parse_bofa_file(uploaded_file)
        
        st.success(f"✅ File parsed successfully! Found {len(df)} rows.")
        
        # Show preview
        with st.expander("Preview Data", expanded=True):
            st.dataframe(df.head(10), use_container_width=True)
        
        # Show column names for debugging
        with st.expander("Detected Columns"):
            st.write(list(df.columns))
        
        # Account type selector
        account_type = st.selectbox(
            "Select Account Type",
            options=["CHECKING", "SAVINGS", "CREDITCARD"],
            index=0
        )
        
        # Convert to QFX
        with st.spinner("Converting to QFX..."):
            qfx_content, transaction_count = convert_to_qfx(df, account_type)
        
        st.success(f"✅ Converted {transaction_count} transactions to QFX format!")
        
        # Download button
        st.download_button(
            label="📥 Download QFX File",
            data=qfx_content,
            file_name=f"bofa_transactions_{datetime.now().strftime('%Y%m%d')}.qfx",
            mime="application/x-ofx"
        )
        
    except Exception as e:
        st.error(f"❌ Error processing file: {str(e)}")
        st.info("💡 Tip: Make sure you're uploading a valid Bank of America CSV or Excel export file.")
