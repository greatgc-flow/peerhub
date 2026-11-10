```python
import sys

LIM = 10 ** 18


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
            toks.append(('n', s))
            i = j
        elif c == '_' or ('a' <= c <= 'z'):
            j = i
            while j < n and (src[j] == '_' or 'a' <= src[j] <= 'z' or '0' <= src[j] <= '9'):
                j += 1
            toks.append(('id', src[i:j]))
            i = j
        elif c in '+-*/%^()=;':
            toks.append((c, None))
            i += 1
        else:
            raise _Err("SYNTAX")
    toks.append(('end', None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def peek(self):
        return self.t[self.p][0]

    def program(self):
        stmts = []
        while True:
            stmts.append(self.stmt())
            k = self.peek()
            if k == ';':
                self.p += 1
                if self.peek() == 'end':
                    break
            elif k == 'end':
                break
            else:
                raise _Err("SYNTAX")
        return stmts

    def stmt(self):
        if self.peek() == 'id' and self.t[self.p + 1][0] == '=':
            name = self.t[self.p][1]
            self.p += 2
            return ('a', name, self.expr())
        return ('e', self.expr())

    def expr(self):
        first = self.term()
        rest = []
        while self.peek() in ('+', '-'):
            op = self.peek()
            self.p += 1
            rest.append((op, self.term()))
        if not rest:
            return first
        return ('b', first, rest)

    def term(self):
        first = self.unary()
        rest = []
        while self.peek() in ('*', '/', '%'):
            op = self.peek()
            self.p += 1
            rest.append((op, self.unary()))
        if not rest:
            return first
        return ('b', first, rest)

    def unary(self):
        k = self.peek()
        if k == '+' or k == '-':
            self.p += 1
            return ('u', k, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.peek() == '^':
            self.p += 1
            return ('p', base, self.unary())
        return base

    def primary(self):
        k, v = self.t[self.p]
        if k == 'n':
            self.p += 1
            val = int(v) if len(v) <= 19 else LIM + 1
            return ('n', val)
        if k == 'id':
            self.p += 1
            return ('v', v)
        if k == '(':
            self.p += 1
            e = self.expr()
            if self.peek() != ')':
                raise _Err("SYNTAX")
            self.p += 1
            return e
        raise _Err("SYNTAX")


def _chk(v):
    if v > LIM or v < -LIM:
        raise _Err("OVERFLOW")
    return v


def _eval(node, env):
    k = node[0]
    if k == 'n':
        return _chk(node[1])
    if k == 'v':
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if k == 'u':
        v = _eval(node[2], env)
        return _chk(-v if node[1] == '-' else v)
    if k == 'p':
        a = _eval(node[1], env)
        b = _eval(node[2], env)
        if b < 0:
            raise _Err("NEG_EXP")
        if a == 0:
            return 1 if b == 0 else 0
        if a == 1:
            return 1
        if a == -1:
            return 1 if b % 2 == 0 else -1
        if b >= 64:
            raise _Err("OVERFLOW")
        return _chk(a ** b)
    # binary chain
    left = _eval(node[1], env)
    for op, rn in node[2]:
        right = _eval(rn, env)
        if op == '+':
            left = _chk(left + right)
        elif op == '-':
            left = _chk(left - right)
        elif op == '*':
            left = _chk(left * right)
        else:
            if right == 0:
                raise _Err("DIV_ZERO")
            q = abs(left) // abs(right)
            if (left < 0) != (right < 0):
                q = -q
            if op == '/':
                left = _chk(q)
            else:
                left = _chk(left - right * q)
    return left


def _run(src):
    if not isinstance(src, str):
        return "SYNTAX"
    if sys.getrecursionlimit() < 20000:
        sys.setrecursionlimit(20000)
    try:
        stmts = _Parser(_lex(src)).program()
    except _Err as e:
        return e.code
    env = {}
    val = 0
    try:
        for s in stmts:
            if s[0] == 'a':
                val = _eval(s[2], env)
                env[s[1]] = val
            else:
                val = _eval(s[1], env)
    except _Err as e:
        return e.code
    return val


def evaluate(src: str):
    try:
        return _run(src)
    except Exception:
        return "SYNTAX"
```
