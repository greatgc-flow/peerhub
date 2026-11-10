```python
import sys

class _Err(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code

_LIM = 10 ** 18
_DIG = set("0123456789")
_NSTART = set("abcdefghijklmnopqrstuvwxyz_")
_NCHAR = _NSTART | _DIG
_SYM = set("+-*/%^()=;")
_WS = set(" \t\n")


def _tokenize(src):
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in _WS:
            i += 1
        elif c in _DIG:
            j = i
            while j < n and src[j] in _DIG:
                j += 1
            s = src[i:j]
            if len(s) > 1 and s[0] == '0':
                raise _Err("SYNTAX")
            v = int(s) if len(s) <= 20 else None
            if v is not None and v > _LIM:
                v = None
            toks.append(('num', v))
            i = j
        elif c in _NSTART:
            j = i
            while j < n and src[j] in _NCHAR:
                j += 1
            toks.append(('name', src[i:j]))
            i = j
        elif c in _SYM:
            toks.append((c, None))
            i += 1
        else:
            raise _Err("SYNTAX")
    toks.append(('eof', None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def kind(self, k=0):
        return self.t[self.p + k][0]

    def adv(self):
        tok = self.t[self.p]
        self.p += 1
        return tok

    def program(self):
        stmts = [self.stmt()]
        while self.kind() == ';':
            self.adv()
            if self.kind() == 'eof':
                break
            stmts.append(self.stmt())
        if self.kind() != 'eof':
            raise _Err("SYNTAX")
        return stmts

    def stmt(self):
        if self.kind() == 'name' and self.kind(1) == '=':
            name = self.adv()[1]
            self.adv()
            return ('assign', name, self.expr())
        return ('expr', self.expr())

    def expr(self):
        first = self.term()
        rest = []
        while self.kind() in ('+', '-'):
            op = self.adv()[0]
            rest.append((op, self.term()))
        return first if not rest else ('chain', first, rest)

    def term(self):
        first = self.unary()
        rest = []
        while self.kind() in ('*', '/', '%'):
            op = self.adv()[0]
            rest.append((op, self.unary()))
        return first if not rest else ('chain', first, rest)

    def unary(self):
        if self.kind() in ('+', '-'):
            op = self.adv()[0]
            return ('un', op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.kind() == '^':
            self.adv()
            return ('pow', base, self.unary())
        return base

    def primary(self):
        k = self.kind()
        if k == 'num':
            return ('num', self.adv()[1])
        if k == 'name':
            return ('var', self.adv()[1])
        if k == '(':
            self.adv()
            e = self.expr()
            if self.kind() != ')':
                raise _Err("SYNTAX")
            self.adv()
            return e
        raise _Err("SYNTAX")


def _chk(v):
    if v > _LIM or v < -_LIM:
        raise _Err("OVERFLOW")
    return v


def _tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _binop(op, a, b):
    if op == '+':
        return _chk(a + b)
    if op == '-':
        return _chk(a - b)
    if op == '*':
        return _chk(a * b)
    if b == 0:
        raise _Err("DIV_ZERO")
    q = _tdiv(a, b)
    if op == '/':
        return _chk(q)
    return _chk(a - b * q)


def _pow(a, b):
    if b < 0:
        raise _Err("NEG_EXP")
    if b == 0:
        return 1
    if a == 0 or a == 1:
        return a
    if a == -1:
        return 1 if b % 2 == 0 else -1
    if b > 64:
        raise _Err("OVERFLOW")
    return _chk(a ** b)


def _eval(node, env):
    k = node[0]
    if k == 'num':
        if node[1] is None:
            raise _Err("OVERFLOW")
        return node[1]
    if k == 'var':
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if k == 'chain':
        v = _eval(node[1], env)
        for op, sub in node[2]:
            r = _eval(sub, env)
            v = _binop(op, v, r)
        return v
    if k == 'un':
        v = _eval(node[2], env)
        return _chk(-v if node[1] == '-' else v)
    if k == 'pow':
        a = _eval(node[1], env)
        b = _eval(node[2], env)
        return _pow(a, b)
    raise _Err("SYNTAX")


def _run(src):
    toks = _tokenize(src)
    stmts = _Parser(toks).program()
    env = {}
    last = None
    for st in stmts:
        if st[0] == 'assign':
            v = _eval(st[2], env)
            env[st[1]] = v
        else:
            v = _eval(st[1], env)
        last = v
    return int(last)


def evaluate(src: str):
    old = sys.getrecursionlimit()
    try:
        if old < 20000:
            sys.setrecursionlimit(20000)
    except Exception:
        pass
    try:
        if not isinstance(src, str):
            return "SYNTAX"
        return _run(src)
    except _Err as e:
        return e.code
    except Exception:
        return "SYNTAX"
    finally:
        try:
            sys.setrecursionlimit(old)
        except Exception:
            pass
```
