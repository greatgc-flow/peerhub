```python
def search(pattern, text):
    try:
        return _search(pattern, text)
    except _PErr:
        return "ERROR"
    except Exception:
        return None


class _PErr(Exception):
    pass


def _search(p, text):
    n = len(p)
    SPECIAL = set('\\.|*+?()[]{}^$')
    ESC_LIT = set('\\.|*+?()[]{}^$-')
    DIG = '0123456789'
    WORD = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
    INF = 10 ** 9
    st = [0, 0]  # groups, max backref

    def err():
        raise _PErr()

    def kind_test(k, c):
        b = k.lower()
        if b == 'd':
            r = '0' <= c <= '9'
        elif b == 'w':
            r = c in WORD
        else:
            r = c in ' \t\n'
        return r if k.islower() else (not r)

    def mk_pred(neg, ranges, kinds):
        cache = {}

        def f(c):
            r = cache.get(c)
            if r is None:
                r = False
                for lo, hi in ranges:
                    if lo <= c <= hi:
                        r = True
                        break
                if not r:
                    for k in kinds:
                        if kind_test(k, c):
                            r = True
                            break
                r = (r != neg)
                cache[c] = r
            return r
        return f

    def read_class_item(i):
        c = p[i]
        if c == '\\':
            if i + 1 >= n:
                err()
            e = p[i + 1]
            if e in ESC_LIT:
                return ('ch', e), i + 2
            if e == 'n':
                return ('ch', '\n'), i + 2
            if e == 't':
                return ('ch', '\t'), i + 2
            if e in 'dwsDWS':
                return ('cls', e), i + 2
            err()
        return ('ch', c), i + 1

    def parse_class(i):
        i += 1
        neg = False
        if i < n and p[i] == '^':
            neg = True
            i += 1
        ranges = []
        kinds = []
        while True:
            if i >= n:
                err()
            if p[i] == ']':
                i += 1
                break
            x, i = read_class_item(i)
            if i < n and p[i] == '-' and i + 1 < n and p[i + 1] != ']':
                y, i = read_class_item(i + 1)
                if x[0] != 'ch' or y[0] != 'ch' or x[1] > y[1]:
                    err()
                ranges.append((x[1], y[1]))
            else:
                if x[0] == 'ch':
                    ranges.append((x[1], x[1]))
                else:
                    kinds.append(x[1])
        return ('set', mk_pred(neg, ranges, kinds)), i

    def parse_escape(i):
        if i + 1 >= n:
            err()
        e = p[i + 1]
        if e in ESC_LIT:
            return ('char', e), i + 2
        if e == 'n':
            return ('char', '\n'), i + 2
        if e == 't':
            return ('char', '\t'), i + 2
        if e in 'dwsDWS':
            return ('set', mk_pred(False, [], [e])), i + 2
        if e in '123456789':
            k = int(e)
            if k > st[1]:
                st[1] = k
            return ('bref', k), i + 2
        err()

    def parse_atom(i):
        c = p[i]
        if c == '(':
            if p.startswith('?:', i + 1):
                i += 3
                body, i = parse_alt(i)
                if i >= n or p[i] != ')':
                    err()
                return body, i + 1
            st[0] += 1
            k = st[0]
            body, i = parse_alt(i + 1)
            if i >= n or p[i] != ')':
                err()
            return ('cap', k, body), i + 1
        if c == '.':
            return ('any',), i + 1
        if c == '[':
            return parse_class(i)
        if c == '\\':
            return parse_escape(i)
        if c in SPECIAL:
            err()
        return ('char', c), i + 1

    def parse_num(i):
        j = i
        while j < n and p[j] in DIG:
            j += 1
        if j == i:
            err()
        return int(p[i:j]), j

    def parse_seq(i):
        items = []
        while i < n and p[i] not in '|)':
            c = p[i]
            if c == '^':
                items.append(('bol',))
                i += 1
                continue
            if c == '$':
                items.append(('eol',))
                i += 1
                continue
            g0 = st[0]
            atom, i = parse_atom(i)
            g1 = st[0]
            if i < n and p[i] in '*+?{':
                q = p[i]
                i += 1
                if q == '*':
                    mn, mx = 0, INF
                elif q == '+':
                    mn, mx = 1, INF
                elif q == '?':
                    mn, mx = 0, 1
                else:
                    mn, i = parse_num(i)
                    if i >= n:
                        err()
                    if p[i] == '}':
                        mx = mn
                        i += 1
                    elif p[i] == ',':
                        i += 1
                        if i < n and p[i] == '}':
                            mx = INF
                            i += 1
                        else:
                            mx, i = parse_num(i)
                            if i >= n or p[i] != '}':
                                err()
                            i += 1
                    else:
                        err()
                    if mn > 1000 or (mx != INF and (mx > 1000 or mn > mx)):
                        err()
                lazy = False
                if i < n and p[i] == '?':
                    lazy = True
                    i += 1
                items.append(('rep', atom, mn, mx, lazy, tuple(range(g0 + 1, g1 + 1))))
            else:
                items.append(atom)
        return ('seq', items), i

    def parse_alt(i):
        seqs = []
        while True:
            s, i = parse_seq(i)
            seqs.append(s)
            if i < n and p[i] == '|':
                i += 1
            else:
                break
        return ('alt', seqs), i

    tree, i = parse_alt(0)
    if i < n:
        err()
    G = st[0]
    if st[1] > G:
        err()
    use_memo = (st[1] == 0)

    CHAR, ANY, SET, BOL, EOL, GS, GE, SPLIT, JMP, LTEST, LITER, LEND, LEXIT, BREF, MATCH = range(15)
    prog = []
    nl = [0]

    def emit(op, a=None, b=None):
        prog.append([op, a, b])
        return len(prog) - 1

    def comp(node):
        t = node[0]
        if t == 'char':
            emit(CHAR, node[1])
        elif t == 'any':
            emit(ANY)
        elif t == 'set':
            emit(SET, node[1])
        elif t == 'bol':
            emit(BOL)
        elif t == 'eol':
            emit(EOL)
        elif t == 'bref':
            emit(BREF, node[1])
        elif t == 'cap':
            emit(GS, node[1])
            comp(node[2])
            emit(GE, node[1])
        elif t == 'seq':
            for it in node[1]:
                comp(it)
        elif t == 'alt':
            seqs = node[1]
            jumps = []
            for idx, s in enumerate(seqs):
                if idx < len(seqs) - 1:
                    sp = emit(SPLIT)
                    comp(s)
                    jumps.append(emit(JMP))
                    prog[sp][1] = len(prog)
                else:
                    comp(s)
            for j in jumps:
                prog[j][1] = len(prog)
        elif t == 'rep':
            _, body, mn, mx, lazy, groups = node
            L = nl[0]
            nl[0] += 1
            tp = emit(LTEST)
            emit(LITER, None, (L, mn, groups))
            comp(body)
            emit(LEND, tp, (L, mn, mx))
            prog[tp][1] = len(prog)
            prog[tp][2] = (L, mn, mx, lazy)
            emit(LEXIT, L)

    comp(tree)
    emit(MATCH)
    NL = nl[0]
    T = G + 1
    CB = 2 * (G + 1)
    SB = CB + NL
    init = [None] * (2 * (G + 1)) + [0] * NL + [-1] * NL
    tn = len(text)
    seen = set()

    for start in range(tn + 1):
        mem = init[:]
        trail = []
        stack = []
        pos = start
        pc = 0
        while True:
            op, a, b = prog[pc]
            if op == CHAR:
                if pos < tn and text[pos] == a:
                    pos += 1
                    pc += 1
                    continue
            elif op == ANY:
                if pos < tn and text[pos] != '\n':
                    pos += 1
                    pc += 1
                    continue
            elif op == SET:
                if pos < tn and a(text[pos]):
                    pos += 1
                    pc += 1
                    continue
            elif op == BOL:
                if pos == 0:
                    pc += 1
                    continue
            elif op == EOL:
                if pos == tn:
                    pc += 1
                    continue
            elif op == GS:
                trail.append((T + a, mem[T + a]))
                mem[T + a] = pos
                pc += 1
                continue
            elif op == GE:
                trail.append((a, mem[a]))
                mem[a] = (mem[T + a], pos)
                pc += 1
                continue
            elif op == SPLIT:
                ok = True
                if use_memo:
                    if NL:
                        key = (pc, pos, tuple(mem[CB:SB]), tuple([s == pos for s in mem[SB:]]))
                    else:
                        key = (pc, pos)
                    if key in seen:
                        ok = False
                    else:
                        seen.add(key)
                if ok:
                    stack.append((a, pos, len(trail)))
                    pc += 1
                    continue
            elif op == JMP:
                pc = a
                continue
            elif op == LTEST:
                L, mn, mx, lazy = b
                c = mem[CB + L]
                if c < mn:
                    pc += 1
                    continue
                if c >= mx:
                    pc = a
                    continue
                ok = True
                if use_memo:
                    key = (pc, pos, tuple(mem[CB:SB]), tuple([s == pos for s in mem[SB:]]))
                    if key in seen:
                        ok = False
                    else:
                        seen.add(key)
                if ok:
                    if lazy:
                        stack.append((pc + 1, pos, len(trail)))
                        pc = a
                    else:
                        stack.append((a, pos, len(trail)))
                        pc += 1
                    continue
            elif op == LITER:
                L, mn, groups = b
                if mem[CB + L] >= mn:
                    trail.append((SB + L, mem[SB + L]))
                    mem[SB + L] = pos
                for g in groups:
                    if mem[g] is not None:
                        trail.append((g, mem[g]))
                        mem[g] = None
                pc += 1
                continue
            elif op == LEND:
                L, mn, mx = b
                c = mem[CB + L]
                if not (c >= mn and mem[SB + L] == pos):
                    nc = c + 1
                    if mx == INF and nc > mn:
                        nc = mn
                    if nc != c:
                        trail.append((CB + L, c))
                        mem[CB + L] = nc
                    pc = a
                    continue
            elif op == LEXIT:
                if mem[CB + a] != 0:
                    trail.append((CB + a, mem[CB + a]))
                    mem[CB + a] = 0
                if mem[SB + a] != -1:
                    trail.append((SB + a, mem[SB + a]))
                    mem[SB + a] = -1
                pc += 1
                continue
            elif op == BREF:
                v = mem[a]
                if v is None:
                    pc += 1
                    continue
                s = text[v[0]:v[1]]
                if text.startswith(s, pos):
                    pos += len(s)
                    pc += 1
                    continue
            elif op == MATCH:
                return ((start, pos),) + tuple(mem[1:G + 1])
            # failure: backtrack
            if not stack:
                break
            pc, pos, tl = stack.pop()
            while len(trail) > tl:
                idx, old = trail.pop()
                mem[idx] = old
    return None
```
