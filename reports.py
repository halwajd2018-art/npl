"""Pure report calculations: source snapshots in, audit-friendly outputs out."""
from collections import defaultdict
from .ingest import BASE_TARGETS,key


def rr(v):return round(float(v or 0),2)

def report1(pdf,targets=None,include_food=False):
    reference=(targets or {}).get('targets', dict(BASE_TARGETS))
    source='Excel' if targets else 'الصورة المرجعية (مبدئي)'
    result=[]; totals={'target':0.,'actual':0.,'food':0.,'count':0}
    for branch,values in pdf['rows'].items():
        t=rr(reference.get(branch,0));act=rr(values['actual']);food=rr(values['food']);net=rr(act-(0 if include_food else food))
        result.append({'branch':branch,'target':t,'actual':act,'food':food,'net':net,
                       'gap':rr(t-net),'achievement':round(net/t*100,2) if t>0 else None,'target_missing':t==0})
        totals['target']+=t;totals['actual']+=act;totals['food']+=food;totals['count']+=1
    for k in ['target','actual','food']:totals[k]=rr(totals[k])
    totals['net']=rr(totals['actual']-(0 if include_food else totals['food']))
    totals['gap']=rr(totals['target']-totals['net'])
    totals['achievement']=round(totals['net']/totals['target']*100,2) if totals['target'] else None
    missing=[b for b in reference if b not in pdf['rows']]
    return {'type':'r1','rows':result,'totals':totals,'period':pdf.get('period'),'include_food':include_food,
            'target_source':source,'missing_target_branches':[x['branch'] for x in result if x['target_missing']],
            'missing_pdf_branches':missing,
            'reconciliation':abs(totals['actual']-(pdf.get('reported_total') or (totals['actual'],))[0])<.01}


def report2(payments,contracts,classification):
    type_by_contract=contracts['mapping'];sector_by_type=classification['mapping']
    group=defaultdict(lambda:{'sector':'','fees':0.,'principal':0.,'total':0.,'transactions':0,'contracts':set()})
    unmatched_contract=[];unmatched_type=[]; details=[]; seen=set()
    for con,a,b in payments['rows']:
        t=type_by_contract.get(con)
        sector=sector_by_type.get(key(t),'') if t else ''
        if not t:
            sector='غير مرتبط بالعقود الإضافية';
            if len(unmatched_contract)<100:unmatched_contract.append(con)
        elif not sector:
            sector='غير مصنف';
            if len(unmatched_type)<100:unmatched_type.append(t)
        obj=group[sector];obj['sector']=sector;obj['fees']+=a;obj['principal']+=b;obj['total']+=a+b;obj['transactions']+=1;obj['contracts'].add(con)
        details.append({'contract':con,'loan_type':t or '', 'sector':sector,'fees':a,'principal':b,'total':rr(a+b)})
    grand=rr(sum(x['total'] for x in group.values()))
    result=[]
    for value in group.values():
        result.append({'sector':value['sector'],'fees':rr(value['fees']),'principal':rr(value['principal']),
                       'total':rr(value['total']),'transactions':value['transactions'],
                       'contracts':len(value['contracts']), 'percentage':round(value['total']/grand*100,2) if grand else 0})
    result.sort(key=lambda x:x['total'],reverse=True)
    return {'type':'r2','rows':result,'details':details,'total':grand,
            'count':len(details),'unmatched_contract':unmatched_contract,'unmatched_type':sorted(set(unmatched_type)),
            'fees':rr(sum(v['fees'] for v in result)),'principal':rr(sum(v['principal'] for v in result)),
            'source_total':payments['total'],'reconciled':abs(grand-payments['total'])<=.02}


