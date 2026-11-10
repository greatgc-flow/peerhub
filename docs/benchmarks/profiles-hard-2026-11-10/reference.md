```python
LIMIT = 10 ** 18
_WS = " \t\n"
_DIG = "0123456789"
_START = "abcdefghijklmnopqrstuvwxyz_"
_NAME = _START + _DIG


class _Err(Exception):
    def __init__(self, code):
        self.code = code


def _lex(src):
    toks, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in _WS:
            i += 1
        elif c in _DIG:
            j = i
            while j < n and src[j] in _DIG:
                j += 1
            if j - i > 1 and c == "0":
                raise _Err("SYNTAX")
            toks.append(("num", src[i:j]))
            i = j
        elif c in _START:
            j = i
            while j < n and src[j] in _NAME:
                j += 1
            toks.append(("name", src[i:j]))
            i = j
        elif c in "+-*/%^()=;":
            toks.append((c, c))
            i += 1
        else:
            raise _Err("SYNTAX")
    return toks


def _parse(toks):
    pos = 0

    def peek(k=0):
        return toks[pos + k][0] if pos + k < len(toks) else None

    def take(kind):
        nonlocal pos
        if peek() != kind:
            raise _Err("SYNTAX")
        pos += 1
        return toks[pos - 1][1]

    def chain(sub, ops):
        nonlocal pos
        first, rest = sub(), []
        while peek() is not None and peek() in ops:
            pos += 1
            rest.append((toks[pos - 1][0], sub()))
        return first if not rest else ("chain", first, rest)

    def expr():
        return chain(term, "+-")

    def term():
        return chain(unary, "*/%")

    def unary():
        nonlocal pos
        if peek() in ("+", "-"):
            pos += 1
            return ("u" + toks[pos - 1][0], unary())
        return power()

    def power():
        nonlocal pos
        base = primary()
        if peek() == "^":
            pos += 1
            return ("chain", base, [("^", unary())])
        return base

    def primary():
        k = peek()
        if k == "num":
            return ("num", take("num"))
        if k == "name":
            return ("name", take("name"))
        take("(")
        e = expr()
        take(")")
        return e

    stmts = []
    while True:
        if peek() == "name" and peek(1) == "=":
            name = take("name")
            take("=")
            stmts.append((name, expr()))
        else:
            stmts.append((None, expr()))
        if peek() is None:
            break
        take(";")
        if peek() is None:
            break
    return stmts


def _chk(v):
    if v > LIMIT or v < -LIMIT:
        raise _Err("OVERFLOW")
    return v


def _pow(a, b):
    if b < 0:
        raise _Err("NEG_EXP")
    if b == 0:
        return 1
    if a in (0, 1):
        return a
    if a == -1:
        return -1 if b % 2 else 1
    r = 1
    for _ in range(b):  # |a| >= 2, so this overflows within 60 steps
        r = _chk(r * a)
    return r


def _binop(op, a, b):
    if op == "+":
        return _chk(a + b)
    if op == "-":
        return _chk(a - b)
    if op == "*":
        return _chk(a * b)
    if op == "^":
        return _pow(a, b)
    if b == 0:
        raise _Err("DIV_ZERO")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q if op == "/" else _chk(a - b * q)


def _eval(node, env):
    kind = node[0]
    if kind == "num":
        s = node[1]
        if len(s) > 19:
            raise _Err("OVERFLOW")
        return _chk(int(s))
    if kind == "name":
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if kind == "u-":
        return _chk(-_eval(node[1], env))
    if kind == "u+":
        return _eval(node[1], env)
    acc = _eval(node[1], env)
    for op, rhs in node[2]:
        acc = _binop(op, acc, _eval(rhs, env))
    return acc


def evaluate(src):
    try:
        if not isinstance(src, str):
            return "SYNTAX"
        toks = _lex(src)
        if not toks:
            return "SYNTAX"
        stmts = _parse(toks)
        env, val = {}, None
        for name, e in stmts:
            val = _eval(e, env)
            if name is not None:
                env[name] = val
        return val
    except _Err as e:
        return e.code
```
