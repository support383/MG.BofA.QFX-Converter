import streamlit as st
import pandas as pd
import io
import hashlib
import re
from datetime import datetime

def normalize_payee(payee_text):
    """Normalize payee text for stable FITID generation"""
    if not payee_text or pd.isna(payee_text):
        return ""
    # Convert to string, uppercase, trim, normalize whitespace
    s = str(payee_text).strip().upper()
    s = re.sub(r'\s+', ' ', s)
    return s

def generate_fitid(date_str, amount, description, reference_number=None):
    """
    Generate stable FITID that works across BofA download types.
    Priority:
    1. Use Reference Number if available (most stable)
    2. Fall back to normalized hash of date+amount+description
    """
    # First priority: Use bank-provided reference number
    if reference_number and str(reference_number).strip() and str(reference_number).strip().lower() != 'nan':
        ref = str(reference_number).strip()
        # Remove "Ref: " prefix if present, keep just the number
        ref = re.sub(r'^ref:\s*', '', ref, flags=re.IGNORECASE)
        return ref[:16]  # Limit to 16 chars for FITID
    
    # Fallback: Create stable hash from normalized fields
    normalized_desc = normalize_payee(description)
    fitid_string = f"{date_str}|{amount:.2f}|{normalized_desc[:60]}"
    return hashlib.md5(fitid_string.encode('utf-8')).hexdigest()[:16]

