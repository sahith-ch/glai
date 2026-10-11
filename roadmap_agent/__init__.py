import sys

# Model output is full of characters a Windows console code page can't encode (non-breaking hyphens, curly
# quotes, arrows). Progress lines print model-written titles, and a print that raises UnicodeEncodeError would
# fail the whole job - so the console speaks UTF-8, and anything it still can't show becomes "?".
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a real text stream (captured by a test runner, or closed)
        pass
