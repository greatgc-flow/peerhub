```python
import sys

class _Err(Exception):
    pass

_LIM = 10 ** 18


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    digits = "0123456789"
    while i < n:
        c = src[i]
        if c in " \t\n":
            i += 1
        elif c in digits:
            j = i
            while j < n and src[j] in digits:
                j += 1
            s = src[i:j]
            if len(s) > 1 and s[0] == "0":
                raise _Err("SYNTAX")
            toks.append(("num", s))
            i = j
        elif ("a" <= c <= "z") or c == "_":
            j = i
            while j < n and (("a" <= src[j] <= "z") or src[j] == "_" or src[j] in digits):
                j += 1
            toks.append(("name", src[i:j]))
            i = j
        elif c in "+-*/%^()=;":
            toks.append(("sym", c))
            i += 1
        else:
            raise _Err("SYNTAX")
    return toks


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.p = 0

    def peek(self):
        if self.p < len(self.t):
            return self.t[self.p]
        return (None, None)

    def is_sym(self, chars):
        k, v = self.peek()
        return k == "sym" and v in chars

    def program(self):
        stmts = [self.stmt()]
        while self.is_sym(";"):
            self.p += 1
            if self.p >= len(self.t):
                break
            stmts.append(self.stmt())
        if self.p != len(self.t):
            raise _Err("SYNTAX")
        return stmts

    def stmt(self):
        if (self.p + 1 < len(self.t) and self.t[self.p][0] == "name"
                and self.t[self.p + 1] == ("sym", "=")):
            name = self.t[self.p][1]
            self.p += 2
            return ("assign", name, self.expr())
        return self.expr()

    def expr(self):
        first = self.term()
        ops = []
        while self.is_sym("+-"):
            op = self.peek()[1]
            self.p += 1
            ops.append((op, self.term()))
        if not ops:
            return first
        return ("b", first, ops)

    def term(self):
        first = self.unary()
        ops = []
        while self.is_sym("*/%"):
            op = self.peek()[1]
            self.p += 1
            ops.append((op, self.unary()))
        if not ops:
            return first
        return ("b", first, ops)

    def unary(self):
        if self.is_sym("+-"):
            op = self.peek()[1]
            self.p += 1
            return ("u", op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.is_sym("^"):
            self.p += 1
            return ("p", base, self.unary())
        return base

    def primary(self):
        k, v = self.peek()
        if k == "num":
            self.p += 1
            if len(v) > 19:
                return ("n", None)
            x = int(v)
            return ("n", x if x <= _LIM else None)
        if k == "name":
            self.p += 1
            return ("v", v)
        if k == "sym" and v == "(":
            self.p += 1
            e = self.expr()
            if not self.is_sym(")"):
                raise _Err("SYNTAX")
            self.p += 1
            return e
        raise _Err("SYNTAX")


def _chk(x):
    if abs(x) > _LIM:
        raise _Err("OVERFLOW")
    return x


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
    if b > 64:
        raise _Err("OVERFLOW")
    return _chk(a ** b)


def _ev(node, env):
    k = node[0]
    if k == "n":
        if node[1] is None:
            raise _Err("OVERFLOW")
        return node[1]
    if k == "v":
        if node[1] not in env:
            raise _Err("UNDEFINED")
        return env[node[1]]
    if k == "u":
        v = _ev(node[2], env)
        return _chk(-v if node[1] == "-" else v)
    if k == "p":
        a = _ev(node[1], env)
        b = _ev(node[2], env)
        return _pow(a, b)
    if k == "b":
        acc = _ev(node[1], env)
        for op, rn in node[2]:
            r = _ev(rn, env)
            if op == "+":
                acc = _chk(acc + r)
            elif op == "-":
                acc = _chk(acc - r)
            elif op == "*":
                acc = _chk(acc * r)
            else:
                if r == 0:
                    raise _Err("DIV_ZERO")
                q = abs(acc) // abs(r)
                if (acc < 0) != (r < 0):
                    q = -q
                if op == "/":
                    acc = _chk(q)
                else:
                    acc = _chk(acc - r * q)
        return acc
    raise _Err("SYNTAX")


def _run(src):
    toks = _lex(src)
    stmts = _Parser(toks).program()
    env = {}
    val = 0
    for s in stmts:
        if s[0] == "assign":
            val = _ev(s[2], env)
            env[s[1]] = val
        else:
            val = _ev(s, env)
    return int(val)


def evaluate(src):
    try:
        if sys.getrecursionlimit() < 20000:
            sys.setrecursionlimit(20000)
        if not isinstance(src, str):
            return "SYNTAX"
        return _run(src)
    except _Err as e:
        return e.args[0]
    except Exception:
        return "SYNTAX"
```
