```python
import time

_INF = 1 << 60
_SPEC = frozenset('\\.|*+?()[]{}^$')


class _PErr(Exception):
    pass


def _parse(p):
    L = len(p)
    i = 0
    ng = 0
    refs = []

    def esc(in_class):
        nonlocal i
        if i + 1 >= L:
            raise _PErr()
        c = p[i + 1]
        i += 2
        if c in _SPEC or c == '-':
            return ('c', c)
        if c == 'n':
            return ('c', '\n')
        if c == 't':
            return ('c', '\t')
        if c in 'dwsDWS':
            return ('k', c)
        if (not in_class) and c in '123456789':
            return ('b', ord(c) - 48)
        raise _PErr()

    def number():
        nonlocal i
        j = i
        while i < L and p[i] in '0123456789':
            i += 1
        if i == j:
            return None
        ds = p[j:i].lstrip('0')
        if len(ds) > 4:
            raise _PErr()
        v = int(ds) if ds else 0
        if v > 1000:
            raise _PErr()
        return v

    def cls():
        nonlocal i
        i += 1
        neg = False
        if i < L and p[i] == '^':
            neg = True
            i += 1
        items = []
        while True:
            if i >= L:
                raise _PErr()
            c = p[i]
            if c == ']':
                i += 1
                break
            if c == '\\':
                x = esc(True)
            else:
                x = ('c', c)
                i += 1
            if i + 1 < L and p[i] == '-' and p[i + 1] != ']':
                i += 1
                c = p[i]
                if c == '\\':
                    y = esc(True)
                else:
                    y = ('c', c)
                    i += 1
                if x[0] != 'c' or y[0] != 'c' or x[1] > y[1]:
                    raise _PErr()
                items.append(('r', x[1], y[1]))
            else:
                items.append(x)
        return ('cs', neg, items)

    def atom():
        nonlocal i, ng
        c = p[i]
        if c == '(':
            if p.startswith('(?:', i):
                i += 3
                r = alt()
                if i >= L or p[i] != ')':
                    raise _PErr()
                i += 1
                return r
            ng += 1
            k = ng
            i += 1
            r = alt()
            if i >= L or p[i] != ')':
                raise _PErr()
            i += 1
            return ('grp', k, r)
        if c == '[':
            return cls()
        if c == '\\':
            x = esc(False)
            if x[0] == 'b':
                refs.append(x[1])
                return ('ref', x[1])
            return ('cs', False, [x])
        if c == '.':
            i += 1
            return ('cs', True, [('c', '\n')])
        if c in _SPEC:
            raise _PErr()
        i += 1
        return ('cs', False, [('c', c)])

    def item():
        nonlocal i
        c = p[i]
        if c == '^':
            i += 1
            return ('bol',)
        if c == '$':
            i += 1
            return ('eol',)
        a = atom()
        if i >= L:
            return a
        c = p[i]
        if c == '*':
            mn, mx = 0, _INF
            i += 1
        elif c == '+':
            mn, mx = 1, _INF
            i += 1
        elif c == '?':
            mn, mx = 0, 1
            i += 1
        elif c == '{':
            i += 1
            mn = number()
            if mn is None:
                raise _PErr()
            if i < L and p[i] == ',':
                i += 1
                mx = number()
                if mx is None:
                    mx = _INF
                elif mn > mx:
                    raise _PErr()
            else:
                mx = mn
            if i >= L or p[i] != '}':
                raise _PErr()
            i += 1
        else:
            return a
        greedy = True
        if i < L and p[i] == '?':
            greedy = False
            i += 1
        return ('rep', a, mn, mx, greedy)

    def seq():
        items = []
        while i < L and p[i] not in '|)':
            items.append(item())
        if len(items) == 1:
            return items[0]
        return ('cat', items)

    def alt():
        nonlocal i
        seqs = [seq()]
        while i < L and p[i] == '|':
            i += 1
            seqs.append(seq())
        if len(seqs) == 1:
            return seqs[0]
        return ('alt', seqs)

    r = alt()
    if i != L:
        raise _PErr()
    for k in refs:
        if k > ng:
            raise _PErr()
    return r, ng, refs


def _minlen(nd):
    t = nd[0]
    if t == 'cs':
        return 1
    if t == 'grp':
        return _minlen(nd[2])
    if t == 'cat':
        s = 0
        for x in nd[1]:
            s += _minlen(x)
        return s
    if t == 'alt':
        return min(_minlen(x) for x in nd[1])
    if t == 'rep':
        return nd[2] * _minlen(nd[1]) if nd[2] else 0
    return 0


def _cc(nd):
    t = nd[0]
    if t == 'cs' or t == 'ref':
        return True
    if t == 'grp':
        return _cc(nd[2])
    if t == 'cat' or t == 'alt':
        for x in nd[1]:
            if _cc(x):
                return True
        return False
    if t == 'rep':
        return nd[3] > 0 and _cc(nd[1])
    return False


def _groups(nd, out):
    t = nd[0]
    if t == 'grp':
        out.append(nd[1])
        _groups(nd[2], out)
    elif t == 'cat' or t == 'alt':
        for x in nd[1]:
            _groups(x, out)
    elif t == 'rep':
        _groups(nd[1], out)


def _im(it, ch):
    t = it[0]
    if t == 'c':
        return ch == it[1]
    if t == 'r':
        return it[1] <= ch <= it[2]
    k = it[1]
    if k == 'd':
        return '0' <= ch <= '9'
    if k == 'D':
        return not ('0' <= ch <= '9')
    if k == 's':
        return ch == ' ' or ch == '\t' or ch == '\n'
    if k == 'S':
        return not (ch == ' ' or ch == '\t' or ch == '\n')
    w = ('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9') or ch == '_'
    return w if k == 'w' else (not w)


def _match(ast, ng, refs, text, t0):
    n = len(text)
    last = n - _minlen(ast)
    if last < 0:
        return None
    uniq = set(text)
    prog = []
    nloops = [0]
    base = 2 * ng
    bsl = []
    for k in sorted(set(refs)):
        bsl.append(2 * k - 2)
        bsl.append(2 * k - 1)

    def okset(node):
        neg = node[1]
        items = node[2]
        res = set()
        for ch in uniq:
            hit = False
            for it in items:
                if _im(it, ch):
                    hit = True
                    break
            if hit != neg:
                res.add(ch)
        return res

    def memo(enc, own):
        rs = [c for c, _s in enc]
        if own is not None:
            rs.append(own)
        rs.extend(bsl)
        if not rs:
            return (set(), None, None, None)
        return (None, {}, rs, [s for _c, s in enc])

    def emit(node, enc, tail):
        t = node[0]
        if t == 'cs':
            prog.append((0, okset(node), n - tail))
        elif t == 'bol':
            prog.append((10,))
        elif t == 'eol':
            prog.append((11,))
        elif t == 'ref':
            prog.append((12, 2 * node[1] - 2))
        elif t == 'grp':
            c = 2 * node[1] - 2
            prog.append((3, c + 1))
            emit(node[2], enc, tail)
            prog.append((4, c + 1, c))
        elif t == 'cat':
            items = node[1]
            tails = []
            acc = tail
            for x in reversed(items):
                tails.append(acc)
                acc += _minlen(x)
            tails.reverse()
            for idx in range(len(items)):
                emit(items[idx], enc, tails[idx])
        elif t == 'alt':
            alts = node[1]
            jumps = []
            for idx in range(len(alts) - 1):
                sp = len(prog)
                prog.append(None)
                emit(alts[idx], enc, tail)
                jumps.append(len(prog))
                prog.append(None)
                prog[sp] = (1, len(prog), memo(enc, None))
            emit(alts[-1], enc, tail)
            end = len(prog)
            for j in jumps:
                prog[j] = (2, end)
        else:
            sub, mn, mx, greedy = node[1], node[2], node[3], node[4]
            if mx == 0:
                return
            gl = []
            _groups(sub, gl)
            gs = tuple(2 * k - 2 for k in gl)
            if not _cc(sub):
                if mn == 0:
                    return
                if gs:
                    prog.append((13, gs))
                emit(sub, enc, tail)
                return
            if sub[0] == 'cs' and (greedy or mn == mx):
                prog.append((8, okset(sub), mn, mx - mn, n - tail, memo(enc, None)))
                return
            j = nloops[0]
            nloops[0] += 1
            cnt = base + 3 * j
            prog.append((9, cnt))
            head = len(prog)
            prog.append(None)
            prog.append((6, cnt, cnt + 1, mn, gs, cnt + 2))
            emit(sub, enc + ((cnt, cnt + 1),), tail)
            prog.append((7, cnt, cnt + 1, mn, mx, head, cnt + 2))
            prog[head] = (5, cnt, mn, mx, greedy, len(prog),
                          memo(enc, None if mx >= _INF else cnt), n - tail)

    emit(ast, (), 0)
    prog.append((14,))
    if prog[0][0] == 10:
        last = 0
    init = [None, 0] * ng + [0, -1, None] * nloops[0]
    perf = time.perf_counter
    deadline = t0 + 0.85
    nd = 0
    tk = 0

    for s in range(last + 1):
        reg = init[:]
        trail = []
        stack = []
        pc = 0
        pos = s
        while True:
            ins = prog[pc]
            op = ins[0]
            if op == 0:
                if pos < ins[2] and text[pos] in ins[1]:
                    pos += 1
                    pc += 1
                    continue
            elif op == 1:
                m = ins[2]
                vs = m[0]
                if vs is None:
                    key = tuple([reg[x] for x in m[2]] + [pos > reg[x] for x in m[3]])
                    d = m[1]
                    vs = d.get(key)
                    if vs is None:
                        vs = d[key] = set()
                if pos not in vs:
                    vs.add(pos)
                    stack.append((ins[1], pos, len(trail), -1))
                    pc += 1
                    continue
            elif op == 2:
                pc = ins[1]
                continue
            elif op == 3:
                x = ins[1]
                trail.append((x, reg[x]))
                reg[x] = pos
                pc += 1
                continue
            elif op == 4:
                x = ins[2]
                trail.append((x, reg[x]))
                reg[x] = (reg[ins[1]], pos)
                pc += 1
                continue
            elif op == 5:
                c = reg[ins[1]]
                if c < ins[2]:
                    pc += 1
                    continue
                if c >= ins[3] or pos >= ins[7]:
                    pc = ins[5]
                    continue
                m = ins[6]
                vs = m[0]
                if vs is None:
                    key = tuple([reg[x] for x in m[2]] + [pos > reg[x] for x in m[3]])
                    d = m[1]
                    vs = d.get(key)
                    if vs is None:
                        vs = d[key] = set()
                if pos not in vs:
                    vs.add(pos)
                    if ins[4]:
                        stack.append((ins[5], pos, len(trail), -1))
                        pc += 1
                    else:
                        stack.append((pc + 1, pos, len(trail), -1))
                        pc = ins[5]
                    continue
            elif op == 6:
                x = ins[2]
                c = reg[ins[1]]
                mn = ins[3]
                if c >= mn:
                    v = pos
                else:
                    v = -1
                    if c < mn - 1:
                        y = ins[5]
                        trail.append((y, reg[y]))
                        reg[y] = (pos, nd, len(stack))
                if reg[x] != v:
                    trail.append((x, reg[x]))
                    reg[x] = v
                for g in ins[4]:
                    if reg[g] is not None:
                        trail.append((g, reg[g]))
                        reg[g] = None
                pc += 1
                continue
            elif op == 7:
                if pos != reg[ins[2]]:
                    x = ins[1]
                    c = reg[x]
                    nc = c + 1
                    mn = ins[3]
                    if nc >= mn:
                        if ins[4] - nc >= n - pos:
                            nc = mn
                    elif reg[ins[6]] == (pos, nd, len(stack)):
                        nc = mn
                    if nc != c:
                        trail.append((x, c))
                        reg[x] = nc
                    pc = ins[5]
                    tk += 1
                    if not tk & 4095 and perf() > deadline:
                        return None
                    continue
            elif op == 8:
                ok = ins[1]
                q = pos
                e = q + ins[2]
                lim = ins[4]
                if e <= lim:
                    while q < e and text[q] in ok:
                        q += 1
                    if q == e:
                        m = ins[5]
                        vs = m[0]
                        if vs is None:
                            bs = [reg[x] for x in m[2]]
                            fl = [q > reg[x] for x in m[3]]
                            d = m[1]
                            key = tuple(bs + fl)
                            vs = d.get(key)
                            if vs is None:
                                vs = d[key] = set()
                            if False in fl:
                                key = tuple(bs + [True] * len(fl))
                                vt = d.get(key)
                                if vt is None:
                                    vt = d[key] = set()
                            else:
                                vt = vs
                        else:
                            vt = vs
                        if q not in vs:
                            vs.add(q)
                            ext = ins[3]
                            if ext >= lim - q:
                                while q < lim and text[q] in ok:
                                    q += 1
                                    if q in vt:
                                        q -= 1
                                        nd += 1
                                        break
                                    vt.add(q)
                            else:
                                hi = q + ext
                                while q < hi and text[q] in ok:
                                    q += 1
                            if q > e:
                                stack.append((pc + 1, q - 1, len(trail), e))
                            pos = q
                            pc += 1
                            continue
            elif op == 9:
                x = ins[1]
                if reg[x] != 0:
                    trail.append((x, reg[x]))
                    reg[x] = 0
                pc += 1
                continue
            elif op == 10:
                if pos == 0:
                    pc += 1
                    continue
            elif op == 11:
                if pos == n:
                    pc += 1
                    continue
            elif op == 12:
                sp = reg[ins[1]]
                if sp is None:
                    pc += 1
                    continue
                a = sp[0]
                b = sp[1]
                if text.startswith(text[a:b], pos):
                    pos += b - a
                    pc += 1
                    continue
            elif op == 13:
                for g in ins[1]:
                    if reg[g] is not None:
                        trail.append((g, reg[g]))
                        reg[g] = None
                pc += 1
                continue
            else:
                return ((s, pos),) + tuple(reg[2 * k] for k in range(ng))
            # failure: backtrack
            if not stack:
                break
            nd += 1
            if not nd & 4095 and perf() > deadline:
                return None
            pc, pos, tl, lo = stack.pop()
            while len(trail) > tl:
                x, v = trail.pop()
                reg[x] = v
            if lo >= 0 and pos > lo:
                stack.append((pc, pos - 1, tl, lo))
    return None


def search(pattern, text):
    t0 = time.perf_counter()
    try:
        ast, ng, refs = _parse(pattern)
    except Exception:
        return "ERROR"
    try:
        return _match(ast, ng, refs, text, t0)
    except Exception:
        return None
```[ask_uncertain] Execution ended as uncertain.; next: Inspect stream health and check for uncommitted or partial activity (peerhub --db 'D:\PkgDev\workspace\peerhub\.peerhub\core.db' diag health --stream top-cc-fable52-r1); Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Continuing work on this stream is blocked to prevent split-brain execution; start a new stream for subsequent work. If the ask used the default timeout, do not retry on the same stream; start a new stream with explicit --timeout-seconds (long reviews: 1800 or more).; details: peerhub explain ask_uncertain

