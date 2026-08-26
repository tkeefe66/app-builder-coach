"""Model pricing, $ per MTok: (input, output, cache_read, cache_write).

Lives in shared/ because both the local usage lane and the server price rows,
and the Dockerfile copies shared/ but not src/.

Applied to Claude Code transcripts these produce an *estimate* of API-equivalent
value; applied to real API traffic (the /api/usage lane) they produce actual
billed cost. Edit freely when pricing changes.
"""
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-sonnet": (3.00, 15.00, 0.30, 3.75),
    "claude-opus": (15.00, 75.00, 1.50, 18.75),
    "claude-fable": (15.00, 75.00, 1.50, 18.75),
}
FALLBACK_PRICE = (15.00, 75.00, 1.50, 18.75)


def price_for(model: str) -> tuple:
    for prefix, p in PRICES.items():
        if model.startswith(prefix):
            return p
    return FALLBACK_PRICE
