from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF

A_REVISAR = "A revisar"

DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
MONEY_RE = re.compile(
    r"(?:R\$\s*)?([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2})"
)

BANK_ALIASES = {
    "Banco do Brasil": [
        "banco do brasil",
        "bb s.a",
        "bb banco",
        "sacbb",
        "sac bb",
        "central de atendimento bb",
        "atendimento bb",
    ],
    "Bradesco": ["bradesco", "banco bradesco"],
    "Itaú": ["itau", "itaú", "banco itau", "banco itaú"],
    "Caixa": ["caixa economica", "caixa econômica", "cef", "caixa"],
    "Santander": ["santander"],
    "Sicoob": ["sicoob"],
    "Sicredi": ["sicredi"],
    "Nubank": ["nubank", "nu pagamentos"],
    "Inter": ["banco inter", "intermedium"],
    "BTG Pactual": ["btg pactual", "btg"],
    "C6 Bank": ["c6 bank", "banco c6"],
    "Mercado Pago": ["mercado pago"],
    "PagBank": ["pagbank", "pagseguro"],
    "Stone": ["stone pagamentos", "stone"],
    "Safra": ["banco safra", "safra"],
    "Banco Original": ["banco original"],
}

DATE_LABELS = {
    "lancamento": [
        "lançamento",
        "lancamento",
        "data de lançamento",
        "data de lancamento",
        "data de registro",
        "registro",
        "data de emissão",
        "data de emissao",
        "emissão",
        "emissao",
        "processamento",
        "data de processamento",
    ],
    "vencimento": [
        "vencimento",
        "data de vencimento",
        "data vencimento",
        "venc.",
        "vcto",
        "data vcto",
    ],
    "pagamento": [
        "pagamento",
        "data de pagamento",
        "pago em",
        "data do pagamento",
        "data pagamento",
        "efetivado em",
        "realizado em",
        "débito em",
        "debito em",
        "data do débito",
        "data do debito",
    ],
}

MONEY_LABELS = {
    "valor_original": [
        "valor original",
        "valor do documento",
        "valor documento",
        "valor do boleto",
        "valor boleto",
        "valor nominal",
        "valor principal",
        "valor título",
        "valor titulo",
        "valor da cobrança",
        "valor da cobranca",
    ],
    "valor": [
        "valor pago",
        "valor do pagamento",
        "valor debitado",
        "valor cobrado",
        "total pago",
        "valor total pago",
        "total a pagar",
        "valor total",
        "valor",
    ],
}

DESCRIPTION_LABELS = [
    "beneficiário",
    "beneficiario",
    "Beneficiário",
    "favorecido",
    "fornecedor",
    "cedente",
    "recebedor",
    "destinatário",
    "destinatario",
    "nome do beneficiário",
    "nome do beneficiario",
    "razão social",
    "razao social",
    "empresa",
]

NOISE_DESCRIPTION_WORDS = [
    "comprovante",
    "pagamento",
    "boleto",
    "vencimento",
    "valor",
    "autenticação",
    "autenticacao",
    "código de barras",
    "codigo de barras",
    "data",
    "agência",
    "agencia",
    "conta",
    "banco",
    "cnpj",
    "cpf",
]


@dataclass
class FieldResult:
    value: object
    confidence: float
    reason: str = ""


def remove_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip(" :-–—|\t")
    return line.strip()


def text_lines(text: str) -> list[str]:
    return [clean_line(x) for x in text.splitlines() if clean_line(x)]


def normalize_date(value: str | None) -> Optional[str]:
    if not value:
        return None
    value = value.strip().replace("-", "/")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


def money_to_float(value: str | float | int | None) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = value.strip().replace("R$", "").replace(" ", "")
    value = value.replace(".", "").replace(",", ".")
    try:
        return round(float(value), 2)
    except ValueError:
        return None


def is_blank_or_review(value: object) -> bool:
    return value is None or value == "" or str(value).strip().lower() == A_REVISAR.lower()


Box = tuple[float, float, float, float, str]  # (x0, y0, x1, y1, texto)


