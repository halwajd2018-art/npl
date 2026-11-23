"""Read only the columns needed by the three reports from OOXML/PDF sources.
The Excel reader streams XML rows instead of materialising the 80MB portfolio in RAM.
"""
from __future__ import annotations
import re, json, gzip, zipfile, os, math
from pathlib import Path
from datetime import datetime, timedelta
from xml.etree.ElementTree import iterparse
from decimal import Decimal, InvalidOperation
from collections import defaultdict

N='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'

def clean(x):
    return re.sub(r'\s+', ' ', str(x or '').replace('\u200f','').replace('\u200e','').replace('\u0640','').replace('\xa0',' ')).strip()

def key(x):
    s=clean(x).lower()
    for a,b in {'أ':'ا','إ':'ا','آ':'ا','ى':'ي','ة':'ه','ؤ':'و','ئ':'ي'}.items():s=s.replace(a,b)
    return re.sub(r'[^\w\u0600-\u06ff]+','',s)

def money(x):
    if isinstance(x,(int,float)):return round(float(x),2)
    s=clean(x).replace(',','').replace('٬','').replace('٫','.')
    if not s or s.lower() in ('none','nan','-'):return 0.0
    if s.endswith('-'):s='-'+s[:-1]
    s=s.replace('−','-')
    try:return round(float(s),2)
    except (ValueError,OverflowError):return 0.0

def contract_id(x):
    s=clean(x)
    if not s or s.lower()=='none':return ''
    if re.fullmatch(r'\d+(?:\.0+)?',s):return s.split('.')[0]
    if re.fullmatch(r'\d+(?:\.\d+)?[eE][+-]?\d+',s):
        try:return format(Decimal(s),'f').split('.')[0]
        except InvalidOperation:pass
    return s

def excel_rows(path):
    """Yields (Excel row number, {col_index_zero: value}) from first sheet.
    XLSX input with unknown or malformed metadata is rejected. Shared strings are
    parsed once; rows are XML-streamed. Do not execute workbook macros/formulas.
    """
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        sheets=sorted([s for s in names if re.fullmatch(r'xl/worksheets/[^/]+\.xml',s,re.I)])
        if not sheets:raise ValueError('لم يتم العثور على ورقة عمل XLSX.')
        if sum(i.file_size for i in z.infolist()) > 3_000_000_000:
            raise ValueError('ملف Excel أكبر من الحد المسموح للبيانات بعد فك الضغط.')
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            with z.open('xl/sharedStrings.xml') as f:
                for _,e in iterparse(f,events=('end',)):
                    if e.tag==N+'si':
                        shared.append(''.join(n.text or '' for n in e.iter() if n.tag==N+'t'))
                        e.clear()
        with z.open(sheets[0]) as f:
            for _,row in iterparse(f,events=('end',)):
                if row.tag!=N+'row':continue
                values={}
                for c in row:
                    if c.tag!=N+'c':continue
                    match=re.match(r'([A-Z]+)',c.attrib.get('r',''))
                    if not match:continue
                    col=0
                    for letter in match.group(1):col=col*26+ord(letter)-64
                    col-=1
                    v=c.find(N+'v'); inline=c.find(N+'is')
                    value=v.text if v is not None else (''.join(t.text or '' for t in inline.iter() if t.tag==N+'t') if inline is not None else '')
                    if c.attrib.get('t')=='s' and value not in (None,''):
                        try:value=shared[int(value)]
                        except (ValueError,IndexError):value=''
                    values[col]=value
                yield int(row.attrib.get('r','0')),values
                row.clear()

def find_header(rows, required, max_scan=30):
    """Find header by semantic aliases. Return (header row, alias -> column index)."""
    iterator=iter(rows)
    for i,(n,r) in enumerate(iterator):
        if i>=max_scan:break
        normalized={key(v):c for c,v in r.items() if clean(v)}
        colmap={}
        for dest, aliases in required.items():
            hit=next((normalized[key(a)] for a in aliases if key(a) in normalized),None)
            if hit is None:break
            colmap[dest]=hit
        if len(colmap)==len(required):return n,colmap,iterator
    raise ValueError('لم يتم العثور على الأعمدة المطلوبة: '+ '، '.join(required))

