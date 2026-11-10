```python
import sys

_LIMIT = 10 ** 18
_WS = frozenset(" \t\n")
_DIGITS = frozenset("0123456789")
_ID_START = frozenset("abcdefghijklmnopqrstuvwxyz_")
_ID_CHAR = _ID_START | _DIGITS
_SYMS = frozenset("+-*/%^()=;")


class _Fail(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in _WS:
            i += 1
        elif c in _DIGITS:
            j = i
            while j < n and src[j] in _DIGITS:
                j += 1
            text = src[i:j]
            if len(text) > 1 and text[0] == "0":
                raise _Fail("SYNTAX")
            toks.append(("num", text))
            i = j
        elif c in _ID_START:
            j = i + 1
            while j < n and src[j] in _ID_CHAR:
                j += 1
            toks.append(("name", src[i:j]))
            i = j
        elif c in _SYMS:
            toks.append(("op", c))
            i += 1
        else:
            raise _Fail("SYNTAX")
    toks.append(("end", None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self, k=0):
        return self.toks[min(self.pos + k, len(self.toks) - 1)]

    def is_op(self, sym, k=0):
        t = self.peek(k)
        return t[0] == "op" and t[1] == sym

    def advance(self):
        t = self.peek()
        self.pos = min(self.pos + 1, len(self.toks) - 1)
        return t

    def program(self):
        stmts = [self.stmt()]
        while self.is_op(";"):
            self.advance()
            if self.peek()[0] == "end":
                break
            stmts.append(self.stmt())
        if self.peek()[0] != "end":
            raise _Fail("SYNTAX")
        return stmts

    def stmt(self):
        if self.peek()[0] == "name" and self.is_op("=", 1):
            target = self.advance()[1]
            self.advance()
            return target, self.expr()
        return None, self.expr()

    def expr(self):
        node = self.term()
        while self.is_op("+") or self.is_op("-"):
            op = self.advance()[1]
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.unary()
        while self.is_op("*") or self.is_op("/") or self.is_op("%"):
            op = self.advance()[1]
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.is_op("+") or self.is_op("-"):
            op = self.advance()[1]
            return ("un", op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.is_op("^"):
            self.advance()
            return ("bin", "^", base, self.unary())
        return base

    def primary(self):
        t = self.peek()
        if t[0] == "num":
            self.advance()
            return ("num", t[1])
        if t[0] == "name":
            self.advance()
            return ("name", t[1])
        if self.is_op("("):
            self.advance()
            e = self.expr()
            if not self.is_op(")"):
                raise _Fail("SYNTAX")
            self.advance()
            return e
        raise _Fail("SYNTAX")


def _check(v):
    if v > _LIMIT or v < -_LIMIT:
        raise _Fail("OVERFLOW")
    return v


def _pow(a, b):
    # b >= 0 is guaranteed by the caller
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return 1 if b % 2 == 0 else -1
    # |a| >= 2: magnitude doubles each step, so this exits within ~61 iterations
    r = 1
    for _ in range(b):
        r = _check(r * a)
    return r


def _ev(node, env):
    kind = node[0]
    if kind == "num":
        text = node[1]
        if len(text) > 19:  # 20+ digits without leading zero is >= 10^19
            raise _Fail("OVERFLOW")
        return _check(int(text))
    if kind == "name":
        if node[1] not in env:
            raise _Fail("UNDEFINED")
        return env[node[1]]
    if kind == "un":
        v = _ev(node[2], env)
        return _check(-v if node[1] == "-" else v)
    # binary
    op = node[1]
    a = _ev(node[2], env)
    b = _ev(node[3], env)
    if op == "+":
        return _check(a + b)
    if op == "-":
        return _check(a - b)
    if op == "*":
        return _check(a * b)
    if op == "^":
        if b < 0:
            raise _Fail("NEG_EXP")
        return _pow(a, b)
    # '/' and '%'
    if b == 0:
        raise _Fail("DIV_ZERO")
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    if op == "/":
        return _check(q)
    return _check(a - b * q)


def evaluate(src):
    old = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(max(old, 20000))
        toks = _lex(src)
        stmts = _Parser(toks).program()  # whole-program syntax check first
        env = {}
        last = None
        for target, node in stmts:
            v = _ev(node, env)
            if target is not None:
                env[target] = v
            last = v
        return last
    except _Fail as e:
        return e.code
    except Exception:
        return "SYNTAX"
    finally:
        sys.setrecursionlimit(old)
```
