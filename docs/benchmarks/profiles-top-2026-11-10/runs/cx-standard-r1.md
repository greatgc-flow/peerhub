```python
def search(pattern: str, text: str):
    try:
        if not isinstance(pattern, str) or not isinstance(text, str):
            return "ERROR"
        if len(pattern) > 100 or len(text) > 1000:
            return "ERROR"

        class Invalid(Exception):
            pass

        class Parser:
            def __init__(self, source):
                self.s = source
                self.i = 0
                self.groups = 0
                self.group_nodes = {}

            def parse(self):
                node = self.alt()
                if self.i != len(self.s):
                    raise Invalid()
                # Backreference validity depends on the total group count.
                def validate(n):
                    if n[0] == "backref" and n[1] > self.groups:
                        raise Invalid()
                    for child in n[1:]:
                        if isinstance(child, tuple):
                            validate(child)
                        elif isinstance(child, list):
                            for x in child:
                                if isinstance(x, tuple):
                                    validate(x)
                validate(node)
                return node, self.groups, self.group_nodes

            def alt(self):
                branches = [self.seq()]
                while self.i < len(self.s) and self.s[self.i] == "|":
                    self.i += 1
                    branches.append(self.seq())
                return ("alt", branches)

            def seq(self):
                items = []
                while self.i < len(self.s) and self.s[self.i] not in ")|":
                    c = self.s[self.i]
                    if c in "*+?{}":
                        raise Invalid()
                    if c in "^$":
                        self.i += 1
                        items.append(("assert", c))
                        continue
                    atom = self.atom()
                    if self.i < len(self.s) and self.s[self.i] in "*+?{":
                        atom = self.quant(atom)
                    items.append(atom)
                return ("seq", items)

            def atom(self):
                c = self.s[self.i]
                if c == ".":
                    self.i += 1
                    return ("dot",)
                if c == "[":
                    return self.charclass()
                if c == "\\":
                    return self.escape(False)
                if c == "(":
                    self.i += 1
                    capturing = True
                    if self.i < len(self.s) and self.s[self.i] == "?":
                        if self.i + 1 >= len(self.s) or self.s[self.i + 1] != ":":
                            raise Invalid()
                        self.i += 2
                        capturing = False
                    gid = None
                    if capturing:
                        self.groups += 1
                        gid = self.groups
                    body = self.alt()
                    if self.i >= len(self.s) or self.s[self.i] != ")":
                        raise Invalid()
                    self.i += 1
                    if capturing:
                        node = ("group", gid, body)
                        self.group_nodes[gid] = node
                        return node
                    return body
                if c in "\\.|*+?()[]{}^$":
                    raise Invalid()
                self.i += 1
                return ("char", c)

            def quant(self, atom):
                c = self.s[self.i]
                self.i += 1
                if c == "*":
                    lo, hi = 0, None
                elif c == "+":
                    lo, hi = 1, None
                elif c == "?":
                    lo, hi = 0, 1
                else:
                    j = self.s.find("}", self.i)
                    if j < 0:
                        raise Invalid()
                    spec = self.s[self.i:j]
                    self.i = j + 1
                    if "," in spec:
                        parts = spec.split(",")
                        if len(parts) != 2 or not parts[0].isascii() or not parts[0].isdigit():
                            raise Invalid()
                        lo = int(parts[0])
                        if parts[1] == "":
                            hi = None
                        elif parts[1].isascii() and parts[1].isdigit():
                            hi = int(parts[1])
                        else:
                            raise Invalid()
                    else:
                        if not spec.isascii() or not spec.isdigit():
                            raise Invalid()
                        lo = hi = int(spec)
                    if lo > 1000 or (hi is not None and (hi > 1000 or lo > hi)):
                        raise Invalid()
                lazy = False
                if self.i < len(self.s) and self.s[self.i] == "?":
                    self.i += 1
                    lazy = True
                return ("repeat", atom, lo, hi, lazy)

            def escape(self, in_class):
                self.i += 1
                if self.i >= len(self.s):
                    raise Invalid()
                c = self.s[self.i]
                self.i += 1
                if c in r"\.|*+?()[]{}^$-":
                    return ("char", c)
                if c == "n":
                    return ("char", "\n")
                if c == "t":
                    return ("char", "\t")
                if c in "dDwWsS":
                    return ("set", c)
                if c in "123456789":
                    if in_class:
                        raise Invalid()
                    return ("backref", int(c))
                raise Invalid()

            def class_item(self):
                if self.i >= len(self.s):
                    raise Invalid()
                if self.s[self.i] == "\\":
                    n = self.escape(True)
                    if n[0] != "char":
                        return n, False
                    return n[1], True
                c = self.s[self.i]
                self.i += 1
                return c, True

            def charclass(self):
                self.i += 1
                neg = self.i < len(self.s) and self.s[self.i] == "^"
                if neg:
                    self.i += 1
                ranges = []
                singles = []
                while self.i < len(self.s) and self.s[self.i] != "]":
                    x, xchar = self.class_item()
                    if (isinstance(x, tuple) and x[0] == "set"):
                        singles.append(x[1])
                        continue
                    if (self.i < len(self.s) and self.s[self.i] == "-"
                            and self.i + 1 < len(self.s)
                            and self.s[self.i + 1] != "]"):
                        if not xchar:
                            raise Invalid()
                        self.i += 1
                        y, ychar = self.class_item()
                        if not ychar or ord(x) > ord(y):
                            raise Invalid()
                        ranges.append((x, y))
                    else:
                        singles.append(x)
                if self.i >= len(self.s):
                    raise Invalid()
                self.i += 1
                return ("class", neg, singles, ranges)

        ast, group_count, group_nodes = Parser(pattern).parse()

        def groups_in(node):
            found = set()
            def walk(n):
                if n[0] == "group":
                    found.add(n[1])
                    walk(n[2])
                elif n[0] in ("alt", "seq"):
                    for x in n[1]:
                        walk(x)
                elif n[0] == "repeat":
                    walk(n[1])
            walk(node)
            return found

        # Ordered backtracking VM. State is (position, capture spans).
        def run(node, pos, caps):
            typ = node[0]
            if typ == "seq":
                states = [(pos, caps)]
                for child in node[1]:
                    nxt = []
                    for p, c in states:
                        nxt.extend(run(child, p, c))
                    states = nxt
                    if not states:
                        break
                return states
            if typ == "alt":
                out = []
                for branch in node[1]:
                    out.extend(run(branch, pos, caps.copy()))
                return out
            if typ == "assert":
                if (node[1] == "^" and pos == 0) or (node[1] == "$" and pos == len(text)):
                    return [(pos, caps)]
                return []
            if typ == "char":
                return [(pos + 1, caps)] if pos < len(text) and text[pos] == node[1] else []
            if typ == "dot":
                return [(pos + 1, caps)] if pos < len(text) and text[pos] != "\n" else []
            if typ == "set":
                c = node[1]
                def positive(ch):
                    if c.lower() == "d":
                        return "0" <= ch <= "9"
                    if c.lower() == "w":
                        return ch == "_" or "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z"
                    return ch in " \t\n"
                ok = pos < len(text) and positive(text[pos])
                if c.isupper():
                    ok = pos < len(text) and not positive(text[pos])
                return [(pos + 1, caps)] if ok else []
            if typ == "class":
                if pos >= len(text):
                    return []
                ch = text[pos]
                hit = ch in node[2]
                if not hit:
                    hit = any(a <= ch <= b for a, b in node[3])
                for s in node[2]:
                    if isinstance(s, str) and s in "dDwWsS":
                        q = s.lower()
                        p = ("0" <= ch <= "9") if q == "d" else (
                            ch == "_" or "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z"
                        ) if q == "w" else ch in " \t\n"
                        if s.isupper():
                            p = not p
                        hit = hit or p
                if node[1]:
                    hit = not hit
                return [(pos + 1, caps)] if hit else []
            if typ == "backref":
                span = caps[node[1]]
                if span is None:
                    return [(pos, caps)]
                a, b = span
                value = text[a:b]
                return [(pos + len(value), caps)] if text.startswith(value, pos) else []
            if typ == "group":
                out = []
                for p, c in run(node[2], pos, caps):
                    d = c.copy()
                    d[node[1]] = (pos, p)
                    out.append((p, d))
                return out
            if typ == "repeat":
                atom, lo, hi, lazy = node[1:]
                reset_ids = groups_in(atom)

                def rec(p, c, count):
                    can_stop = count >= lo
                    can_more = hi is None or count < hi
                    results = []
                    if lazy and can_stop:
                        results.append((p, c))
                    if can_more:
                        d = c.copy()
                        for gid in reset_ids:
                            d[gid] = None
                        for q, e in run(atom, p, d):
                            if q == p and count >= lo:
                                continue
                            results.extend(rec(q, e, count + 1))
                    if not lazy and can_stop:
                        results.append((p, c))
                    return results

                return rec(pos, caps, 0)
            return []

        for start in range(len(text) + 1):
            initial = [None] * (group_count + 1)
            matches = run(ast, start, initial)
            if matches:
                end, caps = matches[0]
                return ((start, end), *caps[1:])
        return None
    except Exception:
        return "ERROR"
```
