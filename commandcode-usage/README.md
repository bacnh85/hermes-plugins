# Command Code usage — Hermes plugin

`/commandcode` slash command + `hermes commandcode-usage` CLI showing
Command Code (commandcode.ai) subscription usage: the 5-hour and weekly
rolling USD windows and the monthly credit balance, from
`GET https://api.commandcode.ai/alpha/billing/credits` (same Provider API
key as model calls — `COMMANDCODE_API_KEY` in `~/.hermes/.env`).

Companion to the repo's `commandcode/` model-provider plugin; it works
standalone too (any machine with a Command Code key can show usage).

## Install

```bash
~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main plugins install \
  bacnh85/hermes-plugins/commandcode-usage --enable
hermes gateway restart
```

## Use

```
/commandcode            # usage windows + credits
/commandcode refresh    # skip the 60s cache
hermes commandcode-usage [--refresh]   # terminal equivalent
```

Example:

```
Command Code usage (key#1a2b3c4d)
  5-hour :   0% of $14.00 used ($0.00) — resets in 3H 12M
  Weekly :  90% of $35.00 used ($31.62) — resets in 5H 04M
  Monthly: $0.07 remaining
```

## Notes

- `windowLimits.*.resetAt` is epoch **milliseconds**; `used`/`cap` are USD.
- `credits.monthlyCredits` = remaining monthly plan allowance in USD.
- Successful fetches are memoized for 60s so `/commandcode` spam doesn't
  hammer the billing endpoint.
- Never prints the key — only a SHA-256 fingerprint prefix (`key#1a2b3c4d`).

## License

MIT
