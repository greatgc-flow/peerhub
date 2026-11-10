```python
import sys

_LIM = 10 ** 18


class _Syn(Exception):
    pass


class _RT(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == ' ' or c == '\t' or c == '\n':
            i += 1
        elif '0' <= c <= '9':
            j = i
            while j < n and '0' <= src[j] <= '9':
                j += 1
            s = src[i:j]
            if len(s) > 1 and s[0] == '0':
                raise _Syn()
            val = int(s) if len(s) <= 19 else _LIM + 1
            toks.append(('n', val))
            i = j
        elif c == '_' or 'a' <= c <= 'z':
            j = i + 1
            while j < n and (src[j] == '_' or 'a' <= src[j] <= 'z' or '0' <= src[j] <= '9'):
                j += 1
            toks.append(('id', src[i:j]))
            i = j
        elif c in '+-*/%^()=;':
            toks.append(('op', c))
            i += 1
        else:
            raise _Syn()
    return toks


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def peek(self):
        if self.p < len(self.t):
            return self.t[self.p]
        return None

    def is_op(self, chars):
        tk = self.peek()
        return tk is not None and tk[0] == 'op' and tk[1] in chars

    def program(self):
        stmts = [self.stmt()]
        while self.is_op(';'):
            self.p += 1
            if self.p >= len(self.t):
                break
            stmts.append(self.stmt())
        if self.p != len(self.t):
            raise _Syn()
        return stmts

    def stmt(self):
        tk = self.peek()
        if tk is None:
            raise _Syn()
        if tk[0] == 'id' and self.p + 1 < len(self.t) and self.t[self.p + 1] == ('op', '='):
            self.p += 2
            return ('assign', tk[1], self.expr())
        return self.expr()

    def expr(self):
        first = self.term()
        items = []
        while self.is_op('+-'):
            op = self.t[self.p][1]
            self.p += 1
            items.append((op, self.term()))
        if not items:
            return first
        return ('chain', first, items)

    def term(self):
        first = self.unary()
        items = []
        while self.is_op('*/%'):
            op = self.t[self.p][1]
            self.p += 1
            items.append((op, self.unary()))
        if not items:
            return first
        return ('chain', first, items)

    def unary(self):
        if self.is_op('+-'):
            op = self.t[self.p][1]
            self.p += 1
            return ('un', op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.is_op('^'):
            self.p += 1
            return ('pow', base, self.unary())
        return base

    def primary(self):
        tk = self.peek()
        if tk is None:
            raise _Syn()
        if tk[0] == 'n':
            self.p += 1
            return ('num', tk[1])
        if tk[0] == 'id':
            self.p += 1
            return ('var', tk[1])
        if tk == ('op', '('):
            self.p += 1
            e = self.expr()
            if not self.is_op(')'):
                raise _Syn()
            self.p += 1
            return e
        raise _Syn()


def _chk(v):
    if v > _LIM or v < -_LIM:
        raise _RT("OVERFLOW")
    return v


def _apply(op, a, b):
    if op == '+':
        return _chk(a + b)
    if op == '-':
        return _chk(a - b)
    if op == '*':
        return _chk(a * b)
    if b == 0:
        raise _RT("DIV_ZERO")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    if op == '/':
        return _chk(q)
    return _chk(a - b * q)


def _pow(a, e):
    if e < 0:
        raise _RT("NEG_EXP")
    if e == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return 1 if e % 2 == 0 else -1
    if e > 62:
        raise _RT("OVERFLOW")
    return _chk(a ** e)


def _ev(node, env):
    k = node[0]
    if k == 'num':
        return _chk(node[1])
    if k == 'var':
        if node[1] not in env:
            raise _RT("UNDEFINED")
        return env[node[1]]
    if k == 'chain':
        acc = _ev(node[1], env)
        for op, sub in node[2]:
            r = _ev(sub, env)
            acc = _apply(op, acc, r)
        return acc
    if k == 'un':
        v = _ev(node[2], env)
        return _chk(-v if node[1] == '-' else v)
    if k == 'pow':
        a = _ev(node[1], env)
        e = _ev(node[2], env)
        return _pow(a, e)
    raise _Syn()


def _run(src):
    toks = _lex(src)
    stmts = _Parser(toks).program()
    env = {}
    val = 0
    try:
        for s in stmts:
            if s[0] == 'assign':
                val = _ev(s[2], env)
                env[s[1]] = val
            else:
                val = _ev(s, env)
    except _RT as e:
        return e.code
    return val


def evaluate(src):
    try:
        if sys.getrecursionlimit() < 6000:
            sys.setrecursionlimit(6000)
        if not isinstance(src, str):
            return "SYNTAX"
        return _run(src)
    except _Syn:
        return "SYNTAX"
    except _RT as e:
        return e.code
    except Exception:
        return "SYNTAX"
```
