import streamlit as st
import pandas as pd
import hashlib
import io
import os
from datetime import datetime, timedelta

st.set_page_config(
    page_title="CSV → QFX Converter",
    page_icon="⇄",
    layout="wide"
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap');

:root {
    --bg: #0e0f11;
    --surface: #16181c;
    --border: #2a2d35;
    --accent: #c8f135;
    --accent2: #4af0c4;
    --text: #e8eaf0;
    --muted: #6b7280;
    --danger: #ff5f5f;
}

html, body, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'Syne', sans-serif;
}
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border);
}
h1, h2, h3 { font-family: 'Syne', sans-serif; font-weight: 800; }
.stButton > button {
    background: var(--accent) !important; color: #0e0f11 !important;
    border: none !important; font-family: 'Space Mono', monospace !important;
    font-weight: 700 !important; font-size: 13px !important;
    letter-spacing: 0.05em !important; padding: 0.6rem 1.4rem !important;
    border-radius: 4px !important; transition: all 0.15s ease !important;
}
.stButton > button:hover {
    background: #d4f550 !important; transform: translateY(-1px);
    box-shadow: 0 4px 20px rgba(200,241,53,0.3) !important;
}
.stDownloadButton > button {
    background: var(--accent2) !important; color: #0e0f11 !important;
    border: none !important; font-family: 'Space Mono', monospace !important;
    font-weight: 700 !important; font-size: 13px !important;
    border-radius: 4px !important; width: 100%;
}
.stSelectbox > div > div, .stRadio > div {
    background: var(--surface) !important; border-color: var(--border) !important;
    color: var(--text) !important;
}
[data-testid="stFileUploader"] {
    background: var(--surface) !important; border: 2px dashed var(--border) !important;
    border-radius: 8px !important;
}
.format-badge {
    display: inline-block; background: var(--accent); color: #0e0f11;
    font-family: 'Space Mono', monospace; font-size: 11px; font-weight: 700;
    padding: 2px 8px; border-radius: 3px; letter-spacing: 0.08em; margin-bottom: 8px;
}
.format-badge.unknown { background: var(--muted); color: white; }
.format-badge.wise    { background: var(--accent2); }
.format-badge.bofa-cc { background: #c084fc; }
.format-badge.bofa-chk{ background: #fb923c; }
.format-badge.chase   { background: #60a5fa; }
.format-badge.bilt    { background: #f472b6; }
.result-box {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.2rem 1.5rem; margin: 0.5rem 0;
}
.result-box.success { border-color: var(--accent); }
.result-box.error   { border-color: var(--danger); }
.stat { font-family: 'Space Mono', monospace; font-size: 28px; font-weight: 700; color: var(--accent); }
.mono { font-family: 'Space Mono', monospace; font-size: 12px; color: var(--muted); }
hr { border-color: var(--border) !important; }
[data-testid="stMetric"] {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 1rem;
}
[data-testid="stMetricValue"] {
    font-family: 'Space Mono', monospace !important; color: var(--accent) !important;
}
</style>
""", unsafe_allow_html=True)


# ── Format Detection ──────────────────────────────────────────────────────────

def detect_format(filename, content_bytes):
    for enc in ['utf-8', 'latin-1', 'cp1252']:
        try:
            text = content_bytes.decode(enc)
            break
        except:
            text = None
    if not text:
        return 'unknown', None
    first_lines = text[:500]
    if ';' in first_lines and 'ID;Status;Direction' in first_lines:
        return 'wise', text
    if 'CardHolder Name' in first_lines and 'Reference ID' in first_lines:
        return 'bofa-business', text
    if 'Posted Date' in first_lines and 'Reference Number' in first_lines and 'Payee' in first_lines:
        return 'bofa-cc', text
    if 'Transaction Date' in first_lines and 'Post Date' in first_lines and 'Memo' in first_lines:
        return 'chase', text
    if ('Transaction Date' in first_lines and 'Posted Date' in first_lines
            and 'Description' in first_lines and 'Address' not in first_lines
            and 'Running' not in first_lines):
        return 'bilt', text
    if 'Running Bal' in first_lines or ('Date,Description,Amount' in first_lines.replace(' ', '')):
        return 'bofa-checking', text
    if 'Date' in first_lines and 'Amount' in first_lines:
        return 'generic', text
    return 'unknown', text


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_date(s, formats=None):
    if not formats:
        formats = ['%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y', '%d/%m/%Y', '%Y%m%d']
    s = str(s).strip()
    if ' ' in s:
        s = s.split(' ')[0]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except:
            pass
    raise ValueError(f"Cannot parse date: {s!r}")


def normalize_amount(val):
    return float(str(val).replace(',', '').strip())


def make_fitid_hash(date_str, amount, description, extra=''):
    key = f"{date_str}|{amount:.2f}|{description[:40]}|{extra}"
    return hashlib.md5(key.encode()).hexdigest()[:16].upper()


def parse_bofa_cc(text, filename):
    rows = []
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
            rows.append({'date': date_obj, 'amount': amount, 'description': payee,
                         'fitid': fitid, 'memo': row.get('Address', '').strip()})
        except:
            continue
    return rows


def parse_bofa_checking(text, filename):
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
            rows.append({'date': date_obj, 'amount': amount, 'description': desc,
                         'fitid': fitid, 'memo': ''})
        except:
            continue
    return rows


def parse_bofa_business(text, filename):
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
            if ref.startswith('Ref:'):
                ref = ref[4:].strip()
            if ref and len(ref) > 5:
                fitid = ref[:22]
            else:
                fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            # D=Debit(charge)=negative, C=Credit(refund)=positive
            if txn_type == 'D':
                amount = -abs(amount)
            elif txn_type == 'C':
                amount = abs(amount)
            else:
                amount = -abs(amount)
            rows.append({'date': date_obj, 'amount': amount, 'description': desc,
                         'fitid': fitid, 'memo': row.get('Merchant Category', '').strip()})
        except:
            continue
    return rows


def parse_chase(text, filename):
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
            rows.append({'date': date_obj, 'amount': amount, 'description': desc,
                         'fitid': fitid, 'memo': row.get('Category', '').strip()})
        except:
            continue
    return rows


def parse_bilt(text, filename):
    rows = []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    import csv
    reader = csv.DictReader(lines)
    for row in reader:
        date_str = (row.get('Posted Date') or row.get('Transaction Date', '')).strip()
        desc = row.get('Description', '').strip()
        amt_str = row.get('Amount', '').strip()
        if not date_str or not amt_str or not desc:
            continue
        try:
            date_obj = parse_date(date_str, ['%m/%d/%Y', '%m/%d/%y', '%-m/%-d/%Y'])
        except:
            try:
                parts = date_str.split('/')
                date_obj = datetime(int(parts[2]), int(parts[0]), int(parts[1]))
            except:
                continue
        try:
            amount = normalize_amount(amt_str)
            if 'payment' not in desc.lower() and 'bilt rewards' not in desc.lower():
                amount = -abs(amount) if amount > 0 else amount
            fitid = make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, desc)
            rows.append({'date': date_obj, 'amount': amount, 'description': desc,
                         'fitid': fitid, 'memo': ''})
        except:
            continue
    return rows


def parse_wise(text, filename, use_source_amount=True):
    rows = []
    import csv
    reader = csv.DictReader(io.StringIO(text), delimiter=';')
    for row in reader:
        txn_id   = row.get('ID', '').strip()
        status    = row.get('Status', '').strip().upper()
        direction = row.get('Direction', '').strip().upper()
        if status == 'CANCELLED':
            src_amt = row.get('Source amount (after fees)', '').strip()
            if not src_amt or src_amt in ('0', '0.00'):
                continue
        date_str = row.get('Created on', '').strip()
        if not date_str:
            continue
        try:
            date_obj = parse_date(date_str)
        except:
            continue
        merchant = (row.get('Source name', '').strip() or 'Transfer In') if direction == 'IN' \
                   else (row.get('Target name', '').strip() or 'Transfer Out')
        try:
            if use_source_amount:
                amt_str  = row.get('Source amount (after fees)', '').strip()
                currency = row.get('Source currency', 'EUR').strip()
            else:
                amt_str  = row.get('Target amount (after fees)', '').strip()
                currency = row.get('Target currency', 'EUR').strip()
            if not amt_str:
                continue
            amount = float(amt_str)
        except:
            continue
        amount = -abs(amount) if direction == 'OUT' else abs(amount)
        fitid = txn_id.replace('-', '')[:22] or make_fitid_hash(date_obj.strftime('%Y%m%d'), amount, merchant)
        memo  = row.get('Category', '').strip()
        ref   = row.get('Reference', '').strip()
        if ref:
            memo = f"{memo} | {ref}" if memo else ref
        rows.append({'date': date_obj, 'amount': amount, 'description': merchant,
                     'fitid': fitid, 'memo': memo, 'currency': currency})
    return rows


# ── QFX Builder ───────────────────────────────────────────────────────────────

def build_qfx(transactions, account_id="UNKNOWN", bank_id="000000000", currency="USD"):
    now = datetime.now().strftime('%Y%m%d%H%M%S')
    if not transactions:
        raise ValueError("No transactions to convert")
    dates = [t['date'] for t in transactions]
    start = min(dates).strftime('%Y%m%d') + '000000'
    end   = max(dates).strftime('%Y%m%d') + '000000'
    lines = [
        'OFXHEADER:100','DATA:OFXSGML','VERSION:102','SECURITY:NONE',
        'ENCODING:USASCII','CHARSET:1252','COMPRESSION:NONE',
        'OLDFILEUID:NONE','NEWFILEUID:NONE','',
        '<OFX>','<SIGNONMSGSRSV1>','<SONRS>','<STATUS>',
        '<CODE>0</CODE>','<SEVERITY>INFO</SEVERITY>','</STATUS>',
        f'<DTSERVER>{now}</DTSERVER>','<LANGUAGE>ENG</LANGUAGE>',
        '</SONRS>','</SIGNONMSGSRSV1>','<BANKMSGSRSV1>',
        '<STMTTRNRS>','<TRNUID>1001</TRNUID>','<STATUS>',
        '<CODE>0</CODE>','<SEVERITY>INFO</SEVERITY>','</STATUS>',
        '<STMTRS>',f'<CURDEF>{currency}</CURDEF>',
        '<BANKACCTFROM>',f'<BANKID>{bank_id}</BANKID>',
        f'<ACCTID>{account_id}</ACCTID>',
        '<ACCTTYPE>CHECKING</ACCTTYPE>','</BANKACCTFROM>',
        '<BANKTRANLIST>',f'<DTSTART>{start}</DTSTART>',
        f'<DTEND>{end}</DTEND>',
    ]
    for t in transactions:
        ttype    = 'CREDIT' if t['amount'] >= 0 else 'DEBIT'
        date_str = t['date'].strftime('%Y%m%d') + '000000'
        desc = t['description'][:32].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        memo = t.get('memo','')[:64].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        lines += [
            '<STMTTRN>',f'<TRNTYPE>{ttype}</TRNTYPE>',
            f'<DTPOSTED>{date_str}</DTPOSTED>',
            f'<TRNAMT>{t["amount"]:.2f}</TRNAMT>',
            f'<FITID>{t["fitid"]}</FITID>',
            f'<NAME>{desc}</NAME>',
        ]
        if memo:
            lines.append(f'<MEMO>{memo}</MEMO>')
        lines.append('</STMTTRN>')
    lines += ['</BANKTRANLIST>','</STMTRS>','</STMTTRNRS>',
              '</BANKMSGSRSV1>','</OFX>']
    return '\n'.join(lines)


FORMAT_LABELS = {
    'bofa-cc':       ('BofA Credit Card',   'bofa-cc'),
    'bofa-checking': ('BofA Checking',      'bofa-chk'),
    'bofa-business': ('BofA Business Card', 'bofa-chk'),
    'chase':         ('Chase',              'chase'),
    'bilt':          ('BILT Rewards',       'bilt'),
    'wise':          ('Wise',               'wise'),
    'generic':       ('Generic CSV',        'unknown'),
    'unknown':       ('Unknown Format',     'unknown'),
}

PARSERS = {
    'bofa-cc':       parse_bofa_cc,
    'bofa-checking': parse_bofa_checking,
    'bofa-business': parse_bofa_business,
    'chase':         parse_chase,
    'bilt':          parse_bilt,
}


# ── UI ────────────────────────────────────────────────────────────────────────

st.markdown("# ⇄ CSV → QFX")
st.markdown('<p class="mono">Multi-institution transaction converter for MoneyGrit</p>', unsafe_allow_html=True)
st.markdown("---")

col_main, col_settings = st.columns([2, 1])

with col_settings:
    st.markdown("### ⚙ Settings")
    currency   = st.selectbox("Account currency", ["USD","EUR","GBP","CAD","AUD","CHF"], index=0)
    account_id = st.text_input("Account ID (optional)", placeholder="e.g. 1234 or CHECKING")
    st.markdown("**Wise options**")
    wise_amount     = st.radio("Wise: which amount to use?",
                               ["Source (account currency)", "Target (merchant currency)"], index=0)
    wise_use_source = wise_amount.startswith("Source")

with col_main:
    st.markdown("### ① Upload files")
    uploaded = st.file_uploader("Drop one or more CSV files",
                                type=['csv','CSV'], accept_multiple_files=True)

    if uploaded:
        st.markdown("---")
        st.markdown("### ② Detected formats")
        all_transactions = []
        file_results     = []

        for f in uploaded:
            content = f.read()
            fmt, text = detect_format(f.name, content)
            label, badge_cls = FORMAT_LABELS.get(fmt, ('Unknown', 'unknown'))
            st.markdown(
                f'<span class="format-badge {badge_cls}">{label}</span> '
                f'<span class="mono">{f.name}</span>',
                unsafe_allow_html=True)
            if fmt == 'unknown':
                st.warning(f"⚠ Could not detect format for {f.name} — skipping.")
                file_results.append({'name': f.name, 'status': 'error', 'count': 0})
                continue
            try:
                txns = parse_wise(text, f.name, use_source_amount=wise_use_source) \
                       if fmt == 'wise' else PARSERS[fmt](text, f.name)
                if not txns:
                    st.warning(f"⚠ No transactions found in {f.name}")
                    file_results.append({'name': f.name, 'status': 'error', 'count': 0})
                    continue
                all_transactions.extend(txns)
                file_results.append({'name': f.name, 'status': 'ok', 'count': len(txns)})
                st.markdown(
                    f'<div class="result-box success"><span class="stat">{len(txns)}</span> '
                    f'<span class="mono"> transactions parsed</span></div>',
                    unsafe_allow_html=True)
            except Exception as e:
                st.error(f"Error parsing {f.name}: {e}")
                file_results.append({'name': f.name, 'status': 'error', 'count': 0})

        if all_transactions:
            st.markdown("---")
            st.markdown("### ③ Preview & Export")
            seen, deduped = {}, []
            for t in all_transactions:
                if t['fitid'] not in seen:
                    seen[t['fitid']] = True
                    deduped.append(t)
            dupes = len(all_transactions) - len(deduped)
            c1, c2, c3 = st.columns(3)
            c1.metric("Total parsed",      len(all_transactions))
            c2.metric("After dedup",       len(deduped))
            c3.metric("Duplicates removed", dupes)
            deduped.sort(key=lambda x: x['date'], reverse=True)
            preview_df = pd.DataFrame([{
                'Date':        t['date'].strftime('%Y-%m-%d'),
                'Description': t['description'],
                'Amount':      f"{t['amount']:+.2f}",
                'FITID':       t['fitid']
            } for t in deduped[:25]])
            st.dataframe(preview_df, use_container_width=True, hide_index=True)
            if len(deduped) > 25:
                st.markdown(f'<p class="mono">Showing 25 of {len(deduped)} transactions</p>',
                            unsafe_allow_html=True)
            try:
                acct        = account_id.strip() if account_id.strip() else "IMPORT"
                qfx_content = build_qfx(deduped, account_id=acct, currency=currency)
                base        = os.path.splitext(uploaded[0].name)[0] if len(uploaded) == 1 \
                              else "combined_transactions"
                out_name    = f"{base}.qfx"
                st.download_button(
                    label=f"⬇  Download {out_name}",
                    data=qfx_content.encode('utf-8'),
                    file_name=out_name,
                    mime='application/x-ofx')
            except Exception as e:
                st.error(f"QFX generation error: {e}")
    else:
        st.markdown("""
        <div class="result-box" style="text-align:center; padding: 3rem;">
            <div style="font-size:3rem; margin-bottom:1rem">⇄</div>
            <div class="mono">Upload one or more CSV files to begin</div>
            <br>
            <div class="mono" style="color: #4af0c4">
            BofA Credit Card · BofA Checking · BofA Business Card<br>
            Chase · BILT Rewards · Wise
            </div>
        </div>
        """, unsafe_allow_html=True)
