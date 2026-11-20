def shift_date(y: int, m: int, d: int, delta: int) -> tuple[int, int, int, int] | None:
    if not (1 <= m <= 12):
        return None
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    dim = (0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[m]
    if not (1 <= d <= dim):
        return None
    # Count days in March-based 400-year cycles so each leap day ends a year.
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (9 if m <= 2 else -3)) + 2) // 5 + d - 1
    z = era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy + delta
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y2 = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d2 = doy - (153 * mp + 2) // 5 + 1
    m2 = mp + (3 if mp < 10 else -9)
    y2 += m2 <= 2
    # This offset makes January 1, 2000 Saturday (5).
    return (y2, m2, d2, (z + 2) % 7)
