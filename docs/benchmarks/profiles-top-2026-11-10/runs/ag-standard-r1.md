```python
def search(pattern: str, text: str):
    try:
        n_pat = len(pattern)
        idx = 0
        group_count = 0

        # Pass 1: count capturing groups for backref validation
        def count_groups():
            i = 0
            cnt = 0
            while i < n_pat:
                c = pattern[i]
                if c == '\\':
                    i += 2
                elif c == '[':
                    i += 1
                    if i < n_pat and pattern[i] == '^':
                        i += 1
                    while i < n_pat and pattern[i] != ']':
                        if pattern[i] == '\\':
                            i += 2
                        else:
                            i += 1
                    i += 1
                elif c == '(':
                    if pattern.startswith('(?:', i):
                        i += 3
                    else:
                        cnt += 1
                        i += 1
                else:
                    i += 1
            return cnt

        try:
            total_groups = count_groups()
        except Exception:
            return "ERROR"

        # AST nodes:
        # ('alt', [seq1, seq2, ...])
        # ('seq', [item1, item2, ...])
        # ('caret',)
        # ('dollar',)
        # ('char', c)
        # ('dot',)
        # ('class', negated: bool, set_of_chars: set, special_checks: list)
        # ('escape_class', type) -> 'd', 'w', 's', 'D', 'W', 'S'
        # ('backref', group_idx)
        # ('group', group_idx, alt_node)
        # ('noncap', alt_node)
        # ('quant', atom_node, min_cnt, max_cnt, is_lazy, contained_groups)

        def parse_quantifier(atom_node, contained_groups):
            nonlocal idx
            if idx >= n_pat:
                return atom_node
            c = pattern[idx]
            min_c, max_c = None, None
            if c == '*':
                min_c, max_c = 0, float('inf')
                idx += 1
            elif c == '+':
                min_c, max_c = 1, float('inf')
                idx += 1
            elif c == '?':
                min_c, max_c = 0, 1
                idx += 1
            elif c == '{':
                idx += 1
                # parse m
                start_m = idx
                while idx < n_pat and pattern[idx].isdigit():
                    idx += 1
                if start_m == idx:
                    raise ValueError
                m = int(pattern[start_m:idx])
                if m > 1000:
                    raise ValueError
                if idx < n_pat and pattern[idx] == '}':
                    idx += 1
                    min_c, max_c = m, m
                elif idx < n_pat and pattern[idx] == ',':
                    idx += 1
                    if idx < n_pat and pattern[idx] == '}':
                        idx += 1
                        min_c, max_c = m, float('inf')
                    else:
                        start_n = idx
                        while idx < n_pat and pattern[idx].isdigit():
                            idx += 1
                        if start_n == idx or idx >= n_pat or pattern[idx] != '}':
                            raise ValueError
                        n = int(pattern[start_n:idx])
                        if n > 1000 or m > n:
                            raise ValueError
                        idx += 1
                        min_c, max_c = m, n
                else:
                    raise ValueError
            else:
                return atom_node

            is_lazy = False
            if idx < n_pat and pattern[idx] == '?':
                is_lazy = True
                idx += 1

            return ('quant', atom_node, min_c, max_c, is_lazy, contained_groups)

        def parse_alt():
            nonlocal idx
            branches = [parse_seq()]
            while idx < n_pat and pattern[idx] == '|':
                idx += 1
                branches.append(parse_seq())
            return ('alt', branches)

        def parse_seq():
            nonlocal idx
            items = []
            while idx < n_pat and pattern[idx] not in '|)':
                items.append(parse_item())
            return ('seq', items)

        def collect_groups(node):
            grps = set()
            t = node[0]
            if t == 'group':
                grps.add(node[1])
                grps |= collect_groups(node[2])
            elif t in ('noncap',):
                grps |= collect_groups(node[1])
            elif t == 'quant':
                grps |= collect_groups(node[1])
            elif t in ('alt', 'seq'):
                for child in node[1]:
                    grps |= collect_groups(child)
            return grps

        def parse_item():
            nonlocal idx
            if idx >= n_pat:
                raise ValueError
            c = pattern[idx]
            if c == '^':
                idx += 1
                return ('caret',)
            if c == '$':
                idx += 1
                return ('dollar',)

            atom = parse_atom()
            grps = collect_groups(atom)
            return parse_quantifier(atom, grps)

        def parse_escape(inside_class=False):
            nonlocal idx
            idx += 1  # skip '\'
            if idx >= n_pat:
                raise ValueError
            c = pattern[idx]
            idx += 1
            if c in r'\.*+?()[]{}^$-':
                return ('char', c)
            if c == 'n':
                return ('char', '\n')
            if c == 't':
                return ('char', '\t')
            if c in 'dwsDWS':
                return ('escape_class', c)
            if c in '123456789':
                if inside_class:
                    raise ValueError
                g_num = int(c)
                if g_num > total_groups or g_num < 1:
                    raise ValueError
                return ('backref', g_num)
            raise ValueError

        def parse_class():
            nonlocal idx
            idx += 1  # skip '['
            if idx >= n_pat:
                raise ValueError
            negated = False
            if pattern[idx] == '^':
                negated = True
                idx += 1

            chars = set()
            esc_classes = []

            # helper to get raw character if item is a single character
            def get_single_char():
                nonlocal idx
                if idx >= n_pat:
                    raise ValueError
                c = pattern[idx]
                if c == '\\':
                    esc = parse_escape(inside_class=True)
                    if esc[0] == 'char':
                        return esc[1]
                    else:
                        return esc
                else:
                    idx += 1
                    return c

            while idx < n_pat and pattern[idx] != ']':
                item1 = get_single_char()
                if idx < n_pat and pattern[idx] == '-':
                    if idx + 1 < n_pat and pattern[idx + 1] != ']':
                        idx += 1  # consume '-'
                        item2 = get_single_char()
                        if not isinstance(item1, str) or not isinstance(item2, str):
                            raise ValueError
                        if ord(item1) > ord(item2):
                            raise ValueError
                        for cp in range(ord(item1), ord(item2) + 1):
                            chars.add(chr(cp))
                    else:
                        # '-' followed by ']'
                        if isinstance(item1, str):
                            chars.add(item1)
                        else:
                            esc_classes.append(item1[1])
                        chars.add('-')
                        idx += 1  # consume '-'
                else:
                    if isinstance(item1, str):
                        chars.add(item1)
                    else:
                        esc_classes.append(item1[1])

            if idx >= n_pat or pattern[idx] != ']':
                raise ValueError
            idx += 1  # skip ']'
            return ('class', negated, chars, esc_classes)

        def parse_atom():
            nonlocal idx, group_count
            c = pattern[idx]
            if c == '.':
                idx += 1
                return ('dot',)
            if c == '\\':
                return parse_escape(inside_class=False)
            if c == '[':
                return parse_class()
            if c == '(':
                if pattern.startswith('(?:', idx):
                    idx += 3
                    alt = parse_alt()
                    if idx >= n_pat or pattern[idx] != ')':
                        raise ValueError
                    idx += 1
                    return ('noncap', alt)
                else:
                    idx += 1
                    group_count += 1
                    my_grp = group_count
                    alt = parse_alt()
                    if idx >= n_pat or pattern[idx] != ')':
                        raise ValueError
                    idx += 1
                    return ('group', my_grp, alt)
            if c in r'\.*+?()[]{}^$':
                raise ValueError
            idx += 1
            return ('char', c)

        ast = parse_alt()
        if idx != n_pat:
            return "ERROR"
    except Exception:
        return "ERROR"

    def match_escape_class(name, ch):
        if name == 'd':
            return '0' <= ch <= '9'
        if name == 'D':
            return not ('0' <= ch <= '9')
        if name == 'w':
            return ('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9') or (ch == '_')
        if name == 'W':
            return not (('a' <= ch <= 'z') or ('A' <= ch <= 'Z') or ('0' <= ch <= '9') or (ch == '_'))
        if name == 's':
            return ch in ' \t\n'
        if name == 'S':
            return ch not in ' \t\n'
        return False

    n_text = len(text)
    total_g = group_count

    # Backtracking engine using continuation-passing style
    def run_match(node, pos, groups, cont):
        t = node[0]
        if t == 'char':
            if pos < n_text and text[pos] == node[1]:
                yield from cont(pos + 1, groups)
        elif t == 'dot':
            if pos < n_text and text[pos] != '\n':
                yield from cont(pos + 1, groups)
        elif t == 'escape_class':
            if pos < n_text and match_escape_class(node[1], text[pos]):
                yield from cont(pos + 1, groups)
        elif t == 'class':
            _, negated, chars, esc_classes = node
            if pos < n_text:
                ch = text[pos]
                m = (ch in chars) or any(match_escape_class(ec, ch) for ec in esc_classes)
                if negated:
                    m = not m
                if m:
                    yield from cont(pos + 1, groups)
        elif t == 'caret':
            if pos == 0:
                yield from cont(pos, groups)
        elif t == 'dollar':
            if pos == n_text:
                yield from cont(pos, groups)
        elif t == 'backref':
            g_idx = node[1]
            span = groups.get(g_idx)
            if span is None:
                # Unset matches empty string
                yield from cont(pos, groups)
            else:
                s, e = span
                ref_str = text[s:e]
                len_r = len(ref_str)
                if pos + len_r <= n_text and text[pos:pos + len_r] == ref_str:
                    yield from cont(pos + len_r, groups)
        elif t == 'seq':
            items = node[1]
            def run_seq(idx_item, cur_pos, cur_groups):
                if idx_item == len(items):
                    yield from cont(cur_pos, cur_groups)
                else:
                    yield from run_match(
                        items[idx_item],
                        cur_pos,
                        cur_groups,
                        lambda p2, g2: run_seq(idx_item + 1, p2, g2)
                    )
            yield from run_seq(0, pos, groups)
        elif t == 'alt':
            for branch in node[1]:
                yield from run_match(branch, pos, groups, cont)
        elif t == 'noncap':
            yield from run_match(node[1], pos, groups, cont)
        elif t == 'group':
            g_idx = node[1]
            old_span = groups.get(g_idx)
            # Groups set start pos? But span is set when ')' is passed: (start, end)
            # Start position of this group evaluation is pos
            start_p = pos
            def group_cont(end_p, new_groups):
                prev = new_groups.get(g_idx)
                new_groups[g_idx] = (start_p, end_p)
                yield from cont(end_p, new_groups)
                if prev is None:
                    new_groups.pop(g_idx, None)
                else:
                    new_groups[g_idx] = prev
            yield from run_match(node[2], pos, groups, group_cont)
        elif t == 'quant':
            _, atom_node, min_c, max_c, is_lazy, contained_groups = node

            def quant_loop(count, cur_pos, cur_groups):
                can_stop = (count >= min_c)
                can_continue = (count < max_c)

                def do_stop():
                    yield from cont(cur_pos, cur_groups)

                def do_continue():
                    # Unset contained groups on new iteration
                    saved = {}
                    for g in contained_groups:
                        if g in cur_groups:
                            saved[g] = cur_groups[g]
                            del cur_groups[g]

                    def after_atom(next_pos, next_groups):
                        if next_pos == cur_pos and count >= min_c:
                            # Empty match fails if min iterations completed
                            return
                        yield from quant_loop(count + 1, next_pos, next_groups)

                    yield from run_match(atom_node, cur_pos, cur_groups, after_atom)

                    for g in contained_groups:
                        cur_groups.pop(g, None)
                    cur_groups.update(saved)

                if is_lazy:
                    if can_stop:
                        yield from do_stop()
                    if can_continue:
                        yield from do_continue()
                else:
                    if can_continue:
                        yield from do_continue()
                    if can_stop:
                        yield from do_stop()

            yield from quant_loop(0, pos, groups)

    for start in range(n_text + 1):
        def final_cont(end_pos, final_groups):
            res = [(start, end_pos)]
            for k in range(1, total_g + 1):
                res.append(final_groups.get(k))
            yield tuple(res)

        for match in run_match(ast, start, {}, final_cont):
            return match

    return None
```
