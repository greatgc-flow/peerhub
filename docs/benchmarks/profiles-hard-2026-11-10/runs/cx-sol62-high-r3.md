```python
import re

_TOKEN = re.compile(
    r"[ \t\n]+|[0-9]+|[a-z_][a-z0-9_]*|[+\-*/%^()=]|.", re.DOTALL
)
_WHITESPACE = " \t\n"
_LIMIT = 10**18
_PRECEDENCE = {
    "+": 1, "-": 1,
    "*": 2, "/": 2, "%": 2,
    "u+": 3, "u-": 3,
    "^": 4,
}


def _is_name(token: str) -> bool:
    return token[0] == "_" or "a" <= token[0] <= "z"


def evaluate(src: str) -> int | str:
    # Parse every statement into postfix form before evaluating any of them.
    statements = src.split(";")
    if len(statements) > 1 and not statements[-1].strip(_WHITESPACE):
        statements.pop()

    program = []
    for statement in statements:
        tokens = [
            token for token in _TOKEN.findall(statement)
            if token[0] not in _WHITESPACE
        ]
        target = None
        if len(tokens) >= 2 and _is_name(tokens[0]) and tokens[1] == "=":
            target = tokens[0]
            tokens = tokens[2:]

        postfix = []
        operators = []
        need_operand = True
        for token in tokens:
            if need_operand:
                if token in ("+", "-"):
                    operators.append("u" + token)
                elif token == "(":
                    operators.append(token)
                elif "0" <= token[0] <= "9":
                    if len(token) > 1 and token[0] == "0":
                        return "SYNTAX"
                    postfix.append(token)
                    need_operand = False
                elif _is_name(token):
                    postfix.append(token)
                    need_operand = False
                else:
                    return "SYNTAX"
            elif token == ")":
                while operators and operators[-1] != "(":
                    postfix.append(operators.pop())
                if not operators:
                    return "SYNTAX"
                operators.pop()
            elif token in _PRECEDENCE:
                precedence = _PRECEDENCE[token]
                while operators and operators[-1] != "(":
                    previous = _PRECEDENCE[operators[-1]]
                    if previous < precedence or (
                        previous == precedence and token == "^"
                    ):
                        break
                    postfix.append(operators.pop())
                operators.append(token)
                need_operand = True
            else:
                return "SYNTAX"

        if need_operand or "(" in operators:
            return "SYNTAX"
        postfix.extend(reversed(operators))
        program.append((target, postfix))

    variables = {}
    for target, postfix in program:
        stack = []
        for token in postfix:
            if "0" <= token[0] <= "9":
                if len(token) > 19:
                    return "OVERFLOW"
                value = int(token)
            elif token in ("u+", "u-"):
                operand = stack.pop()
                value = operand if token == "u+" else -operand
            elif token in _PRECEDENCE:
                right = stack.pop()
                left = stack.pop()
                if token == "+":
                    value = left + right
                elif token == "-":
                    value = left - right
                elif token == "*":
                    value = left * right
                elif token in ("/", "%"):
                    if right == 0:
                        return "DIV_ZERO"
                    quotient = abs(left) // abs(right)
                    if (left < 0) != (right < 0):
                        quotient = -quotient
                    value = quotient if token == "/" else left - right * quotient
                else:
                    if right < 0:
                        return "NEG_EXP"
                    # Even the smallest base with magnitude >= 2 overflows here.
                    if abs(left) >= 2 and right >= 60:
                        return "OVERFLOW"
                    value = pow(left, right)
            else:
                if token not in variables:
                    return "UNDEFINED"
                value = variables[token]

            if abs(value) > _LIMIT:
                return "OVERFLOW"
            stack.append(value)

        result = stack.pop()
        if target is not None:
            variables[target] = result

    return result
```
session: sess_6e83499a04b2e18a fresh gen1 reason=explicit_fresh
