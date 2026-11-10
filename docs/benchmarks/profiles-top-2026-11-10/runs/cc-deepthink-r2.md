```python
_SPECIAL = frozenset('\\.|*+?()[]{}^$')
_ESCLIT = frozenset('\\.|*+?()[]{}^$-')
_DSET = frozenset('0123456789')
_WSET = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_')
_SSET = frozenset(' \t\n')
_PRED = {
    'd': lambda c: c in _DSET,
    'D': lambda c: c not in _DSET,
    'w': lambda c: c in _WSET,
    'W': lambda c: c not in _WSET,
    's': lambda c: c in _SSET,
    'S': lambda c: c not in _SSET,
}

(LIT, TEST, SPLIT, JMP, OPEN, CLOSE, LINIT, LDEC, ISTART, IEND,
 BREF, BOL, EOL, MATCH) = range(14)
_BIG = 10 ** 9


class _Err(Exception):
    pass


class _Parser(object):
    def __init__(self, p):
        self.p = p
        self.L = len(p)
        self.i = 0
        self.ng = 0
        self.refs = []

    def peek(self):
        return self.p[self.i] if self.i < self.L else None

    def parse(self):
        node = self.alt()
        if self.i != self.L:
            raise _Err()
        for r in self.refs:
            if r > self.ng:
                raise _Err()
        return node

    def alt(self):
        br = [self.seq()]
        while self.peek() == '|':
            self.i += 1
            br.append(self.seq())
        return br[0] if len(br) == 1 else ('alt', br)

    def seq(self):
        items = []
        while True:
            c = self.peek()
            if c is None or c == '|' or c == ')':
                break
            if c == '^':
                self.i += 1
                items.append(('bol',))
                continue
            if c == '$':
                self.i += 1
                items.append(('eol',))
                continue
            g0 = self.ng
            a = self.atom()
            g1 = self.ng
            q = self.quant()
            if q is not None:
                a = ('quant', a, q[0], q[1], q[2], g0 + 1, g1)
            items.append(a)
        return ('seq', items)

    def atom(self):
        c = self.peek()
        if c == '(':
            if self.p.startswith('(?:', self.i):
                self.i += 3
                cap = None
            else:
                self.i += 1
                self.ng += 1
                cap = self.ng
            node = self.alt()
            if self.peek() != ')':
                raise _Err()
            self.i += 1
            return ('group', cap, node)
        if c == '.':
            self.i += 1
            return ('test', lambda ch: ch != '\n')
        if c == '[':
            return self.cls()
        if c == '\\':
            self.i += 1
            if self.i >= self.L:
                raise _Err()
            e = self.p[self.i]
            self.i += 1
            if e in _ESCLIT:
                return ('lit', e)
            if e == 'n':
                return ('lit', '\n')
            if e == 't':
                return ('lit', '\t')
            if e in _PRED:
                return ('test', _PRED[e])
            if e in '123456789':
                k = int(e)
                self.refs.append(k)
                return ('backref', k)
            raise _Err()
        if c is None or c in _SPECIAL:
            raise _Err()
        self.i += 1
        return ('lit', c)

    def citem(self):
        c = self.p[self.i]
        if c == '\\':
            self.i += 1
            if self.i >= self.L:
                raise _Err()
            e = self.p[self.i]
            self.i += 1
            if e in _ESCLIT:
                return ('ch', e)
            if e == 'n':
                return ('ch', '\n')
            if e == 't':
                return ('ch', '\t')
            if e in _PRED:
                return ('pred', _PRED[e])
            raise _Err()
        self.i += 1
        return ('ch', c)

    def cls(self):
        self.i += 1
        neg = False
        if self.peek() == '^':
            neg = True
            self.i += 1
        ranges = []
        preds = []
        while True:
            if self.i >= self.L:
                raise _Err()
            if self.p[self.i] == ']':
                self.i += 1
                break
            x = self.citem()
            if (self.i + 1 < self.L and self.p[self.i] == '-'
                    and self.p[self.i + 1] != ']'):
                self.i += 1
                y = self.citem()
                if x[0] != 'ch' or y[0] != 'ch' or x[1] > y[1]:
                    raise _Err()
                ranges.append((x[1], y[1]))
            else:
                if x[0] == 'ch':
                    ranges.append((x[1], x[1]))
                else:
                    preds.append(x[1])
        singles = frozenset(lo for lo, hi in ranges if lo == hi)
        rng = tuple((lo, hi) for lo, hi in ranges if lo != hi)
        preds = tuple(preds)

        def f(ch, singles=singles, rng=rng, preds=preds, neg=neg):
            r = (ch in singles or any(lo <= ch <= hi for lo, hi in rng)
                 or any(pf(ch) for pf in preds))
            return r != neg
        return ('test', f)

    def digits(self):
        j = self.i
        while self.i < self.L and self.p[self.i] in _DSET:
            self.i += 1
        return self.p[j:self.i]

    def quant(self):
        c = self.peek()
        if c == '*':
            self.i += 1
            mn, mx = 0, None
        elif c == '+':
            self.i += 1
            mn, mx = 1, None
        elif c == '?':
            self.i += 1
            mn, mx = 0, 1
        elif c == '{':
            self.i += 1
            s1 = self.digits()
            if not s1:
                raise _Err()
            c2 = self.peek()
            if c2 == '}':
                self.i += 1
                mn = mx = int(s1)
            elif c2 == ',':
                self.i += 1
                s2 = self.digits()
                if self.peek() != '}':
                    raise _Err()
                self.i += 1
                mn = int(s1)
                mx = int(s2) if s2 else None
            else:
                raise _Err()
            if mn > 1000 or (mx is not None and (mx > 1000 or mn > mx)):
                raise _Err()
        else:
            return None
        greedy = True
        if self.peek() == '?':
            self.i += 1
            greedy = False
        return (mn, mx, greedy)


def _required(node):
    t = node[0]
    if t == 'lit':
        return {node[1]}
    if t == 'seq':
        s = set()
        for x in node[1]:
            s |= _required(x)
        return s
    if t == 'alt':
        s = None
        for x in node[1]:
            r = _required(x)
            s = r if s is None else (s & r)
        return s or set()
    if t == 'group':
        return _required(node[2])
    if t == 'quant':
        return _required(node[1]) if node[2] >= 1 else set()
    return set()


def _compile(pattern):
    ps = _Parser(pattern)
    tree = ps.parse()
    G = ps.ng
    LB = 3 * G
    code = []
    loops = []

    def emit(ins):
        code.append(ins)
        return len(code) - 1

    def comp(node):
        t = node[0]
        if t == 'lit':
            emit([LIT, node[1]])
        elif t == 'test':
            emit([TEST, node[1]])
        elif t == 'bol':
            emit([BOL])
        elif t == 'eol':
            emit([EOL])
        elif t == 'backref':
            emit([BREF, 3 * (node[1] - 1) + 1])
        elif t == 'seq':
            for x in node[1]:
                comp(x)
        elif t == 'alt':
            br = node[1]
            jumps = []
            for k, b in enumerate(br):
                if k < len(br) - 1:
                    s = emit([SPLIT, 0, 0])
                    code[s][1] = len(code)
                    comp(b)
                    jumps.append(emit([JMP, 0]))
                    code[s][2] = len(code)
                else:
                    comp(b)
            for j in jumps:
                code[j][1] = len(code)
        elif t == 'group':
            g = node[1]
            if g is None:
                comp(node[2])
            else:
                emit([OPEN, 3 * (g - 1)])
                comp(node[2])
                emit([CLOSE, 3 * (g - 1)])
        elif t == 'quant':
            _, atom, mn, mx, greedy, glo, ghi = node
            L = len(loops)
            c = LB + 2 * L
            s = c + 1
            mxv = mx if mx is not None else _BIG
            cap = mx if mx is not None else mn
            loops.append((c, s, cap))
            resets = []
            for g in range(glo, ghi + 1):
                resets.append(3 * (g - 1) + 1)
                resets.append(3 * (g - 1) + 2)
            emit([LINIT, c])
            dec = emit([LDEC, c, mn, mxv, greedy, 0, 0])
            code[dec][5] = len(code)
            emit([ISTART, s, resets])
            comp(atom)
            emit([IEND, c, mn, cap, dec])
            code[dec][6] = len(code)

    comp(tree)
    emit([MATCH])
    nregs = LB + 2 * len(loops)
    cntregs = [c for (c, s, cap) in loops if cap > 0]
    istregs = [s for (c, s, cap) in loops]
    grpregs = []
    for r in sorted(set(ps.refs)):
        b = 3 * (r - 1)
        grpregs.extend([b, b + 1, b + 2])
    init = [-1] * nregs
    for (c, s, cap) in loops:
        init[c] = 0
    return (code, G, init, cntregs, istregs, grpregs, _required(tree))


def _run(prog, text):
    code, G, init, cntregs, istregs, grpregs, req = prog
    n = len(text)
    if req:
        ts = set(text)
        for ch in req:
            if ch not in ts:
                return None
    keyed = bool(cntregs or istregs or grpregs)
    memo = set()
    N1 = n + 1
    for start in range(n + 1):
        R = init[:]
        trail = []
        stack = []
        pc = 0
        pos = start
        while True:
            op = code[pc]
            t = op[0]
            if t == LIT:
                if pos < n and text[pos] == op[1]:
                    pos += 1
                    pc += 1
                    continue
            elif t == TEST:
                if pos < n and op[1](text[pos]):
                    pos += 1
                    pc += 1
                    continue
            elif t == SPLIT or t == LDEC:
                if keyed:
                    key = (pc, pos,
                           tuple([R[k] for k in cntregs]),
                           tuple([R[k] == pos for k in istregs]),
                           tuple([R[k] for k in grpregs]))
                else:
                    key = pc * N1 + pos
                if key not in memo:
                    memo.add(key)
                    if t == SPLIT:
                        stack.append((op[2], pos, len(trail)))
                        pc = op[1]
                        continue
                    cnt = R[op[1]]
                    if cnt < op[2]:
                        pc = op[5]
                    elif cnt >= op[3]:
                        pc = op[6]
                    elif op[4]:
                        stack.append((op[6], pos, len(trail)))
                        pc = op[5]
                    else:
                        stack.append((op[5], pos, len(trail)))
                        pc = op[6]
                    continue
            elif t == JMP:
                pc = op[1]
                continue
            elif t == OPEN:
                r = op[1]
                trail.append((r, R[r]))
                R[r] = pos
                pc += 1
                continue
            elif t == CLOSE:
                r = op[1]
                trail.append((r + 1, R[r + 1]))
                trail.append((r + 2, R[r + 2]))
                R[r + 1] = R[r]
                R[r + 2] = pos
                pc += 1
                continue
            elif t == LINIT:
                r = op[1]
                trail.append((r, R[r]))
                R[r] = 0
                pc += 1
                continue
            elif t == ISTART:
                r = op[1]
                trail.append((r, R[r]))
                R[r] = pos
                for q in op[2]:
                    if R[q] != -1:
                        trail.append((q, R[q]))
                        R[q] = -1
                pc += 1
                continue
            elif t == IEND:
                c = op[1]
                s = c + 1
                if not (pos == R[s] and R[c] >= op[2]):
                    nc = R[c] + 1
                    if nc > op[3]:
                        nc = op[3]
                    trail.append((c, R[c]))
                    R[c] = nc
                    trail.append((s, R[s]))
                    R[s] = -1
                    pc = op[4]
                    continue
            elif t == BREF:
                r = op[1]
                gs = R[r]
                if gs < 0:
                    pc += 1
                    continue
                ge = R[r + 1]
                ln = ge - gs
                if ln <= 0:
                    pc += 1
                    continue
                if pos + ln <= n and text[pos:pos + ln] == text[gs:ge]:
                    pos += ln
                    pc += 1
                    continue
            elif t == BOL:
                if pos == 0:
                    pc += 1
                    continue
            elif t == EOL:
                if pos == n:
                    pc += 1
                    continue
            elif t == MATCH:
                res = [(start, pos)]
                for g in range(G):
                    gs = R[3 * g + 1]
                    if gs >= 0:
                        res.append((gs, R[3 * g + 2]))
                    else:
                        res.append(None)
                return tuple(res)
            # failure: backtrack
            if not stack:
                break
            pc, pos, tl = stack.pop()
            while len(trail) > tl:
                r, v = trail.pop()
                R[r] = v
    return None


def search(pattern, text):
    try:
        if not isinstance(pattern, str):
            return "ERROR"
        prog = _compile(pattern)
    except Exception:
        return "ERROR"
    try:
        if not isinstance(text, str):
            return None
        return _run(prog, text)
    except Exception:
        return None
```
