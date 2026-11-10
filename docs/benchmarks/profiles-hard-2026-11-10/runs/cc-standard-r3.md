```python
LIMIT = 10 ** 18
_WS = " \t\n"
_SYMBOLS = "+-*/%^()=;"


class _Syntax(Exception):
    pass


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c in _WS:
            i += 1
        elif "0" <= c <= "9":
            j = i
            while j < n and "0" <= src[j] <= "9":
                j += 1
            s = src[i:j]
            if len(s) > 1 and s[0] == "0":
                raise _Syntax()
            toks.append(("num", s))
            i = j
        elif ("a" <= c <= "z") or c == "_":
            j = i + 1
            while j < n and (("a" <= src[j] <= "z") or ("0" <= src[j] <= "9") or src[j] == "_"):
                j += 1
            toks.append(("name", src[i:j]))
            i = j
        elif c in _SYMBOLS:
            toks.append(("sym", c))
            i += 1
        else:
            raise _Syntax()
    toks.append(("eof", None))
    return toks


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self):
        return self.toks[self.pos]

    def at_sym(self, s):
        return self.toks[self.pos] == ("sym", s)

    def program(self):
        stmts = [self.stmt()]
        while self.at_sym(";"):
            self.pos += 1
            if self.peek()[0] == "eof":
                break
            stmts.append(self.stmt())
        if self.peek()[0] != "eof":
            raise _Syntax()
        return stmts

    def stmt(self):
        t = self.peek()
        nxt = self.toks[self.pos + 1]
        if t[0] == "name" and nxt == ("sym", "="):
            self.pos += 2
            return ("assign", t[1], self.expr())
        return self.expr()

    def expr(self):
        node = self.term()
        while True:
            t = self.peek()
            if t == ("sym", "+") or t == ("sym", "-"):
                self.pos += 1
                node = ("bin", t[1], node, self.term())
            else:
                return node

    def term(self):
        node = self.unary()
        while True:
            t = self.peek()
            if t == ("sym", "*") or t == ("sym", "/") or t == ("sym", "%"):
                self.pos += 1
                node = ("bin", t[1], node, self.unary())
            else:
                return node

    def unary(self):
        minus = 0
        while True:
            t = self.peek()
            if t == ("sym", "+"):
                self.pos += 1
            elif t == ("sym", "-"):
                minus += 1
                self.pos += 1
            else:
                break
        node = self.power()
        if minus % 2 == 1:
            node = ("neg", node)
        return node

    def power(self):
        base = self.primary()
        if self.at_sym("^"):
            self.pos += 1
            return ("pow", base, self.unary())
        return base

    def primary(self):
        t = self.peek()
        if t[0] == "num":
            self.pos += 1
            return ("num", t[1])
        if t[0] == "name":
            self.pos += 1
            return ("name", t[1])
        if t == ("sym", "("):
            self.pos += 1
            node = self.expr()
            if not self.at_sym(")"):
                raise _Syntax()
            self.pos += 1
            return node
        raise _Syntax()


def _chk(v):
    return "OVERFLOW" if abs(v) > LIMIT else v


def _num(s):
    if len(s) > 19:
        return "OVERFLOW"
    v = int(s)
    return "OVERFLOW" if v > LIMIT else v


def _power(a, b):
    # b >= 0 and |a| <= LIMIT here
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return 1 if b % 2 == 0 else -1
    if b >= 60:  # |a| >= 2 implies |a|**60 >= 2**60 > 10**18
        return "OVERFLOW"
    return _chk(a ** b)


def _ev(node, env):
    k = node[0]
    if k == "num":
        return _num(node[1])
    if k == "name":
        return env[node[1]] if node[1] in env else "UNDEFINED"
    if k == "neg":
        v = _ev(node[1], env)
        if isinstance(v, str):
            return v
        return _chk(-v)
    if k == "assign":
        v = _ev(node[2], env)
        if isinstance(v, str):
            return v
        env[node[1]] = v
        return v
    if k == "bin":
        a = _ev(node[2], env)
        if isinstance(a, str):
            return a
        b = _ev(node[3], env)
        if isinstance(b, str):
            return b
        op = node[1]
        if op == "+":
            return _chk(a + b)
        if op == "-":
            return _chk(a - b)
        if op == "*":
            return _chk(a * b)
        if b == 0:
            return "DIV_ZERO"
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        if op == "/":
            return _chk(q)
        return _chk(a - b * q)  # op == "%"
    if k == "pow":
        a = _ev(node[1], env)
        if isinstance(a, str):
            return a
        b = _ev(node[2], env)
        if isinstance(b, str):
            return b
        if b < 0:
            return "NEG_EXP"
        return _power(a, b)
    raise _Syntax()


def evaluate(src: str):
    try:
        stmts = _Parser(_lex(src)).program()
    except Exception:
        return "SYNTAX"
    env = {}
    last = 0
    for st in stmts:
        v = _ev(st, env)
        if isinstance(v, str):
            return v
        last = v
    return last
```
