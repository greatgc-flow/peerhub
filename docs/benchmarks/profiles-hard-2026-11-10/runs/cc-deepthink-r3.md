```python
import sys

_LIMIT = 10 ** 18


class _Syn(Exception):
    pass


class _RT(Exception):
    def __init__(self, kind):
        Exception.__init__(self, kind)
        self.kind = kind


def _lex(src):
    toks = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == ' ' or c == '\t' or c == '\n':
            i += 1
            continue
        if '0' <= c <= '9':
            j = i
            while j < n and '0' <= src[j] <= '9':
                j += 1
            s = src[i:j]
            if len(s) > 1 and s[0] == '0':
                raise _Syn()
            toks.append(('num', s))
            i = j
            continue
        if ('a' <= c <= 'z') or c == '_':
            j = i + 1
            while j < n:
                d = src[j]
                if ('a' <= d <= 'z') or ('0' <= d <= '9') or d == '_':
                    j += 1
                else:
                    break
            toks.append(('name', src[i:j]))
            i = j
            continue
        if c in '+-*/%^()=;':
            toks.append((c, c))
            i += 1
            continue
        raise _Syn()
    toks.append(('eof', None))
    return toks


class _Parser(object):
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i][0]

    def program(self):
        stmts = []
        while True:
            stmts.append(self.stmt())
            k = self.peek()
            if k == ';':
                self.i += 1
                if self.peek() == 'eof':
                    break
                continue
            if k == 'eof':
                break
            raise _Syn()
        return stmts

    def stmt(self):
        code = []
        if self.peek() == 'name' and self.t[self.i + 1][0] == '=':
            name = self.t[self.i][1]
            self.i += 2
            self.expr(code)
            code.append(('store', name))
        else:
            self.expr(code)
        return code

    def expr(self, code):
        self.term(code)
        while self.peek() in ('+', '-'):
            op = self.peek()
            self.i += 1
            self.term(code)
            code.append((op,))

    def term(self, code):
        self.unary(code)
        while self.peek() in ('*', '/', '%'):
            op = self.peek()
            self.i += 1
            self.unary(code)
            code.append((op,))

    def signs(self):
        sg = []
        while self.peek() in ('+', '-'):
            sg.append(self.peek())
            self.i += 1
        return sg

    @staticmethod
    def apply_signs(code, sg):
        for s in reversed(sg):
            code.append(('neg',) if s == '-' else ('pos',))

    def unary(self, code):
        sg = self.signs()
        self.power(code)
        self.apply_signs(code, sg)

    def power(self, code):
        self.primary(code)
        pend = []
        while self.peek() == '^':
            self.i += 1
            sg = self.signs()
            self.primary(code)
            pend.append(sg)
        for sg in reversed(pend):
            self.apply_signs(code, sg)
            code.append(('^',))

    def primary(self, code):
        k, v = self.t[self.i]
        if k == 'num':
            self.i += 1
            code.append(('num', v))
        elif k == 'name':
            self.i += 1
            code.append(('var', v))
        elif k == '(':
            self.i += 1
            self.expr(code)
            if self.peek() != ')':
                raise _Syn()
            self.i += 1
        else:
            raise _Syn()


def _chk(v):
    if v > _LIMIT or v < -_LIMIT:
        raise _RT("OVERFLOW")
    return v


def _tdiv(a, b):
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q


def _pow(a, b):
    if b < 0:
        raise _RT("NEG_EXP")
    if b == 0:
        return 1
    if a == 0:
        return 0
    if a == 1:
        return 1
    if a == -1:
        return -1 if (b & 1) else 1
    if b >= 60:
        raise _RT("OVERFLOW")
    return _chk(a ** b)


def _exec(stmts):
    env = {}
    last = None
    for code in stmts:
        st = []
        for ins in code:
            op = ins[0]
            if op == 'num':
                s = ins[1]
                if len(s) > 19:
                    raise _RT("OVERFLOW")
                st.append(_chk(int(s)))
            elif op == 'var':
                if ins[1] not in env:
                    raise _RT("UNDEFINED")
                st.append(env[ins[1]])
            elif op == 'neg':
                st.append(_chk(-st.pop()))
            elif op == 'pos':
                st.append(_chk(st.pop()))
            elif op == 'store':
                env[ins[1]] = st[-1]
            else:
                b = st.pop()
                a = st.pop()
                if op == '+':
                    r = _chk(a + b)
                elif op == '-':
                    r = _chk(a - b)
                elif op == '*':
                    r = _chk(a * b)
                elif op == '/':
                    if b == 0:
                        raise _RT("DIV_ZERO")
                    r = _chk(_tdiv(a, b))
                elif op == '%':
                    if b == 0:
                        raise _RT("DIV_ZERO")
                    r = _chk(a - b * _tdiv(a, b))
                else:
                    r = _pow(a, b)
                st.append(r)
        last = st[-1]
    return last


def evaluate(src: str):
    try:
        if not isinstance(src, str):
            return "SYNTAX"
        try:
            if sys.getrecursionlimit() < 10000:
                sys.setrecursionlimit(10000)
        except Exception:
            pass
        try:
            toks = _lex(src)
            stmts = _Parser(toks).program()
        except (_Syn, RecursionError, IndexError):
            return "SYNTAX"
        try:
            return int(_exec(stmts))
        except _RT as e:
            return e.kind
    except Exception:
        return "SYNTAX"
```
