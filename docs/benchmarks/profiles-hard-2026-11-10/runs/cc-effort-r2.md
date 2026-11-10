```python
import sys


class _Err(Exception):
    def __init__(self, code):
        self.code = code


LIM = 10 ** 18


def evaluate(src: str):
    try:
        if sys.getrecursionlimit() < 20000:
            sys.setrecursionlimit(20000)
        return _run(src)
    except _Err as e:
        return e.code
    except Exception:
        return "SYNTAX"


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    digits = "0123456789"
    lower = "abcdefghijklmnopqrstuvwxyz_"
    namech = lower + digits
    while i < n:
        c = src[i]
        if c in " \t\n":
            i += 1
        elif c in digits:
            j = i
            while j < n and src[j] in digits:
                j += 1
            s = src[i:j]
            if len(s) >= 2 and s[0] == "0":
                raise _Err("SYNTAX")
            toks.append(("d", s))
            i = j
        elif c in lower:
            j = i
            while j < n and src[j] in namech:
                j += 1
            toks.append(("n", src[i:j]))
            i = j
        elif c in "+-*/%^()=;":
            toks.append(("s", c))
            i += 1
        else:
            raise _Err("SYNTAX")
    return toks


def _parse(toks):
    pos = [0]
    nt = len(toks)

    def peek_sym():
        if pos[0] < nt:
            k, v = toks[pos[0]]
            if k == "s":
                return v
        return None

    def expect(ch):
        if peek_sym() != ch:
            raise _Err("SYNTAX")
        pos[0] += 1

    def primary():
        if pos[0] >= nt:
            raise _Err("SYNTAX")
        k, v = toks[pos[0]]
        if k == "d":
            pos[0] += 1
            return ("num", v)
        if k == "n":
            pos[0] += 1
            return ("var", v)
        if k == "s" and v == "(":
            pos[0] += 1
            e = expr()
            expect(")")
            return e
        raise _Err("SYNTAX")

    def power():
        base = primary()
        if peek_sym() == "^":
            pos[0] += 1
            ex = unary()
            return ("pow", base, ex)
        return base

    def unary():
        s = peek_sym()
        if s == "+" or s == "-":
            pos[0] += 1
            return ("un", s, unary())
        return power()

    def term():
        left = unary()
        ops = []
        while peek_sym() in ("*", "/", "%"):
            op = peek_sym()
            pos[0] += 1
            ops.append((op, unary()))
        return ("chain", left, ops) if ops else left

    def expr():
        left = term()
        ops = []
        while peek_sym() in ("+", "-"):
            op = peek_sym()
            pos[0] += 1
            ops.append((op, term()))
        return ("chain", left, ops) if ops else left

    def stmt():
        if (pos[0] + 1 < nt and toks[pos[0]][0] == "n"
                and toks[pos[0] + 1] == ("s", "=")):
            name = toks[pos[0]][1]
            pos[0] += 2
            return ("assign", name, expr())
        return expr()

    stmts = []
    while True:
        stmts.append(stmt())
        if peek_sym() == ";":
            pos[0] += 1
            if pos[0] >= nt:
                break
        elif pos[0] >= nt:
            break
        else:
            raise _Err("SYNTAX")
    return stmts


def _chk(v):
    if v > LIM or v < -LIM:
        raise _Err("OVERFLOW")
    return v


def _tdiv(a, b):
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q


def _pow(a, b):
    if b < 0:
        raise _Err("NEG_EXP")
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return -1 if b % 2 else 1
    if b > 62:
        raise _Err("OVERFLOW")
    return _chk(a ** b)


def _eval(node, env):
    t = node[0]
    if t == "num":
        s = node[1]
        if len(s) > 19:
            raise _Err("OVERFLOW")
        return _chk(int(s))
    if t == "var":
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if t == "un":
        v = _eval(node[2], env)
        return _chk(-v if node[1] == "-" else v)
    if t == "pow":
        a = _eval(node[1], env)
        b = _eval(node[2], env)
        return _pow(a, b)
    if t == "chain":
        acc = _eval(node[1], env)
        for op, rn in node[2]:
            r = _eval(rn, env)
            if op == "+":
                acc = _chk(acc + r)
            elif op == "-":
                acc = _chk(acc - r)
            elif op == "*":
                acc = _chk(acc * r)
            else:
                if r == 0:
                    raise _Err("DIV_ZERO")
                q = _tdiv(acc, r)
                if op == "/":
                    acc = _chk(q)
                else:
                    acc = _chk(acc - r * q)
        return acc
    if t == "assign":
        v = _eval(node[2], env)
        env[node[1]] = v
        return v
    raise _Err("SYNTAX")


def _run(src):
    toks = _lex(src)
    stmts = _parse(toks)
    env = {}
    result = 0
    for s in stmts:
        result = _eval(s, env)
    return result
```
