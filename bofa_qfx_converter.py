import streamlit as st
import pandas as pd
import io
import hashlib
import re
from datetime import datetime, timedelta, timedelta

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
    Generate FITID that's stable across BofA's data changes.
    
    Strategy: Create a "fuzzy" FITID that matches even when BofA changes:
    - Dates by 1-2 days (pending -> posted)
    - Minor description variations
    - Exact penny amounts
    
    We intentionally make this slightly fuzzy so the same real-world
    transaction creates the same FITID across different downloads.
    """
    
    # Parse the date
    date_obj = datetime.strptime(date_str, '%Y%m%d')
    
    # Normalize description heavily
    normalized_desc = normalize_payee(description)
    
    # Take only the CORE merchant name (first 12 chars)
    # This handles: "AMAZON MKTPL*8C4UB9G83" vs "AMAZON MKTPL*8C4UB9Z99"
    merchant_core = normalized_desc[:12]
    
    # Round amount to nearest dollar to handle pending vs final
    # $23.45 and $23.99 both become $23
    amount_rounded = round(abs(amount))
    
    # Use week-based date instead of exact date
    # This handles Mon vs Tue posting for the same transaction
    # Get the Monday of the week this transaction falls in
    days_since_monday = date_obj.weekday()
    week_start = date_obj - timedelta(days=days_since_monday)
    week_key = week_start.strftime('%Y%m%d')
    
    # Create FITID from fuzzy components
    fitid_string = f"{week_key}|{amount_rounded}|{merchant_core}"
    
    return hashlib.md5(fitid_string.encode('utf-8')).hexdigest()[:16]

def extract_transactions_from_qfx(qfx_content):
    """Extract transaction data from existing QFX file"""
    transactions = []
    
    # Parse QFX to extract transactions
    lines = qfx_content.split('\n')
    current_txn = {}
    
    for line in lines:
        line = line.strip()
        if '<STMTTRN>' in line:
            current_txn = {}
        elif '<DTPOSTED>' in line:
            current_txn['date'] = line.replace('<DTPOSTED>', '').replace('</DTPOSTED>', '')
        elif '<TRNAMT>' in line:
            current_txn['amount'] = float(line.replace('<TRNAMT>', '').replace('</TRNAMT>', ''))
        elif '<NAME>' in line:
            current_txn['description'] = line.replace('<NAME>', '').replace('</NAME>', '')
        elif '<FITID>' in line:
            current_txn['fitid'] = line.replace('<FITID>', '').replace('</FITID>', '')
        elif '</STMTTRN>' in line:
            if current_txn:
                transactions.append(current_txn.copy())
    
    return transactions

def fuzzy_match(txn1, txn2, date_tolerance_days=2, amount_tolerance=1.0):
    """Check if two transactions are likely the same despite data changes"""
    # Compare dates (allow +/- tolerance)
    try:
        date1 = datetime.strptime(txn1['date'], '%Y%m%d')
        date2 = datetime.strptime(txn2['date'], '%Y%m%d')
        date_diff = abs((date1 - date2).days)
        if date_diff > date_tolerance_days:
            return False
    except:
        if txn1['date'] != txn2['date']:
            return False
    
    # Compare amounts (allow small variance)
    amt_diff = abs(txn1['amount'] - txn2['amount'])
    if amt_diff > amount_tolerance:
        return False
    
    # Compare descriptions (check if one starts with the other)
    desc1 = normalize_payee(txn1['description'])[:20]
    desc2 = normalize_payee(txn2['description'])[:20]
    
    # If first 15 chars match, likely the same merchant
    if desc1[:15] == desc2[:15]:
        return True
    
    return False

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
            # DON'T modify the amount - keep it as-is from the CSV
            trans_type = str(row.get("transaction_type") or "").strip().upper()
            
            # Get description - handle both formats
            name = str(row.get("description") or row.get("name") or 
                      row.get("payee") or row.get("merchant_category") or "N/A").strip()
            
            # Skip if description contains summary keywords
            if any(word in name.lower() for word in ["beginning balance", "ending balance", "total credits", "total debits"]):
                continue
            
            memo = str(row.get("memo") or name).strip()
            
            # Get reference number for stable FITID
            reference_number = row.get("reference_number") or row.get("reference_id") or None
            
            # Create FITID using fuzzy approach
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
        
        # Convert to QFX
        with st.spinner("Converting to QFX..."):
            qfx_content, transaction_count, skipped_rows = convert_to_qfx(df, 'CHECKING')
        
        st.success(f"✅ Converted {transaction_count} transactions to QFX format!")
        
        # Show skipped rows if any
        if skipped_rows and len(skipped_rows) > 0:
            with st.expander(f"⚠️ Skipped {len(skipped_rows)} rows"):
                for skip in skipped_rows[:10]:
                    st.text(skip)
                if len(skipped_rows) > 10:
                    st.text(f"... and {len(skipped_rows) - 10} more")
        
        # Download button
        original_filename = uploaded_file.name.rsplit('.', 1)[0]
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
