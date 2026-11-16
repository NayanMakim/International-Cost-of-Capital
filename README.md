# Cross-border Cost of Capital (USD vs. Local Currency) — Streamlit App

Compares a project's **cost of equity, cost of debt and WACC in USD** and in the **currency of a selected country**, using live market and tax data.

## Quick start (Python 3.12)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

## Default settings on launch

| Setting | Default |
|---|---|
| Industry | Software (System & Application) |
| Selected country | United States |
| Project tax rate | U.S. |
| Equity risk premium | 6.5% |
| Target debt share D/(D+E) | 30% |
| Currency conversion method | Interest-rate parity |

With the United States selected, the USD and "local" columns are identical by construction. That's a quick sanity check.

## Data sources

| Input | Source | How it is read |
|---|---|---|
| U.S. industry unlevered betas | Damodaran, [betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | "Industry Averages" sheet, **Unlevered beta** column (not the cash-corrected one) |
| USD BBB borrowing spread | Damodaran, [ratings.html](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | **Baa2/BBB** row of the first table (large non-financial service firms) |
| Corporate tax rates | OECD Corporate Tax Statistics ([report page](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb)) | The report page shows the rates as a chart, so the app reads the same data from the OECD SDMX API (`OECD.CTP.TPS, DSD_TAX_CIT@DF_CIT`), using the **combined (central + sub-central) statutory CIT rate** for the latest year. If the API fails, it tries HTML tables on the report page |
| 10-year government bond yields | [Trading Economics bonds](https://tradingeconomics.com/bonds) | Scraped (no API key). Rows are matched by their `/<country>/government-bond-yield` link. If a country isn't in the table, the app tries that country's own bond page |

The **Data sources and dates** panel lists every value the app uses, with its status (**Live**, **Manual** or **Fallback**), the data date reported by the source, when it was fetched, and a link to the source. Data is cached for 6 hours. **🔄 Refresh data** clears the cache and downloads everything again.

**Fallback values.** If a source can't be reached (network block, site layout change, rate limiting), the app keeps running on built-in **approximate offline values** and shows a yellow warning naming the affected inputs. These values are only there so the app doesn't crash. Don't submit results that use them: press Refresh, or type the correct values under **Manual overrides**.

## Controls

* **Industry** and **Target country** (49 countries, each with its OECD code, Trading Economics slug and currency).
* **Equity risk premium** and **target debt share**.
* **Project tax rate** toggle: U.S. rate or selected-country rate. The same rate is used in both currencies.
* **Currency conversion method:** interest-rate parity (default), additive yield differential, or local CAPM. This lets you match whichever formula is used in class.
* **Manual overrides:** unlevered beta, U.S. tax rate, selected-country tax rate, USD BBB spread, U.S. 10Y yield and selected-country 10Y yield. Each one has its own checkbox. With the box unticked, the field shows the fetched value.
* **Foreign BBB spread override** toggle. When it's off, the app shows the automatically calculated spread. When it's on, your value is used.

## Formulas

Let $w_D = D/V$, $E/V = 1-w_D$ and $D/E = w_D/(1-w_D)$. Let $t$ be the project tax rate.

1. **Relevered beta (Hamada):** $\beta_L = \beta_U\,[1+(1-t)\,D/E]$
2. **USD cost of equity (CAPM):** $k_e^{USD} = r_f^{US} + \beta_L \cdot ERP$
3. **USD pre-tax cost of debt:** $k_d^{USD} = r_f^{US} + s_{BBB}^{USD}$. After tax: $k_d(1-t)$
4. **Foreign-currency rates** (default, interest-rate parity / international Fisher effect):
   $(1+k^{FC}) = (1+k^{USD})\cdot\dfrac{1+r_f^{FC}}{1+r_f^{US}}$, applied to both $k_e$ and $k_d$
5. **Automatic foreign BBB spread:** $s^{FC} = k_d^{FC} - r_f^{FC}$. With a manual spread, $k_d^{FC} = r_f^{FC} + s^{FC}$
6. **WACC in each currency:** $WACC = \frac{E}{V}k_e + \frac{D}{V}k_d(1-t)$

The other conversion methods:

* **Additive:** $k^{FC} = k^{USD} + (r_f^{FC} - r_f^{US})$, so the foreign spread equals the USD spread.
* **Local CAPM:** $k_e^{FC} = r_f^{FC} + \beta_L \cdot ERP$ and $k_d^{FC} = r_f^{FC} + s^{USD}$.

## Main assumptions

* The industry beta is a U.S. industry average. It is applied to the project in any country.
* Each 10-year government yield is treated as the risk-free rate in its own currency. No sovereign default spread is removed. The yield gap stands for the expected inflation / currency differential.
* The same ERP, debt share and project tax rate are used in both currencies.
* The tax rate is the OECD combined statutory rate, not an effective rate.

## All-industry comparison and downloads

* A bar chart and a table cover **every Damodaran industry** under the current settings: unlevered and relevered beta, plus cost of equity and WACC in USD and the local currency. The selected industry is highlighted in red.
* Downloads:
  * results as CSV
  * the all-industry table as CSV
  * an Excel workbook with sheets *Inputs*, *Results*, *All industries* and *Sources*, in plain Calibri with no fills, no bold headers and no borders

## Verifying the calculations

The finance logic is all in one pure function, `compute()` in `app.py`, so you can check it separately:

```python
from app import compute
r = compute(beta_u=1.23, tax=0.258, debt_share=0.30, erp=0.065,
            rf_us=0.041, rf_fc=0.065, spread_us=0.0111)
# beta_L = 1.23 * (1 + 0.742 * 0.4286) = 1.6211
# ke_USD = 4.10% + 1.6211 * 6.5% = 14.637%
# kd_USD = 4.10% + 1.11% = 5.21%;  after-tax = 3.866%
# WACC_USD = 0.7 * 14.637% + 0.3 * 3.866% = 11.406%
# ke_FC = 1.14637 * 1.065 / 1.041 - 1 = 17.280%
# kd_FC = 1.0521 * 1.065 / 1.041 - 1 = 7.636%   (auto spread 1.136%)
# WACC_FC = 0.7 * 17.280% + 0.3 * 7.636% * 0.742 = 13.796%
```

## Known limitations

* **Scraping can break.** Trading Economics and the OECD can change their page layouts or block automated requests. When that happens the app falls back and tells you. Use the manual overrides until the parser is updated.
* **Coverage gaps.** OECD statutory rates don't cover every jurisdiction or the latest year, and the same goes for IMF-type datasets. The data date column shows the year actually used.
* **Hosting.** Hosted environments such as Streamlit Community Cloud may be blocked by some sites. Running locally is the most reliable option.

## Files

* `app.py`: the Streamlit app, with data fetchers, parsers, `compute()` and the UI
* `requirements.txt`: dependencies, compatible with Python 3.12
* `README.md`: this file