def boxes_to_text(boxes: list[Box], y_tolerance: float = 0.7) -> str:
    """Reconstrói linhas de texto a partir de caixas posicionadas.

    Comprovantes e boletos usam layout em duas colunas (rótulo à esquerda,
    valor à direita). A leitura ingênua devolve rótulos e valores em linhas
    separadas. Aqui agrupamos caixas com centro vertical próximo na mesma
    linha lógica e as ordenamos pela posição horizontal, de modo que, por
    exemplo, "VALOR DO DOCUMENTO" e "500,00" fiquem na mesma linha.
    """
    if not boxes:
        return ""

    ordered = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    lines: list[list[Box]] = []
    current: list[Box] = []
    center_y: Optional[float] = None

    for box in ordered:
        height = max(box[3] - box[1], 1.0)
        box_center = (box[1] + box[3]) / 2
        if center_y is None or abs(box_center - center_y) <= height * y_tolerance:
            current.append(box)
            center_y = sum((b[1] + b[3]) / 2 for b in current) / len(current)
        else:
            lines.append(current)
            current = [box]
            center_y = box_center
    if current:
        lines.append(current)

    rendered = []
    for line in lines:
        line_sorted = sorted(line, key=lambda b: b[0])
        rendered.append(" ".join(b[4] for b in line_sorted if b[4].strip()))
    return "\n".join(r for r in rendered if r.strip())


def extract_text_direct(pdf_path: str | Path) -> str:
    pieces: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            words = page.get_text("words")  # x0, y0, x1, y1, palavra, bloco, linha, n
            if words:
                boxes = [(w[0], w[1], w[2], w[3], w[4]) for w in words]
                pieces.append(boxes_to_text(boxes))
            else:
                pieces.append(page.get_text("text"))
    return normalize_text("\n".join(pieces))


_RAPIDOCR_ENGINE = None
_RAPIDOCR_TRIED = False
_RAPIDOCR_ERROR = ""


def _get_rapidocr():
    """Carrega o RapidOCR uma única vez (modelos ONNX embutidos no pacote)."""
    global _RAPIDOCR_ENGINE, _RAPIDOCR_TRIED, _RAPIDOCR_ERROR
    if _RAPIDOCR_TRIED:
        return _RAPIDOCR_ENGINE
    _RAPIDOCR_TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR

        _RAPIDOCR_ENGINE = RapidOCR()
    except Exception as exc:
        _RAPIDOCR_ENGINE = None
        _RAPIDOCR_ERROR = f"{type(exc).__name__}: {exc}"
    return _RAPIDOCR_ENGINE


def rapidocr_error() -> str:
    """Mensagem do erro ao carregar o RapidOCR (vazia se carregou bem)."""
    _get_rapidocr()
    return _RAPIDOCR_ERROR


# Limita o lado maior da imagem renderizada. PDFs escaneados costumam ter páginas
# enormes; sem limite, cada página vira uma imagem de >100 MB e estoura a memória
# de servidores pequenos (ex.: Streamlit Cloud), além de deixar o OCR lento.
MAX_RENDER_SIDE = 2600


def _render_page_array(page, scale: float = 2.0):
    import numpy as np

    longest_pts = max(page.rect.width, page.rect.height) or 1.0
    # Não aumenta além de `scale`, mas reduz quando a página é muito grande.
    effective = min(scale, MAX_RENDER_SIDE / longest_pts)
    effective = max(effective, 0.2)
    pix = page.get_pixmap(matrix=fitz.Matrix(effective, effective), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = arr[:, :, :3]
    return np.ascontiguousarray(arr)


def _configure_tesseract_path() -> None:
    """Tenta localizar o tesseract.exe em instalações comuns no Windows."""
    import pytesseract

    if pytesseract.pytesseract.tesseract_cmd and Path(pytesseract.pytesseract.tesseract_cmd).exists():
        return
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


def _extract_text_tesseract(pdf_path: str | Path, lang: str) -> str:
    try:
        import pytesseract
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Nenhum motor de OCR disponível. Instale com: pip install rapidocr-onnxruntime"
        ) from exc

    _configure_tesseract_path()
    pieces: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            pieces.append(pytesseract.image_to_string(img, lang=lang))
    return normalize_text("\n".join(pieces))


