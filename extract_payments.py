"""Extract payment details from the client payment email export.

One output row per payment instruction. Values are copied from the email text
as written; nothing is inferred. A payment with no stated date gets "NaN".

Usage:
    python3 extract_payments.py INPUT.csv [-o OUTPUT.csv]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

MISSING_DATE = "NaN"
QUOTE_SEPARATOR = "-----Original Message-----"

CURRENCIES = (
    "AED AUD BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF ILS INR JPY KRW MXN "
    "NOK NZD PLN RON SAR SEK SGD THB TRY USD ZAR"
).split()

AMOUNT_RE = re.compile(rf"\b({'|'.join(CURRENCIES)})\s+(\d[\d,]*(?:\.\d+)?)(?![\d-])")
DATE_RE = re.compile(r"\b(?:on|by)\s+(\d{4}-\d{2}-\d{2})\b")
SENTENCE_SPLIT_RE = re.compile(r"(?<=\.)\s+(?=[A-Z])")

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: [A-Z0-9]{1,4}){2,8}\b")
US_ACCOUNT_RE = re.compile(r"\b\d{4}-\d{3}-\d{4}\b")
UK_ACCOUNT_RE = re.compile(r"\b\d{4} \d{4}\b")
SORT_CODE_RE = re.compile(r"\bsort(?: code)?:?\s+(\d{2}-\d{2}-\d{2})\b", re.IGNORECASE)

PAY_VERBS = "pay|payer|fund|settle|release|remit|send|wire|process|transfer"
NAME_AFTER_VERB_RE = re.compile(rf"\b(?:{PAY_VERBS})\s+(\S.*)$")
NAME_ALSO_NEEDS_RE = re.compile(r"^(.+?)\s+also needs$")
NAME_AFTER_AMOUNT_RE = re.compile(
    r"^(?:to|with|for)\s+(.+?)"
    r"(?=\s+(?:account|IBAN|at|on|by|using|to)\b|\s+[A-Z]{2}\d{2}\b|\s+\d{4}-\d{3}-|\.?$)"
)

OUTPUT_COLUMNS = [
    "email_id",
    "payment_no",
    "client_id",
    "client_name",
    "beneficiary",
    "currency",
    "amount",
    "payment_date",
    "account_number",
    "other_account_number",
    "sort_code",
]


def find_accounts(text: str) -> list[str]:
    """Return account numbers in order of appearance, as written, without repeats."""
    found: list[tuple[int, str]] = []
    remaining = text
    for pattern in (IBAN_RE, US_ACCOUNT_RE, UK_ACCOUNT_RE):
        for match in pattern.finditer(remaining):
            found.append((match.start(), match.group()))
        # Blank out matches so a later pattern cannot match inside an earlier one.
        remaining = pattern.sub(lambda m: " " * len(m.group()), remaining)
    ordered = [account for _, account in sorted(found)]
    return list(dict.fromkeys(ordered))


def find_payment_date(segment: str) -> str:
    match = DATE_RE.search(segment)
    if not match:
        return MISSING_DATE
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        return MISSING_DATE
    return match.group(1)


def find_beneficiary(sentence: str, amount_match: re.Match) -> str:
    before = sentence[: amount_match.start()].strip()
    after = sentence[amount_match.end() :].strip()
    for pattern, text in (
        (NAME_ALSO_NEEDS_RE, before),
        (NAME_AFTER_VERB_RE, before),
        (NAME_AFTER_AMOUNT_RE, after),
    ):
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


def split_payment_segments(main_text: str) -> list[tuple[str, str]]:
    """Split the instruction into (payment sentence, full segment) pairs.

    A segment runs from a sentence that states an amount up to the next such
    sentence, so follow-on sentences ("Use account X instead of Y.") stay with
    the payment they describe.
    """
    sentences = SENTENCE_SPLIT_RE.split(main_text.strip())
    segments: list[list[str]] = []
    for sentence in sentences:
        if AMOUNT_RE.search(sentence):
            segments.append([sentence])
        elif segments:
            segments[-1].append(sentence)
    return [(parts[0], " ".join(parts)) for parts in segments]


def extract_payments(email: dict[str, str], warn) -> list[dict[str, str]]:
    body = email["email_body"]
    main_text, _, quoted_text = body.partition(QUOTE_SEPARATOR)
    quoted_accounts = find_accounts(quoted_text)

    rows = []
    for payment_no, (sentence, segment) in enumerate(split_payment_segments(main_text), start=1):
        amounts = list(AMOUNT_RE.finditer(sentence))
        if len(amounts) > 1:
            warn(f"{email['email_id']}: several amounts in one sentence; only the first is used")
        amount_match = amounts[0]

        accounts = find_accounts(segment)
        account_number = accounts[0] if accounts else ""
        other_accounts = [a for a in accounts[1:] + quoted_accounts if a != account_number]
        other_accounts = list(dict.fromkeys(other_accounts))
        if not account_number:
            warn(f"{email['email_id']} payment {payment_no}: no account number found")
        if len(other_accounts) > 1:
            warn(f"{email['email_id']} payment {payment_no}: more than two accounts mentioned")

        sort_code = SORT_CODE_RE.search(segment)
        rows.append(
            {
                "email_id": email["email_id"],
                "payment_no": str(payment_no),
                "client_id": email["client_id"],
                "client_name": email["client_name"],
                "beneficiary": find_beneficiary(sentence, amount_match),
                "currency": amount_match.group(1),
                "amount": amount_match.group(2).replace(",", ""),
                "payment_date": find_payment_date(segment),
                "account_number": account_number,
                "other_account_number": "; ".join(other_accounts),
                "sort_code": sort_code.group(1) if sort_code else "",
            }
        )
    if not rows:
        warn(f"{email['email_id']}: no payment found")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="email export CSV")
    parser.add_argument("-o", "--output", type=Path, help="output CSV (default: payment_details.csv next to the input)")
    args = parser.parse_args()
    output = args.output or args.input.with_name("payment_details.csv")

    warnings: list[str] = []
    with args.input.open(newline="", encoding="utf-8-sig") as f:
        emails = list(csv.DictReader(f))

    rows = [row for email in emails for row in extract_payments(email, warnings.append)]

    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    for message in warnings:
        print(f"warning: {message}", file=sys.stderr)
    print(f"{len(emails)} emails -> {len(rows)} payments written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
