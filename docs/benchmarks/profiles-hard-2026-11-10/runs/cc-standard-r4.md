```python
import sys

_LIMIT = 10 ** 18
_DIGITS = "0123456789"
_NAME_START = "abcdefghijklmnopqrstuvwxyz_"
_NAME_REST = _NAME_START + _DIGITS
_SYMBOLS = "+-*/%^()=;"
_SPACE = " \t\n"


class _Syntax(Exception):
    pass


class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _lex(src):
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in _SPACE:
            i += 1
        elif c in _DIGITS:
            j = i
            while j < n and src[j] in _DIGITS:
                j += 1
            run = src[i:j]
            if len(run) > 1 and run[0] == "0":
                raise _Syntax()
            toks.append(("num", run))
            i = j
        elif c in _NAME_START:
            j = i + 1
            while j < n and src[j] in _NAME_REST:
                j += 1
            toks.append(("name", src[i:j]))
            i = j
        elif c in _SYMBOLS:
            toks.append((c, c))
            i += 1
        else:
            raise _Syntax()
    toks.append(("eof", None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def _kind(self):
        return self.toks[self.pos][0]

    def _advance(self):
        self.pos += 1

    def program(self):
        stmts = [self._stmt()]
        while self._kind() == ";":
            self._advance()
            if self._kind() == "eof":
                break
            stmts.append(self._stmt())
        if self._kind() != "eof":
            raise _Syntax()
        return stmts

    def _stmt(self):
        if self._kind() == "name" and self.toks[self.pos + 1][0] == "=":
            name = self.toks[self.pos][1]
            self.pos += 2
            return ("assign", name, self._expr())
        return ("expr", self._expr())

    def _expr(self):
        node = self._term()
        while self._kind() in ("+", "-"):
            op = self._kind()
            self._advance()
            node = ("bin", op, node, self._term())
        return node

    def _term(self):
        node = self._unary()
        while self._kind() in ("*", "/", "%"):
            op = self._kind()
            self._advance()
            node = ("bin", op, node, self._unary())
        return node

    def _unary(self):
        k = self._kind()
        if k in ("+", "-"):
            self._advance()
            return ("neg" if k == "-" else "pos", self._unary())
        return self._power()

    def _power(self):
        base = self._primary()
        if self._kind() == "^":
            self._advance()
            return ("bin", "^", base, self._unary())
        return base

    def _primary(self):
        k, v = self.toks[self.pos]
        if k == "num":
            self._advance()
            return ("num", v)
        if k == "name":
            self._advance()
            return ("var", v)
        if k == "(":
            self._advance()
            node = self._expr()
            if self._kind() != ")":
                raise _Syntax()
            self._advance()
            return node
        raise _Syntax()


def _check(v):
    if abs(v) > _LIMIT:
        raise _Err("OVERFLOW")
    return v


def _power(a, b):
    # caller guarantees b >= 0
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return 1 if b % 2 == 0 else -1
    # |a| >= 2: |a**b| >= 2**61 > 10**18 when b > 60
    if b > 60:
        raise _Err("OVERFLOW")
    return _check(a ** b)


def _eval(node, env):
    tag = node[0]
    if tag == "num":
        d = node[1]
        if len(d) > 19 or int(d) > _LIMIT:
            raise _Err("OVERFLOW")
        return int(d)
    if tag == "var":
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if tag == "neg":
        return _check(-_eval(node[1], env))
    if tag == "pos":
        return _eval(node[1], env)

    # binary: left operand first, then right operand (no short-circuit)
    op = node[1]
    a = _eval(node[2], env)
    b = _eval(node[3], env)
    if op == "+":
        return _check(a + b)
    if op == "-":
        return _check(a - b)
    if op == "*":
        return _check(a * b)
    if op == "^":
        if b < 0:
            raise _Err("NEG_EXP")
        return _power(a, b)
    if b == 0:
        raise _Err("DIV_ZERO")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    if op == "/":
        return q
    return a - b * q  # remainder takes the sign of a


def evaluate(src):
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 10000))
    try:
        try:
            stmts = _Parser(_lex(src)).program()
        except (_Syntax, RecursionError):
            return "SYNTAX"
        env = {}
        value = None
        for st in stmts:
            if st[0] == "assign":
                value = _eval(st[2], env)
                env[st[1]] = value
            else:
                value = _eval(st[1], env)
        return value
    except _Err as e:
        return e.code
    except RecursionError:
        return "SYNTAX"
    finally:
        sys.setrecursionlimit(old_limit)
```
