```python
import sys

_LIMIT = 10 ** 18


class _Fail(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _chk(r):
    if -_LIMIT <= r <= _LIMIT:
        return r
    raise _Fail("OVERFLOW")


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in " \t\n":
            i += 1
        elif "0" <= c <= "9":
            j = i
            while j < n and "0" <= src[j] <= "9":
                j += 1
            text = src[i:j]
            if len(text) > 1 and text[0] == "0":
                raise _Fail("SYNTAX")
            toks.append(("NUM", text))
            i = j
        elif ("a" <= c <= "z") or c == "_":
            j = i + 1
            while j < n and (("a" <= src[j] <= "z") or src[j] == "_" or ("0" <= src[j] <= "9")):
                j += 1
            toks.append(("NAME", src[i:j]))
            i = j
        elif c in "+-*/%^()=;":
            toks.append(("OP", c))
            i += 1
        else:
            raise _Fail("SYNTAX")
    toks.append(("EOF", None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def _kind(self):
        return self.t[self.p][0]

    def _op(self, ch, k=0):
        tok = self.t[min(self.p + k, len(self.t) - 1)]
        return tok[0] == "OP" and tok[1] == ch

    def program(self):
        stmts = [self.stmt()]
        while self._op(";"):
            self.p += 1
            if self._kind() == "EOF":
                break
            stmts.append(self.stmt())
        if self._kind() != "EOF":
            raise _Fail("SYNTAX")
        return stmts

    def stmt(self):
        if self._kind() == "NAME" and self._op("=", 1):
            name = self.t[self.p][1]
            self.p += 2
            return ("assign", name, self.expr())
        return ("expr", self.expr())

    def expr(self):
        node = self.term()
        while self._op("+") or self._op("-"):
            op = self.t[self.p][1]
            self.p += 1
            right = self.term()
            node = ("bin", op, node, right)
        return node

    def term(self):
        node = self.unary()
        while self._op("*") or self._op("/") or self._op("%"):
            op = self.t[self.p][1]
            self.p += 1
            right = self.unary()
            node = ("bin", op, node, right)
        return node

    def unary(self):
        if self._op("+") or self._op("-"):
            op = self.t[self.p][1]
            self.p += 1
            return ("un", op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self._op("^"):
            self.p += 1
            exp = self.unary()
            return ("bin", "^", base, exp)
        return base

    def primary(self):
        kind, val = self.t[self.p]
        if kind == "NUM":
            self.p += 1
            return ("num", val)
        if kind == "NAME":
            self.p += 1
            return ("name", val)
        if self._op("("):
            self.p += 1
            node = self.expr()
            if not self._op(")"):
                raise _Fail("SYNTAX")
            self.p += 1
            return node
        raise _Fail("SYNTAX")


def _pow(a, b):
    if b < 0:
        raise _Fail("NEG_EXP")
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return 1 if b % 2 == 0 else -1
    if b > 64:  # |a| >= 2 implies |a**b| >= 2**65 > 10**18
        raise _Fail("OVERFLOW")
    return _chk(a ** b)


def _binop(op, a, b):
    if op == "^":
        return _pow(a, b)
    if op == "+":
        return _chk(a + b)
    if op == "-":
        return _chk(a - b)
    if op == "*":
        return _chk(a * b)
    if b == 0:
        raise _Fail("DIV_ZERO")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q  # truncate toward zero
    if op == "/":
        return _chk(q)
    return _chk(a - b * q)  # '%'


def _ev(node, env):
    tag = node[0]
    if tag == "num":
        text = node[1]
        if len(text) > 19:
            raise _Fail("OVERFLOW")
        v = int(text)
        if v > _LIMIT:
            raise _Fail("OVERFLOW")
        return v
    if tag == "name":
        if node[1] not in env:
            raise _Fail("UNDEFINED")
        return env[node[1]]
    if tag == "un":
        v = _ev(node[2], env)
        return _chk(v if node[1] == "+" else -v)
    _, op, ln, rn = node
    a = _ev(ln, env)  # left operand first, always
    b = _ev(rn, env)
    return _binop(op, a, b)


def _run(src):
    toks = _lex(src)
    stmts = _Parser(toks).program()  # whole-program syntax check first
    env = {}
    last = None
    for st in stmts:
        if st[0] == "assign":
            v = _ev(st[2], env)
            env[st[1]] = v
            last = v
        else:
            last = _ev(st[1], env)
    return last


def evaluate(src):
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 20000))
    try:
        return _run(src)
    except _Fail as f:
        return f.code
    except RecursionError:
        return "SYNTAX"
    finally:
        sys.setrecursionlimit(old)
```