def records(path, required):
    _, colmap, itr=find_header(excel_rows(path),required)
    for lineno,row in itr:
        yield lineno,{k:row.get(v,'') for k,v in colmap.items()}

def save_json(path,obj):
    with gzip.open(path,'wt',encoding='utf8',compresslevel=5) as f:json.dump(obj,f,ensure_ascii=False,separators=(',',':'))

def load_json(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return json.load(f)

ALIASES={
'contract':['رقم العقد'],
'loan_type':['نوع القرض'],
'sector':['القطاع','التصنيف الأساسي للقرض','التصنيف الاساسي للقرض'],
'fees':['المسدد رسوم (متابعة/جدولة)','المسدد رسوم متابعة جدولة'],
'principal_paid':['المسدد الاساسي مع الفائض','المسدد الأساسي مع الفائض'],
'paid_date':['تاريخ اخر سداد خلال الفترة','تاريخ آخر سداد خلال الفترة'],
'branch':['اسم الفرع','الفرع'],
'balance_loan':['الرصيد قرض'],
'balance_fee':['الرصيد مقابل مالي'],
'due_loan':['المستحق قرض'],
'due_fee':['المستحق مقابل مالي'],
'writeoff':['المبلغ المشطوب الكلي','إجمالي المبلغ المشطوب محاسبياً','إجمالي المبلغ المشطوب محاسبيا'],
'writeoff_loan':['المبلغ المشطوب اساسي','المبلغ المشطوب أساسي'],
'writeoff_fee':['المبلغ المشطوب رسوم'],
'debt_transfer':['المسدد الاعتبارنتيجة نقل الدين','المسدد الاعتبار نتيجة نقل الدين','المسدد الاعتباري نتيجة نقل الدين'],
'restructured':['المبلغ المجدول'],
'year_target':['المستهدف سداد نقدي ملايين الريالات','المستهدف سداد نقدي (ملايين الريالات)','المستهدف سداد نقدي'],
}

def fields(*ks):return {k:ALIASES[k] for k in ks}

def normalize_branch(s):
    a=key(s).replace('فرع','').replace('منطقه','').replace('ال','',1) if False else key(s).replace('فرع','').replace('منطقه','')
    a=a.replace('مكّه','مكه').replace('المكرمه','')
    if 'مكه' in a:return 'مكة المكرمة'
    if 'الرياض' in a:return 'الرياض'
    if 'القصيم' in a:return 'القصيم'
    if 'عسير' in a:return 'عسير'
    if 'شرقي' in a:return 'المنطقة الشرقية'
    if 'المدينه' in a or 'مدينه' in a:return 'المدينة'
    if 'حايل' in a or 'حائل' in a:return 'حائل'
    if 'الجوف' in a:return 'الجوف'
    if 'جازان' in a:return 'جازان'
    if 'تبوك' in a:return 'تبوك'
    if 'الخرج' in a:return 'الخرج'
    if 'نجران' in a:return 'نجران'
    if 'الحدودالشماليه' in a:return 'الحدود الشمالية'
    if 'الباحه' in a:return 'الباحة'
    return clean(s)

BASE_TARGETS=[('الرياض',396203991.81),('مكة المكرمة',252595653.71),('القصيم',156109885.24),('عسير',197590256.44),('المنطقة الشرقية',118541033.09),('المدينة',27740033.36),('حائل',167127964.72),('الجوف',76072704.40),('جازان',27789857.34),('تبوك',34579972.16),('الخرج',350955855.03),('نجران',73019826.70),('الحدود الشمالية',20940545.09),('الباحة',28712307.95)]

def ingest_targets(path):
    result={}; duplicate=[]; skipped=0
    for num,r in fast_records(path,{'branch':['الفرع','اسم الفرع'],'year_target':ALIASES['year_target']}):
        b=normalize_branch(r['branch']); v=money(r['year_target'])
        if not b or not clean(r['year_target']):skipped+=1;continue
        if b in result:duplicate.append(b)
        # This field is named millions, but some reports contain full SAR.
        result[b]=round(v*1_000_000 if abs(v)<100_000 else v,2)
    if duplicate:raise ValueError('الفروع مكررة في ملف المستهدفات: '+', '.join(sorted(set(duplicate))))
    if not result:raise ValueError('لا توجد مستهدفات قابلة للاستخراج.')
    return {'targets':result,'count':len(result),'skipped':skipped,'amount':round(sum(result.values()),2)}

def ingest_contracts(path):
    mapping={}; contradictions=[]; duplicates=0; count=0
    for n,r in fast_records(path,fields('contract','loan_type')):
        contract=contract_id(r['contract']);typ=clean(r['loan_type'])
        if not contract or not typ or not re.match(r'^[A-Za-z0-9]{5,}$',contract):continue
        count+=1
        if contract in mapping:
            duplicates+=1
            if mapping[contract]!=typ and len(contradictions)<30:contradictions.append(contract)
        else:mapping[contract]=typ
    if contradictions:raise ValueError('تعارض نوع القرض لبعض العقود: '+', '.join(contradictions))
    return {'mapping':mapping,'count':count,'unique':len(mapping),'duplicates':duplicates}

def ingest_classification(path):
    mapping={};duplicates=0;contradictions=[]
    for n,r in fast_records(path,fields('loan_type','sector')):
        typ=key(r['loan_type']);sector=clean(r['sector'])
        if not typ or not sector:continue
        if typ in mapping:
            duplicates+=1
            if mapping[typ]!=sector and len(contradictions)<30:contradictions.append(clean(r['loan_type']))
        else:mapping[typ]=sector
    if contradictions:raise ValueError('تعارض التصنيف الأساسي لأنواع القروض: '+', '.join(contradictions))
    return {'mapping':mapping,'count':len(mapping),'duplicates':duplicates}

def ingest_payments(path):
    rows=[]; fees=0.;principal=0.;bad=0
    for n,r in fast_records(path,fields('contract','fees','principal_paid')):
        con=contract_id(r['contract'])
        if not re.match(r'^[A-Za-z0-9]{5,}$',con):
            bad+=1;continue
        a=money(r['fees']);b=money(r['principal_paid'])
        fees+=a;principal+=b
        rows.append([con,a,b])
    return {'rows':rows,'count':len(rows),'skipped':bad,'fees':round(fees,2),'principal':round(principal,2),'total':round(fees+principal,2)}

def ingest_portfolio(path):
    required=fields('contract','branch','balance_loan','balance_fee','due_loan','due_fee','writeoff','writeoff_loan','writeoff_fee','debt_transfer')
    result={}; all_ids=set();duplicate=[]; scanned=0
    for n,r in fast_records(path,required):
        con=contract_id(r['contract'])
        if not re.match(r'^[A-Za-z0-9]{5,}$',con):continue
        scanned+=1; all_ids.add(con)
        wo=money(r['writeoff'])
        if wo<=0:continue
        if con in result:duplicate.append(con);continue
        result[con]={
          'contract':con,'branch':normalize_branch(r['branch']),
          'balance_loan':money(r['balance_loan']),'balance_fee':money(r['balance_fee']),
          'due_loan':money(r['due_loan']),'due_fee':money(r['due_fee']),
          'writeoff':wo,'writeoff_loan':money(r['writeoff_loan']),
          'writeoff_fee':money(r['writeoff_fee']),'debt_transfer':money(r['debt_transfer'])}
    if duplicate:raise ValueError('تكرار عقود مشطوبة في المحفظة: '+', '.join(duplicate[:10]))
    return {'contracts':result,'all_contract_ids':list(all_ids),'count':len(result),
            'scanned':scanned,'total_writeoff':round(sum(c['writeoff'] for c in result.values()),2)}

def ingest_rescheduling(path):
    data={};duplicates=0;count=0
    for n,r in fast_records(path,fields('contract','restructured')):
        con=contract_id(r['contract'])
        if not re.match(r'^[A-Za-z0-9]{5,}$',con):continue
        count+=1;val=money(r['restructured'])
        if con in data:duplicates+=1
        data[con]=round(data.get(con,0)+val,2)
    return {'mapping':data,'count':count,'unique':len(data),'duplicates':duplicates,'total':round(sum(data.values()),2)}

def ingest_pdf(path):
    import pdfplumber
    result={}; period=None; lines=0;reported_total=None
    with pdfplumber.open(path) as pdf:
        if not pdf.pages:raise ValueError('ملف PDF دون صفحات.')
        for page in pdf.pages:
            txt=page.extract_text() or ''
            dates=re.findall(r'\b(?:20\d{2})[/\-](?:0?[1-9]|1[012])[/\-](?:0?[1-9]|[12]\d|3[01])\b',txt)
            if dates:
                # Printed date can be later than reporting period.
                candidates=[]
                for d in dates:
                    try:candidates.append(datetime.strptime(d,'%Y/%m/%d').date())
                    except ValueError:
                        try:candidates.append(datetime.strptime(d,'%Y-%m-%d').date())
                        except ValueError:pass
                if candidates:period=min(candidates).isoformat()
            for table in page.extract_tables():
                if len(table)<3:continue
                # On the supplied report the branch names are joined in the last header cell.
                branches=[]
                for row in table:
                    for cell in row:
                        if not cell:continue
                        for line in str(cell).splitlines():
                            w=clean(line)
                            if ('عرف' in w or 'فرع' in w) and not any(ch.isdigit() for ch in w):
                                value=clean(w[::-1] if 'عرف' in w else w)
                                if value.startswith('فرع '):
                                    b=normalize_branch(value)
                                    if b not in branches:branches.append(b)
                numeric=[]
                for row in table:
                    if len(row)<7:continue
                    first=clean(row[1]); actual=clean(row[6])
                    if not re.match(r'^-?\d[\d,.]*$',first) or not re.match(r'^-?\d[\d,.]*$',actual):continue
                    numeric.append((money(actual),money(first)))
                if len(numeric)==len(branches)+1:reported_total=numeric.pop()
                if len(numeric)==len(branches):
                    for i,(act,food) in enumerate(numeric):result[branches[i]]={'actual':act,'food':food}
                    lines+=len(numeric)
            if not result:
                # Fallback: rows extracted as reversed Arabic text, while numbers are LTR.
                for line in txt.splitlines():
                    found=re.findall(r'(?<![\d])\d{1,3}(?:,\d{3})+\.\d{2}|(?<!\d)\d+\.\d{2}',line)
                    if len(found)<6:continue
                    branchtext=clean(line.rsplit(found[5],1)[-1]); b=normalize_branch(branchtext[::-1]);
                    if b and 'فرع' in branchtext[::-1]:
                        result[b]={'actual':money(found[5]),'food':money(found[0])}
    if not result:raise ValueError('تعذر استخراج جدول المحصل الفعلي والأمن الغذائي من PDF. تحقق من أنه تقرير فروع نصي.')
    return {'rows':result,'period':period,'count':len(result),'total_actual':round(sum(v['actual'] for v in result.values()),2),'total_food':round(sum(v['food'] for v in result.values()),2),'reported_total':reported_total}

INGESTERS={'branch_pdf':ingest_pdf,'targets':ingest_targets,'payments':ingest_payments,'contracts':ingest_contracts,'classification':ingest_classification,'portfolio':ingest_portfolio,'rescheduling':ingest_rescheduling}

# Performance-oriented XML row scanner for very large (80MB+ compressed) workbooks.
# It only examines requested cell columns after reading the header, and does not
# read formula chains, formulas, PII columns, or any unneeded cell values.
_CELL_VALUE=re.compile(rb'<v>(.*?)</v>',re.S)
_CELL_INLINE=re.compile(rb'<t(?: [^>]*)?>(.*?)</t>',re.S)
_ROW_NUMBER=re.compile(rb'<row r="(\d+)"')

def _raw_excel_rows(z,sheet):
    """Stream row-sized XML slices without building millions of Element objects."""
    with z.open(sheet) as f:
        pending=b''; cursor=0; eof=False
        while True:
            if cursor>=len(pending) or pending.find(b'</row>',cursor)<0:
                if cursor:
                    pending=pending[cursor:];cursor=0
                chunk=f.read(2_097_152)
                if not chunk:break
                pending+=chunk
            while True:
                a=pending.find(b'<row ',cursor)
                if a<0:cursor=len(pending);break
                b=pending.find(b'</row>',a)
                if b<0:
                    cursor=a;break
                end=b+6
                yield pending[a:end]
                cursor=end
                if len(pending)-cursor<1024:break

def fast_records(path,required):
    """Read large XLSX using a low-allocation byte-oriented row scanner.
    Source formats follow OOXML; XML escaping and inline shared strings supported.
    """
    from html import unescape
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        sheets=sorted([s for s in names if re.fullmatch(r'xl/worksheets/[^/]+\.xml',s,re.I)])
        if not sheets:raise ValueError('لم يتم العثور على ورقة عمل XLSX.')
        if sum(info.file_size for info in z.infolist())>3_000_000_000:raise ValueError('ملف Excel يتجاوز حد فك الضغط.')
        shared=[]
        if 'xl/sharedStrings.xml' in names:
            with z.open('xl/sharedStrings.xml') as f:
                for _,e in iterparse(f,events=('end',)):
                    if e.tag==N+'si':
                        shared.append(''.join(n.text or '' for n in e.iter() if n.tag==N+'t'))
                        e.clear()
        selected=None;colmap={}
        cell_re=re.compile(rb'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>',re.S)
        for i,raw in enumerate(_raw_excel_rows(z,sheets[0])):
            row_num_match=_ROW_NUMBER.search(raw[:100])
            if not row_num_match:continue
            lineno=int(row_num_match.group(1))
            values={}
            for m in cell_re.finditer(raw):
                col=m.group(1).decode(); t=m.group(2); body=m.group(3)
                v=_CELL_VALUE.search(body)
                if v:
                    value=v.group(1).decode('utf8')
                    if b't="s"' in t:
                        try:value=shared[int(value)]
                        except (IndexError,ValueError):value=''
                    else:value=unescape(value)
                else:
                    inline=_CELL_INLINE.search(body)
                    value=unescape(inline.group(1).decode('utf8')) if inline else ''
                values[col]=value
            if selected is None:
                hdr={key(v):col for col,v in values.items() if clean(v)}
                colmap={}
                for dest,aliases in required.items():
                    found=next((hdr[key(alias)] for alias in aliases if key(alias) in hdr),None)
                    if not found:break
                    colmap[dest]=found
                if len(colmap)==len(required):
                    selected=set(colmap.values())
                    cell_re=re.compile(rb'<c r="(' +b'|'.join(x.encode() for x in sorted(selected,key=len,reverse=True))+rb')\d+"([^>]*)>(.*?)</c>',re.S)
                    continue
                if lineno>30:raise ValueError('الأعمدة المطلوبة غير موجودة: '+', '.join(required))
            else:
                yield lineno,{k:values.get(c,'') for k,c in colmap.items()}
        if selected is None:raise ValueError('الأعمدة المطلوبة غير موجودة: '+', '.join(required))
