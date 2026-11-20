# Zero Payment Screening Pipeline

Reads client payment emails from a shared operations inbox export and pulls the
payment details out of each one into a flat CSV, one row per payment.

Extraction is done by a language model through [OpenRouter](https://openrouter.ai).
The script then checks that every value the model returned appears word for word
in the email, and warns about any that do not.

## Setup

Requires Python 3.10 or later. There are no third-party packages to install.

1. Copy the example config and add your key:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env`:

   | Variable | Purpose |
   | --- | --- |
   | `OPENROUTER_API_KEY` | Your key from <https://openrouter.ai/keys>. |
   | `OPENROUTER_BASE_URL` | API base URL. Leave as is. |
   | `OPENROUTER_MODEL` | Model to use. Free models end in `:free`. It must support structured output. |

3. Put the email export in `data/`.

`.env` and `data/` are gitignored. Never commit a key, an email export, or an
extraction output.

## Usage

```bash
python3 extract_payments.py "data/<email export>.csv" -o data/payment_details.csv
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-o`, `--output` | `payment_details.csv` next to the input | Where to write the result. |
| `--limit N` | all emails | Only process the first N emails. Useful for a trial run. |
| `--batch-size N` | 5 | Emails sent per model request. |
| `--workers N` | 3 | Requests made in parallel. |

Model answers are cached in `.extraction_cache.json` beside the output, keyed by
model. An interrupted run picks up where it stopped, and a repeat run makes no
requests. Delete the cache file to force a fresh extraction.

A full run of 300 emails makes 60 requests. OpenRouter caps free-model requests
per day and per minute; the script backs off and retries when it is throttled.

## Input

A CSV with these columns: `email_id`, `client_id`, `client_name`, `subject`,
`email_body`. Other columns are ignored.

## Output

One row per payment. An email that instructs two payments produces two rows.

| Column | Meaning |
| --- | --- |
| `email_id` | The source email. |
| `payment_no` | Position of the payment within the email, starting at 1. |
| `client_id`, `client_name` | Copied from the input. |
| `beneficiary` | Payee name as written. |
| `currency` | Three-letter currency code. |
| `amount` | Amount as written, for example `184250.00`. |
| `payment_date` | `YYYY-MM-DD`, or `NaN` when the email states no date for that payment. |
| `account_number` | The account the email says to pay, as written. |
| `other_account_number` | A second, different account the email mentions, such as an old or replaced one. Empty if none. |
| `sort_code` | Bank sort code. Empty if none is stated. |

## Extraction rules

- **No guessing.** A date is recorded only when the email states one for that
  specific payment. It is never taken from the received time, from another
  payment in the same email, or from a quoted message.
- **Quoted messages are context.** Text after `-----Original Message-----` never
  creates a payment. An account that appears only there is kept as
  `other_account_number`.
- **A correction is one payment.** When an email replaces an earlier instruction,
  the result is a single row with both accounts.
- **Values are copied as written.** IBANs keep their spaces and account numbers
  keep their dashes.

## Checking the result

After extraction, the script confirms that the currency, amount, date, both
account numbers and the sort code each appear verbatim in the email body. Any
that do not are printed as `warning:` lines, along with payments that have no
account number and emails where no payment was found. Review every warning
before relying on the output.

## Data handling

Email text is sent to OpenRouter and on to the model provider. Free models may
log prompts or use them for training, depending on the provider and your
OpenRouter privacy settings. Use a paid model with a suitable data policy for
real client data.
