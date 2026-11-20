def diff_intervals(base: list[tuple[int, int]], remove: list[tuple[int, int]]) -> list[tuple[int, int]]:
    def merge(segs: list[tuple[int, int]]) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for a, b in sorted((a, b) for a, b in segs if a <= b):
            if out and a <= out[-1][1] + 1:
                if b > out[-1][1]:
                    out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
        return out

    b_list, r_list = merge(base), merge(remove)
    res: list[tuple[int, int]] = []
    j, nr = 0, len(r_list)
    for a, b in b_list:
        cur = a
        while j < nr and r_list[j][1] < cur:
            j += 1
        while j < nr and r_list[j][0] <= b:
            ra, rb = r_list[j]
            if ra > cur:
                res.append((cur, ra - 1))
            if rb >= b:
                cur = b + 1
                break
            cur = rb + 1
            j += 1
        if cur <= b:
            res.append((cur, b))
    return res
