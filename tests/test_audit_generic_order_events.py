"""Local engine lifecycle supplements must not misidentify primary generic tables."""
import pytest

from qwb.strategy_imports import SourceFile,normalize
from qwb.strategy_import_formats import detect_adapter
from qwb.audit_decisions import selected_not_ordered


def source(name,text):
    return SourceFile(name,text.encode())


def orders():
    return source('orders.csv',
        'time,event,order_id,code,side,quantity,filled_quantity,status,message\n'
        '2024-01-03 09:31:00,LOCAL_PRETRADE_REJECT,local-1,510180.SH,BUY,100,0,rejected,超过了成交量\n')


def test_generic_tables_with_engine_neutral_order_lifecycle():
    files=[source('trades.csv','date,time,code,side,quantity,price,fee,order_id\n'
        '2024-01-02,09:31:00,510180.SH,buy,100,3,5,filled-1\n'),
        source('account.csv','date,equity,cash\n2024-01-02,995,695\n2024-01-03,995,695\n'),
        orders()]
    assert detect_adapter(files)=='generic'
    bundle=normalize(files,opening_cash=1000)
    assert bundle['adapter']=='generic_v1'
    assert len(bundle['orders'])==1
    assert bundle['orders'][0]['reason']=='超过当根成交量上限'
    assert bundle['orders'][0]['filled']==0


@pytest.mark.parametrize('message',['超过了成交量','当前资金：100 所需资金：305'])
def test_order_supplement_does_not_change_joinquant_origin(message):
    files=[source('trades.csv','日期,标的,交易类型,成交数量,成交价,手续费\n'
        '2024-01-02,510180.XSHG,买,100,3,5\n'),
        source('holdings.csv','日期,品种,标的,多空,数量,可用数量,收盘价/结算价,市值/价值,总资产,仓位占比\n'
            '2024-01-02,股票,510180.XSHG,多,100股,0股,3,300,995,30%\n'
            '2024-01-02,,Cash,,,,,695,,0\n'),
        source('orders.csv',orders().data.decode().replace('超过了成交量',message))]
    assert detect_adapter(files)=='joinquant'
    bundle=normalize(files,opening_cash=1000)
    assert bundle['adapter']=='joinquant_v1'
    assert bundle['snapshots'][0]['cash']==695
    if message.startswith('当前资金'):
        assert bundle['orders'][0]['available_cash']==100
        assert bundle['orders'][0]['required_cash']==305


def test_order_only_keeps_legacy_detection_and_true_mixing_still_fails():
    assert detect_adapter([orders()])=='ricequant'
    files=[source('generic.csv','date,code,quantity,price\n2024-01-02,510180.SH,100,3\n'),
        source('ricequant.csv','合约代码,买/卖,成交量,成交价,时间,费用\n'
        '510180.XSHG,买,100,3,2024-01-02 09:31:00,5\n')]
    with pytest.raises(ValueError,match='不同平台'):
        detect_adapter(files)


def test_recorded_pretrade_rejection_is_an_attempt_not_a_missing_order():
    days=['2024-01-02','2024-01-03','2024-01-04','2024-01-05']
    bundle={'scores':[{'date':d,'code':'510180.SH','score':1.,'selected':True} for d in days],
        'snapshots':[{'date':d} for d in days],'positions':[],'fills':[],
        'orders':[{'date':d,'code':'510180.SH','side':'buy','status':'rejected','quantity':100,'filled':0} for d in days]}
    result=selected_not_ordered(bundle)
    assert result['days']==0 and result['status']=='normal'
