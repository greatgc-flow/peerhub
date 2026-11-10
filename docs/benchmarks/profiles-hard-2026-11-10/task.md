# Task (answer with ONE python code block only; no tools, no prose)
Implement `evaluate(src: str)` for a tiny integer language. Return the value of the last statement as a Python `int`, or exactly one of the strings "SYNTAX", "DIV_ZERO", "UNDEFINED", "NEG_EXP", "OVERFLOW". Never raise. Standard library only.

Lexing
- Whitespace is only ' ', '\t' and '\n'; it is ignored between tokens. Any character not allowed below is SYNTAX.
- Number: a maximal run of ASCII digits 0-9. A run of two or more digits starting with '0' is SYNTAX.
- Name: a maximal run matching [a-z_][a-z0-9_]* (ASCII only).
- Symbols: + - * / % ^ ( ) = ;

Grammar (lowest to highest precedence)
```
program := stmt (';' stmt)* [';']      one optional trailing ';'
stmt    := name '=' expr | expr
expr    := term (('+' | '-') term)*     left-associative
term    := unary (('*' | '/' | '%') unary)*   left-associative
unary   := ('+' | '-') unary | power
power   := primary ['^' unary]          right-associative
primary := number | name | '(' expr ')'
```
So 2^3^2 = 512, -2^2 = -4, and 2^-1 is valid syntax. Anything the grammar does not derive (including an empty program) is SYNTAX.

Semantics
- The whole program is checked for SYNTAX before anything is evaluated; a syntax error anywhere wins over every runtime error.
- Statements run in order. `name = expr` evaluates expr, then binds name; the statement's value is that value. Variables start empty on every call. Reading an unbound name is UNDEFINED.
- Always evaluate the left operand before the right one (also for '^'); no short-circuiting. The first runtime error stops the program and is returned.
- '/' truncates toward zero; a % b = a - b*(a/b), so the remainder has the sign of a. A zero divisor ('/' or '%') is DIV_ZERO.
- a^b: after both operands are evaluated, b < 0 is NEG_EXP; 0^0 = 1.
- Every number literal and every operation result (unary included) whose absolute value exceeds 10^18 is OVERFLOW, at the moment that value is produced. For '^' only the operands and the result count, so (-1)^999999999999999999 = -1, while (-1)^1000000000000000001 is OVERFLOW (its exponent literal exceeds 10^18).
- Limits: input up to 10,000 characters, parentheses nested up to 100 deep, chains of up to 100 '^' or unary operators. Each call must return within 1 second.