def _run_ocr(engine, img) -> tuple[list[Box], list[float]]:
    result, _ = engine(img)
    boxes: list[Box] = []
    scores: list[float] = []
    for box, text, score in (result or []):
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        boxes.append((min(xs), min(ys), max(xs), max(ys), text))
        try:
            scores.append(float(score))
        except (TypeError, ValueError):
            scores.append(0.0)
    return boxes, scores


def _tall_fraction(boxes: list[Box]) -> float:
    """Fração de caixas mais altas que largas (indício de página rotacionada)."""
    if not boxes:
        return 0.0
    tall = sum(1 for b in boxes if (b[3] - b[1]) > (b[2] - b[0]))
    return tall / len(boxes)


def _orientation_score(boxes: list[Box], scores: list[float]) -> float:
    """Mede a qualidade de uma orientação: texto horizontal e alta confiança."""
    if not boxes:
        return 0.0
    horiz = 1.0 - _tall_fraction(boxes)
    mean_score = sum(scores) / len(scores) if scores else 0.0
    chars = sum(len(b[4]) for b in boxes)
    return horiz * 10 + mean_score + min(chars / 1000.0, 1.0)


def _ocr_image_oriented(engine, img) -> list[Box]:
    """Roda OCR corrigindo rotação de 90/270 graus quando necessário."""
    import numpy as np

    boxes, scores = _run_ocr(engine, img)
    # Se o texto estiver predominantemente "em pé" (caixas altas), a página
    # provavelmente está deitada. Testamos as rotações e ficamos com a melhor.
    if _tall_fraction(boxes) <= 0.5:
        return boxes

    best_boxes, best_score = boxes, _orientation_score(boxes, scores)
    for k in (1, 3):  # 90° e 270°
        rot = np.ascontiguousarray(np.rot90(img, k))
        rb, rs = _run_ocr(engine, rot)
        score = _orientation_score(rb, rs)
        if score > best_score:
            best_boxes, best_score = rb, score
    return best_boxes


def _ocr_page_boxes(engine, page) -> list[Box]:
    img = _render_page_array(page)
    return _ocr_image_oriented(engine, img)


def collect_page_boxes(
    pdf_path: str | Path, use_ocr: bool = True
) -> list[list[Box]]:
    """Devolve as caixas de texto de cada página (texto nativo ou OCR)."""
    pages: list[list[Box]] = []
    engine = _get_rapidocr() if use_ocr else None
    with fitz.open(pdf_path) as doc:
        for page in doc:
            words = page.get_text("words")
            if words and len(words) > 5:
                pages.append([(w[0], w[1], w[2], w[3], w[4]) for w in words])
            elif engine is not None:
                pages.append(_ocr_page_boxes(engine, page))
            else:
                pages.append([])
    return pages


def extract_text_ocr(pdf_path: str | Path, lang: str = "por+eng") -> str:
    """Extrai texto de PDFs escaneados.

    Usa o RapidOCR (instalável via pip, sem dependência de sistema) como motor
    principal, reconstruindo as linhas pela posição das caixas e corrigindo
    páginas rotacionadas. Se o RapidOCR não estiver disponível, recorre ao
    Tesseract via pytesseract.
    """
    engine = _get_rapidocr()
    if engine is not None:
        pieces: list[str] = []
        with fitz.open(pdf_path) as doc:
            for page in doc:
                pieces.append(boxes_to_text(_ocr_page_boxes(engine, page)))
        return normalize_text("\n".join(pieces))

    return _extract_text_tesseract(pdf_path, lang)


def label_in_line(line: str, labels: Iterable[str]) -> bool:
    clean = remove_accents(line.lower())
    return any(remove_accents(label.lower()) in clean for label in labels)


def text_after_label(line: str, label: str) -> str:
    """Devolve o trecho da linha após o rótulo (para pegar o valor à direita)."""
    line_norm = remove_accents(line.lower())
    label_norm = remove_accents(label.lower())
    idx = line_norm.find(label_norm)
    if idx < 0:
        return line
    # Como remove_accents pode alterar o tamanho, usamos o texto normalizado
    # apenas para localizar e devolvemos a partir de uma posição equivalente.
    return line[idx + len(label_norm):] if len(line_norm) == len(line) else line[idx:]


