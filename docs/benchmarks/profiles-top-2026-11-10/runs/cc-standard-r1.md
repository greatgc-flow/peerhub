```python
_CH, _SPLIT, _JMP, _BOL, _EOL, _MARK, _GCLOSE, _CLEAR, _CHECK, _BREF, _ACC = range(11)

_ESC_LIT = frozenset('\\.|*+?()[]{}^$-')


class _PatternError(Exception):
    pass


def _is_digit(c):
    return '0' <= c <= '9'


def _is_word(c):
    return ('0' <= c <= '9') or ('a' <= c <= 'z') or ('A' <= c <= 'Z') or c == '_'


def _is_space(c):
    return c == ' ' or c == '\t' or c == '\n'


_CLASS_PREDS = {
    'd': _is_digit,
    'w': _is_word,
    's': _is_space,
    'D': lambda c: not _is_digit(c),
    'W': lambda c: not _is_word(c),
    'S': lambda c: not _is_space(c),
}


def _lit(ch):
    return lambda c: c == ch


def _not_newline(c):
    return c != '\n'


def _make_set(neg, ranges, preds):
    def pred(c):
        o = ord(c)
        hit = False
        for lo, hi in ranges:
            if lo <= o <= hi:
                hit = True
                break
        if not hit:
            for f in preds:
                if f(c):
                    hit = True
                    break
        return hit != neg
    return pred


class _Parser:
    def __init__(self, pat):
        self.p = pat
        self.n = len(pat)
        self.i = 0
        self.ngroups = 0
        self.max_ref = 0
        self.has_ref = False

    def peek(self):
        return self.p[self.i] if self.i < self.n else None

    def parse(self):
        node = self.alt()
        if self.i != self.n:
            raise _PatternError()
        if self.max_ref > self.ngroups:
            raise _PatternError()
        return node

    def alt(self):
        seqs = [self.seq()]
        while self.peek() == '|':
            self.i += 1
            seqs.append(self.seq())
        return seqs[0] if len(seqs) == 1 else ('alt', seqs)

    def seq(self):
        items = []
        while self.i < self.n and self.p[self.i] not in '|)':
            c = self.p[self.i]
            if c == '^':
                self.i += 1
                items.append(('bol',))
            elif c == '$':
                self.i += 1
                items.append(('eol',))
            else:
                items.append(self.quant(self.atom()))
        return ('seq', items)

    def quant(self, atom):
        c = self.peek()
        if c == '*':
            self.i += 1
            m, n = 0, None
        elif c == '+':
            self.i += 1
            m, n = 1, None
        elif c == '?':
            self.i += 1
            m, n = 0, 1
        elif c == '{':
            m, n = self.braces()
        else:
            return atom
        lazy = self.peek() == '?'
        if lazy:
            self.i += 1
        return ('rep', atom, m, n, lazy)

    def braces(self):
        self.i += 1
        m = self.digits()
        if m is None:
            raise _PatternError()
        if self.peek() == '}':
            self.i += 1
            n = m
        elif self.peek() == ',':
            self.i += 1
            n = self.digits()
            if self.peek() != '}':
                raise _PatternError()
            self.i += 1
        else:
            raise _PatternError()
        if m > 1000 or (n is not None and (n > 1000 or m > n)):
            raise _PatternError()
        return m, n

    def digits(self):
        start = self.i
        while self.i < self.n and '0' <= self.p[self.i] <= '9':
            self.i += 1
        return int(self.p[start:self.i]) if self.i > start else None

    def atom(self):
        c = self.p[self.i]
        if c == '.':
            self.i += 1
            return ('char', _not_newline)
        if c == '(':
            if self.p.startswith('(?:', self.i):
                self.i += 3
                k = None
            elif self.p.startswith('(?', self.i):
                raise _PatternError()
            else:
                self.i += 1
                self.ngroups += 1
                k = self.ngroups
            inner = self.alt()
            if self.peek() != ')':
                raise _PatternError()
            self.i += 1
            return ('group', k, inner)
        if c == '[':
            return ('char', self.char_class())
        if c == '\\':
            return self.escape()
        if c in '\\.|*+?()[]{}^$':
            raise _PatternError()
        self.i += 1
        return ('char', _lit(c))

    def escape(self):
        if self.i + 1 >= self.n:
            raise _PatternError()
        e = self.p[self.i + 1]
        self.i += 2
        if e in _ESC_LIT:
            return ('char', _lit(e))
        if e == 'n':
            return ('char', _lit('\n'))
        if e == 't':
            return ('char', _lit('\t'))
        if e in _CLASS_PREDS:
            return ('char', _CLASS_PREDS[e])
        if '1' <= e <= '9':
            k = ord(e) - 48
            self.has_ref = True
            self.max_ref = max(self.max_ref, k)
            return ('backref', k)
        raise _PatternError()

    def class_item(self):
        c = self.p[self.i]
        if c == '\\':
            if self.i + 1 >= self.n:
                raise _PatternError()
            e = self.p[self.i + 1]
            self.i += 2
            if e in _ESC_LIT:
                return 'c', e
            if e == 'n':
                return 'c', '\n'
            if e == 't':
                return 'c', '\t'
            if e in _CLASS_PREDS:
                return 'p', _CLASS_PREDS[e]
            raise _PatternError()
        self.i += 1
        return 'c', c

    def char_class(self):
        self.i += 1  # skip '['
        neg = False
        if self.peek() == '^':
            neg = True
            self.i += 1
        ranges = []
        preds = []
        while True:
            if self.i >= self.n:
                raise _PatternError()
            if self.p[self.i] == ']':
                self.i += 1
                break
            kind, val = self.class_item()
            if self.i + 1 < self.n and self.p[self.i] == '-' and self.p[self.i + 1] != ']':
                self.i += 1
                kind2, val2 = self.class_item()
                if kind != 'c' or kind2 != 'c' or ord(val) > ord(val2):
                    raise _PatternError()
                ranges.append((ord(val), ord(val2)))
            elif kind == 'c':
                ranges.append((ord(val), ord(val)))
            else:
                preds.append(val)
        return _make_set(neg, ranges, preds)


def _groups_in(node, out):
    kind = node[0]
    if kind == 'group':
        if node[1] is not None:
            out.append(node[1])
        _groups_in(node[2], out)
    elif kind == 'seq':
        for it in node[1]:
            _groups_in(it, out)
    elif kind == 'alt':
        for a in node[1]:
            _groups_in(a, out)
    elif kind == 'rep':
        _groups_in(node[1], out)
    return out


class _Compiler:
    def __init__(self):
        self.code = []
        self.regs = {}

    def reg(self, key):
        if key not in self.regs:
            self.regs[key] = len(self.regs)
        return self.regs[key]

    def emit(self, node):
        code = self.code
        kind = node[0]
        if kind == 'char':
            code.append((_CH, node[1]))
        elif kind == 'bol':
            code.append((_BOL,))
        elif kind == 'eol':
            code.append((_EOL,))
        elif kind == 'backref':
            code.append((_BREF, node[1]))
        elif kind == 'group':
            k = node[1]
            if k is None:
                self.emit(node[2])
            else:
                r = self.reg(('g', k))
                code.append((_MARK, r))
                self.emit(node[2])
                code.append((_GCLOSE, k, r))
        elif kind == 'seq':
            for item in node[1]:
                self.emit(item)
        elif kind == 'alt':
            alts = node[1]
            jumps = []
            for idx, alt in enumerate(alts):
                if idx < len(alts) - 1:
                    s = len(code)
                    code.append(None)
                    self.emit(alt)
                    jumps.append(len(code))
                    code.append(None)
                    code[s] = (_SPLIT, s + 1, len(code))
                else:
                    self.emit(alt)
            end = len(code)
            for j in jumps:
                code[j] = (_JMP, end)
        else:
            self.emit_rep(node)

    def emit_rep(self, node):
        _, atom, m, n, lazy = node
        code = self.code
        groups = tuple(_groups_in(atom, []))
        r = self.reg(('r', id(node)))
        for _ in range(m):
            self.emit_iter(atom, groups, None)
        if n is None:
            s = len(code)
            code.append(None)
            body = len(code)
            self.emit_iter(atom, groups, r)
            code.append((_JMP, s))
            end = len(code)
            code[s] = (_SPLIT, end, body) if lazy else (_SPLIT, body, end)
        else:
            splits = []
            for _ in range(n - m):
                s = len(code)
                code.append(None)
                body = len(code)
                self.emit_iter(atom, groups, r)
                splits.append((s, body))
            end = len(code)
            for s, body in splits:
                code[s] = (_SPLIT, end, body) if lazy else (_SPLIT, body, end)

    def emit_iter(self, atom, groups, r):
        if groups:
            self.code.append((_CLEAR, groups))
        if r is None:
            self.emit(atom)
        else:
            self.code.append((_MARK, r))
            self.emit(atom)
            self.code.append((_CHECK, r))


def _set(tup, i, v):
    return tup[:i] + (v,) + tup[i + 1:]


def _run(code, nregs, ngroups, text, start, has_ref, visited):
    n = len(text)
    regs = (None,) * nregs
    caps = (None,) * (ngroups + 1)
    stack = [(0, start, regs, caps)]
    while stack:
        pc, pos, regs, caps = stack.pop()
        while True:
            ins = code[pc]
            op = ins[0]
            if op == _CH:
                if pos < n and ins[1](text[pos]):
                    pc += 1
                    pos += 1
                    continue
                break
            if op == _SPLIT:
                key = (pc, pos, regs, caps) if has_ref else (pc, pos, regs)
                if key in visited:
                    break
                visited.add(key)
                stack.append((ins[2], pos, regs, caps))
                pc = ins[1]
            elif op == _JMP:
                pc = ins[1]
            elif op == _BOL:
                if pos != 0:
                    break
                pc += 1
            elif op == _EOL:
                if pos != n:
                    break
                pc += 1
            elif op == _MARK:
                regs = _set(regs, ins[1], pos)
                pc += 1
            elif op == _GCLOSE:
                k, r = ins[1], ins[2]
                caps = _set(caps, k, (regs[r], pos))
                regs = _set(regs, r, None)
                pc += 1
            elif op == _CLEAR:
                for k in ins[1]:
                    caps = _set(caps, k, None)
                pc += 1
            elif op == _CHECK:
                r = ins[1]
                if regs[r] == pos:
                    break
                regs = _set(regs, r, None)
                pc += 1
            elif op == _BREF:
                span = caps[ins[1]]
                if span is not None:
                    s, e = span
                    L = e - s
                    if pos + L > n or text[pos:pos + L] != text[s:e]:
                        break
                    pos += L
                pc += 1
            else:  # _ACC
                return (start, pos, caps)
    return None


def search(pattern, text):
    try:
        parser = _Parser(pattern)
        ast = parser.parse()
        comp = _Compiler()
        comp.emit(ast)
        comp.code.append((_ACC,))
        code = comp.code
        nregs = len(comp.regs)
        ngroups = parser.ngroups
        has_ref = parser.has_ref
    except Exception:
        return "ERROR"

    visited = set()
    for start in range(len(text) + 1):
        res = _run(code, nregs, ngroups, text, start, has_ref, visited)
        if res is not None:
            s, e, caps = res
            return ((s, e),) + tuple(caps[1:])
    return None
```
