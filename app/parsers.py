from __future__ import annotations

import io
import re
import unicodedata
from difflib import get_close_matches
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

import easyocr
import fitz
import numpy as np
from PIL import Image

from .config import DEFAULT_CURRENCY, OCR_CACHE_DIR


SPANISH_MONTHS = {
    "ENE": 1,
    "ENR": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
}

ENGLISH_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

MONTH_ALIASES = {
    "MYO": 5,
    "MZO": 3,
    "SET": 9,
}

OCR_CHAR_MAP = str.maketrans({"O": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"})


@dataclass
class ParsedMovement:
    posted_at: date | None
    description_raw: str
    amount: float
    movement_type: str
    owner_name: str | None = None
    account_label: str | None = None
    installment_info: str | None = None
    currency: str = DEFAULT_CURRENCY
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedStatement:
    institution: str
    parser_name: str
    original_filename: str
    owner_name: str | None
    masked_account: str | None
    statement_date: date | None
    period_start: date | None
    period_end: date | None
    payment_due_date: date | None
    min_payment: float | None
    total_payment: float | None
    movements: list[ParsedMovement]
    metadata: dict = field(default_factory=dict)


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value or "unknown"


def normalize_description(value: str) -> str:
    value = normalize_whitespace(value).upper()
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def clean_person_name(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])", " ", value)
    return normalize_whitespace(value).title()


def parse_amount(raw: str | None) -> float | None:
    if not raw:
        return None
    raw = raw.strip().replace("$", "").replace(" ", "")
    if not raw:
        return None
    sign = -1 if raw.endswith("-") or raw.startswith("-") else 1
    raw = raw.replace("-", "").replace("+", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts[-1]) == 2:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        return sign * float(raw)
    except ValueError:
        return None


def parse_pdf_text(file_path: Path) -> str:
    doc = fitz.open(file_path)
    return "\n".join(page.get_text("text") for page in doc)


@lru_cache(maxsize=1)
def get_ocr_reader():
    return easyocr.Reader(["en"], gpu=False, verbose=False)


def ocr_pdf_text(file_path: Path) -> str:
    cache_path = OCR_CACHE_DIR / f"{file_path.stem}.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    doc = fitz.open(file_path)
    reader = get_ocr_reader()
    pages: list[str] = []
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.open(io.BytesIO(pix.tobytes("png")))
        text = "\n".join(reader.readtext(np.array(image), detail=0, paragraph=True))
        pages.append(text)
    joined = "\n\n".join(pages)
    cache_path.write_text(joined, encoding="utf-8")
    return joined


def resolve_month_token(token: str) -> int | None:
    token = (token or "").upper()
    if token in SPANISH_MONTHS:
        return SPANISH_MONTHS[token]
    if token in ENGLISH_MONTHS:
        return ENGLISH_MONTHS[token]
    if token in MONTH_ALIASES:
        return MONTH_ALIASES[token]
    candidates = list(SPANISH_MONTHS.keys()) + list(ENGLISH_MONTHS.keys()) + list(MONTH_ALIASES.keys())
    match = get_close_matches(token, candidates, n=1, cutoff=0.6)
    if match:
        return SPANISH_MONTHS.get(match[0]) or ENGLISH_MONTHS.get(match[0]) or MONTH_ALIASES.get(match[0])
    return None


def detect_institution(file_path: Path) -> str:
    text = parse_pdf_text(file_path)
    upper = text.upper()
    if "DINERS" in upper or "CLUB MILES" in upper:
        return "diners"
    if "BANCO INTERNACIONAL" in upper or "INTERMILLAS" in upper:
        return "internacional"
    if not upper.strip() or "PACIF" in file_path.name.upper() or "PACIF" in upper:
        return "pacifico"
    return "unknown"


def parse_statement(file_path: Path) -> ParsedStatement:
    institution = detect_institution(file_path)
    if institution == "diners":
        return parse_diners_statement(file_path)
    if institution == "internacional":
        return parse_internacional_statement(file_path)
    if institution == "pacifico":
        return parse_pacifico_statement(file_path)
    raise ValueError(f"No se pudo detectar el banco para {file_path.name}")


def parse_owner_name_from_caps(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = normalize_whitespace(line)
        if len(cleaned) < 8:
            continue
        if re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]+", cleaned):
            if cleaned not in {"ESTADO DE CUENTA", "DETALLE DE MOVIMIENTOS", "DETALLE DÉBITOS Y CRÉDITOS"}:
                return clean_person_name(cleaned)
    return None


def extract_date(pattern: str, text: str, parser) -> date | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return parser(match.group(1))


def parse_spanish_date(raw: str | None, fallback_year: int | None = None) -> date | None:
    if not raw:
        return None
    value = normalize_whitespace(raw.upper()).replace(".", "").replace("-", "/")
    if "/" in value and re.fullmatch(r"\d{2}/[A-Z]{3}/\d{4}", value):
        day, month, year = value.split("/")
        month_num = resolve_month_token(month)
        if month_num:
            return date(int(year), month_num, int(day))
    if re.fullmatch(r"\d{2}\s+[A-Z]{3}\s+\d{4}", value):
        day, month, year = value.split()
        month_num = resolve_month_token(month)
        if month_num:
            return date(int(year), month_num, int(day))
    if re.fullmatch(r"\d{2}/\d{2}", value) and fallback_year:
        day, month = value.split("/")
        return date(fallback_year, int(month), int(day))
    return None


def parse_english_date(raw: str | None, fallback_year: int | None = None) -> date | None:
    if not raw:
        return None
    value = normalize_whitespace(raw.upper())
    if re.fullmatch(r"\d{2}-[A-Z]{3}-\d{4}", value):
        day, month, year = value.split("-")
        month_num = resolve_month_token(month)
        if month_num:
            return date(int(year), month_num, int(day))
    if re.fullmatch(r"\d{2}-[A-Z]{3}", value) and fallback_year:
        day, month = value.split("-")
        month_num = resolve_month_token(month)
        if month_num:
            return date(fallback_year, month_num, int(day))
    return None


def parse_ocr_date(raw: str | None, fallback_year: int | None = None) -> date | None:
    if not raw:
        return None
    value = normalize_whitespace(raw.upper()).replace(" ", "").translate(OCR_CHAR_MAP)
    value = value.replace("MMAR", "MAR")
    for pattern in [r"(\d{1,2})([A-Z]{3})/(\d{4})", r"(\d{1,2})/([A-Z]{3})/(\d{4})"]:
        match = re.search(pattern, value)
        if match:
            day, month, year = match.groups()
            month_num = resolve_month_token(month)
            if month_num:
                return date(int(year), month_num, int(day))
    match = re.search(r"(\d{1,2})([A-Z]{3})", value)
    if match and fallback_year:
        day, month = match.groups()
        month_num = resolve_month_token(month)
        if month_num:
            return date(fallback_year, month_num, int(day))
    return None


def classify_category(description: str) -> str | None:
    text = normalize_description(description)
    rules = {
        "gasolina": ["GASOLIN", "PETRO", "ESTACION DE SERVICIO"],
        "restaurante": ["KFC", "RESTAURANTE", "PIZZA"],
        "supermercado": ["SUPERMAXI", "SUPER ", "TIA"],
        "digital": ["PAYPAL", "AMAZON", "STEAM", "LOVABLE", "TEMU", "DOMINIOS"],
        "seguro": ["SEGURO", "DESGRAVAMEN", "ASEGURADA"],
        "pago": ["MUCHAS GRACIAS", "PAGO"],
        "impuestos": ["IVA", "SOLCA", "IMPUESTO"],
    }
    for category, keywords in rules.items():
        if any(keyword in text for keyword in keywords):
            return category
    return None


def amount_after_label(lines: list[str], label: str) -> float | None:
    for index, line in enumerate(lines):
        if line.upper() == label.upper():
            for candidate in lines[index + 1 : index + 4]:
                amount = parse_amount(candidate)
                if amount is not None:
                    return amount
    return None


def amount_before_label(lines: list[str], label: str) -> float | None:
    for index, line in enumerate(lines):
        if line.upper() == label.upper():
            for candidate in reversed(lines[max(0, index - 3) : index]):
                amount = parse_amount(candidate)
                if amount is not None:
                    return amount
    return None


def parse_diners_statement(file_path: Path) -> ParsedStatement:
    text = parse_pdf_text(file_path)
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    statement_date = extract_date(r"FECHA DE CORTE:\s*([0-9]{2}/[A-Z]{3}/[0-9]{4})", text, parse_spanish_date)
    period_match = re.search(
        r"Período actual:\s*([0-9]{2}\s+[A-Z]{3}\s+[0-9]{4})\s+AL\s+([0-9]{2}\s+[A-Z]{3}\s+[0-9]{4})",
        text,
        re.IGNORECASE,
    )
    period_start = parse_spanish_date(period_match.group(1)) if period_match else None
    period_end = parse_spanish_date(period_match.group(2)) if period_match else None
    payment_due_date = extract_date(r"Fecha máxima de pago:\s*([0-9]{2}\s+[A-Z]{3}\s+[0-9]{4})", text, parse_spanish_date)
    min_payment = amount_after_label(lines, "MÍNIMO A PAGAR:") or amount_after_label(lines, "Minimo a pagar:")
    total_payment = amount_before_label(lines, "TOTAL A PAGAR:") or amount_after_label(lines, "TOTAL A PAGAR:")
    owner_name = parse_owner_name_from_caps(text)
    masked_account = None
    movements: list[ParsedMovement] = []
    current_owner = owner_name
    current_account = None
    current_section = "consumption"
    fallback_year = period_end.year if period_end else statement_date.year if statement_date else None
    i = 0
    while i < len(lines):
        line = lines[i]
        account_match = re.match(r"([A-ZÁÉÍÓÚÑ ]+)\s+(\d{4}-xxxx-xxxx-\d{4})", line, re.IGNORECASE)
        if account_match:
            current_owner = clean_person_name(account_match.group(1))
            current_account = account_match.group(2)
            masked_account = masked_account or current_account
            i += 1
            continue
        upper = line.upper()
        if upper in {"DETALLE DÉBITOS Y CRÉDITOS", "DETALLE DEBITOS Y CREDITOS"}:
            current_section = "debits_credits"
            i += 1
            continue
        if upper in {"DETALLE DE DÉBITOS", "DETALLE DE DEBITOS"}:
            current_section = "fee"
            i += 1
            continue
        if upper in {"DETALLE DE PAGOS Y CRÉDITOS", "DETALLE DE PAGOS Y CREDITOS"}:
            current_section = "payment"
            i += 1
            continue
        if re.fullmatch(r"\d{2}/\d{2}", line):
            posted_at = parse_spanish_date(line, fallback_year=fallback_year)
            lookahead = lines[i + 1 : i + 7]
            vale = lookahead[0] if lookahead else ""
            desc = lookahead[1] if len(lookahead) > 1 else ""
            installment = None
            amount = None
            step = 1
            if len(lookahead) > 2 and re.fullmatch(r"\(\d+/\d+\)", lookahead[2]):
                installment = lookahead[2]
                amount = parse_amount(lookahead[3]) if len(lookahead) > 3 else None
                step = 4
            elif len(lookahead) > 2 and re.search(r"^[\d\.,]+(?:\s*-\s*)?$", lookahead[2]):
                amount = parse_amount(lookahead[2])
                step = 3
            elif len(lookahead) > 3 and re.search(r"^[\d\.,]+(?:\s*-\s*)?$", lookahead[3]):
                amount = parse_amount(lookahead[3])
                if lookahead[2] in {"N/D", "N/C"}:
                    desc = f"{desc} {lookahead[2]}".strip()
                step = 4
            if desc and amount is not None:
                movement_type = "consumption"
                if current_section == "payment" or "PAGO" in desc.upper() or "N/C" in desc.upper():
                    movement_type = "payment"
                    amount = -abs(amount)
                elif current_section == "fee" or "IMPUESTO" in desc.upper():
                    movement_type = "fee"
                    amount = abs(amount)
                movements.append(
                    ParsedMovement(
                        posted_at=posted_at,
                        description_raw=desc,
                        amount=amount,
                        movement_type=movement_type,
                        owner_name=current_owner,
                        account_label=current_account,
                        installment_info=installment,
                        metadata={"vale": vale},
                    )
                )
                i += step + 1
                continue
        i += 1
    if not movements:
        raise ValueError("No se pudieron extraer movimientos de Diners")
    return ParsedStatement(
        institution="diners",
        parser_name="diners_pdf_text",
        original_filename=file_path.name,
        owner_name=owner_name,
        masked_account=masked_account,
        statement_date=statement_date,
        period_start=period_start,
        period_end=period_end,
        payment_due_date=payment_due_date,
        min_payment=min_payment,
        total_payment=total_payment,
        movements=movements,
        metadata={"raw_owner": owner_name},
    )


def parse_internacional_statement(file_path: Path) -> ParsedStatement:
    text = parse_pdf_text(file_path)
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    statement_date = extract_date(r"(\d{2}-[A-Za-z]{3}-\d{4})", text, parse_english_date)
    fallback_year = statement_date.year if statement_date else date.today().year
    owner_match = re.search(r"\n([A-Za-zÁÉÍÓÚÑáéíóúñ ]+)\nCI:\s*\d+", text)
    owner_name = clean_person_name(owner_match.group(1)) if owner_match else None
    masked_account = None
    min_payment = None
    total_payment = None
    for idx, line in enumerate(lines):
        if re.fullmatch(r"4\d{3}X+\d{4}", line) or re.fullmatch(r"4\d{3}X+\d{3}", line):
            masked_account = line
            break
    header_match = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4})\s+\$?([\d,]+\.\d{2})\s+\$?([\d,]+\.\d{2})", text)
    if header_match:
        statement_date = parse_english_date(header_match.group(1))
        total_payment = parse_amount(header_match.group(2))
        min_payment = parse_amount(header_match.group(3))
    movements: list[ParsedMovement] = []
    current_account = None
    current_owner = owner_name
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r"4\d{3}X+\d{3,4}", line):
            current_account = line
            if i + 1 < len(lines):
                current_owner = clean_person_name(lines[i + 1])
            i += 2
            continue
        if re.fullmatch(r"\d{2}-[A-Za-z]{3}", line):
            posted_at = parse_english_date(line, fallback_year=fallback_year)
            lookahead = []
            j = i + 1
            while (
                j < len(lines)
                and not re.fullmatch(r"\d{2}-[A-Za-z]{3}", lines[j])
                and not re.fullmatch(r"4\d{3}X+\d{3,4}", lines[j])
            ):
                lookahead.append(lines[j])
                if re.search(r"[\d,]+\.\d{2}\s*[+-]$", lines[j]):
                    break
                j += 1
            amount_line = next((item for item in reversed(lookahead) if re.search(r"[\d,]+\.\d{2}\s*[+-]$", item)), None)
            if amount_line:
                amount = parse_amount(amount_line)
                if amount is None:
                    i = j
                    continue
                operation = next((item for item in lookahead if item in {"CONS.", "N/D", "N/D(*)"}), None)
                desc_parts = []
                for item in lookahead:
                    if item in {amount_line, operation}:
                        continue
                    if item.upper().startswith("SUBTOTAL"):
                        continue
                    if re.fullmatch(r"4\d{3}X+\d{3,4}", item):
                        continue
                    if current_owner and normalize_whitespace(item).upper() == normalize_whitespace(current_owner).upper():
                        continue
                    desc_parts.append(item)
                description = " ".join(desc_parts)
                if "*** SU PAGO" in description.upper():
                    movement_type = "payment"
                    amount = -abs(amount)
                elif operation and operation.startswith("N/D"):
                    movement_type = "fee"
                    amount = abs(amount)
                else:
                    movement_type = "consumption"
                    amount = abs(amount)
                movements.append(
                    ParsedMovement(
                        posted_at=posted_at,
                        description_raw=description,
                        amount=amount,
                        movement_type=movement_type,
                        owner_name=current_owner,
                        account_label=current_account,
                    )
                )
                i = j
                continue
        i += 1
    if not movements:
        raise ValueError("No se pudieron extraer movimientos de Banco Internacional")
    return ParsedStatement(
        institution="internacional",
        parser_name="internacional_pdf_text",
        original_filename=file_path.name,
        owner_name=clean_person_name(owner_name),
        masked_account=masked_account,
        statement_date=statement_date,
        period_start=None,
        period_end=statement_date,
        payment_due_date=statement_date,
        min_payment=min_payment,
        total_payment=total_payment,
        movements=movements,
        metadata={},
    )


