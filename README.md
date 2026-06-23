# Controle de Despesas por PDF

MVP em Python + Streamlit para automatizar a leitura de boletos e comprovantes de pagamento em PDF, revisar os dados em uma tabela editável e exportar para Excel `.xlsx`.

## Funcionalidades

- Upload de vários PDFs ao mesmo tempo.
- Leitura direta de texto do PDF com PyMuPDF.
- OCR como fallback para PDFs escaneados.
- Extração automática de:
  - LANÇAMENTO
  - DESCRIÇÃO
  - VENCIMENTO
  - PAGAMENTO
  - VALOR ORIGINAL
  - VALOR
  - PAGO BANCO
- Marcação automática como `A revisar` quando algum campo não é identificado com segurança.
- Tabela editável.
- Nova linha manual.
- Exclusão de linhas.
- Filtro de registros que precisam de revisão.
- Cards com total pago, valor original, juros, lançamentos e pendências.
- Exportação para Excel formatado, com cabeçalho, totais, moeda em reais e destaque para linhas pendentes.

## Como rodar

### 1. Crie o ambiente virtual

No Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

No Linux/Mac:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Instale as dependências Python

```bash
pip install -r requirements.txt
```

### 3. OCR para PDFs escaneados (automático)

A maioria dos comprovantes bancários é escaneada (imagem, sem texto). O OCR
já vem incluído via `rapidocr-onnxruntime` (instalado no passo 2), funciona
offline e **não exige nenhum programa adicional no sistema**. Os modelos são
baixados na primeira execução.

O Tesseract é apenas uma alternativa opcional. Se você já o tiver instalado, o
sistema o detecta automaticamente nos caminhos padrão do Windows
(`C:\Program Files\Tesseract-OCR\tesseract.exe`).

### 4. Execute o sistema

```bash
streamlit run app.py
```

Depois, abra o endereço exibido no terminal, geralmente:

```text
http://localhost:8501
```

## Observações importantes

Este MVP usa regras e expressões regulares para extrair dados. Isso funciona bem quando os comprovantes seguem padrões parecidos. Para aumentar a precisão em produção, o ideal é testar com PDFs reais e ir ajustando os padrões de leitura de acordo com os layouts dos bancos usados pela empresa.

Quando o sistema não tiver certeza, ele marca o campo como `A revisar`, permitindo conferência manual antes de exportar a planilha.

## Estrutura dos arquivos

```text
app.py              # Interface principal em Streamlit
extractor.py        # Leitura dos PDFs, OCR e extração dos campos
excel_exporter.py   # Geração do Excel formatado
requirements.txt    # Dependências do projeto
```

## Próximas melhorias recomendadas

- Login de usuários.
- Banco de dados PostgreSQL ou SQLite para histórico mensal.
- Cadastro de empresas/favorecidos recorrentes.
- Treinamento de padrões por banco.
- Integração com OCR profissional, como Google Vision, AWS Textract ou Azure Document Intelligence.
- Tela de comparação entre imagem/PDF original e dados extraídos.
