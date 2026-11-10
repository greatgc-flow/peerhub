```python
def evaluate(src: str):
    import re

    SYNTAX = "SYNTAX"
    DIV_ZERO = "DIV_ZERO"
    UNDEFINED = "UNDEFINED"
    NEG_EXP = "NEG_EXP"
    OVERFLOW = "OVERFLOW"
    LIMIT = 10**18

    try:
        if not isinstance(src, str) or len(src) > 10000:
            return SYNTAX

        tokens = []
        i = 0
        n = len(src)
        while i < n:
            c = src[i]
            if c in " \t\n":
                i += 1
            elif "0" <= c <= "9":
                j = i + 1
                while j < n and "0" <= src[j] <= "9":
                    j += 1
                s = src[i:j]
                if len(s) > 1 and s[0] == "0":
                    return SYNTAX
                tokens.append(("num", s))
                i = j
            elif "a" <= c <= "z" or c == "_":
                j = i + 1
                while j < n and (
                    "a" <= src[j] <= "z"
                    or "0" <= src[j] <= "9"
                    or src[j] == "_"
                ):
                    j += 1
                tokens.append(("name", src[i:j]))
                i = j
            elif c in "+-*/%^()=;":
                tokens.append((c, c))
                i += 1
            else:
                return SYNTAX

        if not tokens:
            return SYNTAX

        pos = 0
        depth = 0

        def peek():
            return tokens[pos][0] if pos < len(tokens) else None

        def accept(kind):
            nonlocal pos
            if peek() == kind:
                pos += 1
                return True
            return False

        def parse_primary():
            nonlocal pos, depth
            kind = peek()
            if kind in ("num", "name"):
                pos += 1
                return
            if accept("("):
                depth += 1
                if depth > 100:
                    raise ValueError
                parse_expr()
                if not accept(")"):
                    raise ValueError
                depth -= 1
                return
            raise ValueError

        def parse_power():
            parse_primary()
            if accept("^"):
                parse_unary()

        def parse_unary():
            count = 0
            while peek() in ("+", "-"):
                pos_plus_minus()
                count += 1
                if count > 100:
                    raise ValueError
            parse_power()

        def pos_plus_minus():
            nonlocal pos
            pos += 1

        def parse_term():
            parse_unary()
            while peek() in ("*", "/", "%"):
                pos += 1
                parse_unary()

        def parse_expr():
            parse_term()
            while peek() in ("+", "-"):
                pos += 1
                parse_term()

        def parse_stmt():
            nonlocal pos
            if (
                peek() == "name"
                and pos + 1 < len(tokens)
                and tokens[pos + 1][0] == "="
            ):
                pos += 2
            parse_expr()

        parse_stmt()
        while accept(";"):
            if peek() is None:
                break
            parse_stmt()
        if pos != len(tokens):
            return SYNTAX

        # Build an AST in a second pass, now that the complete program is known
        # to be syntactically valid.
        pos = 0

        def ast_primary():
            nonlocal pos
            kind, value = tokens[pos]
            if kind == "num":
                pos += 1
                return ("num", int(value))
            if kind == "name":
                pos += 1
                return ("name", value)
            pos += 1  # '('
            node = ast_expr()
            pos += 1  # ')'
            return node

        def ast_power():
            node = ast_primary()
            if peek() == "^":
                pos += 1
                node = ("bin", "^", node, ast_unary())
            return node

        def ast_unary():
            if peek() in ("+", "-"):
                op = tokens[pos][0]
                pos += 1
                return ("un", op, ast_unary())
            return ast_power()

        def ast_term():
            node = ast_unary()
            while peek() in ("*", "/", "%"):
                op = tokens[pos][0]
                pos += 1
                node = ("bin", op, node, ast_unary())
            return node

        def ast_expr():
            node = ast_term()
            while peek() in ("+", "-"):
                op = tokens[pos][0]
                pos += 1
                node = ("bin", op, node, ast_term())
            return node

        statements = []
        while True:
            if (
                peek() == "name"
                and pos + 1 < len(tokens)
                and tokens[pos + 1][0] == "="
            ):
                name = tokens[pos][1]
                pos += 2
                statements.append((name, ast_expr()))
            else:
                statements.append((None, ast_expr()))
            if peek() != ";":
                break
            pos += 1
            if pos == len(tokens):
                break

        env = {}

        def checked(value):
            if abs(value) > LIMIT:
                raise OverflowError
            return value

        def calc(node):
            kind = node[0]
            if kind == "num":
                return checked(node[1])
            if kind == "name":
                if node[1] not in env:
                    raise NameError
                return env[node[1]]
            if kind == "un":
                value = calc(node[2])
                return checked(value if node[1] == "+" else -value)

            op, left_node, right_node = node[1], node[2], node[3]
            left = calc(left_node)
            right = calc(right_node)

            if op == "+":
                return checked(left + right)
            if op == "-":
                return checked(left - right)
            if op == "*":
                return checked(left * right)
            if op == "/":
                if right == 0:
                    raise ZeroDivisionError
                q = abs(left) // abs(right)
                return checked(-q if (left < 0) != (right < 0) else q)
            if op == "%":
                if right == 0:
                    raise ZeroDivisionError
                q = abs(left) // abs(right)
                q = -q if (left < 0) != (right < 0) else q
                return checked(left - right * q)
            if right < 0:
                raise ArithmeticError
            if left in (0, 1, -1):
                result = 1 if right == 0 else (
                    0 if left == 0 else (-1 if right & 1 else 1)
                )
                return checked(result)
            if left == 0:
                return 1
            # Any exponent above 60 on |base| >= 2 exceeds the limit.
            if right > 60:
                raise OverflowError
            return checked(pow(left, right))

        result = None
        for name, expr in statements:
            result = calc(expr)
            if name is not None:
                env[name] = result
        return result

    except ValueError:
        return SYNTAX
    except OverflowError:
        return OVERFLOW
    except NameError:
        return UNDEFINED
    except ZeroDivisionError:
        return DIV_ZERO
    except ArithmeticError:
        return NEG_EXP
    except Exception:
        return SYNTAX
```
