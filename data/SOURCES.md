# Grounding sources for the synthetic world

The generator and simulator are synthetic, but their **structure** is taken
from public documentation of how Indian recurring payments actually fail.
Verify each claim against the source before the pitch; update this file if a
number moves.

**Live-scraped numbers:** `python3 -m data.scrape_sources` (or `make sources`)
fetches NPCI's published statistics directly — bank-wise remitter performance
(volume share, approved / business-decline / technical-decline %), monthly and
daily UPI volumes, and the downtime/incident list — and writes
`data/real_distributions.json`. When that file exists, the generator's bank
mix and mandate-decline split come from the real numbers instead of the
assumptions below. Raw API responses are cached in `data/raw_sources/`.

| Assumption in the code | Source to cite |
|---|---|
| UPI failure codes and the business/technical decline split (U30-style debit failures dominate; U28/U68-class technical declines and bank unavailability follow) | NPCI — UPI error/response code documentation and monthly UPI product statistics (npci.org.in → Statistics) |
| Bank-wise UPI performance differs materially (approval rates, technical decline %) | NPCI monthly **UPI Chargeback/ TD (technical decline) bank-wise reports** published on npci.org.in |
| Failed e-mandate debits may be re-presented only a limited number of times, and auto-debit needs pre-debit notification | RBI circular framework on e-mandates for recurring transactions (RBI/2019-20/47 and subsequent amendments); NACH presentation & re-presentation rules on npci.org.in |
| Contact-hour restrictions on collection calls (roughly 8:00–19:00/21:00, no harassment) | RBI Fair Practices Code for lenders / digital lending guidelines on collection conduct |
| Salary-day effect: balances are highest in the first days of the month; insufficient-balance bounces spike before month-end | Widely reported NACH/eNACH bounce-rate patterns (see NPCI NACH statistics; industry coverage of month-end bounce spikes) |
| NPCI maintenance/downtime windows degrade success at night | NPCI/UPI scheduled-downtime notifications; bank UPI downtime advisories |

**Honesty note for the README:** these sources justify the *shape* of the
simulator (which failure classes exist, what makes them recoverable, and
when), not the exact probabilities. The exact numbers are assumptions, and
the evaluation measures the agent against baselines *within the same world*,
so the comparison is fair even where an assumed number is off.
