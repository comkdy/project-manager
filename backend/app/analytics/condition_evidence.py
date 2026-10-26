"""Check model-extracted conditions against explicit user text, without inference."""
from datetime import datetime, timedelta, timezone
import re


_MONTHS = {name: index for index, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), 1)}
_MONTH_NAMES = "|".join(name + "|" + name[:3] for name in _MONTHS)
_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"
    r"|(?:(?P<ko_year>\d{4})\s*년\s*)?(?P<ko_month>\d{1,2})\s*월\s*(?P<ko_day>\d{1,2})\s*일"
    rf"|(?P<en_month>{_MONTH_NAMES})\.?\s+(?P<en_day>\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*|\s+)(?P<en_year>\d{{4}})(?!\d)",
    re.I,
)
_CLOCK = re.compile(
    r"(?:[Tt]|\s+)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?P<fraction>\.\d{1,6})?)?"
    r"|\s*(?P<ko_hour>\d{1,2})\s*시(?:\s*(?P<ko_minute>\d{1,2})\s*분)?(?:\s*(?P<ko_second>\d{1,2})\s*초)?"
)
_ZONE = re.compile(r"(?<![A-Za-z])(?:UTC|GMT)(?:\s*[+-]\d{2}:?\d{2})?(?![A-Za-z])|KST|한국\s*시간|서울(?:\s*시간)?", re.I)
_OFFSET = re.compile(r"\s*(Z|[+-]\d{2}:?\d{2})(?!\d)", re.I)
_SHORT_END = re.compile(r"\s*(?:부터|~|〜|–|—|\bto\b|\bthrough\b|\buntil\b)\s*(?:(\d{1,2})\s*월\s*)?(\d{1,2})\s*일", re.I)
_UNSUPPORTED_CLOCK = re.compile(
    r"(?:오전|오후)|(?:[ap]\.?m\.?)(?![A-Za-z])|at\s+\d"
    r"|\d{1,2}(?::\d{2}(?::\d{2})?)?\s*[ap]\.?m\.?(?![A-Za-z])", re.I)


def _unsupported_clock(text, position):
    tail = text[position:].lstrip()
    zone = _ZONE.match(tail)
    if zone:
        tail = tail[zone.end():].lstrip()
    return _UNSUPPORTED_CLOCK.match(tail) is not None


def _zone(text):
    value = re.sub(r"\s", "", text.upper())
    if value in {"KST", "한국시간", "서울", "서울시간"}:
        return timezone(timedelta(hours=9))
    value = re.sub(r"^(UTC|GMT)", "", value)
    if value in {"", "Z"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", value)
    if not match or int(match[2]) > 23 or int(match[3]) > 59:
        return None
    offset = timedelta(hours=int(match[2]), minutes=int(match[3]))
    return timezone(offset if match[1] == "+" else -offset)


def explicit_dates(text, *, context=""):
    """Only explicit calendar dates, clocks and zones; never 'recent' or a year default.

    A date without a clock means its midnight boundary. A shared named timezone
    or year is usable only when the containing user message specifies one value.
    Embedded ISO offsets remain attached to their own timestamp.
    """
    context = context or text
    zones = {_zone(match[0]) for match in _ZONE.finditer(context)} - {None}
    shared_zone = next(iter(zones)) if len(zones) == 1 else None
    years = set(re.findall(r"(?<!\d)(\d{4})(?=\s*년|[-/.]\d{1,2}[-/.])", context))
    shared_year = int(next(iter(years))) if len(years) == 1 else None
    result = []
    for match in _DATE.finditer(text):
        groups = match.groupdict()
        year = groups["year"] or groups["ko_year"] or groups["en_year"] or shared_year
        if not year:
            continue
        month = groups["month"] or groups["ko_month"]
        if month is None:
            name = groups["en_month"].lower()
            month = next(number for full, number in _MONTHS.items() if full.startswith(name))
        day = groups["day"] or groups["ko_day"] or groups["en_day"]
        end = match.end()
        # Never reduce an explicitly stated but unsupported clock to midnight,
        # or read a 12-hour clock as its corresponding 24-hour clock value.
        if _unsupported_clock(text, end):
            continue
        clock = _CLOCK.match(text, end)
        parts = clock.groupdict() if clock else {}
        if clock:
            end = clock.end()
            if _unsupported_clock(text, end):
                continue
        offset = _OFFSET.match(text, end)
        if offset and _unsupported_clock(text, offset.end()):
            continue
        zone = _zone(offset[1]) if offset else shared_zone
        if zone is None:
            continue
        try:
            date = datetime(int(year), int(month), int(day),
                int(parts.get("hour") or parts.get("ko_hour") or 0),
                int(parts.get("minute") or parts.get("ko_minute") or 0),
                int(parts.get("second") or parts.get("ko_second") or 0),
                int((parts.get("fraction") or ".")[1:].ljust(6, "0")), tzinfo=zone)
            result.append(date)
            short = _SHORT_END.match(text, offset.end() if offset else end)
            if short:
                result.append(datetime(date.year, int(short[1] or date.month), int(short[2]), tzinfo=zone))
        except ValueError:
            continue
    return result


def date_supported(value, quote, message):
    # A short quote must not erase a clock/offset present in the original text.
    return value in explicit_dates(quote, context=message) and value in explicit_dates(message)


def policy_supported(value, quote):
    patterns = {
        "latest_per_site": r"\blatest_per_site\b|\b(?:latest|last|final)(?:\s+per\s+site|\s+(?:measurement|reading|value|event)s?)\b|(?:마지막|최종|최신)\s*(?:측정|검사|값|이벤트|건)",
        "all_events": r"\ball_events\b|\ball\s+(?:measurement(?:\s+events)?|reading|event)s?\b|(?:모든|전체)\s*(?:측정|검사|이벤트)|(?:재측정|측정)(?:은|을)?\s*모두\s*포함",
    }
    supported = set()
    for policy, pattern in patterns.items():
        for match in re.finditer(pattern, quote, re.I):
            before, after = quote[max(0, match.start() - 60):match.start()], quote[match.end():match.end() + 30]
            # A negated option is not evidence for choosing that option.
            if re.search(r"(?:\b(?:not|never|without|don't|do not)\s+(?:(?:want|intend|wish)\s+to\s+)?"
                         r"(?:(?:use|include|using|including)\s+)?|\b(?:avoid|skip|exclude|omit)\s+"
                         r"(?:(?:using|including)\s+)?)(?:the\s+)?$", before, re.I):
                continue
            if re.match(r"[^.,;\n]{0,15}?(?:하지\s*(?:않|말|마)|사용\s*안|제외|배제|빼고|아니|말고)", after):
                continue
            supported.add(policy)
    return supported == {value}
