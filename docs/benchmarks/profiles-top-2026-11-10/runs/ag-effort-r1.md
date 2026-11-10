```python
import sys

sys.setrecursionlimit(200000)


class Char:
    def __init__(self, c):
        self.c = c
        self.contained_groups = set()


class Dot:
    def __init__(self):
        self.contained_groups = set()


class Class:
    def __init__(self, negated, items):
        self.negated = negated
        self.items = items
        self.contained_groups = set()

    def matches(self, ch):
        o = ord(ch)
        matched = False
        for it in self.items:
            if isinstance(it, str):
                if len(it) == 1:
                    if ch == it:
                        matched = True
                        break
                elif it == "d":
                    if "0" <= ch <= "9":
                        matched = True
                        break
                elif it == "w":
                    if (
                        "a" <= ch <= "z"
                        or "A" <= ch <= "Z"
                        or "0" <= ch <= "9"
                        or ch == "_"
                    ):
                        matched = True
                        break
                elif it == "s":
                    if ch in (" ", "\t", "\n"):
                        matched = True
                        break
                elif it == "D":
                    if not ("0" <= ch <= "9"):
                        matched = True
                        break
                elif it == "W":
                    if not (
                        "a" <= ch <= "z"
                        or "A" <= ch <= "Z"
                        or "0" <= ch <= "9"
                        or ch == "_"
                    ):
                        matched = True
                        break
                elif it == "S":
                    if ch not in (" ", "\t", "\n"):
                        matched = True
                        break
            elif isinstance(it, tuple):
                if ord(it[0]) <= o <= ord(it[1]):
                    matched = True
                    break
        return not matched if self.negated else matched


class Anchor:
    def __init__(self, kind):
        self.kind = kind
        self.contained_groups = set()


class Backref:
    def __init__(self, gid):
        self.gid = gid
        self.contained_groups = set()


class CaptureGroup:
    def __init__(self, gid, alt):
        self.gid = gid
        self.alt = alt
        self.contained_groups = {gid} | alt.contained_groups


class NonCaptureGroup:
    def __init__(self, alt):
        self.alt = alt
        self.contained_groups = set(alt.contained_groups)


class Quant:
    def __init__(self, atom, min_count, max_count, lazy):
        self.atom = atom
        self.min_count = min_count
        self.max_count = max_count
        self.lazy = lazy
        self.contained_groups = set(atom.contained_groups)


class Seq:
    def __init__(self, items):
        self.items = items
        cg = set()
        for it in items:
            cg |= it.contained_groups
        self.contained_groups = cg


class Alt:
    def __init__(self, seqs):
        self.seqs = seqs
        cg = set()
        for s in seqs:
            cg |= s.contained_groups
        self.contained_groups = cg


class ClassToken:
    def __init__(self, is_single_char, char=None, set_name=None):
        self.is_single_char = is_single_char
        self.char = char
        self.set_name = set_name


class Parser:
    def __init__(self, p):
        self.p = p
        self.n = len(p)
        self.pos = 0
        self.group_count = 0
        self.backrefs = []

    def parse(self):
        ast = self.parse_alt()
        if self.pos != self.n:
            raise ValueError("Unconsumed characters")
        for gid in self.backrefs:
            if gid < 1 or gid > self.group_count:
                raise ValueError(f"Backreference \\{gid} out of range")
        return ast, self.group_count

    def parse_alt(self):
        seqs = [self.parse_seq()]
        while self.pos < self.n and self.p[self.pos] == "|":
            self.pos += 1
            seqs.append(self.parse_seq())
        return Alt(seqs)

    def parse_seq(self):
        items = []
        while self.pos < self.n and self.p[self.pos] not in ("|", ")"):
            items.append(self.parse_item())
        return Seq(items)

    def parse_item(self):
        if self.pos >= self.n:
            raise ValueError("Unexpected EOF")
        ch = self.p[self.pos]
        if ch == "^":
            self.pos += 1
            return Anchor("^")
        if ch == "$":
            self.pos += 1
            return Anchor("$")

        atom = self.parse_atom()
        quant = self.parse_quant_opt()
        if quant is not None:
            return Quant(atom, quant[0], quant[1], quant[2])
        return atom

    def parse_quant_opt(self):
        if self.pos >= self.n:
            return None
        ch = self.p[self.pos]
        if ch == "*":
            self.pos += 1
            return (0, float("inf"), self.parse_lazy_opt())
        if ch == "+":
            self.pos += 1
            return (1, float("inf"), self.parse_lazy_opt())
        if ch == "?":
            self.pos += 1
            return (0, 1, self.parse_lazy_opt())
        if ch == "{":
            self.pos += 1
            start_m = self.pos
            while self.pos < self.n and self.p[self.pos].isdigit():
                self.pos += 1
            if self.pos == start_m:
                raise ValueError("Expected digits for m")
            m = int(self.p[start_m : self.pos])
            if m > 1000:
                raise ValueError("m > 1000")

            if self.pos >= self.n:
                raise ValueError("Unclosed {")

            if self.p[self.pos] == "}":
                self.pos += 1
                return (m, m, self.parse_lazy_opt())
            elif self.p[self.pos] == ",":
                self.pos += 1
                if self.pos >= self.n:
                    raise ValueError("Unclosed {")
                if self.p[self.pos] == "}":
                    self.pos += 1
                    return (m, float("inf"), self.parse_lazy_opt())
                elif self.p[self.pos].isdigit():
                    start_n = self.pos
                    while self.pos < self.n and self.p[self.pos].isdigit():
                        self.pos += 1
                    n = int(self.p[start_n : self.pos])
                    if n > 1000 or m > n:
                        raise ValueError("n > 1000 or m > n")
                    if self.pos >= self.n or self.p[self.pos] != "}":
                        raise ValueError("Expected }")
                    self.pos += 1
                    return (m, n, self.parse_lazy_opt())
                else:
                    raise ValueError("Expected digit or } after comma")
            else:
                raise ValueError("Expected , or }")
        return None

    def parse_lazy_opt(self):
        if self.pos < self.n and self.p[self.pos] == "?":
            self.pos += 1
            return True
        return False

    def parse_atom(self):
        if self.pos >= self.n:
            raise ValueError("Unexpected EOF in atom")
        ch = self.p[self.pos]
        if ch == ".":
            self.pos += 1
            return Dot()
        if ch == "[":
            return self.parse_class()
        if ch == "\\":
            return self.parse_escape()
        if ch == "(":
            self.pos += 1
            if self.pos + 1 < self.n and self.p[self.pos : self.pos + 2] == "?:":
                self.pos += 2
                alt = self.parse_alt()
                if self.pos >= self.n or self.p[self.pos] != ")":
                    raise ValueError("Expected )")
                self.pos += 1
                return NonCaptureGroup(alt)
            else:
                self.group_count += 1
                gid = self.group_count
                alt = self.parse_alt()
                if self.pos >= self.n or self.p[self.pos] != ")":
                    raise ValueError("Expected )")
                self.pos += 1
                return CaptureGroup(gid, alt)

        if ch in r"\.|*+?()[]{}^$":
            raise ValueError(f"Disallowed char {ch}")
        self.pos += 1
        return Char(ch)

    def parse_escape(self):
        self.pos += 1
        if self.pos >= self.n:
            raise ValueError("Trailing backslash")
        ch = self.p[self.pos]
        self.pos += 1
        if ch in r"\.|*+?()[]{}^$-":
            return Char(ch)
        if ch == "n":
            return Char("\n")
        if ch == "t":
            return Char("\t")
        if ch in ("d", "w", "s", "D", "W", "S"):
            return Class(negated=False, items=[ch])
        if "1" <= ch <= "9":
            gid = int(ch)
            self.backrefs.append(gid)
            return Backref(gid)
        raise ValueError(f"Invalid escape \\{ch}")

    def parse_class(self):
        self.pos += 1
        if self.pos >= self.n:
            raise ValueError("Unclosed [")

        negated = False
        if self.p[self.pos] == "^":
            negated = True
            self.pos += 1

        if self.pos < self.n and self.p[self.pos] == "]":
            self.pos += 1
            return Class(negated=negated, items=[])

        items = []
        prev_item = None

        while self.pos < self.n and self.p[self.pos] != "]":
            if self.p[self.pos] == "-" and (
                self.pos + 1 < self.n and self.p[self.pos + 1] != "]"
            ):
                if prev_item is not None:
                    if not prev_item.is_single_char:
                        raise ValueError("Range start not single char")
                    self.pos += 1
                    if self.pos >= self.n or self.p[self.pos] == "]":
                        raise ValueError("Expected range end")
                    token_y = self.parse_class_token()
                    if not token_y.is_single_char:
                        raise ValueError("Range end not single char")
                    if ord(prev_item.char) > ord(token_y.char):
                        raise ValueError("Range x > y")
                    items.pop()
                    items.append((prev_item.char, token_y.char))
                    prev_item = None
                else:
                    token = [REDACTED]
                    items.append(token.char)
                    prev_item = token
            else:
                token = [REDACTED]
                if token.is_single_char:
                    items.append(token.char)
                else:
                    items.append(token.set_name)
                prev_item = token

        if self.pos >= self.n or self.p[self.pos] != "]":
            raise ValueError("Unclosed [")
        self.pos += 1
        return Class(negated=negated, items=items)

    def parse_class_token(self):
        if self.pos >= self.n:
            raise ValueError("Unexpected EOF in class")
        ch = self.p[self.pos]
        if ch == "\\":
            self.pos += 1
            if self.pos >= self.n:
                raise ValueError("Trailing backslash in class")
            esc = self.p[self.pos]
            self.pos += 1
            if esc in r"\.|*+?()[]{}^$-":
                return ClassToken(True, char=esc)
            if esc == "n":
                return ClassToken(True, char="\n")
            if esc == "t":
                return ClassToken(True, char="\t")
            if esc in ("d", "w", "s", "D", "W", "S"):
                return ClassToken(False, set_name=esc)
            raise ValueError(f"Invalid escape in class \\{esc}")
        else:
            self.pos += 1
            return ClassToken(True, char=ch)


def match_node(node, pos, groups, cont, text):
    if isinstance(node, Char):
        if pos < len(text) and text[pos] == node.c:
            return cont(pos + 1, groups)
        return None

    if isinstance(node, Dot):
        if pos < len(text) and text[pos] != "\n":
            return cont(pos + 1, groups)
        return None

    if isinstance(node, Class):
        if pos < len(text) and node.matches(text[pos]):
            return cont(pos + 1, groups)
        return None

    if isinstance(node, Anchor):
        if node.kind == "^":
            if pos == 0:
                return cont(pos, groups)
            return None
        elif node.kind == "$":
            if pos == len(text):
                return cont(pos, groups)
            return None

    if isinstance(node, Backref):
        span = groups.get(node.gid)
        if span is None:
            return cont(pos, groups)
        s, e = span
        target = text[s:e]
        length = len(target)
        if pos + length <= len(text) and text[pos : pos + length] == target:
            return cont(pos + length, groups)
        return None

    if isinstance(node, CaptureGroup):
        start_pos = pos

        def group_cont(end_pos, new_groups):
            updated = dict(new_groups)
            updated[node.gid] = (start_pos, end_pos)
            return cont(end_pos, updated)

        return match_node(node.alt, pos, groups, group_cont, text)

    if isinstance(node, NonCaptureGroup):
        return match_node(node.alt, pos, groups, cont, text)

    if isinstance(node, Alt):
        for seq in node.seqs:
            res = match_node(seq, pos, groups, cont, text)
            if res is not None:
                return res
        return None

    if isinstance(node, Seq):

        def step(index, p, g):
            if index == len(node.items):
                return cont(p, g)
            item = node.items[index]
            return match_node(
                item, p, g, lambda p2, g2: step(index + 1, p2, g2), text
            )

        return step(0, pos, groups)

    if isinstance(node, Quant):
        min_count = node.min_count
        max_count = node.max_count
        lazy = node.lazy
        contained = node.atom.contained_groups

        def run_quant(count, cur_pos, cur_groups):
            can_stop = count >= min_count
            can_step = count < max_count

            def do_stop():
                return cont(cur_pos, cur_groups)

            def do_step():
                step_groups = dict(cur_groups)
                for g in contained:
                    step_groups[g] = None

                def iter_cont(next_pos, next_groups):
                    if next_pos == cur_pos and count >= min_count:
                        return None
                    return run_quant(count + 1, next_pos, next_groups)

                return match_node(
                    node.atom, cur_pos, step_groups, iter_cont, text
                )

            if not can_stop:
                return do_step()
            elif not can_step:
                return do_stop()
            else:
                if lazy:
                    res = do_stop()
                    if res is not None:
                        return res
                    return do_step()
                else:
                    res = do_step()
                    if res is not None:
                        return res
                    return do_stop()

        return run_quant(0, pos, groups)


def search(pattern: str, text: str):
    if not isinstance(pattern, str) or not isinstance(text, str):
        return "ERROR"
    try:
        parser = Parser(pattern)
        ast, total_groups = parser.parse()
    except Exception:
        return "ERROR"

    for start in range(len(text) + 1):
        init_groups = {g: None for g in range(1, total_groups + 1)}

        def cont(end_pos, final_groups):
            res = [(start, end_pos)]
            for g in range(1, total_groups + 1):
                res.append(final_groups.get(g, None))
            return tuple(res)

        match_res = match_node(ast, start, init_groups, cont, text)
        if match_res is not None:
            return match_res

    return None
```
