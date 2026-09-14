# Basira Semantic Mapper MVP v0.2 + النموذج المالي + الاستنتاجات

المسار الكامل:

Excel → Profiling → Gemini Semantic Mapping → normalized_transactions → Financial Model → Insights مالية عربية

النموذج الافتراضي:

`gemini-3.5-flash-lite`

ضع مفتاح Gemini في CMD:

```cmd
set GEMINI_API_KEY=YOUR_GEMINI_KEY
```

ثم شغّل الـMVP كاملًا:

```cmd
py src\run_pipeline.py samples\01_clear_english.xlsx
```

النتائج:

- `outputs\01_clear_english_mapping.json`
- `outputs\01_clear_english_normalized_transactions.json`
- `outputs\01_clear_english_financial_model.json`
- `outputs\01_clear_english_insights.json`

طبقة الـInsights لا تسمح للنموذج بحساب الأرقام من تلقاء نفسه. يقوم Python بحساب المقارنات والترتيبات ونسب حالات الدفع وتجهيز الأدلة، ثم يستخدم Gemini لصياغة استنتاجات عربية منظمة ومقيدة بالأدلة.
