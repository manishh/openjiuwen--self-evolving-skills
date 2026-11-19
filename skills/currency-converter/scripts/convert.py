#!/usr/bin/env python3
"""Convert an amount between two ISO 4217 currencies using live exchange rates.

Usage:
    python convert.py <amount> <FROM> <TO> [--decimals N]

Prints a single JSON object on success. Exits with status 1 and an error
message on stderr when the conversion can't be completed safely.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

RATES_URL = "https://open.er-api.com/v6/latest/{base}"

# ISO 4217 currencies whose minor unit is 0, so amounts must be whole numbers.
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
}


def fetch_rate(base: str, target: str) -> tuple[float, str]:
    with urllib.request.urlopen(RATES_URL.format(base=base), timeout=15) as resp:
        data = json.load(resp)
    if data.get("result") != "success":
        raise SystemExit(f"error: rate service returned {data.get('error-type', 'an error')} for {base}")
    rates = data["rates"]
    if target not in rates:
        raise SystemExit(f"error: unknown target currency {target}")
    return float(rates[target]), data.get("time_last_update_utc", "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("amount", type=float)
    parser.add_argument("from_currency", metavar="FROM")
    parser.add_argument("to_currency", metavar="TO")
    parser.add_argument("--decimals", type=int, default=2,
                        help="decimal places in converted_amount (default: 2)")
    args = parser.parse_args()

    src, dst = args.from_currency.upper(), args.to_currency.upper()
    rate, rate_date = fetch_rate(src, dst)
    converted = round(args.amount * rate, args.decimals)
    if args.decimals == 0:
        converted = int(converted)

    # Guard: never emit an amount the target currency can't represent.
    if dst in ZERO_DECIMAL_CURRENCIES and converted != int(converted):
        sys.exit(
            f"error: {converted} {dst} has a fractional part, but {dst} has 0 decimal "
            f"places under ISO 4217. Re-run with --decimals 0."
        )

    print(json.dumps({
        "amount": args.amount,
        "from": src,
        "to": dst,
        "rate": rate,
        "converted_amount": converted,
        "rate_date": rate_date,
    }))


if __name__ == "__main__":
    main()
