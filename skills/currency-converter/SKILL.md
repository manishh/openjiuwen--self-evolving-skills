---
name: currency-converter
description: Convert a monetary amount from one currency to another using live exchange rates (ISO 4217 codes), returning the converted amount and the rate used. Use this skill whenever the user asks to convert, translate, or price an amount between currencies, e.g. "convert 100 USD to EUR", even if they don't say "exchange rate".
---

# Currency Converter

Convert an amount between two currencies with the bundled script. Always use the
script rather than calculating the result yourself, so every answer uses the
same live rate and the same rounding rules.

## Usage

Run the script from this skill's directory:

```bash
python scripts/convert.py <amount> <FROM> <TO>
```

- `<amount>` is a number, for example `100` or `19.99`.
- `<FROM>` and `<TO>` are three-letter ISO 4217 currency codes, for example `USD`, `EUR`, `GBP`.

The script prints one JSON object:

```json
{"amount": 100.0, "from": "USD", "to": "EUR", "rate": 0.892111, "converted_amount": 89.21, "rate_date": "Tue, 06 Oct 2026 00:02:31 +0000"}
```

## Reporting the result

Report `converted_amount` with the target currency code, the `rate` used, and
the `rate_date`. Example: "100 USD is 89.21 EUR (rate 0.892111, as of 6 Oct 2026)."
