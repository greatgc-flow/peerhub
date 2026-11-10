# Task (answer with ONE python code block only; no tools, no prose)
Implement `search(pattern: str, text: str)` for the regex dialect below. Do not use `re` or any regex library; standard library only. Never raise. Return "ERROR" if the pattern is invalid, None if there is no match, else a tuple `(g0, g1, ..., gN)` (N = number of capturing groups): g0 is the `(start, end)` span of the match, gk the span of group k or None if group k is unset. Example: `search("(a)|b", "xb")` returns `((1, 2), None)`.

Syntax (anything not derived here is ERROR)
- `alt := seq ('|' seq)*`; `seq := item*` (may be empty); `item := '^' | '$' | atom [quant]`
- `atom := char | '.' | class | escape | '(' alt ')' | '(?:' alt ')'`. A char is any character except `\ . | * + ? ( ) [ ] { } ^ $`. `( )` groups capture and are numbered 1, 2, ... by the position of their `(`.
- `quant := ('*' | '+' | '?' | '{m}' | '{m,}' | '{m,n}') ['?']`; m, n are ASCII digit strings with value <= 1000 and m <= n. A trailing `?` makes it lazy.
- `escape`: `\` followed by one of `\ . | * + ? ( ) [ ] { } ^ $ -` (that character), `n` (newline), `t` (tab), `d` [0-9], `w` [A-Za-z0-9_], `s` (space, tab, newline only), `D` `W` `S` (their complements), or one digit 1-9: a backreference to that group (`\10` is `\1` then `0`); ERROR if the pattern has fewer groups. Any other escape is ERROR.
- `class := '[' ['^'] items ']'`; `^` negates. The first unescaped `]` ends it, so `[]` matches nothing and `[^]` any character. Inside, escapes are as above except backreferences (ERROR), and every other character is literal. After an item x, a `-` not directly before `]` makes a range x-y with the next item y; x and y must each be one character (literal or an escape such as `\n`, `\-`; not `\d` etc.) with x <= y, else ERROR. Any other `-` is literal.

Matching
- Try start positions 0, 1, ..., len(text) in order; return the first match. At each start, backtrack: alternatives left to right; a greedy quantifier prefers one more iteration (below its max) over stopping, a lazy one prefers stopping (once at its min); on failure, resume at the most recent untried choice.
- `.` matches any character except newline. `^` matches only at position 0, `$` only at len(text).
- A group's span is set when its `)` is passed; backtracking restores earlier spans.
- Each time a quantifier starts an iteration, every group inside its atom becomes unset.
- An iteration that matches the empty string fails if the quantifier had already completed its minimum number of iterations (0 for `*` and `?`, 1 for `+`, m for `{m...}`).
- A backreference matches the text its group currently holds; if the group is unset it matches the empty string.
- Patterns have <= 100 characters, texts <= 1,000. Each call must return within 1 second.