def find_value_near_label(
    lines: list[str], labels: Iterable[str], pattern: re.Pattern, max_next_lines: int = 2
) -> tuple[Optional[str], float, str]:
    # Percorre os rótulos em ordem de prioridade (mais específicos primeiro),
    # evitando que um rótulo genérico como "valor" capture o campo errado.
    for label in labels:
        for i, line in enumerate(lines):
            if not label_in_line(line, [label]):
                continue

            # Mesma linha: procura o valor à direita do rótulo.
            same_line = text_after_label(line, label)
            match = pattern.search(same_line)
            if match:
                return match.group(1), 0.95, f"Encontrado ao lado do rótulo: {line[:80]}"

            # Linhas seguintes (layouts em que o valor cai abaixo do rótulo).
            for offset in range(1, max_next_lines + 1):
                if i + offset < len(lines):
                    match = pattern.search(lines[i + offset])
                    if match:
                        return match.group(1), 0.82, f"Encontrado abaixo do rótulo: {line[:80]}"

    return None, 0.0, "Rótulo não encontrado"


def extract_date_field(lines: list[str], field_name: str) -> FieldResult:
    raw, confidence, reason = find_value_near_label(lines, DATE_LABELS[field_name], DATE_RE)
    normalized = normalize_date(raw)
    if normalized:
        return FieldResult(normalized, confidence, reason)
    return FieldResult(A_REVISAR, 0.0, reason)


def extract_money_field(lines: list[str], field_name: str) -> FieldResult:
    raw, confidence, reason = find_value_near_label(lines, MONEY_LABELS[field_name], MONEY_RE)
    value = money_to_float(raw)
    if value is not None:
        return FieldResult(value, confidence, reason)
    return FieldResult(A_REVISAR, 0.0, reason)


def extract_bank(text: str, filename: str = "") -> FieldResult:
    search_space = remove_accents((text + "\n" + filename).lower())
    for bank, aliases in BANK_ALIASES.items():
        for alias in aliases:
            if remove_accents(alias.lower()) in search_space:
                return FieldResult(bank, 0.95, f"Banco identificado por: {alias}")
    return FieldResult(A_REVISAR, 0.0, "Banco não identificado")


