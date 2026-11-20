```python
import re


def evaluate(src: str):
    limit = 10**18
    tokens = []
    for match in re.finditer(r"[ \t\n]+|[0-9]+|[a-z_][a-z0-9_]*|[\s\S]", src):
        token = match.group()
        first = token[0]
        if first in " \t\n":
            continue
        if first == "0" and len(token) > 1:
            return "SYNTAX"
        if not ("0" <= first <= "9" or "a" <= first <= "z"
                or first in "_+-*/%^()=;"):
            return "SYNTAX"
        tokens.append(token)

    if not tokens:
        return "SYNTAX"

    def is_name(token):
        return token[0] == "_" or "a" <= token[0] <= "z"

    precedence = {
        "+": 1, "-": 1, "*": 2, "/": 2, "%": 2,
        "u+": 3, "u-": 3, "^": 4,
    }
    statements = []
    i = 0
    while i < len(tokens):
        target = None
        if is_name(tokens[i]) and i + 1 < len(tokens) and tokens[i + 1] == "=":
            target = tokens[i]
            i += 2

        output = []
        operators = []
        need_operand = True
        while i < len(tokens) and tokens[i] != ";":
            token = tokens[i]
            i += 1
            if need_operand:
                if token in ("+", "-"):
                    operators.append("u" + token)
                elif token == "(":
                    operators.append(token)
                elif is_name(token) or "0" <= token[0] <= "9":
                    output.append(token)
                    need_operand = False
                else:
                    return "SYNTAX"
            elif token == ")":
                while operators and operators[-1] != "(":
                    output.append(operators.pop())
                if not operators:
                    return "SYNTAX"
                operators.pop()
            elif token in precedence:
                while operators and operators[-1] != "(":
                    top = precedence[operators[-1]]
                    if top < precedence[token] or (top == precedence[token] and token == "^"):
                        break
                    output.append(operators.pop())
                operators.append(token)
                need_operand = True
            else:
                return "SYNTAX"

        if need_operand or "(" in operators:
            return "SYNTAX"
        output.extend(reversed(operators))
        statements.append((target, output))
        if i < len(tokens):
            i += 1

    variables = {}
    for target, output in statements:
        stack = []
        for token in output:
            if token in ("u+", "u-"):
                if token == "u-":
                    stack[-1] = -stack[-1]
                continue

            if token in precedence:
                b = stack.pop()
                a = stack.pop()
                if token == "+":
                    value = a + b
                elif token == "-":
                    value = a - b
                elif token == "*":
                    value = a * b
                elif token in ("/", "%"):
                    if b == 0:
                        return "DIV_ZERO"
                    quotient = abs(a) // abs(b)
                    if (a < 0) != (b < 0):
                        quotient = -quotient
                    value = quotient if token == "/" else a - b * quotient
                else:
                    if b < 0:
                        return "NEG_EXP"
                    if abs(a) >= 2 and b >= 60:
                        return "OVERFLOW"
                    value = pow(a, b)
            elif is_name(token):
                if token not in variables:
                    return "UNDEFINED"
                value = variables[token]
            else:
                if len(token) > 19:
                    return "OVERFLOW"
                value = int(token)

            if abs(value) > limit:
                return "OVERFLOW"
            stack.append(value)

        result = stack.pop()
        if target is not None:
            variables[target] = result

    return result
```
session: sess_f8a876f5133d9ded fresh gen1 reason=explicit_fresh
