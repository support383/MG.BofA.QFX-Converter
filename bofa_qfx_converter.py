import streamlit as st
import pandas as pd
import hashlib
import io
import os
from datetime import datetime, timedelta

st.set_page_config(
    page_title="CSV/Excel to QFX Converter",
    page_icon="⇄",
    layout="centered"
)

# ── Styling ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #ffffff !important; }

.stDownloadButton > button {
    background: #1a6ef5 !important;
    color: #ffffff !important;
    border: none !important;
    font-weight: 600 !important;
    font-size: 15px !important;
    border-radius: 6px !important;
    padding: 0.6rem 1.6rem !important;
    width: 100%;
    margin-top: 1rem;
}

.format-badge {
    display: inline-block;
    background: #e8f0fe;
    color: #1a6ef5;
    font-size: 12px;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 12px;
    margin-right: 6px;
}
.format-badge.unknown { background: #f1f3f4; color: #5f6368; }

.result-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 0;
    border-bottom: 1px solid #dadce0;
    font-size: 14px;
}
.txn-count { font-weight: 600; color: #188038; }
.txn-error { font-weight: 600; color: #c5221f; }

[data-testid="stMetric"] {
    background: #f8f9fa;
    border: 1px solid #dadce0;
    border-radius: 8px;
    padding: 1rem;
}
</style>
""", unsafe_allow_html=True)


# ── Format Detection ──────────────────────────────────────────────────────────

def detect_format(filename, content_bytes):
    """Returns a format string or 'unknown'."""
    fname = filename.lower()
    
    # Try decoding
    for enc in ['utf-8', 'latin-1', 'cp1252']:
        try:
            text = content_bytes.decode(enc)
            break
        except:
            text = None
    if not text:
        return 'unknown', None

    first_lines = text[:500]

    # Wise: semicolon delimited with ID column
    if ';' in first_lines and 'ID;Status;Direction' in first_lines:
        return 'wise', text

    # BofA Corporate/Business card: has CardHolder Name column
    if 'CardHolder Name' in first_lines and 'Reference ID' in first_lines:
        return 'bofa-business', text

    # BofA Credit Card: Posted Date, Reference Number, Payee
    if 'Posted Date' in first_lines and 'Reference Number' in first_lines and 'Payee' in first_lines:
        return 'bofa-cc', text

    # Chase: Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    if 'Transaction Date' in first_lines and 'Post Date' in first_lines and 'Memo' in first_lines:
        return 'chase', text

    # Capital One: has separate Debit and Credit columns
    if 'Transaction Date' in first_lines and 'Posted Date' in first_lines and 'Debit' in first_lines and 'Credit' in first_lines:
        return 'capital-one', text

    # BILT: Transaction Date, Posted Date, Description, Amount (no address/running bal)
    if 'Transaction Date' in first_lines and 'Posted Date' in first_lines and 'Description' in first_lines and 'Address' not in first_lines and 'Running' not in first_lines:
        return 'bilt', text

    # Discover: Trans. Date, Post Date, Description, Amount, Category
    if 'Trans. Date' in first_lines and 'Post Date' in first_lines and 'Description' in first_lines:
        return 'discover', text

    # BofA Checking: Date, Description, Amount, Running Bal (with summary header)
    if 'Running Bal' in first_lines or ('Date,Description,Amount' in first_lines.replace(' ', '')):
        return 'bofa-checking', text

    # Wells Fargo / generic checking fallback
    if 'Date' in first_lines and 'Amount' in first_lines:
        return 'generic', text

    # Wells Fargo: no header row, 5 columns: date, amount, *, blank, description
    import csv as _csv, io as _io
    try:
        sample_rows = list(_csv.reader(_io.StringIO(text)))[:3]
        if all(len(r) == 5 and r[2].strip() in ('*', '') for r in sample_rows if r):
            try:
                from datetime import datetime as _dt
                _dt.strptime(sample_rows[0][0].strip(), '%m/%d/%Y')
                float(sample_rows[0][1].strip())
                return 'wells-fargo', text
            except:
                pass
    except:
        pass

    return 'unknown', text


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_date(s, formats=None):
    if not formats:
        formats = ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y', '%d/%m/%Y', '%Y%m%d']
    s = str(s).strip()
    # Handle datetime strings like "2026-03-20 14:18:43"
    if ' ' in s:
        s = s.split(' ')[0]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except:
            pass
    raise ValueError(f"Cannot parse date: {s!r}")


def normalize_amount(val):
    """Strip commas, convert to float."""
    return float(str(val).replace(',', '').strip())


def make_fitid_hash(date_str, amount, description, extra=''):
    """Strict hash-based FITID."""
    key = f"{date_str}|{amount:.2f}|{description[:40]}|{extra}"
    return hashlib.md5(key.encode()).hexdigest()[:16].upper()


def parse_bofa_cc(text, filename):
    """BofA Credit Card: Posted Date, Reference Number, Payee, Address, Amount"""
    rows = []
    # Handle \r\r\n (double CRLF)
    lines = text.replace('\r\r\n', '\n').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith('Posted Date') and 'Reference Number' in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row")

    import csv
    reader = csv.DictReader(lines[header_idx:])
    for row in reader:
        if not row.get('Posted Date', '').strip():
            continue
        try:
            date_obj = parse_date(row['Posted Date'])
            amount = normalize_amount(row['Amount'])
            payee = row.get('Payee', '').strip().strip('"')
            ref = row.get('Reference Number', '').strip()

            if ref and len(ref) > 5:
                fitid = ref[:22]
            else:
                fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, payee)

            rows.append({
                'date': date_obj,
                'amount': amount,
                'description': payee,
                'fitid': fitid,
                'memo': row.get('Address', '').strip()
            })
        except Exception as e:
            continue
    return rows


def parse_bofa_checking(text, filename):
    """BofA Checking: Date, Description, Amount, Running Bal. (with summary header block)"""
    rows = []
    lines = text.replace('\r\r\n', '\n').replace('\r\n', '\n').replace('\r', '\n').split('\n')

    header_idx = None
    skip_descs = {'beginning balance', 'total credits', 'total debits', 'ending balance'}
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped.startswith('date,') and 'description' in stripped and 'amount' in stripped:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find transaction header row")

    import csv
    reader = csv.DictReader(lines[header_idx:])
    for row in reader:
        date_str = row.get('Date', '').strip()
        desc = row.get('Description', '').strip().strip('"')
        amt_str = row.get('Amount', '').strip()

        if not date_str or not amt_str:
            continue
        if any(s in desc.lower() for s in skip_descs):
            continue

        try:
            date_obj = parse_date(date_str)
            amount = normalize_amount(amt_str)
            fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            rows.append({
                'date': date_obj,
                'amount': amount,
                'description': desc,
                'fitid': fitid,
                'memo': ''
            })
        except:
            continue
    return rows


def parse_bofa_business(text, filename):
    """BofA Business/Corporate card: CardHolder Name, Account/Card Number, Posting Date, Trans. Date, Reference ID, Description, Amount..."""
    rows = []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    header_idx = None
    for i, line in enumerate(lines):
        if 'CardHolder Name' in line and 'Reference ID' in line:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row")

    import csv
    reader = csv.DictReader(lines[header_idx:])
    for row in reader:
        date_str = row.get('Posting Date', '').strip()
        desc = row.get('Description', '').strip().strip('"')
        amt_str = row.get('Amount', '').strip()
        ref = row.get('Reference ID', '').strip()

        if not date_str or not amt_str or not desc:
            continue
        try:
            date_obj = parse_date(date_str)
            amount = normalize_amount(amt_str)
            txn_type = row.get('Transaction Type', '').strip().upper()
            # Strip "Ref: " prefix
            if ref.startswith('Ref:'):
                ref = ref[4:].strip()
            if ref and len(ref) > 5:
                fitid = ref[:22]
            else:
                fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)

            # D=Debit(charge)=negative, C=Credit(payment)=positive (standard QFX convention)
            if txn_type == 'D':
                amount = -abs(amount)
            elif txn_type == 'C':
                amount = abs(amount)
            else:
                amount = -abs(amount)

            rows.append({
                'date': date_obj,
                'amount': amount,
                'description': desc,
                'fitid': fitid,
                'memo': row.get('Merchant Category', '').strip()
            })
        except:
            continue
    return rows


def parse_chase(text, filename):
    """Chase: Transaction Date, Post Date, Description, Category, Type, Amount, Memo"""
    rows = []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    import csv
    reader = csv.DictReader(lines)
    for row in reader:
        date_str = (row.get('Transaction Date') or row.get('Post Date', '')).strip()
        desc = row.get('Description', '').strip()
        amt_str = row.get('Amount', '').strip()

        if not date_str or not amt_str or not desc:
            continue
        try:
            date_obj = parse_date(date_str)
            amount = normalize_amount(amt_str)
            fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            rows.append({
                'date': date_obj,
                'amount': amount,
                'description': desc,
                'fitid': fitid,
                'memo': row.get('Category', '').strip()
            })
        except:
            continue
    return rows


def parse_bilt(text, filename):
    """BILT: Transaction Date, Posted Date, Description, Amount
    Supports old format (4 cols) and new format (7 cols with Card Last 4 etc.)
    New format: charges positive, payments negative — already correct for MoneyGrit credit card convention."""
    rows = []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')

    import csv
    reader = csv.DictReader(lines)
    for row in reader:
        # Use Posted Date for stability, fall back to Transaction Date
        date_str = (row.get('Posted Date') or row.get('Transaction Date', '')).strip()
        desc = row.get('Description', '').strip()
        amt_str = row.get('Amount', '').strip()

        if not date_str or not amt_str or not desc:
            continue

        # Robust date parsing — handles M/D/YYYY, MM/DD/YYYY, M/D/YY
        date_obj = None
        for fmt in ['%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d']:
            try:
                date_obj = datetime.strptime(date_str, fmt)
                break
            except:
                pass
        if date_obj is None:
            try:
                parts = date_str.split('/')
                date_obj = datetime(int(parts[2]), int(parts[0]), int(parts[1]))
            except:
                continue

        try:
            amount = normalize_amount(amt_str)
            # Standard QFX convention: charges negative, payments/refunds positive
            # BILT new format: positive = charge, negative = refund/payment
            # We need: charges negative, refunds/payments positive
            if 'payment' in desc.lower() or 'bilt rewards' in desc.lower():
                amount = abs(amount)    # payments always positive
            elif amount < 0:
                amount = abs(amount)    # negative in BILT = refund = positive in QFX
            else:
                amount = -abs(amount)   # positive in BILT = charge = negative in QFX

            fitid = make_fitid_hash(
                date_obj.strftime('%Y%m%d'),
                amount,
                desc,
                row.get('Raw Merchant Name', '').strip()[:20]
            )
            rows.append({
                'date':        date_obj,
                'amount':      amount,
                'description': desc,
                'fitid':       fitid,
                'memo':        row.get('Category', '').strip()
            })
        except:
            continue
    return rows


def parse_wise(text, filename, use_source_amount=True):
    """Wise: semicolon-delimited with ID, Status, Direction, amounts in source/target currencies"""
    rows = []
    import csv
    reader = csv.DictReader(io.StringIO(text), delimiter=';')
    
    for row in reader:
        txn_id = row.get('ID', '').strip()
        status = row.get('Status', '').strip().upper()
        direction = row.get('Direction', '').strip().upper()
        
        # Skip zero-value refunded/cancelled (they're noise)
        if status == 'CANCELLED':
            # still include if non-zero
            src_amt = row.get('Source amount (after fees)', '').strip()
            if not src_amt or src_amt == '0' or src_amt == '0.00':
                continue

        date_str = row.get('Created on', '').strip()
        if not date_str:
            continue

        try:
            date_obj = parse_date(date_str)
        except:
            continue

        # Merchant name
        if direction == 'IN':
            merchant = row.get('Source name', '').strip() or 'Transfer In'
        else:
            merchant = row.get('Target name', '').strip() or 'Transfer Out'

        # Amount selection
        try:
            if use_source_amount:
                amt_str = row.get('Source amount (after fees)', '').strip()
                currency = row.get('Source currency', 'EUR').strip()
            else:
                amt_str = row.get('Target amount (after fees)', '').strip()
                currency = row.get('Target currency', 'EUR').strip()

            if not amt_str:
                continue
            amount = float(amt_str)
        except:
            continue

        # Apply sign: OUT = negative (expense), IN = positive (income/refund)
        if direction == 'OUT':
            amount = -abs(amount)
        else:
            amount = abs(amount)

        # FITID: use the unique Wise transaction ID directly
        fitid = txn_id.replace('-', '')[:22]
        if not fitid:
            fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, merchant)

        memo = row.get('Category', '').strip()
        if row.get('Reference', '').strip():
            memo = f"{memo} | {row['Reference'].strip()}" if memo else row['Reference'].strip()

        rows.append({
            'date': date_obj,
            'amount': amount,
            'description': merchant,
            'fitid': fitid,
            'memo': memo,
            'currency': currency
        })
    return rows


def parse_capital_one(text, filename):
    """Capital One: Transaction Date, Posted Date, Card No., Description, Category, Debit, Credit"""
    rows = []
    import csv
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    reader = csv.DictReader(lines)
    for row in reader:
        date_str = (row.get('Posted Date') or row.get('Transaction Date', '')).strip()
        desc     = row.get('Description', '').strip()
        debit    = row.get('Debit', '').strip()
        credit   = row.get('Credit', '').strip()

        if not date_str or not desc:
            continue
        try:
            date_obj = parse_date(date_str)
            # Standard QFX convention: charges negative, payments positive
            if debit:
                amount = -abs(normalize_amount(debit))
            elif credit:
                amount = abs(normalize_amount(credit))
            else:
                continue

            fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            rows.append({
                'date':        date_obj,
                'amount':      amount,
                'description': desc,
                'fitid':       fitid,
                'memo':        row.get('Category', '').strip()
            })
        except:
            continue
    return rows


def parse_discover(text, filename):
    """Discover: Trans. Date, Post Date, Description, Amount, Category
    Charges are positive, payments/credits are negative — flip signs."""
    rows = []
    import csv
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    reader = csv.DictReader(lines)
    for row in reader:
        date_str = (row.get('Trans. Date') or row.get('Post Date', '')).strip()
        desc     = row.get('Description', '').strip().strip('"')
        amt_str  = row.get('Amount', '').strip()
        if not date_str or not amt_str or not desc:
            continue
        try:
            date_obj = parse_date(date_str)
            amount   = normalize_amount(amt_str)
            # Standard QFX convention: charges negative, payments positive — flip Discover's signs
            amount = -amount
            fitid    = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            rows.append({
                'date':        date_obj,
                'amount':      amount,
                'description': desc,
                'fitid':       fitid,
                'memo':        row.get('Category', '').strip()
            })
        except:
            continue
    return rows


def parse_wells_fargo(text, filename):
    """Wells Fargo checking: no header, columns are date, amount, *, blank, description"""
    rows = []
    import csv
    reader = csv.reader(text.splitlines())
    for row in reader:
        if len(row) < 5:
            continue
        try:
            date_obj = parse_date(row[0].strip())
            amount   = normalize_amount(row[1].strip())
            desc     = row[4].strip()
            if not desc:
                continue
            fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            rows.append({
                'date':        date_obj,
                'amount':      amount,
                'description': desc,
                'fitid':       fitid,
                'memo':        ''
            })
        except:
            continue
    return rows


# ── QFX Builder ───────────────────────────────────────────────────────────────

def build_qfx(transactions, account_id="UNKNOWN", bank_id="000000000", currency="USD"):
    now = datetime.now().strftime('%Y%m%d%H%M%S')
    if not transactions:
        raise ValueError("No transactions to convert")

    dates = [t['date'] for t in transactions]
    start = min(dates).strftime('%Y%m%d') + '000000'
    end = max(dates).strftime('%Y%m%d') + '000000'

    lines = [
        'OFXHEADER:100',
        'DATA:OFXSGML',
        'VERSION:102',
        'SECURITY:NONE',
        'ENCODING:USASCII',
        'CHARSET:1252',
        'COMPRESSION:NONE',
        'OLDFILEUID:NONE',
        'NEWFILEUID:NONE',
        '',
        '<OFX>',
        '<SIGNONMSGSRSV1>',
        '<SONRS>',
        '<STATUS>',
        '<CODE>0</CODE>',
        '<SEVERITY>INFO</SEVERITY>',
        '</STATUS>',
        f'<DTSERVER>{now}</DTSERVER>',
        '<LANGUAGE>ENG</LANGUAGE>',
        '</SONRS>',
        '</SIGNONMSGSRSV1>',
        '<BANKMSGSRSV1>',
        '<STMTTRNRS>',
        '<TRNUID>1001</TRNUID>',
        '<STATUS>',
        '<CODE>0</CODE>',
        '<SEVERITY>INFO</SEVERITY>',
        '</STATUS>',
        '<STMTRS>',
        f'<CURDEF>{currency}</CURDEF>',
        '<BANKACCTFROM>',
        f'<BANKID>{bank_id}</BANKID>',
        f'<ACCTID>{account_id}</ACCTID>',
        '<ACCTTYPE>CHECKING</ACCTTYPE>',
        '</BANKACCTFROM>',
        '<BANKTRANLIST>',
        f'<DTSTART>{start}</DTSTART>',
        f'<DTEND>{end}</DTEND>',
    ]

    for t in transactions:
        ttype = 'CREDIT' if t['amount'] >= 0 else 'DEBIT'
        date_str = t['date'].strftime('%Y%m%d') + '000000'
        desc = t['description'][:32].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        memo = t.get('memo', '')[:64].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        lines += [
            '<STMTTRN>',
            f'<TRNTYPE>{ttype}</TRNTYPE>',
            f'<DTPOSTED>{date_str}</DTPOSTED>',
            f'<TRNAMT>{t["amount"]:.2f}</TRNAMT>',
            f'<FITID>{t["fitid"]}</FITID>',
            f'<NAME>{desc}</NAME>',
        ]
        if memo:
            lines.append(f'<MEMO>{memo}</MEMO>')
        lines.append('</STMTTRN>')

    lines += [
        '</BANKTRANLIST>',
        '</STMTRS>',
        '</STMTTRNRS>',
        '</BANKMSGSRSV1>',
        '</OFX>',
    ]
    return '\n'.join(lines)


FORMAT_LABELS = {
    'bofa-cc': ('BofA Credit Card', 'bofa-cc'),
    'bofa-checking': ('BofA Checking', 'bofa-chk'),
    'bofa-business': ('BofA Business Card', 'bofa-chk'),
    'chase': ('Chase', 'chase'),
    'bilt': ('BILT Rewards', 'bilt'),
    'wise': ('Wise', 'wise'),
    'generic': ('Generic CSV', 'unknown'),
    'capital-one': ('Capital One', 'chase'),
    'discover':    ('Discover',    'chase'),
    'wells-fargo': ('Wells Fargo', 'wells'),
    'unknown': ('Unknown Format', 'unknown'),
}

PARSERS = {
    'bofa-cc': parse_bofa_cc,
    'bofa-checking': parse_bofa_checking,
    'bofa-business': parse_bofa_business,
    'chase': parse_chase,
    'bilt': parse_bilt,
    'capital-one':  parse_capital_one,
    'discover':     parse_discover,
    'wells-fargo':  parse_wells_fargo,
}



def repair_rbc_qfx(content_bytes):
    """RBC QFX files have unstable FITIDs that change every download.
    Re-stamp all FITIDs with stable hashes based on date+amount+name."""
    import re, hashlib
    text = content_bytes.decode('latin-1')
    
    # Detect RBC by looking for their FITID pattern
    if '90000010' not in text:
        return None, "Not an RBC QFX file"
    
    # Fix known RBC formatting issues while we're at it
    text = text.replace('SECURITY:TYPE1', 'SECURITY:NONE')
    text = re.sub(r'(\d{14})\[-?\d+\]', r'\1', text)  # strip timezone offsets
    
    def replace_fitid(match):
        full_txn = match.group(0)
        date_m  = re.search(r'<DTPOSTED>(\d{8})', full_txn)
        amt_m   = re.search(r'<TRNAMT>([\d.\-]+)', full_txn)
        name_m  = re.search(r'<n>(.*?)(?=<|\n)', full_txn)
        memo_m  = re.search(r'<MEMO>(.*?)(?=<|\n)', full_txn)
        
        date_s = date_m.group(1) if date_m else '00000000'
        amt_s  = amt_m.group(1)  if amt_m  else '0'
        name_s = name_m.group(1).strip() if name_m else ''
        memo_s = memo_m.group(1).strip() if memo_m else ''
        
        key   = f"{date_s}|{amt_s}|{name_s[:40]}|{memo_s[:20]}"
        fitid = hashlib.md5(key.encode()).hexdigest()[:16].upper()
        
        return re.sub(r'<FITID>.*?(?=<|\n)', f'<FITID>{fitid}', full_txn)
    
    # Replace FITIDs within each transaction
    fixed = re.sub(r'<STMTTRN>.*?</STMTTRN>', replace_fitid, text, flags=re.DOTALL)
    
    # Count transactions
    count = len(re.findall(r'<STMTTRN>', fixed))
    return fixed.encode('utf-8'), f"{count} transactions re-stamped with stable IDs"


# ── UI ────────────────────────────────────────────────────────────────────────────────────

# Default settings (no visible settings panel)
currency        = "USD"
account_id      = ""
wise_use_source = True

st.title("⇄ CSV/Excel to QFX Converter")
st.caption("Upload a bank CSV or Excel export — or an RBC QFX file to fix duplicate detection.")
st.markdown("---")

uploaded = st.file_uploader(
    "Upload a CSV/Excel or RBC QFX file",
    type=['csv', 'CSV', 'xls', 'xlsx', 'qfx', 'QFX'],
    accept_multiple_files=False
)

if uploaded:
    content_bytes = uploaded.read()

    # RBC QFX repair path
    if uploaded.name.lower().endswith('.qfx'):
        fixed_bytes, msg = repair_rbc_qfx(content_bytes)
        if fixed_bytes is None:
            st.error(f"⚠ {msg}")
        else:
            st.markdown(
                f'<div class="result-row"><span><span class="format-badge">RBC QFX</span>{uploaded.name}</span>'
                f'<span class="txn-count">{msg}</span></div>',
                unsafe_allow_html=True)
            out_name = os.path.splitext(uploaded.name)[0] + "_fixed.qfx"
            st.download_button(
                label=f"⬇  Download {out_name}",
                data=fixed_bytes,
                file_name=out_name,
                mime='application/x-ofx'
            )

    # CSV/Excel conversion path
    else:
        fmt, text = detect_format(uploaded.name, content_bytes)
        label, badge_cls = FORMAT_LABELS.get(fmt, ('Unknown', 'unknown'))

        if fmt == 'unknown':
            st.error(f"⚠ Could not detect the format of {uploaded.name}. Please contact support@moneygrit.com.")
        else:
            try:
                txns = parse_wise(text, uploaded.name, use_source_amount=wise_use_source) \
                       if fmt == 'wise' else PARSERS[fmt](text, uploaded.name)

                if not txns:
                    st.error(f"⚠ No transactions found in {uploaded.name}.")
                else:
                    st.markdown(
                        f'<div class="result-row"><span><span class="format-badge">{label}</span>{uploaded.name}</span>'
                        f'<span class="txn-count">{len(txns)} transactions found</span></div>',
                        unsafe_allow_html=True)

                    st.markdown("")

                    with st.expander("Preview transactions", expanded=False):
                        preview_df = pd.DataFrame([{
                            'Date':        t['date'].strftime('%Y-%m-%d'),
                            'Description': t['description'],
                            'Amount':      f"{t['amount']:+.2f}",
                        } for t in sorted(txns, key=lambda x: x['date'], reverse=True)[:50]])
                        st.dataframe(preview_df, use_container_width=True, hide_index=True)

                    qfx_content = build_qfx(txns, account_id="IMPORT", currency=currency)
                    out_name = os.path.splitext(uploaded.name)[0] + ".qfx"
                    st.download_button(
                        label=f"⬇  Download {out_name}",
                        data=qfx_content.encode('utf-8'),
                        file_name=out_name,
                        mime='application/x-ofx'
                    )

            except Exception as e:
                st.error(f"Error converting {uploaded.name}: {e}")
