# WFP–TPM Monitoring Dashboard — Full Package (v1.2)

Somali Region Streamlit app for allocating **WFP (30%) / TPM (70%)** monitoring with **Act 3 (Refugees) full TPM**, **WFP overnight spot‑checks**, **named rosters**, **seaborn charts**, **interactive map**, and **Excel/PDF export** (with optional **Power Automate** webhook).

## Quick start
```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements_dashboard.txt
streamlit run dashboard_app.py
```

## Content
- `dashboard_app.py` — app
- `requirements_dashboard.txt` — dependencies
- `sample_activity_plan.csv`, `sample_roster_combined.csv` — sample inputs
- `sample_export_2025-11-04.xlsx`, `sample_report_2025-11-04.pdf` — sample outputs
- Manuals: `WFP_TPM_Dashboard_Manual.pdf`, `WFP_TPM_Dashboard_Manual_with_Screenshots.pdf`
- `.streamlit/config.toml` — theme
