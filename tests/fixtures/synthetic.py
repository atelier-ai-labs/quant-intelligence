from datetime import date, timedelta
from quant_intelligence.models import Bar

def bars(closes: list[float], opens: list[float] | None = None) -> list[Bar]:
    opens = opens or closes
    return [Bar(date(2020, 1, 1) + timedelta(days=i), o, max(o, c), min(o, c), c, 1000) for i, (o, c) in enumerate(zip(opens, closes))]
