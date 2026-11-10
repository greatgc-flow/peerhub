```python
class _Err(Exception):
    pass


_LIT = set('\\.|*+?()[]{}^$-')
_SPECIAL = set('\\.|*+?()[]{}^$')
_DIG = set('0123456789')
_WSET = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_')


def _pd(ch):
    return '0' <= ch <= '9'


def _pw(ch):
    return ch in _WSET


def _ps(ch):
    return ch in ' \t\n'


_PREDS = {
    'd': _pd,
    'w': _pw,
    's': _ps,
    'D': lambda ch: not _pd(ch),
    'W': lambda ch: not _pw(ch),
    'S': lambda ch: not _ps(ch),
}


class _Parser:
    def __init__(self, p):
        self.p = p
        self.i = 0
        self.L = len(p)
        self.ng = 0
        self.brefs = set()

    def parse(self):
        node = self.alt()
        if self.i != self.L:
            raise _Err()
        for k in self.brefs:
            if k > self.ng:
                raise _Err()
        return node

    def alt(self):
        seqs = [self.seq()]
        while self.i < self.L and self.p[self.i] == '|':
            self.i += 1
            seqs.append(self.seq())
        return ('alt', seqs)

    def seq(self):
        items = []
        p = self.p
        while self.i < self.L and p[self.i] not in '|)':
            c = p[self.i]
            if c == '^':
                items.append(('bol',))
                self.i += 1
                continue
            if c == '$':
                items.append(('eol',))
                self.i += 1
                continue
            atom = self.atom()
            items.append(self.quant(atom))
        return items

    def atom(self):
        p = self.p
        c = p[self.i]
        if c == '(':
            if self.i + 1 < self.L and p[self.i + 1] == '?':
                if self.i + 2 < self.L and p[self.i + 2] == ':':
                    self.i += 3
                    k = 0
                else:
                    raise _Err()
            else:
                self.i += 1
                self.ng += 1
                k = self.ng
            sub = self.alt()
            if self.i >= self.L or p[self.i] != ')':
                raise _Err()
            self.i += 1
            return ('group', k, sub)
        if c == '[':
            return self.cls()
        if c == '\\':
            self.i += 1
            if self.i >= self.L:
                raise _Err()
            e = p[self.i]
            self.i += 1
            if e in _LIT:
                return ('char', e)
            if e == 'n':
                return ('char', '\n')
            if e == 't':
                return ('char', '\t')
            if e in _PREDS:
                return ('pred', _PREDS[e])
            if e in '123456789':
                k = ord(e) - 48
                self.brefs.add(k)
                return ('bref', k)
            raise _Err()
        if c == '.':
            self.i += 1
            return ('pred', lambda ch: ch != '\n')
        if c in _SPECIAL:
            raise _Err()
        self.i += 1
        return ('char', c)

    def citem(self):
        p = self.p
        if self.i >= self.L:
            raise _Err()
        c = p[self.i]
        if c == '\\':
            self.i += 1
            if self.i >= self.L:
                raise _Err()
            e = p[self.i]
            self.i += 1
            if e in _LIT:
                return ('c', e)
            if e == 'n':
                return ('c', '\n')
            if e == 't':
                return ('c', '\t')
            if e in _PREDS:
                return ('p', _PREDS[e])
            raise _Err()
        self.i += 1
        return ('c', c)

    def cls(self):
        p = self.p
        self.i += 1
        neg = False
        if self.i < self.L and p[self.i] == '^':
            neg = True
            self.i += 1
        ranges = []
        preds = []
        while True:
            if self.i >= self.L:
                raise _Err()
            if p[self.i] == ']':
                self.i += 1
                break
            x = self.citem()
            if (self.i < self.L and p[self.i] == '-' and self.i + 1 < self.L
                    and p[self.i + 1] != ']'):
                self.i += 1
                y = self.citem()
                if x[0] != 'c' or y[0] != 'c' or x[1] > y[1]:
                    raise _Err()
                ranges.append((x[1], y[1]))
            else:
                if x[0] == 'c':
                    ranges.append((x[1], x[1]))
                else:
                    preds.append(x[1])
        ranges = tuple(ranges)
        preds = tuple(preds)

        def f(ch, ranges=ranges, preds=preds, neg=neg):
            r = False
            for lo, hi in ranges:
                if lo <= ch <= hi:
                    r = True
                    break
            if not r:
                for pr in preds:
                    if pr(ch):
                        r = True
                        break
            return r != neg
        return ('pred', f)

    def quant(self, atom):
        p = self.p
        if self.i >= self.L:
            return atom
        c = p[self.i]
        if c == '*':
            m, M = 0, None
            self.i += 1
        elif c == '+':
            m, M = 1, None
            self.i += 1
        elif c == '?':
            m, M = 0, 1
            self.i += 1
        elif c == '{':
            self.i += 1
            j = self.i
            while self.i < self.L and p[self.i] in _DIG:
                self.i += 1
            if self.i == j:
                raise _Err()
            m = int(p[j:self.i])
            if self.i < self.L and p[self.i] == '}':
                M = m
                self.i += 1
            elif self.i < self.L and p[self.i] == ',':
                self.i += 1
                j = self.i
                while self.i < self.L and p[self.i] in _DIG:
                    self.i += 1
                if self.i < self.L and p[self.i] == '}':
                    M = None if self.i == j else int(p[j:self.i])
                    self.i += 1
                else:
                    raise _Err()
            else:
                raise _Err()
            if m > 1000 or (M is not None and (M > 1000 or m > M)):
                raise _Err()
        else:
            return atom
        greedy = True
        if self.i < self.L and p[self.i] == '?':
            greedy = False
            self.i += 1
        return ('quant', atom, m, M, greedy)


CH, FN, BOL, EOL, OPEN, CLOSE, BREF, SPLIT, JMP, LINIT, LDEC, LEND, MATCH = range(13)


def _groups_in(node, out):
    t = node[0]
    if t == 'alt':
        for s in node[1]:
            for it in s:
                _groups_in(it, out)
    elif t == 'group':
        if node[1]:
            out.append(node[1])
        _groups_in(node[2], out)
    elif t == 'quant':
        _groups_in(node[1], out)


def _compile(root):
    prog = []
    loops = []  # (m, M, dec, lend)

    def comp(node):
        t = node[0]
        if t == 'alt':
            seqs = node[1]
            jmps = []
            last = len(seqs) - 1
            for idx, sq in enumerate(seqs):
                if idx < last:
                    sp = len(prog)
                    prog.append([SPLIT, sp + 1, None])
                    for it in sq:
                        comp(it)
                    jmps.append(len(prog))
                    prog.append([JMP, None])
                    prog[sp][2] = len(prog)
                else:
                    for it in sq:
                        comp(it)
            for j in jmps:
                prog[j][1] = len(prog)
        elif t == 'char':
            prog.append([CH, node[1]])
        elif t == 'pred':
            prog.append([FN, node[1]])
        elif t == 'bol':
            prog.append([BOL])
        elif t == 'eol':
            prog.append([EOL])
        elif t == 'bref':
            prog.append([BREF, node[1]])
        elif t == 'group':
            k = node[1]
            if k:
                prog.append([OPEN, k])
                comp(node[2])
                prog.append([CLOSE, k])
            else:
                comp(node[2])
        elif t == 'quant':
            atom, m, M, greedy = node[1], node[2], node[3], node[4]
            gs = []
            _groups_in(atom, gs)
            L = len(loops)
            loops.append(None)
            prog.append([LINIT, L])
            dec = len(prog)
            prog.append([LDEC, L, m, M, greedy, dec + 1, None, tuple(gs)])
            comp(atom)
            lend = len(prog)
            prog.append([LEND, L, m, dec])
            prog[dec][6] = len(prog)
            loops[L] = (m, M, dec, lend)

    comp(root)
    prog.append([MATCH])
    return prog, loops


def _search(pattern, text):
    try:
        ps = _Parser(pattern)
        root = ps.parse()
    except _Err:
        return "ERROR"
    except RecursionError:
        return "ERROR"
    G = ps.ng
    refd = tuple(sorted(ps.brefs))
    prog, loops = _compile(root)
    nl = len(loops)
    n = len(text)
    N1 = n + 2
    encl = [[] for _ in prog]
    clip = [0] * nl
    for L, (m, M, dec, lend) in enumerate(loops):
        clip[L] = M if M is not None else m
        for q in range(dec, lend + 1):
            encl[q].append(L)
    encl = [tuple(e) for e in encl]
    visited = set()
    caps0 = (None,) * (G + 1)
    cnt0 = (0,) * nl
    st0 = (0,) * nl

    def mkkey(pc, pos, cnt, st, caps, opens):
        el = encl[pc]
        if not el and not refd:
            return pc * N1 + pos
        lk = tuple((cnt[L] if cnt[L] < clip[L] else clip[L], st[L] == pos) for L in el)
        if refd:
            ck = tuple(caps[k] for k in refd) + tuple(opens[k] for k in refd)
        else:
            ck = None
        return (pc, pos, lk, ck)

    for start in range(n + 1):
        stack = []
        pc = 0
        pos = start
        caps = caps0
        opens = caps0
        cnt = cnt0
        st = st0
        while True:
            op = prog[pc]
            t = op[0]
            if t == CH:
                if pos < n and text[pos] == op[1]:
                    pos += 1
                    pc += 1
                    continue
            elif t == FN:
                if pos < n and op[1](text[pos]):
                    pos += 1
                    pc += 1
                    continue
            elif t == SPLIT:
                key = mkkey(pc, pos, cnt, st, caps, opens)
                if key not in visited:
                    visited.add(key)
                    stack.append((op[2], pos, caps, opens, cnt, st))
                    pc = op[1]
                    continue
            elif t == JMP:
                pc = op[1]
                continue
            elif t == LDEC:
                L = op[1]
                c = cnt[L]
                m = op[2]
                M = op[3]
                can_it = M is None or c < M
                can_stop = c >= m
                if can_it:
                    nst = st[:L] + (pos,) + st[L + 1:]
                    gs = op[7]
                    if gs:
                        lst = list(caps)
                        for g in gs:
                            lst[g] = None
                        ncaps = tuple(lst)
                    else:
                        ncaps = caps
                    if can_stop:
                        key = mkkey(pc, pos, cnt, st, caps, opens)
                        if key in visited:
                            pass
                        else:
                            visited.add(key)
                            if op[4]:
                                stack.append((op[6], pos, caps, opens, cnt, st))
                                pc = op[5]
                                st = nst
                                caps = ncaps
                            else:
                                stack.append((op[5], pos, ncaps, opens, cnt, nst))
                                pc = op[6]
                            continue
                    else:
                        pc = op[5]
                        st = nst
                        caps = ncaps
                        continue
                elif can_stop:
                    pc = op[6]
                    continue
            elif t == LEND:
                L = op[1]
                c = cnt[L]
                if not (pos == st[L] and c >= op[2]):
                    cnt = cnt[:L] + (c + 1,) + cnt[L + 1:]
                    pc = op[3]
                    continue
            elif t == LINIT:
                L = op[1]
                cnt = cnt[:L] + (0,) + cnt[L + 1:]
                pc += 1
                continue
            elif t == OPEN:
                k = op[1]
                opens = opens[:k] + (pos,) + opens[k + 1:]
                pc += 1
                continue
            elif t == CLOSE:
                k = op[1]
                caps = caps[:k] + ((opens[k], pos),) + caps[k + 1:]
                pc += 1
                continue
            elif t == BREF:
                sp = caps[op[1]]
                if sp is None:
                    pc += 1
                    continue
                s, e = sp
                if text.startswith(text[s:e], pos):
                    pos += e - s
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
                return ((start, pos),) + caps[1:]
            # failure: backtrack
            if not stack:
                break
            pc, pos, caps, opens, cnt, st = stack.pop()
    return None


def search(pattern: str, text: str):
    try:
        if not isinstance(pattern, str) or not isinstance(text, str):
            return "ERROR"
        return _search(pattern, text)
    except Exception:
        return "ERROR"
```
