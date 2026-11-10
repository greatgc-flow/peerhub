```python
import sys

_LIMIT = 10 ** 18
_SYMBOLS = set("+-*/%^()=;")


class _SyntaxErr(Exception):
    pass


class _RuntimeErr(Exception):
    def __init__(self, kind):
        Exception.__init__(self, kind)
        self.kind = kind


def _is_digit(c):
    return "0" <= c <= "9"


def _is_name_start(c):
    return ("a" <= c <= "z") or c == "_"


def _is_name_char(c):
    return ("a" <= c <= "z") or ("0" <= c <= "9") or c == "_"


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == " " or c == "\t" or c == "\n":
            i += 1
        elif _is_digit(c):
            j = i
            while j < n and _is_digit(src[j]):
                j += 1
            s = src[i:j]
            if len(s) > 1 and s[0] == "0":
                raise _SyntaxErr()
            if len(s) <= 19:
                v = int(s)
            else:
                v = _LIMIT + 1  # definitely overflows when evaluated
            toks.append(("num", v))
            i = j
        elif _is_name_start(c):
            j = i
            while j < n and _is_name_char(src[j]):
                j += 1
            toks.append(("name", src[i:j]))
            i = j
        elif c in _SYMBOLS:
            toks.append((c, None))
            i += 1
        else:
            raise _SyntaxErr()
    toks.append(("eof", None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self, k=0):
        p = self.pos + k
        if p >= len(self.toks):
            return "eof"
        return self.toks[p][0]

    def advance(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def expect(self, kind):
        if self.peek() != kind:
            raise _SyntaxErr()
        return self.advance()

    def program(self):
        stmts = []
        while True:
            stmts.append(self.stmt())
            k = self.peek()
            if k == ";":
                self.advance()
                if self.peek() == "eof":
                    break
            elif k == "eof":
                break
            else:
                raise _SyntaxErr()
        return stmts

    def stmt(self):
        if self.peek() == "name" and self.peek(1) == "=":
            name = self.advance()[1]
            self.advance()
            return ("a", name, self.expr())
        return ("e", self.expr())

    def expr(self):
        left = self.term()
        while self.peek() in ("+", "-"):
            op = self.advance()[0]
            right = self.term()
            left = ("b", op, left, right)
        return left

    def term(self):
        left = self.unary()
        while self.peek() in ("*", "/", "%"):
            op = self.advance()[0]
            right = self.unary()
            left = ("b", op, left, right)
        return left

    def unary(self):
        ops = []
        while self.peek() in ("+", "-"):
            ops.append(self.advance()[0])
        node = self.power()
        for op in reversed(ops):
            node = ("u", op, node)
        return node

    def power(self):
        base = self.primary()
        if self.peek() == "^":
            self.advance()
            exp = self.unary()
            return ("b", "^", base, exp)
        return base

    def primary(self):
        k = self.peek()
        if k == "num":
            return ("n", self.advance()[1])
        if k == "name":
            return ("v", self.advance()[1])
        if k == "(":
            self.advance()
            e = self.expr()
            self.expect(")")
            return e
        raise _SyntaxErr()


def _check(v):
    if v > _LIMIT or v < -_LIMIT:
        raise _RuntimeErr("OVERFLOW")
    return v


def _pow(a, b):
    if b < 0:
        raise _RuntimeErr("NEG_EXP")
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return -1 if (b & 1) else 1
    # |a| >= 2
    if b >= 64:
        raise _RuntimeErr("OVERFLOW")
    return _check(a ** b)


def _divtrunc(a, b):
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q


def _eval(node, env):
    t = node[0]
    if t == "n":
        return _check(node[1])
    if t == "v":
        name = node[1]
        if name not in env:
            raise _RuntimeErr("UNDEFINED")
        return env[name]
    if t == "u":
        v = _eval(node[2], env)
        if node[1] == "-":
            return _check(-v)
        return _check(v)
    # binary
    op = node[1]
    a = _eval(node[2], env)
    b = _eval(node[3], env)
    if op == "+":
        return _check(a + b)
    if op == "-":
        return _check(a - b)
    if op == "*":
        return _check(a * b)
    if op == "/":
        if b == 0:
            raise _RuntimeErr("DIV_ZERO")
        return _check(_divtrunc(a, b))
    if op == "%":
        if b == 0:
            raise _RuntimeErr("DIV_ZERO")
        return _check(a - b * _divtrunc(a, b))
    if op == "^":
        return _pow(a, b)
    raise _SyntaxErr()


def evaluate(src: str):
    try:
        old = sys.getrecursionlimit()
        if old < 20000:
            try:
                sys.setrecursionlimit(20000)
            except Exception:
                pass
    except Exception:
        pass
    try:
        if not isinstance(src, str):
            return "SYNTAX"
        try:
            toks = _lex(src)
            stmts = _Parser(toks).program()
        except _SyntaxErr:
            return "SYNTAX"
        except RecursionError:
            return "SYNTAX"
        env = {}
        result = None
        try:
            for st in stmts:
                if st[0] == "a":
                    v = _eval(st[2], env)
                    env[st[1]] = v
                    result = v
                else:
                    result = _eval(st[1], env)
        except _RuntimeErr as e:
            return e.kind
        except RecursionError:
            return "OVERFLOW"
        if result is None:
            return "SYNTAX"
        return int(result)
    except Exception:
        return "SYNTAX"
```