def report3(current,previous=None,rescheduling=None,payments=None,payments_aligned=False):
    current_contracts=current['contracts']; prev_contracts=(previous or {}).get('contracts',{})
    schedule=(rescheduling or {}).get('mapping',{})
    paid_by_contract=defaultdict(float)
    if payments and payments_aligned:
        for con,a,b in payments.get('rows',[]):paid_by_contract[con]+=a+b
    cases=[]; summary=defaultdict(float)
    ids=set(current_contracts)
    # Contracts absent in the current snapshot can be materially important.
    if previous: ids|=set(prev_contracts)
    for con in sorted(ids):
        cur=current_contracts.get(con); old=prev_contracts.get(con)
        if not cur and not old:continue
        p=cur or old
        prev_bal=rr((old or {}).get('balance_loan',0)+(old or {}).get('balance_fee',0)) if old else None
        now_bal=rr((cur or {}).get('balance_loan',0)+(cur or {}).get('balance_fee',0)) if cur else None
        old_due=rr((old or {}).get('due_loan',0)+(old or {}).get('due_fee',0)) if old else None
        new_due=rr((cur or {}).get('due_loan',0)+(cur or {}).get('due_fee',0)) if cur else None
        moved=rr((cur or {}).get('debt_transfer',0))
        sched=rr(schedule.get(con,0))
        cash=rr(paid_by_contract.get(con,0)) if payments_aligned else None
        kinds=[];transfer_amount=0.;partial_cash=0.;full_cash=0.;due_cash=0.;unexplained=0.;stock_status=''
        if cur:
            if now_bal<=.01 and moved>.01:stock_status='رصيد صفري - نقل دين ظاهر'
            elif now_bal<=.01:stock_status='رصيد صفري - دون نقل دين ظاهر'
            elif new_due<=.01:stock_status='رصيد قائم - المستحق صفر'
            else:stock_status='رصيد ومستحق قائمان'
        else:stock_status='غائب عن المحفظة الحالية - يتطلب تدقيقاً'
        if sched>.01:
            kinds.append('إعادة جدولة (ارتباط بالملف)')
            summary['rescheduled']+=sched;summary['rescheduled_contracts']+=1
        if old is not None and cur is not None:
            reduction=max(0.,rr(prev_bal-now_bal))
            due_reduction=max(0.,rr(old_due-new_due))
            transfer_event=now_bal<=.01 and prev_bal>.01 and moved>.01
            if transfer_event:
                transfer_amount=min(prev_bal,moved)
                kinds.append('نقل مديونية')
                summary['transferred']+=transfer_amount;summary['transfer_contracts']+=1
            remaining_reduction=max(0.,rr(reduction-transfer_amount))
            if payments_aligned:
                # Positive, window-aligned payment transactions are evidence of cash.
                confirmed_cash=max(0.,min(remaining_reduction,rr(cash)))
            elif sched>.01 or transfer_event:
                # Never present reductions driven by restructure as confirmed cash.
                confirmed_cash=0.
            else:
                confirmed_cash=0.
            # Full settlement: non-transfer and transition from outstanding to zero.
            if now_bal<=.01 and prev_bal>.01 and not transfer_event:
                kinds.append('سداد كلي' if confirmed_cash>=remaining_reduction-.01 and confirmed_cash>0 else 'إقفال كامل - السداد غير مؤكد')
                full_cash=confirmed_cash
                summary['full_paid_contracts']+=1
            elif old_due>.01 and new_due<=.01 and now_bal>.01:
                if confirmed_cash>.01:
                    due_cash=min(confirmed_cash,due_reduction)
                    kinds.append('سداد الأقساط المستحقة')
                    summary['due_paid_contracts']+=1
                elif sched<=.01:
                    kinds.append('تصفير المستحق - السبب غير مؤكد')
            elif old_due>new_due+.01 and new_due>.01 and confirmed_cash>.01:
                kinds.append('سداد جزئي للمستحق')
                partial_cash=min(confirmed_cash,due_reduction)
                summary['partial_paid_contracts']+=1
            # A prior cash payment can happen before either restructure or transfer.
            # If it is cash not accounted for by due/full buckets, allocate to partial.
            assigned=rr(full_cash+due_cash+partial_cash)
            if confirmed_cash>assigned+.01:
                partial_cash=rr(partial_cash+confirmed_cash-assigned)
                if 'سداد جزئي للمستحق' not in kinds:
                    kinds.append('سداد جزئي قبل/مع المعالجة')
                    summary['partial_paid_contracts']+=1
            unexplained=max(0.,rr(remaining_reduction-confirmed_cash))
            summary['confirmed_cash']+=confirmed_cash
            summary['unexplained_reduction']+=unexplained
        else:
            if not old:kinds.append('لا توجد محفظة سابقة لإثبات حركة الفترة')
            if not cur:kinds.append('العقد غير موجود في المحفظة الحالية')
        if not kinds:kinds=['لا توجد حركة مؤكدة']
        summary['writeoff']+=p['writeoff'];summary['current_balance']+=(now_bal or 0)
        if (cur or old):summary['contracts']+=1
        cases.append({'contract':con,'branch':p['branch'],'status':stock_status,'events':'، '.join(kinds),
                      'old_balance':prev_bal,'current_balance':now_bal,'old_due':old_due,'current_due':new_due,
                      'writeoff':p['writeoff'],'writeoff_loan':p['writeoff_loan'],'writeoff_fee':p['writeoff_fee'],
                      'schedule_amount':sched,'transfer_amount':rr(transfer_amount),
                      'cash_matched':rr(cash) if cash is not None else None,'partial_paid':rr(partial_cash),
                      'full_paid':rr(full_cash),'due_paid':rr(due_cash),'unexplained':rr(unexplained)})
    for k in list(summary):summary[k]=rr(summary[k])
    summary['contracts']=int(summary['contracts']);summary['rescheduled_contracts']=int(summary['rescheduled_contracts']);summary['transfer_contracts']=int(summary['transfer_contracts']);summary['partial_paid_contracts']=int(summary['partial_paid_contracts']);summary['full_paid_contracts']=int(summary['full_paid_contracts']);summary['due_paid_contracts']=int(summary['due_paid_contracts'])
    # Related schedule does not mean this happened within the chosen period.
    unmatched_schedule=[con for con in schedule if con not in current_contracts and con not in prev_contracts]
    return {'type':'r3','rows':cases,'summary':summary,'has_previous':bool(previous),
            'has_rescheduling':bool(rescheduling),'has_payment_evidence':bool(payments and payments_aligned),
            'unmatched_schedule':unmatched_schedule[:100], 'unmatched_schedule_count':len(unmatched_schedule)}
