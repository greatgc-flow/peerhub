```python
import sys

_LIM = 10 ** 18
_MAX_DEPTH = 500


class _Err(Exception):
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
                raise _Err("SYNTAX")
            toks.append(('num', s))
            i = j
        elif ('a' <= c <= 'z') or c == '_':
            j = i + 1
            while j < n and (('a' <= src[j] <= 'z') or ('0' <= src[j] <= '9') or src[j] == '_'):
                j += 1
            toks.append(('name', src[i:j]))
            i = j
        elif c in '+-*/%^()=;':
            toks.append((c, c))
            i += 1
        else:
            raise _Err("SYNTAX")
    toks.append(('eof', None))
    return toks


class _Parser(object):
    def __init__(self, toks):
        self.t = toks
        self.p = 0
        self.depth = 0

    def peek(self):
        return self.t[self.p][0]

    def adv(self):
        tok = self.t[self.p]
        self.p += 1
        return tok

    def enter(self):
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise _Err("SYNTAX")

    def program(self):
        stmts = []
        while True:
            stmts.append(self.stmt())
            k = self.peek()
            if k == ';':
                self.adv()
                if self.peek() == 'eof':
                    break
            elif k == 'eof':
                break
            else:
                raise _Err("SYNTAX")
        return stmts

    def stmt(self):
        if self.peek() == 'name' and self.t[self.p + 1][0] == '=':
            name = self.adv()[1]
            self.adv()
            return ('asg', name, self.expr())
        return ('exp', self.expr())

    def expr(self):
        first = self.term()
        rest = []
        while self.peek() in ('+', '-'):
            op = self.adv()[0]
            rest.append((op, self.term()))
        return ('ch', first, rest) if rest else first

    def term(self):
        first = self.unary()
        rest = []
        while self.peek() in ('*', '/', '%'):
            op = self.adv()[0]
            rest.append((op, self.unary()))
        return ('ch', first, rest) if rest else first

    def unary(self):
        signs = []
        while self.peek() in ('+', '-'):
            signs.append(self.adv()[0])
        node = self.power()
        return ('un', signs, node) if signs else node

    def power(self):
        base = self.primary()
        if self.peek() == '^':
            self.adv()
            self.enter()
            ex = self.unary()
            self.depth -= 1
            return ('pow', base, ex)
        return base

    def primary(self):
        k = self.peek()
        if k == 'num':
            s = self.adv()[1]
            if len(s) > 19:
                v = None
            else:
                v = int(s)
                if v > _LIM:
                    v = None
            return ('num', v)
        if k == 'name':
            return ('var', self.adv()[1])
        if k == '(':
            self.adv()
            self.enter()
            e = self.expr()
            if self.peek() != ')':
                raise _Err("SYNTAX")
            self.adv()
            self.depth -= 1
            return e
        raise _Err("SYNTAX")


def _chk(v):
    if v > _LIM or v < -_LIM:
        raise _Err("OVERFLOW")
    return v


def _binop(op, a, b):
    if op == '+':
        return _chk(a + b)
    if op == '-':
        return _chk(a - b)
    if op == '*':
        return _chk(a * b)
    # '/' or '%'
    if b == 0:
        raise _Err("DIV_ZERO")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    if op == '/':
        return _chk(q)
    return _chk(a - b * q)


def _powop(a, b):
    if b < 0:
        raise _Err("NEG_EXP")
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return 1 if b % 2 == 0 else -1
    if b >= 64:
        raise _Err("OVERFLOW")
    return _chk(a ** b)


def _ev(node, env):
    k = node[0]
    if k == 'num':
        if node[1] is None:
            raise _Err("OVERFLOW")
        return node[1]
    if k == 'var':
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if k == 'ch':
        v = _ev(node[1], env)
        for op, nd in node[2]:
            r = _ev(nd, env)
            v = _binop(op, v, r)
        return v
    if k == 'un':
        v = _ev(node[2], env)
        for s in reversed(node[1]):
            if s == '-':
                v = -v
            v = _chk(v)
        return v
    if k == 'pow':
        a = _ev(node[1], env)
        b = _ev(node[2], env)
        return _powop(a, b)
    raise _Err("SYNTAX")


def _run(src):
    if not isinstance(src, str):
        return "SYNTAX"
    try:
        toks = _lex(src)
        stmts = _Parser(toks).program()
    except _Err as e:
        return e.code
    except RecursionError:
        return "SYNTAX"
    env = {}
    last = None
    try:
        for st in stmts:
            if st[0] == 'asg':
                v = _ev(st[2], env)
                env[st[1]] = v
                last = v
            else:
                last = _ev(st[1], env)
    except _Err as e:
        return e.code
    return last


def evaluate(src: str):
    old = sys.getrecursionlimit()
    try:
        try:
            if old < 10000:
                sys.setrecursionlimit(10000)
        except Exception:
            pass
        res = _run(src)
        if res is None:
            return "SYNTAX"
        return res
    except Exception:
        return "SYNTAX"
    finally:
        try:
            sys.setrecursionlimit(old)
        except Exception:
            pass
```
