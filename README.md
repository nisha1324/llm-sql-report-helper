# LLM SQL report helper

**Status: in progress.** Part 1 (guard rails, schema card, evaluation set) is done. The LLM backend, the offline path and the scored evaluation come next ([roadmap](#roadmap)).

Ask a business question in plain English, get back the SQL, the result and a short report. The point of this project is the part that makes that **safe and measurable**: a guard that the database itself enforces, a shared definition of each metric, and a set of business questions with known answers to score any model against.

## The business problem

Managers wait on analysts for simple numbers ("revenue by country last year?"). Letting an LLM write the SQL is tempting, but it creates four risks a business can't accept:

| Risk | What could go wrong | How this project handles it |
|---|---|---|
| Wrong numbers | Plausible SQL with the wrong join or metric | 32 questions with gold answers; every model is scored on them before anyone trusts it |
| Personal data leaks | "Email our top customers" returns their emails | Contact details are blocked inside SQLite, however the SQL is written |
| Changed data | A `DELETE` or `UPDATE` slips through | Read-only file, `query_only`, and only `SELECT` is authorised |
| Inconsistent metrics | "Revenue" means different things in different answers | One schema card with the finance definitions, given to every model |

## Data

[Chinook](https://github.com/lerocha/chinook-database) v1.4.5: a fictional online music store (SQLite, 1.07 MB, 11 tables). 59 customers in 24 countries, 412 invoices from 2009-01-01 to 2013-12-22, 3,503 tracks. Licence: MIT-style (© Luis Rocha). `scripts/download_data.py` fetches it and checks a pinned SHA-256, so every gold answer is computed on the same file. It's the same database as my [sql-business-analytics](https://github.com/nisha1324/sql-business-analytics) project, whose published figures are used as an independent cross-check here.

## How it works

```
question ──► schema card ──► model (Claude, or an offline path) ──► SQL
                                                                     │
          report ◄── result ◄── guarded SQLite (read-only, authorizer, limits)
```

### 1. The guard ([`sqlhelper/db.py`](sqlhelper/db.py), policy in [`config/policy.yml`](config/policy.yml))

Four layers, so no single check has to be perfect:

1. **Shape check**: one statement, starting with `SELECT` or `WITH`, at most 4,000 characters.
2. **Read-only connection**: the file is opened with `mode=ro` and `PRAGMA query_only = ON`.
3. **SQLite authorizer**: SQLite calls it while it compiles the query, for every table, column and function the query touches. It allows only `SELECT`, the 11 business tables, non-personal columns and an allow-list of functions. Because the check runs inside the engine, aliases, CTEs, subqueries, `SELECT *` and comments can't hide a column from it. Restricted: customer and employee address, postcode, phone, fax and email, employee birth date, invoice billing address and postcode.
4. **Limits**: 5 seconds per query and 500 rows returned (with a "truncated" flag).

Every rejection comes back with a reason a person, or an LLM retrying, can act on:

| Query | Result |
|---|---|
| `SELECT Email FROM Customer` | rejected: column Customer.Email is restricted (personal data) |
| `SELECT * FROM Customer` | rejected: column Customer.Address is restricted (personal data) |
| `WITH x AS (SELECT Email FROM Customer) SELECT COUNT(*) FROM x` | rejected: column Customer.Email is restricted (personal data) |
| `DELETE FROM Invoice WHERE ...` | rejected: only SELECT queries are allowed |
| `SELECT 1; DROP TABLE Customer` | rejected: only one statement per query |
| `SELECT name FROM sqlite_master` | rejected: table sqlite_master is not available |
| `SELECT load_extension('x')` | rejected: function load_extension() is not allowed |

Recursive CTEs are refused too: reports don't need them, and they're the easiest way to write a query that never ends. The tests also bypass the shape check to show that the engine alone still refuses writes, and they confirm that the database file is byte-identical after an attempted `DELETE`.

### 2. The schema card ([`docs/SCHEMA_CARD.md`](docs/SCHEMA_CARD.md))

This is what the model sees (4,867 characters, roughly 1,200 tokens). It is generated from the database plus [`config/business_notes.yml`](config/business_notes.yml):

- allowed tables and columns with keys and row counts; restricted columns are named as unavailable but not described
- exact values for lookup columns (countries, genres, media types, job titles, playlists), so filters use the right spelling
- **metric definitions**: revenue, orders, average order value, units, active customers, a customer's sales rep
- **gotchas** found while exploring, e.g. dates are text (use `strftime`), 1,519 of 3,503 tracks never sold, playlist names aren't unique, and there's no cost, margin or marketing data

Every fact in the notes is checked against the database by a test, so the card can't drift from the data.

### 3. The evaluation set ([`eval/questions.yml`](eval/questions.yml), answers in [`results/GOLD_ANSWERS.md`](results/GOLD_ANSWERS.md))

There are **32 answerable questions**, worded as a manager would ask them: 8 easy, 15 medium, 9 hard, across KPIs, trend, geography, product, customer, staff and basket. Each has hand-written gold SQL. There are also **7 questions the helper must decline**: 3 ask for personal data, 2 ask to change data, 2 can't be answered from this data (profit margin, marketing return).

An answer is scored by **running it and comparing results** with the gold answer ([`sqlhelper/compare.py`](sqlhelper/compare.py)), not by comparing SQL text. Column names and order don't matter, extra columns are fine, row order only counts for rankings, and numbers must agree within a cent (0.05 for percentages to one decimal).

Before any model is scored, [`scripts/check_gold.py`](scripts/check_gold.py) checks the set itself:

- all 32 gold queries pass the production guard (so the policy doesn't block legitimate questions)
- 11 answers match the figures published by sql-business-analytics (total and yearly revenue, orders, AOV, active customers, year-on-year change, top countries, USA share, revenue per customer)
- no top-N question has a tie at the cut-off ("top 5 customers" did, so it became top 3)
- the guard rejects the naive SQL a careless model would write for all 5 privacy and write cases

That cross-check paid off immediately. My first year-on-year gold query filtered `WHERE year > '2009'` *before* the `LAG()` window function ran, so 2010 had no previous year and came out empty. The published figures caught it. It's exactly the kind of plausible-looking mistake the evaluation exists to catch in generated SQL.

## So what for the business (so far)

- **Safety can't depend on the model behaving.** The privacy and write rules are enforced by the database engine, so a confused or manipulated model gets a clear refusal, not the data.
- **"How accurate is it?" needs a measured answer.** With 32 questions and known answers, any model or prompt change gets a score before it goes in front of managers.
- **Metric definitions are a governance decision, not a prompt detail.** Writing them once in a reviewed file means a generated "revenue" uses the same definition as finance.

## How to run

```bash
uv venv && source .venv/bin/activate     # or: python -m venv .venv
pip install -r requirements.txt
python scripts/download_data.py          # 1.07 MB, SHA-256 checked
python -m scripts.build_schema_card      # docs/SCHEMA_CARD.md
python -m scripts.check_gold             # results/GOLD_ANSWERS.md, exits 1 on any failed check
pytest                                   # 81 tests; the guard tests use a small fixture database
```

## Project structure

```
config/policy.yml          allowed tables, restricted columns, limits
config/business_notes.yml  metric definitions and data gotchas for the schema card
sqlhelper/db.py            guarded executor (shape check, read-only, authorizer, limits)
sqlhelper/schema.py        schema card builder
sqlhelper/compare.py       result comparator (execution accuracy)
sqlhelper/questions.py     evaluation set loader and validation
eval/questions.yml         32 answerable + 7 must-decline questions with gold SQL
scripts/                   download, build the card, check the gold set
docs/SCHEMA_CARD.md        what the model sees
results/GOLD_ANSWERS.md    gold answers and checks
```

## Roadmap

- [x] Part 1: guard, schema card, evaluation set and comparator
- [ ] Part 2: an offline path (no API key needed) and the evaluation harness, with accuracy by level and area plus a failure analysis
- [ ] Part 3: Claude backend (prompted with the schema card; one retry using the guard's reason) and the must-decline cases. It runs only if `ANTHROPIC_API_KEY` is set in a gitignored `.env`; otherwise it is tested with a mock
- [ ] Part 4: report writer (every number in the text must come from the result), then findings and recommendations
