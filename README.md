# Basira Semantic Mapper MVP v0.2 + Financial Model + Insights

Pipeline:

Excel → Data Profiling → Gemini Semantic Mapping → normalized_transactions → Financial Model → Arabic Financial Insights

Default Gemini model: `gemini-3.5-flash-lite`

Set API key in Windows CMD:

```cmd
set GEMINI_API_KEY=YOUR_GEMINI_KEY
```

Run the full pipeline:

```cmd
py src\run_pipeline.py samples\01_clear_english.xlsx
```

Outputs:

- `outputs\01_clear_english_mapping.json`
- `outputs\01_clear_english_normalized_transactions.json`
- `outputs\01_clear_english_financial_model.json`
- `outputs\01_clear_english_insights.json`

The Insights layer deliberately separates deterministic analysis from language generation: Python computes period changes, customer/product ranking, payment-status shares, and evidence. Gemini is used only to synthesize that evidence into Arabic structured JSON.

Individual steps:

```cmd
py src\run_mapper.py samples\01_clear_english.xlsx
py src\build_normalized_transactions.py samples\01_clear_english.xlsx outputs\01_clear_english_mapping.json
py src\build_financial_model.py outputs\01_clear_english_normalized_transactions.json
py src\build_insights.py outputs\01_clear_english_normalized_transactions.json outputs\01_clear_english_financial_model.json
```

Run tests:

```cmd
py -m pytest -q
```
