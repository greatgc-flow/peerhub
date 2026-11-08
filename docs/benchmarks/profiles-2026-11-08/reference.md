```python
import re
from fractions import Fraction
_N=r"(\d+(?:[.,]\d+)?)"
_RX=re.compile(rf"([+-]?)P(?:{_N}Y)?(?:{_N}M)?(?:{_N}W)?(?:{_N}D)?(?:T(?:{_N}H)?(?:{_N}M)?(?:{_N}S)?)?")
_U=[365*86400,30*86400,7*86400,86400,3600,60,1]
def parse_duration(s):
    m=_RX.fullmatch(s) if isinstance(s,str) else None
    if not m: raise ValueError(s)
    parts=m.groups()[1:]
    if all(p is None for p in parts): raise ValueError(s)
    if "T" in s and all(p is None for p in parts[4:]): raise ValueError(s)
    present=[i for i,p in enumerate(parts) if p is not None]
    total=Fraction(0)
    for i in present:
        p=parts[i]
        if ("." in p or "," in p) and i!=present[-1]: raise ValueError(s)
        total+=Fraction(p.replace(",","."))*_U[i]
    return -total if m.group(1)=="-" else total
```