def parse_pacifico_statement(file_path: Path) -> ParsedStatement:
    text = ocr_pdf_text(file_path)
    normalized = text.upper().replace("MA STERCARD", "MASTERCARD")
    owner_match = re.search(r"CLIENTE\s+\d+\s+([A-Z ]+?)\s+MACROEQUIPOS", normalized)
    owner_name = clean_person_name(owner_match.group(1)) if owner_match else None
    account_match = re.search(r"(?:MASTERCARD|VISA):\s*([0-9X\-]+)", normalized)
    masked_account = account_match.group(1) if account_match else None
    period_match = re.search(r"PERIODO DE CORTE DESDE:\s*([A-Z0-9/]+)\s+HASTA\s+([A-Z0-9/]+)", normalized)
    period_start = parse_ocr_date(period_match.group(1)) if period_match else None
    period_end = parse_ocr_date(period_match.group(2)) if period_match else None
    statement_date = extract_date(r"FECHA DE CORTE:\s*([A-Z0-9/]+)", normalized, parse_ocr_date)
    payment_due_date = extract_date(r"FECHA MAX(?:I|1)MA DE PAGO SIN RECARGOS:\s*([A-Z0-9/]+)", normalized, parse_ocr_date)
    if not payment_due_date:
        payment_due_date = extract_date(r"FECHA MAXIMO DE PAGO SIN RECARGOS\s*([0-9A-Z/]+)", normalized, parse_ocr_date)
    total_match = re.search(r"TOTAL PAGAR DE\s+([\d\.,]+)", normalized)
    min_match = re.search(r"MINIMO PAGAR\s+([\d\.,]+)", normalized)
    total_payment = parse_amount(total_match.group(1)) if total_match else None
    min_payment = parse_amount(min_match.group(1)) if min_match else None
    fallback_year = period_end.year if period_end else statement_date.year if statement_date else date.today().year
    movements: list[ParsedMovement] = []
    lines = [normalize_whitespace(line) for line in text.splitlines() if normalize_whitespace(line)]
    for idx, line in enumerate(lines):
        upper = line.upper().translate(OCR_CHAR_MAP)
        date_match = re.search(r"(\d{1,2})\s*([A-Z]{3})", upper)
        if not date_match or "FECHA" in upper or "CORTE" in upper:
            continue
        posted_at = parse_ocr_date("".join(date_match.groups()), fallback_year=fallback_year)
        if not posted_at:
            continue
        window = " ".join(lines[idx + 1 : idx + 6])
        amount_match = re.search(r"([\d\.,]+)", window)
        if not amount_match:
            continue
        amount = parse_amount(amount_match.group(1))
        if amount is None:
            continue
        movement_type = "payment" if "PAGO" in window.upper() else "consumption"
        if movement_type == "payment":
            amount = -abs(amount)
        description = normalize_whitespace(window.split(amount_match.group(1))[0]) or "Movimiento OCR"
        movements.append(
            ParsedMovement(
                posted_at=posted_at,
                description_raw=description,
                amount=amount,
                movement_type=movement_type,
                owner_name=owner_name,
                account_label=masked_account,
                confidence=0.65,
            )
        )
        if len(movements) >= 3:
            break
    if not movements:
        movements.append(
            ParsedMovement(
                posted_at=period_end or statement_date,
                description_raw="Movimiento OCR pendiente de revisión",
                amount=total_payment or 0.0,
                movement_type="payment" if total_payment else "unknown",
                owner_name=owner_name,
                account_label=masked_account,
                confidence=0.3,
            )
        )
    return ParsedStatement(
        institution="pacifico",
        parser_name="pacifico_pdf_ocr",
        original_filename=file_path.name,
        owner_name=owner_name,
        masked_account=masked_account,
        statement_date=statement_date,
        period_start=period_start,
        period_end=period_end,
        payment_due_date=payment_due_date,
        min_payment=min_payment,
        total_payment=total_payment,
        movements=movements,
        metadata={"ocr_used": True},
    )