def parse_bofa_file(file):
    """Parse BofA CSV or Excel file"""
    file_type = file.name.split('.')[-1].lower()
    
    # Handle Excel files
    if file_type in ['xls', 'xlsx']:
        try:
            raw_df = pd.read_excel(file, header=None)
            
            # Find header row
            header_row_index = None
            for i, row in raw_df.iterrows():
                row_lower = row.astype(str).str.lower().tolist()
                # Check for checking/savings format OR credit card format
                if ('date' in row_lower and 'amount' in row_lower) or \
                   ('posting date' in ' '.join(row_lower) or 'posting_date' in ' '.join(row_lower)):
                    header_row_index = i
                    break
            
            if header_row_index is None:
                raise ValueError("Could not find header row with transaction columns")
            
            # Re-read with proper header
            file.seek(0)
            df = pd.read_excel(file, skiprows=header_row_index)
            df.columns = [str(col).strip().lower().replace(" ", "_") for col in df.columns]
            df = df.dropna(how='all')
            return df
            
        except Exception as e:
            raise ValueError(f"Unable to read Excel file: {str(e)}")
    
    # Handle CSV files
    else:
        content = file.read().decode("utf-8", errors="ignore")
        
        # Split into lines and find the header
        lines = content.split('\n')
        header_line_index = None
        
        for i, line in enumerate(lines):
            line_lower = line.lower()
            # Look for checking/savings format OR credit card format
            if ('date' in line_lower and 'amount' in line_lower and 'summary' not in line_lower) or \
               ('posting date' in line_lower or 'posting_date' in line_lower):
                header_line_index = i
                break
        
        if header_line_index is None:
            raise ValueError("Could not find the transaction header row")
        
        # Get all lines from header onwards
        data_lines = lines[header_line_index:]
        
        # Detect delimiter from header line
        header_line = data_lines[0]
        if header_line.count('\t') >= 2:
            delimiter = '\t'
        elif header_line.count(';') > header_line.count(','):
            delimiter = ';'
        else:
            delimiter = ','
        
        # Reconstruct content from header onwards
        data_content = '\n'.join(data_lines)
        
        try:
            df = pd.read_csv(
                io.StringIO(data_content),
                delimiter=delimiter,
                thousands=',',
                on_bad_lines='skip'
            )
            
            # Clean column names
            df.columns = [str(col).strip().lower().replace(" ", "_") for col in df.columns]
            
            # Remove empty rows and unnamed columns
            df = df.dropna(how='all')
            df = df.loc[:, ~df.columns.str.contains('^unnamed', case=False, na=False)]
            
            return df
            
        except Exception as e:
            raise ValueError(f"Unable to parse CSV data: {str(e)}")

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
    skipped_rows = []
    
    for i, row in df.iterrows():
        try:
            # Get date - handle both checking and credit card formats
            date_value = (row.get("date") or row.get("posted_date") or 
                         row.get("posting_date") or row.get("trans._date") or
                         row.get("transaction_date"))
            if pd.isna(date_value) or date_value == '':
                continue
            
            # Skip summary rows
            date_str_lower = str(date_value).lower()
            if any(word in date_str_lower for word in ["beginning", "ending", "balance", "total", "summary"]):
                continue
                
            date = pd.to_datetime(date_value, errors='coerce')
            if pd.isna(date):
                skipped_rows.append(f"Row {i+1}: Invalid date '{date_value}'")
                continue
            
            date_str = date.strftime('%Y%m%d')
            
            # Get amount - handle both formats
            amount_value = row.get("amount") or row.get("transaction_amount")
            if pd.isna(amount_value) or amount_value == '':
                continue
            
            # Clean and convert amount (handle commas)
            amount_str = str(amount_value).replace(",", "").replace("$", "").strip()
            if amount_str == '' or amount_str == 'nan':
                continue
                
            amount = float(amount_str)
            
            # Handle transaction type for credit cards
            # For credit cards: D=Debit/charge, C=Credit/payment
            # DON'T modify the amount - keep it as-is from the CSV
            # The QFX TRNTYPE will indicate if it's a debit or credit
            trans_type = str(row.get("transaction_type") or "").strip().upper()
            
            # Get description - handle both formats
            name = str(row.get("description") or row.get("name") or 
                      row.get("payee") or row.get("merchant_category") or "N/A").strip()
            
            # Skip if description contains summary keywords
            if any(word in name.lower() for word in ["beginning balance", "ending balance", "total credits", "total debits"]):
                continue
            
            memo = str(row.get("memo") or name).strip()
            
            # Get reference number for stable FITID (works across download types)
            reference_number = row.get("reference_number") or row.get("reference_id") or None
            
            # Create unique FITID using new stable strategy
            fitid = generate_fitid(date_str, amount, name, reference_number)
            
            # Determine transaction type
            trntype = "DEBIT" if amount < 0 else "CREDIT"
            
            qfx.append("          <STMTTRN>")
            qfx.append(f"            <TRNTYPE>{trntype}</TRNTYPE>")
            qfx.append(f"            <DTPOSTED>{date_str}</DTPOSTED>")
            qfx.append(f"            <TRNAMT>{amount:.2f}</TRNAMT>")
            qfx.append(f"            <FITID>{fitid}</FITID>")
            qfx.append(f"            <NAME>{name[:32]}</NAME>")
            qfx.append(f"            <MEMO>{memo[:255]}</MEMO>")
            qfx.append("          </STMTTRN>")
            
            transaction_count += 1
            
        except Exception as e:
            skipped_rows.append(f"Row {i+1}: {str(e)}")
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
    
    return "\n".join(qfx), transaction_count, skipped_rows

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
        
        # Convert to QFX (default to CHECKING)
        with st.spinner("Converting to QFX..."):
            qfx_content, transaction_count, skipped_rows = convert_to_qfx(df, 'CHECKING')
        
        st.success(f"✅ Converted {transaction_count} transactions to QFX format!")
        
        # Debug: Show sample FITIDs for troubleshooting
        with st.expander("🔍 Debug: Sample FITIDs (for troubleshooting duplicates)"):
            st.write("First 5 transactions and their FITIDs:")
            st.write("If FITIDs change when converting the same file twice, there's a bug!")
            debug_info = []
            temp_count = 0
            for i, row in df.iterrows():
                if temp_count >= 5:
                    break
                try:
                    date_value = (row.get("date") or row.get("posted_date") or 
                                 row.get("posting_date") or row.get("trans._date") or
                                 row.get("transaction_date"))
                    if pd.isna(date_value) or date_value == '':
                        continue
                    date = pd.to_datetime(date_value, errors='coerce')
                    if pd.isna(date):
                        continue
                    date_str = date.strftime('%Y%m%d')
                    
                    amount_value = row.get("amount") or row.get("transaction_amount")
                    if pd.isna(amount_value) or amount_value == '':
                        continue
                    amount = float(str(amount_value).replace(",", "").replace("$", "").strip())
                    
                    name = str(row.get("description") or row.get("name") or 
                              row.get("payee") or "N/A").strip()
                    
                    reference_number = row.get("reference_number") or row.get("reference_id") or None
                    
                    # Show what's going into the hash
                    normalized_name = normalize_payee(name)
                    
                    debug_info.append({
                        "Date": date_str,
                        "Amount": f"{amount:.2f}",
                        "Description": name[:30],
                        "Normalized": normalized_name[:30],
                        "Ref#": str(reference_number)[:20] if reference_number else "None",
                        "Hash Input": f"{date_str}|{amount:.2f}|{normalized_name[:60]}"[:50],
                        "FITID": generate_fitid(date_str, amount, name, reference_number)
                    })
                    temp_count += 1
                except Exception as e:
                    st.write(f"Debug error on row {i}: {e}")
                    continue
            
            if debug_info:
                for item in debug_info:
                    st.write("---")
                    for key, value in item.items():
                        st.write(f"**{key}:** {value}")
        
        # Show skipped rows if any
        if skipped_rows and len(skipped_rows) > 0:
            with st.expander(f"⚠️ Skipped {len(skipped_rows)} rows"):
                for skip in skipped_rows[:10]:
                    st.text(skip)
                if len(skipped_rows) > 10:
                    st.text(f"... and {len(skipped_rows) - 10} more")
        
        # Download button
        original_filename = uploaded_file.name.rsplit('.', 1)[0]  # Remove original extension
        qfx_filename = f"{original_filename}.qfx"
        
        st.download_button(
            label="📥 Download QFX File",
            data=qfx_content,
            file_name=qfx_filename,
            mime="application/x-ofx"
        )
        
    except Exception as e:
        st.error(f"❌ Error processing file: {str(e)}")
        st.info("💡 Tip: Make sure you're uploading a valid Bank of America CSV or Excel export file.")
        
        # Show detailed error for debugging
        with st.expander("Show detailed error"):
            import traceback
            st.code(traceback.format_exc())
