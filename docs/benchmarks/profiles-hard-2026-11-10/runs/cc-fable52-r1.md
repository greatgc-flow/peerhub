```python
def evaluate(src):
    try:
        return _evaluate(src)
    except Exception:
        return "SYNTAX"


_LIM = 10 ** 18
_WS = frozenset(' \t\n')
_DIGITS = frozenset('0123456789')
_NAME_START = frozenset('abcdefghijklmnopqrstuvwxyz_')
_NAME_CHARS = frozenset('abcdefghijklmnopqrstuvwxyz_0123456789')
_SYMBOLS = frozenset('+-*/%^()=;')
_PREC = {'+': 1, '-': 1, '*': 2, '/': 2, '%': 2, 'u+': 3, 'u-': 3, '^': 4}
_BINARY = frozenset('+-*/%^')


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in _WS:
            i += 1
        elif c in _DIGITS:
            j = i + 1
            while j < n and src[j] in _DIGITS:
                j += 1
            if j - i > 1 and c == '0':
                return None
            if j - i > 19:
                toks.append(('n', None))  # certainly exceeds 10^18
            else:
                toks.append(('n', int(src[i:j])))
            i = j
        elif c in _NAME_START:
            j = i + 1
            while j < n and src[j] in _NAME_CHARS:
                j += 1
            toks.append(('v', src[i:j]))
            i = j
        elif c in _SYMBOLS:
            toks.append(('o', c))
            i += 1
        else:
            return None
    return toks


def _parse_stmt(seg):
    target = None
    body = seg
    if len(seg) >= 2 and seg[0][0] == 'v' and seg[1][0] == 'o' and seg[1][1] == '=':
        target = seg[0][1]
        body = seg[2:]
    out = []
    ops = []
    expect_operand = True
    for kind, val in body:
        if expect_operand:
            if kind == 'n' or kind == 'v':
                out.append((kind, val))
                expect_operand = False
            elif kind == 'o' and val == '(':
                ops.append('(')
            elif kind == 'o' and (val == '+' or val == '-'):
                ops.append('u' + val)
            else:
                return None
        else:
            if kind != 'o':
                return None
            if val == ')':
                while ops and ops[-1] != '(':
                    out.append(('o', ops.pop()))
                if not ops:
                    return None
                ops.pop()
            elif val in _BINARY:
                if val != '^':
                    p = _PREC[val]
                    while ops and ops[-1] != '(' and _PREC[ops[-1]] >= p:
                        out.append(('o', ops.pop()))
                ops.append(val)
                expect_operand = True
            else:
                return None
    if expect_operand:
        return None
    while ops:
        op = ops.pop()
        if op == '(':
            return None
        out.append(('o', op))
    return (target, out)


def _evaluate(src):
    if not isinstance(src, str):
        return "SYNTAX"
    toks = _lex(src)
    if toks is None:
        return "SYNTAX"

    segs = [[]]
    for t in toks:
        if t[0] == 'o' and t[1] == ';':
            segs.append([])
        else:
            segs[-1].append(t)
    if len(segs) > 1 and not segs[-1]:
        segs.pop()

    stmts = []
    for seg in segs:
        if not seg:
            return "SYNTAX"
        parsed = _parse_stmt(seg)
        if parsed is None:
            return "SYNTAX"
        stmts.append(parsed)
    if not stmts:
        return "SYNTAX"

    env = {}
    last = None
    for target, rpn in stmts:
        st = []
        for kind, val in rpn:
            if kind == 'n':
                if val is None or val > _LIM:
                    return "OVERFLOW"
                st.append(val)
            elif kind == 'v':
                if val not in env:
                    return "UNDEFINED"
                st.append(env[val])
            else:
                if val == 'u+':
                    continue
                if val == 'u-':
                    st[-1] = -st[-1]
                    continue
                b = st.pop()
                a = st.pop()
                if val == '+':
                    r = a + b
                elif val == '-':
                    r = a - b
                elif val == '*':
                    r = a * b
                elif val == '/' or val == '%':
                    if b == 0:
                        return "DIV_ZERO"
                    q = abs(a) // abs(b)
                    if (a < 0) != (b < 0):
                        q = -q
                    r = q if val == '/' else a - b * q
                else:  # '^'
                    if b < 0:
                        return "NEG_EXP"
                    if b == 0:
                        r = 1
                    elif a == 0:
                        r = 0
                    elif a == 1:
                        r = 1
                    elif a == -1:
                        r = -1 if (b & 1) else 1
                    elif b > 60:
                        return "OVERFLOW"
                    else:
                        r = a ** b
                if r > _LIM or r < -_LIM:
                    return "OVERFLOW"
                st.append(r)
        value = st[-1]
        if target is not None:
            env[target] = value
        last = value
    return last
```