def sanitize_description(value: str) -> str:
    value = clean_line(value)
    value = re.sub(r"\b(CNPJ|CPF)\b.*", "", value, flags=re.I).strip()
    value = re.sub(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", "", value)
    value = re.sub(r"\d{3}\.\d{3}\.\d{3}-\d{2}", "", value)
    value = clean_line(value)
    return value[:90].strip()


def extract_description(lines: list[str]) -> FieldResult:
    for i, line in enumerate(lines):
        matched = next((lbl for lbl in DESCRIPTION_LABELS if label_in_line(line, [lbl])), None)
        if not matched:
            continue

        # Nome antes do rótulo na mesma linha (ex.: "MEDI SYSTEMS BENEFICIARIO:").
        low = remove_accents(line.lower())
        pos = low.find(remove_accents(matched.lower()))
        if pos > 0:
            candidate = sanitize_description(line[:pos])
            if valid_description(candidate) and looks_like_company_name(candidate):
                return FieldResult(candidate.upper(), 0.86, f"Descrição antes do rótulo: {line[:80]}")

        after_label = re.split(r":| - |–|—", line, maxsplit=1)
        if len(after_label) > 1:
            candidate = sanitize_description(after_label[1])
            if valid_description(candidate):
                return FieldResult(candidate.upper(), 0.88, f"Descrição extraída do rótulo: {line[:80]}")

        for offset in range(1, 3):
            if i + offset < len(lines):
                candidate = sanitize_description(lines[i + offset])
                if valid_description(candidate):
                    return FieldResult(candidate.upper(), 0.82, f"Descrição extraída após rótulo: {line[:80]}")

    # Heurística de fallback: pega uma linha com cara de razão social/nome.
    for line in lines[:50]:
        candidate = sanitize_description(line)
        if valid_description(candidate) and looks_like_company_name(candidate):
            return FieldResult(candidate.upper(), 0.55, "Descrição por heurística; revisar")

    return FieldResult(A_REVISAR, 0.0, "Descrição não identificada")


def valid_description(candidate: str) -> bool:
    if not candidate or len(candidate) < 4:
        return False
    candidate_low = remove_accents(candidate.lower())
    # Usa limites de palavra para não rejeitar, p.ex., "CONTABILIDADE" por conter "conta".
    for word in NOISE_DESCRIPTION_WORDS:
        if re.search(r"\b" + re.escape(remove_accents(word.lower())) + r"\b", candidate_low):
            return False
    if DATE_RE.search(candidate) or MONEY_RE.search(candidate):
        return False
    letters = re.findall(r"[A-Za-zÀ-ÿ]", candidate)
    return len(letters) >= 4


def looks_like_company_name(candidate: str) -> bool:
    low = remove_accents(candidate.lower())
    keywords = ["ltda", "eireli", "mei", "sa", "s/a", "servicos", "serviços", "comercio", "comércio", "gestao", "gestão", "contabil", "contábil"]
    if any(remove_accents(k) in low for k in keywords):
        return True
    # Linha em maiúsculas com pelo menos duas palavras costuma ser beneficiário/empresa.
    return candidate.upper() == candidate and len(candidate.split()) >= 2


def confidence_mean(fields: list[FieldResult]) -> float:
    if not fields:
        return 0.0
    return round(sum(f.confidence for f in fields) / len(fields), 2)


def build_status(row: dict, confidence: float, text_found: bool) -> str:
    if not text_found:
        return "Erro na leitura"
    required = [
        "LANÇAMENTO",
        "DESCRIÇÃO",
        "VENCIMENTO",
        "PAGAMENTO",
        "VALOR ORIGINAL",
        "VALOR",
        "PAGO BANCO",
    ]
    if any(is_blank_or_review(row.get(col)) for col in required):
        return "A revisar"
    if confidence < 0.70:
        return "A revisar"
    return "Conferido"


# Grade do comprovante "COMPROVANTE DE PAGAMENTO DE TITULOS" (Banco do Brasil e
# similares): rótulos numa coluna à esquerda e valores numa coluna à direita,
# com os valores impressos ligeiramente abaixo do rótulo correspondente.
GRID_FIELDS = [
    ("nr documento", None, "num"),
    ("nr. documento", None, "num"),
    ("data de vencimento", "vencimento", "date"),
    ("data do vencimento", "vencimento", "date"),
    ("data do pagamento", "pagamento", "date"),
    ("data de pagamento", "pagamento", "date"),
    ("valor do documento", "valor_original", "money"),
    ("juros multa", None, "money"),
    ("valor cobrado", "valor", "money"),
]


def _norm_box_text(text: str) -> str:
    text = remove_accents(text.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_bb_grid(boxes: list[Box]) -> dict:
    """Pareia rótulo→valor na grade do comprovante usando a posição das caixas.

    Como o valor é impresso um pouco abaixo do rótulo, pegamos, para cada
    rótulo, o primeiro valor (do tipo esperado) cujo centro está logo abaixo
    dele na coluna da direita. Isso evita o erro de associar o valor ao rótulo
    de baixo, que fica mais próximo.
    """
    if not boxes:
        return {}

    labels = []  # (cy, fieldname, vtype, x1)
    for b in boxes:
        nt = _norm_box_text(b[4])
        for key, fieldname, vtype in GRID_FIELDS:
            if nt == key or nt.startswith(key + " ") or nt.startswith(key):
                labels.append(((b[1] + b[3]) / 2, fieldname, vtype, b[2]))
                break
    if not labels:
        return {}

    found = {f for _, f, _, _ in labels if f}
    if "vencimento" not in found and "valor" not in found:
        return {}

    label_x_max = max(l[3] for l in labels)
    label_cys = sorted(l[0] for l in labels)
    grid_top, grid_bottom = label_cys[0], label_cys[-1]
    pitch = (grid_bottom - grid_top) / max(len(label_cys) - 1, 1) or 60.0

    values = []  # (cy, text) somente na coluna da direita e dentro da grade
    for b in boxes:
        if b[0] < label_x_max - 20:
            continue
        cy = (b[1] + b[3]) / 2
        if cy < grid_top - pitch * 0.6 or cy > grid_bottom + pitch * 1.5:
            continue
        values.append((cy, b[4]))
    values.sort()

    out: dict[str, str] = {}
    for cy, fieldname, vtype, _ in sorted(labels):
        if not fieldname:
            continue
        pattern = DATE_RE if vtype == "date" else MONEY_RE
        for vcy, vtext in values:
            if vcy < cy - pitch * 0.2:  # ignora valores acima do rótulo
                continue
            match = pattern.search(vtext)
            if match:
                out[fieldname] = match.group(1)
                break
    return out


COMPROVANTE_MARKERS = [
    "nr autenticacao",        # comprovante de pagamento de títulos (BB)
    "autenticacao sisbb",     # autoatendimento SISBB / contas CAESB
    "comprovante de pagamento",
    "comprovante de transferencia",
    "comprovante de pix",
]


def _is_comprovante_page(boxes: list[Box]) -> bool:
    """Identifica a página que comprova um pagamento (início de um lançamento).

    Todo comprovante tem um número de autenticação, que boletos e notas fiscais
    não têm — por isso ele é o marcador mais confiável para separar cada
    pagamento quando vários vêm escaneados no mesmo PDF.
    """
    low = remove_accents(boxes_to_text(boxes).lower())
    low = re.sub(r"[^a-z0-9]+", " ", low)
    return any(marker in low for marker in COMPROVANTE_MARKERS)


def parse_caesb(text: str) -> Optional[dict]:
    """Extrai os campos de uma conta de água da CAESB (layout SISBB).

    O OCR dessas contas é irregular, então usamos sinais robustos: o total é o
    maior valor com centavos em vírgula da página; o vencimento é a data que
    aparece imediatamente antes de um valor (linha de resumo).
    """
    low = remove_accents(text.lower())
    if "caesb" not in low and "saneamento ambiental" not in low:
        return None

    out: dict[str, object] = {"descricao": "CAESB", "banco": "Banco do Brasil"}

    # Total: maior valor monetário com vírgula decimal (evita percentuais com ponto).
    monies = [money_to_float(m) for m in re.findall(r"\d{1,3}(?:\.\d{3})*,\d{2}", text)]
    monies = [m for m in monies if m is not None]
    if monies:
        out["valor"] = max(monies)

    # Vencimento: data seguida de um valor na linha de resumo (ex.: "22/06/2026 193,69").
    match = re.search(r"(\d{2}/\d{2}/\d{4})\s+\d{1,3}(?:\.\d{3})*[.,]\d{2}", text)
    if match:
        out["vencimento"] = match.group(1)

    # Data do pagamento: só aceita quando a data vem logo após o rótulo
    # (sem dígitos no meio), evitando capturar datas de leitura/instalação.
    low_full = remove_accents(text.lower())
    match = re.search(r"data\s*d[oe]\s*pagamento[^0-9]{0,40}(\d{2}/\d{2}/\d{4})", low_full)
    if match:
        out["pagamento"] = match.group(1)

    return out


def _row_from_pages(
    pages_boxes: list[list[Box]],
    filename: str,
    extraction_mode: str,
    observations: Optional[list[str]] = None,
) -> dict:
    """Constrói uma linha da tabela a partir das páginas de UM comprovante."""
    observations = list(observations or [])
    text = normalize_text("\n".join(boxes_to_text(p) for p in pages_boxes))
    lines = text_lines(text)
    text_found = len(text.strip()) >= 20

    lancamento = extract_date_field(lines, "lancamento")
    vencimento = extract_date_field(lines, "vencimento")
    pagamento = extract_date_field(lines, "pagamento")
    valor_original = extract_money_field(lines, "valor_original")
    valor = extract_money_field(lines, "valor")
    banco = extract_bank(text, filename)

    # A descrição (beneficiário) é mais confiável na página do comprovante, que
    # é a primeira de cada grupo; tenta página a página e usa a primeira válida.
    descricao = FieldResult(A_REVISAR, 0.0, "Descrição não identificada")
    for page in pages_boxes:
        candidate = extract_description(text_lines(boxes_to_text(page)))
        if not is_blank_or_review(candidate.value):
            descricao = candidate
            break
    if is_blank_or_review(descricao.value):
        descricao = extract_description(lines)

    # Parser posicional da grade do comprovante: tem prioridade quando encontra
    # o campo, por ser mais confiável que a busca por linha de texto.
    grid: dict[str, str] = {}
    for page in pages_boxes:
        page_grid = parse_bb_grid(page)
        for key, value in page_grid.items():
            grid.setdefault(key, value)
    if grid.get("vencimento"):
        normalized = normalize_date(grid["vencimento"])
        if normalized:
            vencimento = FieldResult(normalized, 0.96, "Grade do comprovante (posicional).")
    if grid.get("pagamento"):
        normalized = normalize_date(grid["pagamento"])
        if normalized:
            pagamento = FieldResult(normalized, 0.96, "Grade do comprovante (posicional).")
    if grid.get("valor_original") is not None:
        money = money_to_float(grid["valor_original"])
        if money is not None:
            valor_original = FieldResult(money, 0.96, "Grade do comprovante (posicional).")
    if grid.get("valor") is not None:
        money = money_to_float(grid["valor"])
        if money is not None:
            valor = FieldResult(money, 0.96, "Grade do comprovante (posicional).")

    # Conta de água CAESB (layout próprio): sobrescreve com o parser dedicado.
    caesb = parse_caesb(text)
    if caesb:
        descricao = FieldResult(caesb["descricao"], 0.90, "Conta CAESB.")
        banco = FieldResult(caesb["banco"], 0.90, "Pago via SISBB (Banco do Brasil).")
        if caesb.get("vencimento"):
            nd = normalize_date(caesb["vencimento"])
            if nd:
                vencimento = FieldResult(nd, 0.88, "Conta CAESB.")
        # Data de pagamento: o OCR da CAESB costuma ser ruidoso; só preenche se
        # estiver legível, senão marca para revisão (evita "Conferido" errado).
        pag_nd = normalize_date(caesb["pagamento"]) if caesb.get("pagamento") else None
        if pag_nd:
            pagamento = FieldResult(pag_nd, 0.88, "Conta CAESB.")
        else:
            pagamento = FieldResult(A_REVISAR, 0.0, "CAESB: data de pagamento ilegível; revisar.")
        if caesb.get("valor") is not None:
            valor = FieldResult(caesb["valor"], 0.88, "Conta CAESB (total a pagar).")
            valor_original = FieldResult(caesb["valor"], 0.88, "Conta CAESB (total a pagar).")

    # Se não houver lançamento, usa a data de pagamento como melhor fallback, mas com baixa confiança.
    if is_blank_or_review(lancamento.value) and not is_blank_or_review(pagamento.value):
        lancamento = FieldResult(
            pagamento.value,
            0.55,
            "Lançamento não encontrado; usando data de pagamento como fallback.",
        )

    fields = [lancamento, vencimento, pagamento, descricao, valor_original, valor, banco]
    confidence = confidence_mean(fields)

    row = {
        "LANÇAMENTO": lancamento.value,
        "DESCRIÇÃO": descricao.value,
        "VENCIMENTO": vencimento.value,
        "PAGAMENTO": pagamento.value,
        "VALOR ORIGINAL": valor_original.value,
        "VALOR": valor.value,
        "PAGO BANCO": banco.value,
        "STATUS": "",
        "ARQUIVO": filename,
        "CONFIANÇA": confidence,
        "OBSERVAÇÃO": " | ".join([f.reason for f in fields if f.reason] + observations),
        "MODO LEITURA": extraction_mode,
    }
    row["STATUS"] = build_status(row, confidence, text_found)

    return row


def _group_pages_by_comprovante(pages_boxes: list[list[Box]]) -> list[list[int]]:
    """Agrupa as páginas por comprovante.

    Cada página com o cabeçalho de comprovante inicia um novo grupo; páginas
    seguintes sem o cabeçalho (boleto, nota fiscal) são anexadas ao comprovante
    mais próximo. Assim um único PDF com vários comprovantes vira vários grupos.
    """
    comp_idx = [i for i, p in enumerate(pages_boxes) if _is_comprovante_page(p)]
    if len(comp_idx) <= 1:
        return [list(range(len(pages_boxes)))] if pages_boxes else []

    # Cada comprovante define uma fronteira. As páginas de apoio (boleto, nota)
    # que vêm depois de um comprovante e antes do próximo ficam com ele; as
    # páginas iniciais (antes do 1º comprovante) entram no primeiro grupo.
    ranges: list[list[int]] = []
    for n, idx in enumerate(comp_idx):
        start = 0 if n == 0 else idx
        end = comp_idx[n + 1] if n + 1 < len(comp_idx) else len(pages_boxes)
        ranges.append(list(range(start, end)))
    return ranges


def extract_rows_from_pdf(
    pdf_path: str | Path,
    filename: str | None = None,
    use_ocr: bool = True,
    ocr_lang: str = "por+eng",
) -> list[dict]:
    """Lê um PDF e devolve UMA linha por comprovante encontrado.

    Funciona tanto para PDFs com um único comprovante quanto para PDFs em que
    a consultora escaneou vários comprovantes (várias empresas) num arquivo só.
    """
    filename = filename or Path(pdf_path).name

    pages_boxes: list[list[Box]] = []
    extraction_mode = "texto"
    try:
        pages_boxes = collect_page_boxes(pdf_path, use_ocr=use_ocr)
        if any(not page.get_text("words") for page in fitz.open(pdf_path)):
            extraction_mode = "ocr"
    except Exception as exc:
        return [_row_from_pages([], filename, "erro", [f"Falha ao ler o PDF: {exc}"])]

    combined = normalize_text("\n".join(boxes_to_text(p) for p in pages_boxes))

    # PDF escaneado (sem texto) e RapidOCR indisponível: tenta Tesseract; se não
    # houver, devolve um erro explícito com a causa real (ajuda no diagnóstico).
    if use_ocr and len(combined.strip()) < 40 and _get_rapidocr() is None:
        ocr_err = rapidocr_error()
        try:
            text = normalize_text(extract_text_ocr(pdf_path, lang=ocr_lang))
            if len(text.strip()) < 40:
                raise RuntimeError("Tesseract não retornou texto.")
            fake_page: list[Box] = [(0, i, 1, i + 1, ln) for i, ln in enumerate(text.splitlines())]
            return [_row_from_pages([fake_page], filename, "ocr")]
        except Exception as exc:
            detalhe = ocr_err or str(exc)
            return [
                _row_from_pages(
                    [], filename, "erro",
                    [f"Motor de OCR indisponível no servidor ({detalhe})."],
                )
            ]

    groups = _group_pages_by_comprovante(pages_boxes)
    multi = sum(1 for p in pages_boxes if _is_comprovante_page(p)) >= 2

    rows: list[dict] = []
    for n, idx_group in enumerate(groups, start=1):
        subset = [pages_boxes[i] for i in idx_group]
        label = f"{filename} (comprovante {n})" if multi else filename
        rows.append(_row_from_pages(subset, label, extraction_mode))
    if not rows:
        rows.append(_row_from_pages(pages_boxes, filename, extraction_mode))
    return rows


def extract_from_pdf(
    pdf_path: str | Path,
    filename: str | None = None,
    use_ocr: bool = True,
    ocr_lang: str = "por+eng",
) -> dict:
    """Compatibilidade: devolve apenas a primeira linha do PDF.

    Para PDFs com vários comprovantes, use ``extract_rows_from_pdf``.
    """
    rows = extract_rows_from_pdf(pdf_path, filename=filename, use_ocr=use_ocr, ocr_lang=ocr_lang)
    return rows[0]


def validate_status_for_row(row: dict) -> str:
    """Valida uma linha já editada pelo usuário.

    Diferente da extração automática, aqui a confiança baixa não deve impedir
    a marcação como Conferido se todos os campos obrigatórios foram corrigidos.
    """
    required = [
        "LANÇAMENTO",
        "DESCRIÇÃO",
        "VENCIMENTO",
        "PAGAMENTO",
        "VALOR ORIGINAL",
        "VALOR",
        "PAGO BANCO",
    ]
    if any(is_blank_or_review(row.get(col)) for col in required):
        return "Campo incompleto"
    return "Conferido"
