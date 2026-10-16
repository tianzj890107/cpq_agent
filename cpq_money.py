"""业务金额到分；中间几何、工时和费率不使用此函数。"""
from decimal import Decimal, ROUND_HALF_UP


def money(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def total(unit, quantity):
    return money(Decimal(str(money(unit))) * Decimal(str(quantity)))
