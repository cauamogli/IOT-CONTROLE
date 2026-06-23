from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from excel_exporter import build_excel
from extractor import (
    A_REVISAR,
    extract_rows_from_pdf,
    is_blank_or_review,
    rapidocr_error,
    validate_status_for_row,
)

BASE_COLUMNS = [
    "LANÇAMENTO",
    "DESCRIÇÃO",
    "VENCIMENTO",
    "PAGAMENTO",
    "VALOR ORIGINAL",
    "VALOR",
    "PAGO BANCO",
    "STATUS",
    "ARQUIVO",
    "CONFIANÇA",
    "OBSERVAÇÃO",
    "MODO LEITURA",
]

EDITABLE_COLUMNS = [
    "LANÇAMENTO",
    "DESCRIÇÃO",
    "VENCIMENTO",
    "PAGAMENTO",
    "VALOR ORIGINAL",
    "VALOR",
    "PAGO BANCO",
    "STATUS",
]

STATUS_OPTIONS = ["Conferido", "A revisar", "Erro na leitura", "Campo incompleto"]

st.set_page_config(
    page_title="Controle de Despesas por PDF",
    page_icon="📄",
    layout="wide",
)

BRAND = "#034b79"
BRAND_DARK = "#A50312"

st.markdown(
    f"""
    <style>
    .block-container {{ padding-top: 2.2rem; }}
    /* Cabeçalho com a marca */
    .app-header {{
        display: flex;
        align-items: center;
        gap: 20px;
        background: linear-gradient(135deg, #ffffff 0%, #eef5fb 100%);
        border: 1px solid #d9e6f1;
        border-left: 6px solid {BRAND};
        padding: 18px 26px;
        border-radius: 18px;
        box-shadow: 0 6px 22px rgba(3, 75, 121, 0.08);
        margin-bottom: 22px;
    }}
    /* A logo é branca: vai sobre um selo escuro da marca para aparecer. */
    .app-header .logo {{
        flex: 0 0 auto;
        background: {BRAND};
        padding: 12px 18px;
        border-radius: 14px;
        box-shadow: 0 4px 12px rgba(3, 75, 121, 0.25);
        display: flex;
        align-items: center;
    }}
    .app-header .logo img {{ height: 46px; width: auto; display: block; }}
    .app-header .titles {{ line-height: 1.25; }}
    .app-header .titles .t1 {{ font-size: 1.35rem; font-weight: 800; color: {BRAND}; letter-spacing: .3px; }}
    .app-header .titles .t2 {{ font-size: .95rem; font-weight: 700; color: #475569; }}
    .app-header .titles .t3 {{ font-size: .82rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; }}

    /* Cartões de indicadores */
    .metric-card {{
        border: 1px solid #eceff3;
        border-radius: 16px;
        padding: 18px 20px;
        background: #ffffff;
        box-shadow: 0 4px 14px rgba(17, 24, 39, 0.05);
        min-height: 100px;
        transition: transform .15s ease, box-shadow .15s ease;
    }}
    .metric-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 22px rgba(17,24,39,.09); }}
    .metric-label {{
        font-size: 0.78rem; color: #8a94a6; text-transform: uppercase;
        letter-spacing: .6px; margin-bottom: 8px; font-weight: 700;
    }}
    .metric-value {{ font-size: 1.45rem; font-weight: 800; color: #111827; }}
    .metric-card.accent {{ border-top: 3px solid {BRAND}; }}
    .success-card {{ background: #f0fbf5; border-color: #c4ecd6; }}
    .success-card .metric-value {{ color: #15803d; }}
    .warning-card {{ background: #fffaf0; border-color: #ffe2a8; }}
    .warning-card .metric-value {{ color: #b45309; }}
    .danger-card  {{ background: #fef2f3; border-color: #f6c9cf; }}
    .danger-card .metric-value {{ color: {BRAND_DARK}; }}
    .info-card {{ background: #f3f7ff; border-color: #cfe0ff; }}
    .info-card .metric-value {{ color: #1d4ed8; }}

    /* Botões com a marca */
    .stButton > button, .stDownloadButton > button {{ border-radius: 10px; font-weight: 600; }}
    div[data-testid="stSidebar"] {{ background: #fbfbfc; }}
    [data-testid="stHeader"] {{ background: transparent; }}
    .legend {{ font-size: .82rem; color: #64748b; }}
    .legend .chip {{
        display:inline-block; padding:2px 10px; border-radius:999px;
        font-weight:700; font-size:.74rem; margin-right:6px;
    }}
    .chip-red {{ background:#fde2e4; color:{BRAND_DARK}; }}
    .chip-green {{ background:#dcfce7; color:#15803d; }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def load_logo_uri() -> str:
    """Lê a logo (logo/logo.svg) e devolve um data URI para usar em <img>.

    Embutir como imagem base64 é mais robusto que SVG inline, que o Streamlit
    pode remover por segurança.
    """
    import base64

    logo_path = Path(__file__).parent / "logo" / "logo.svg"
    try:
        raw = logo_path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
    except Exception:
        return ""


def empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=BASE_COLUMNS)


def ensure_df_shape(df: pd.DataFrame) -> pd.DataFrame:
    safe = df.copy()
    for col in BASE_COLUMNS:
        if col not in safe.columns:
            safe[col] = ""
    return safe[BASE_COLUMNS]


def to_number(series: pd.Series) -> pd.Series:
    if series.empty:
        return pd.Series(dtype=float)
    clean = (
        series.astype(str)
        .str.replace("R$", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .replace({A_REVISAR: "", "nan": "", "None": ""})
    )
    return pd.to_numeric(clean, errors="coerce").fillna(0)


def brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


FIELD_COLUMNS = ["LANÇAMENTO", "DESCRIÇÃO", "VENCIMENTO", "PAGAMENTO", "VALOR ORIGINAL", "VALOR", "PAGO BANCO"]
RED_CELL = "background-color:#fde2e4; color:#A50312; font-weight:600;"
GREEN_CELL = "background-color:#dcfce7; color:#15803d; font-weight:700;"


def fmt_value(value) -> str:
    """Formata moeda quando possível; senão devolve o texto como está."""
    if is_blank_or_review(value):
        return A_REVISAR
    try:
        return brl(float(value))
    except (TypeError, ValueError):
        return str(value)


def style_review(df_view: pd.DataFrame):
    """Pinta de vermelho os campos pendentes (vazios ou 'A revisar')."""

    def cell(val):
        return RED_CELL if is_blank_or_review(val) else ""

    def status_cell(val):
        s = str(val).lower()
        if any(k in s for k in ["revis", "erro", "incompleto"]):
            return RED_CELL.replace("600", "700")
        if "conferido" in s:
            return GREEN_CELL
        return ""

    styler = df_view.style
    present_fields = [c for c in FIELD_COLUMNS if c in df_view.columns]
    style_map = getattr(styler, "map", None) or styler.applymap
    if present_fields:
        styler = style_map(cell, subset=present_fields)
    style_map = getattr(styler, "map", None) or styler.applymap
    if "STATUS" in df_view.columns:
        styler = style_map(status_cell, subset=["STATUS"])
    money_cols = [c for c in ["VALOR ORIGINAL", "VALOR"] if c in df_view.columns]
    if money_cols:
        styler = styler.format(fmt_value, subset=money_cols)
    return styler


def metric_card(label: str, value: str, css_class: str = "") -> None:
    st.markdown(
        f"""
        <div class="metric-card {css_class}">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def recalc_statuses(df: pd.DataFrame) -> pd.DataFrame:
    safe = ensure_df_shape(df)
    for idx, row in safe.iterrows():
        safe.at[idx, "STATUS"] = validate_status_for_row(row.to_dict())
    return safe


def add_empty_row(df: pd.DataFrame) -> pd.DataFrame:
    new_row = {
        "LANÇAMENTO": A_REVISAR,
        "DESCRIÇÃO": A_REVISAR,
        "VENCIMENTO": A_REVISAR,
        "PAGAMENTO": A_REVISAR,
        "VALOR ORIGINAL": 0.0,
        "VALOR": 0.0,
        "PAGO BANCO": A_REVISAR,
        "STATUS": "A revisar",
        "ARQUIVO": "Inserido manualmente",
        "CONFIANÇA": 0.0,
        "OBSERVAÇÃO": "Linha adicionada manualmente.",
        "MODO LEITURA": "manual",
    }
    return pd.concat([ensure_df_shape(df), pd.DataFrame([new_row])], ignore_index=True)


if "df" not in st.session_state:
    st.session_state.df = empty_df()
if "show_review_only" not in st.session_state:
    st.session_state.show_review_only = False

logo_uri = load_logo_uri()
logo_html = f'<div class="logo"><img src="{logo_uri}" alt="Logo"/></div>' if logo_uri else ""
st.markdown(
    f"""
    <div class="app-header">
        {logo_html}
        <div class="titles">
            <div class="t1">CONTROLE DE DESPESAS</div>
            <div class="t2">IOT — Instituto Ortopédico</div>
            <div class="t3">Despesas Gerais</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

_ocr_err = rapidocr_error()
if _ocr_err:
    st.error(
        "⚠️ O motor de OCR (RapidOCR) não pôde ser carregado no servidor, então "
        "PDFs escaneados não serão lidos. Detalhe técnico: "
        f"`{_ocr_err}`. Em geral resolve-se adicionando um arquivo `packages.txt` "
        "com `libgl1` e `libglib2.0-0` e reiniciando o app."
    )

# Sidebar de importação
with st.sidebar:
    st.header("Importar PDFs")
    uploaded_files = st.file_uploader(
        "Selecione boletos ou comprovantes em PDF",
        type=["pdf"],
        accept_multiple_files=True,
    )
    use_ocr = st.toggle("Usar OCR quando necessário", value=True)
    ocr_lang = st.text_input("Idioma OCR", value="por+eng", help="Ex.: por, eng ou por+eng")

    process = st.button("📥 Processar PDFs", type="primary", use_container_width=True)

    st.divider()
    st.caption(
      "Desenvolvido por Cauã 2026. "
    )

if process:
    if not uploaded_files:
        st.warning("Selecione pelo menos um PDF para processar.")
    else:
        extracted_rows = []
        progress = st.progress(0, text="Iniciando leitura dos PDFs...")
        for index, file in enumerate(uploaded_files, start=1):
            progress.progress(index / len(uploaded_files), text=f"Processando {file.name}...")
            suffix = Path(file.name).suffix or ".pdf"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file.getbuffer())
                tmp_path = tmp.name
            try:
                extracted_rows.extend(
                    extract_rows_from_pdf(
                        tmp_path,
                        filename=file.name,
                        use_ocr=use_ocr,
                        ocr_lang=ocr_lang,
                    )
                )
            except Exception as exc:
                extracted_rows.append(
                    {
                        "LANÇAMENTO": A_REVISAR,
                        "DESCRIÇÃO": A_REVISAR,
                        "VENCIMENTO": A_REVISAR,
                        "PAGAMENTO": A_REVISAR,
                        "VALOR ORIGINAL": A_REVISAR,
                        "VALOR": A_REVISAR,
                        "PAGO BANCO": A_REVISAR,
                        "STATUS": "Erro na leitura",
                        "ARQUIVO": file.name,
                        "CONFIANÇA": 0.0,
                        "OBSERVAÇÃO": f"Erro geral ao processar arquivo: {exc}",
                        "MODO LEITURA": "erro",
                    }
                )

        new_df = ensure_df_shape(pd.DataFrame(extracted_rows))
        st.session_state.df = ensure_df_shape(
            pd.concat([st.session_state.df, new_df], ignore_index=True)
        )
        st.success(
            f"{len(uploaded_files)} arquivo(s) processado(s), "
            f"{len(extracted_rows)} comprovante(s) lido(s). Confira a tabela antes de exportar."
        )

current_df = ensure_df_shape(st.session_state.df)
valor_original_total = float(to_number(current_df["VALOR ORIGINAL"]).sum())
valor_pago_total = float(to_number(current_df["VALOR"]).sum())
juros_total = valor_pago_total - valor_original_total
qtd_lancamentos = len(current_df)
qtd_revisar = int(current_df["STATUS"].astype(str).str.contains("revis|erro|incompleto", case=False, regex=True).sum())

m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    metric_card("Total pago", brl(valor_pago_total), "success-card")
with m2:
    metric_card("Valor original", brl(valor_original_total), "accent")
with m3:
    metric_card("Juros", brl(juros_total), "warning-card")
with m4:
    metric_card("Lançamentos", str(qtd_lancamentos), "info-card")
with m5:
    metric_card("A revisar", str(qtd_revisar), "danger-card" if qtd_revisar else "")

st.write("")

b1, b2, b3, b4, b5, b6 = st.columns([1, 1.1, 1.2, 1, 1.1, 1.6])
if b1.button("➕ Nova linha", use_container_width=True):
    st.session_state.df = add_empty_row(st.session_state.df)
    st.rerun()

if b2.button("🔎 Filtrar revisão", use_container_width=True):
    st.session_state.show_review_only = not st.session_state.show_review_only
    st.rerun()

if b3.button("✅ Recalcular status", use_container_width=True):
    st.session_state.df = recalc_statuses(st.session_state.df)
    st.rerun()

if b4.button("🧹 Limpar tabela", use_container_width=True):
    st.session_state.df = empty_df()
    st.session_state.show_review_only = False
    st.rerun()

with b5:
    excel_bytes = build_excel(current_df)
    st.download_button(
        "📊 Exportar Excel",
        data=excel_bytes,
        file_name=f"controle_despesas_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

with b6:
    visible_columns = st.multiselect(
        "Colunas visíveis",
        options=BASE_COLUMNS,
        default=EDITABLE_COLUMNS,
        label_visibility="collapsed",
    )

if not visible_columns:
    visible_columns = EDITABLE_COLUMNS

st.markdown(
    '<div class="legend">'
    '<span class="chip chip-red">A revisar</span> campo vazio ou incerto &nbsp;·&nbsp; '
    '<span class="chip chip-green">Conferido</span> validado'
    "</div>",
    unsafe_allow_html=True,
)

if current_df.empty:
    st.info("Importe PDFs ou clique em 'Nova linha' para começar.")
else:
    has_pending = bool(
        current_df["STATUS"].astype(str).str.contains("revis|erro|incompleto", case=False, regex=True).any()
    )

    if st.session_state.show_review_only:
        view_df = current_df[current_df["STATUS"].astype(str).str.contains("revis|erro|incompleto", case=False, regex=True)].copy()
        st.warning("Filtro de revisão ativo. Para ver todos os registros, clique novamente em 'Filtrar revisão'.")
        num_rows_mode = "fixed"
    else:
        view_df = current_df.copy()
        num_rows_mode = "dynamic"

    # Visão destacada (somente leitura): campos pendentes em vermelho.
    st.dataframe(
        style_review(view_df[visible_columns]),
        use_container_width=True,
        hide_index=True,
    )

    column_config = {
        "LANÇAMENTO": st.column_config.TextColumn("LANÇAMENTO", help="Data de lançamento ou registro"),
        "DESCRIÇÃO": st.column_config.TextColumn("DESCRIÇÃO", width="large"),
        "VENCIMENTO": st.column_config.TextColumn("VENCIMENTO"),
        "PAGAMENTO": st.column_config.TextColumn("PAGAMENTO"),
        "VALOR ORIGINAL": st.column_config.NumberColumn("VALOR ORIGINAL", format="R$ %.2f"),
        "VALOR": st.column_config.NumberColumn("VALOR", format="R$ %.2f"),
        "PAGO BANCO": st.column_config.TextColumn("PAGO BANCO"),
        "STATUS": st.column_config.SelectboxColumn("STATUS", options=STATUS_OPTIONS),
        "CONFIANÇA": st.column_config.NumberColumn("CONFIANÇA", format="%.2f", disabled=True),
        "OBSERVAÇÃO": st.column_config.TextColumn("OBSERVAÇÃO", width="large"),
        "MODO LEITURA": st.column_config.TextColumn("MODO LEITURA", disabled=True),
    }
    for col in BASE_COLUMNS:
        if col not in visible_columns:
            column_config[col] = None

    with st.expander("✏️ Editar / corrigir dados", expanded=has_pending):
        edited_df = st.data_editor(
            view_df[BASE_COLUMNS],
            use_container_width=True,
            hide_index=True,
            num_rows=num_rows_mode,
            column_config=column_config,
            key="expense_table",
        )

        edited_df = ensure_df_shape(pd.DataFrame(edited_df))

        if st.session_state.show_review_only:
            # Atualiza apenas as linhas filtradas, preservando o restante da tabela.
            updated = current_df.copy()
            for local_idx, original_idx in enumerate(view_df.index):
                if local_idx < len(edited_df):
                    updated.loc[original_idx, BASE_COLUMNS] = edited_df.iloc[local_idx][BASE_COLUMNS]
            st.session_state.df = ensure_df_shape(updated)
        else:
            st.session_state.df = ensure_df_shape(edited_df)

    delete_labels = {
        f"Linha {idx + 1} - {str(row.get('DESCRIÇÃO', ''))[:55]} - {row.get('ARQUIVO', '')}": idx
        for idx, row in current_df.iterrows()
    }
    with st.expander("🗑️ Excluir linhas manualmente"):
        selected_to_delete = st.multiselect("Selecione as linhas que deseja excluir", list(delete_labels.keys()))
        if st.button("Excluir selecionadas", type="secondary"):
            indexes = [delete_labels[label] for label in selected_to_delete]
            st.session_state.df = current_df.drop(index=indexes).reset_index(drop=True)
            st.rerun()

    with st.expander("Detalhes técnicos da extração"):
        st.dataframe(current_df[["ARQUIVO", "MODO LEITURA", "CONFIANÇA", "OBSERVAÇÃO"]], use_container_width=True)
