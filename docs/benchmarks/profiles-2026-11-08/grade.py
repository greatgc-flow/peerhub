import sys, re, importlib.util
from fractions import Fraction as F
src = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"```(?:python)?\n(.*?)```", src, re.S)
code = m.group(1) if m else src
ns = {}
try:
    exec(code, ns); f = ns["parse_duration"]
except Exception as e:
    print("LOAD_FAIL", e); sys.exit(0)
D = 86400
ok = [("P1D", D), ("PT1H", 3600), ("PT1M", 60), ("P1M", 30*D), ("P1Y", 365*D), ("P2W", 14*D), ("PT0.5S", F(1,2)),
      ("PT1,25S", F(5,4)), ("-P1D", -D), ("+PT1S", 1), ("P1Y2M3W4DT5H6M7S", 365*D+60*D+21*D+4*D+5*3600+6*60+7),
      ("PT1.5H", 5400), ("P0D", 0), ("PT0S", 0), ("P1DT0.1S", D+F(1,10)), ("P1.5Y", F(3,2)*365*D),
      ("PT0.333333333333333333S", F(333333333333333333, 10**18)), ("P10000D", 10000*D), ("P1MT1M", 30*D+60)]
bad = ["P", "PT", "1D", "P1", "PD", "P1DT", "P1.D", "P.5D", "P1.5DT1H", "P1D1D", "P1DT1H1H", "PT1D", "P1H", "P1M1Y",
       "p1d", "P1d", "P 1D", "P1e3S", "--P1D", "P-1D", "PT1.5H2M", "P1W1Y", "", "PT1M1H", "P1,D", "P1.5M2D"]
score = 0
for s, want in ok:
    try: score += f(s) == want and isinstance(f(s), F)
    except Exception: pass
for s in bad:
    try: f(s)
    except ValueError: score += 1
    except Exception: pass
print(f"SCORE {score}/{len(ok)+len(bad)}")
