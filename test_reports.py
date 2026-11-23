from app.reports import report1, report2, report3


def test_branch_report_excludes_food_and_keeps_separate_figure():
    pdf={'rows':{'الرياض':{'actual':1000,'food':300}},'period':'2026-10-31','reported_total':(1000,300)}
    targ={'targets':{'الرياض':1000}}
    r=report1(pdf,targ)
    assert r['totals']['actual']==1000
    assert r['totals']['food']==300
    assert r['totals']['net']==700
    assert r['totals']['achievement']==70
    r=report1(pdf,targ,include_food=True)
    assert r['totals']['net']==1000


def test_payment_classification_preserves_alpha_contracts_and_reconciles():
    payments={'rows':[['000123',50,500],['A00012',0,200]],'total':750}
    contracts={'mapping':{'000123':'نوع أ','A00012':'نوع ب'}}
    sectors={'mapping':{'نوعا':'قطاع ١','نوعب':'قطاع ٢'}}
    r=report2(payments,contracts,sectors)
    assert r['total']==750 and r['count']==2 and r['reconciled']
    assert len(r['rows'])==2


def _case(balance,due,transfer=0):
    return {'contract':'ABC123','branch':'الرياض','balance_loan':balance,'balance_fee':0,
            'due_loan':due,'due_fee':0,'writeoff':1000,'writeoff_loan':1000,'writeoff_fee':0,'debt_transfer':transfer}


def test_partial_cash_before_transfer_is_not_double_counted():
    prev={'contracts':{'ABC123':_case(1000,600)}}
    curr={'contracts':{'ABC123':_case(0,0,800)}}
    payments={'rows':[['ABC123',0,200]]}
    r=report3(curr,prev,payments=payments,payments_aligned=True)
    assert r['summary']['transferred']==800
    assert r['summary']['confirmed_cash']==200
    assert r['summary']['partial_paid_contracts']==1
    assert r['summary']['unexplained_reduction']==0


def test_partial_cash_before_reschedule_and_no_previous_is_cautious():
    prev={'contracts':{'ABC123':_case(1000,600)}}
    curr={'contracts':{'ABC123':_case(800,400)}}
    schedule={'mapping':{'ABC123':800}}
    paid={'rows':[['ABC123',0,200]]}
    r=report3(curr,prev,schedule,paid,True)
    assert r['summary']['rescheduled']==800
    assert r['summary']['confirmed_cash']==200
    assert r['summary']['partial_paid_contracts']==1
    r=report3(curr,None,schedule,None,False)
    assert r['summary']['confirmed_cash']==0
    assert not r['has_previous']
