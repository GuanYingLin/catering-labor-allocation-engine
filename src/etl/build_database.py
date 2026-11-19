#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ==============================================================================
# Project : M-System 餐飲門市人事成本最適資源配置系統
# Module  : build_database.py (端到端座標級 ETL 萃取與 SQLite 星狀倉儲建置)
# SSOT    : 2018 營所稅推估期標記 / 收支 24 筆斷言 / 困境 45 筆斷言
# Version : v2.0 (Final Pitch Release)
# ==============================================================================
"""
餐飲門市人事成本最適資源配置系統 - 座標級全動態 ETL 資料萃取與 SQLite 建庫管線
使用 pdfplumber 像素座標群聚 (Y-axis clustering + X-axis sorting) 精準解析跨年度異質 PDF 表格。
"""


import re
import sqlite3
from pathlib import Path
import pandas as pd
import pdfplumber

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DB_PATH = PROJECT_ROOT / "data" / "db" / "catering_benchmark.db"
SCHEMA_PATH = PROJECT_ROOT / "src" / "sql" / "schema.sql"


def _cluster_words_by_row(page, y_tolerance: float = 5.0) -> list[str]:
    """
    依據文字方塊的垂直中心座標 (top) 進行水平列群聚，並依水平座標 (x0) 由左至右排序。
    可徹底解決 PDF 底部儲存格文字垂直偏移 1~4pt 導致純文字斷行的問題。
    """
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows = []
    current_row = [words_sorted[0]]
    current_y = words_sorted[0]["top"]

    for w in words_sorted[1:]:
        if abs(w["top"] - current_y) <= y_tolerance:
            current_row.append(w)
        else:
            current_row.sort(key=lambda item: item["x0"])
            rows.append(" ".join(item["text"] for item in current_row))
            current_row = [w]
            current_y = w["top"]

    if current_row:
        current_row.sort(key=lambda item: item["x0"])
        rows.append(" ".join(item["text"] for item in current_row))

    return rows


def _extract_numbers(text: str) -> list[float]:
    """萃取出字串中的所有數值（自動移除千分位逗號）"""
    tokens = re.findall(r"-?\d[\d,]*\.?\d*", text)
    nums = []
    for t in tokens:
        try:
            nums.append(float(t.replace(",", "")))
        except ValueError:
            continue
    return nums


def parse_cost_structure_pdfs() -> pd.DataFrame:
    """
    動態掃描 data/raw/*_cost_structure_raw.pdf，萃取 56(餐飲業)、561/5611(餐館業)、562/5631(飲料店業) 收支數據
    """
    pdf_files = sorted(RAW_DIR.glob("*_cost_structure_raw.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"在 {RAW_DIR} 找不到 *_cost_structure_raw.pdf")

    records = []
    target_map = {
        "56": "餐飲業總計",
        "561": "餐館業",
        "5611": "餐館業",
        "562": "飲料店業",
        "5631": "飲料店業",
    }

    for pdf_path in pdf_files:
        year_match = re.match(r"(\d{4})_", pdf_path.name)
        if not year_match:
            continue
        survey_year = int(year_match.group(1))
        roc_year = survey_year - 1911

        with pdfplumber.open(pdf_path) as pdf:
            rows = _cluster_words_by_row(pdf.pages[0], y_tolerance=6.0)

        for idx, row_str in enumerate(rows):
            code_match = re.match(r"^(5611|5631|561|562|56)\b", row_str.strip())
            if not code_match or row_str.strip().startswith("5620"):
                continue

            raw_code = code_match.group(1)
            rest = row_str.strip()[len(raw_code):]
            nums = _extract_numbers(rest)

            if len(nums) < 9 and idx + 1 < len(rows):
                nums.extend(_extract_numbers(rows[idx + 1]))

            if len(nums) == 11:
                rev, total_exp, cogs, labor, rent, tax, depr, bad_debt, freight, other_opex, op_profit = nums
                other_exp = bad_debt + freight + other_opex
            elif len(nums) == 10:
                rev, total_exp, cogs, labor, rent, tax, depr, bad_debt, other_opex, op_profit = nums
                other_exp = bad_debt + other_opex
            else:
                raise ValueError(f"解析 {pdf_path.name} 代碼 {raw_code} 欄位數異常 ({len(nums)} 欄): {row_str}")

            records.append({
                "survey_year": survey_year,
                "roc_year": roc_year,
                "regime_period": "Pre-2018 (調查推估期)" if survey_year < 2018 else "Post-2018 (營所稅推估期)",
                "industry_code_raw": raw_code,
                "industry_category": target_map[raw_code],
                "revenue_mn": rev,
                "total_expense_mn": total_exp,
                "cogs_mn": cogs,
                "labor_cost_mn": labor,
                "rent_mn": rent,
                "tax_fee_mn": tax,
                "depreciation_mn": depr,
                "other_expense_mn": other_exp,
                "operating_profit_mn": op_profit,
            })

    df = pd.DataFrame(records).sort_values(["survey_year", "industry_code_raw"]).reset_index(drop=True)
    if len(df) != 24:
        raise RuntimeError(f"預期萃取 24 筆 (8年 x 3業別)，實際取得 {len(df)} 筆。")

    df["cogs_pct"] = (df["cogs_mn"] / df["revenue_mn"] * 100).round(2)
    df["labor_cost_pct"] = (df["labor_cost_mn"] / df["revenue_mn"] * 100).round(2)
    df["rent_pct"] = (df["rent_mn"] / df["revenue_mn"] * 100).round(2)
    df["operating_margin_pct"] = (df["operating_profit_mn"] / df["revenue_mn"] * 100).round(2)
    df["other_opex_pct"] = (
        100.0 - df["cogs_pct"] - df["labor_cost_pct"] - df["rent_pct"] - df["operating_margin_pct"]
    ).round(2)

    return df


def parse_operating_obstacles_pdfs() -> pd.DataFrame:
    """
    動態掃描 data/raw/*_operating_obstacles_raw.pdf，跨頁合併第 1 頁與第 2 頁困境調查指標
    """
    pdf_files = sorted(RAW_DIR.glob("*_operating_obstacles_raw.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"在 {RAW_DIR} 找不到 *_operating_obstacles_raw.pdf")

    row_specs = [
        (r"\b56\b",                 "業別",     "餐飲業總計"),
        (r"\b(5611|561)\b",         "業別",     "餐館業"),
        (r"\b(5631|562)\b",         "業別",     "飲料店業"),
        (r"未\s*滿\s*5\s*人",       "員工人數", "未滿5人"),
        (r"5\s*人\s*~\s*49\s*人",   "員工人數", "5人~49人"),
    ]

    all_records = []

    for pdf_path in pdf_files:
        year_match = re.match(r"(\d{4})_", pdf_path.name)
        if not year_match:
            continue
        survey_year = int(year_match.group(1))
        roc_year = survey_year - 1911

        def extract_page_dict(page) -> dict[str, list[float]]:
            rows = _cluster_words_by_row(page, y_tolerance=6.5)
            found = {}
            for idx, r_str in enumerate(rows):
                if "5620" in r_str:
                    continue
                for pat, _, std_name in row_specs:
                    if re.search(pat, r_str) and std_name not in found:
                        cleaned = re.sub(r"^\s*(5611|5631|561|562|56)\s+", "", r_str)
                        cleaned = re.sub(r"未\s*滿\s*5\s*人|5\s*人\s*~\s*49\s*人", "", cleaned)
                        nums = _extract_numbers(cleaned)
                        if len(nums) < 5 and idx + 1 < len(rows):
                            nums.extend(_extract_numbers(rows[idx + 1]))
                        if len(nums) >= 5:
                            found[std_name] = nums
            return found

        with pdfplumber.open(pdf_path) as pdf:
            p1_data = extract_page_dict(pdf.pages[0])
            p2_data = extract_page_dict(pdf.pages[1]) if len(pdf.pages) > 1 else {}

        for _, seg_type, std_name in row_specs:
            n1 = p1_data.get(std_name, [])
            n2 = p2_data.get(std_name, [])
            if not n1 or not n2:
                raise RuntimeError(f"解析 {pdf_path.name} [{std_name}] 失敗: P1={n1}, P2={n2}")

            sample_size = int(n1[0])
            high_rent = n1[2]
            high_turnover = n1[3]

            if survey_year <= 2017:
                fierce_comp = n2[0]
                ingredient_vol = n2[2]
                labor_shortage = None
                high_labor_cost = None
            elif survey_year in (2018, 2019, 2020, 2021):
                fierce_comp = n1[7]
                ingredient_vol = n2[1]
                labor_shortage = n2[4]
                high_labor_cost = n2[5]
            else:
                fierce_comp = n1[7]
                ingredient_vol = n2[1]
                labor_shortage = n2[3]
                high_labor_cost = n2[4]

            all_records.append({
                "survey_year": survey_year,
                "roc_year": roc_year,
                "segment_type": seg_type,
                "segment_name": std_name,
                "sample_size": sample_size,
                "high_turnover_pct": high_turnover,
                "labor_shortage_pct": labor_shortage,
                "high_labor_cost_pct": high_labor_cost,
                "ingredient_volatility_pct": ingredient_vol,
                "high_rent_pct": high_rent,
                "fierce_competition_pct": fierce_comp,
            })

    df = pd.DataFrame(all_records)
    if len(df) != 45:
        raise RuntimeError(f"預期萃取 45 筆 (9年 x 5維度)，實際取得 {len(df)} 筆。")
    return df


def parse_minimum_wage_csv() -> pd.DataFrame:
    """動態讀取 data/raw/T07_m3_minimum_wage_monthly_raw.csv 並計算負擔指數"""
    csv_path = RAW_DIR / "T07_m3_minimum_wage_monthly_raw.csv"
    df_raw = pd.read_csv(csv_path, dtype=str)

    records = []
    for _, row in df_raw.iterrows():
        eff_date_str = str(row["實施日期（西元）"]).strip()
        if len(eff_date_str) == 8 and eff_date_str.isdigit():
            eff_year = int(eff_date_str[:4])
            if 2015 <= eff_year <= 2026:
                content = str(row["內容/調整金額（新台幣）"]).replace(",", "")
                m_match = re.search(r"月薪(\d+)", content)
                h_match = re.search(r"時薪(\d+)", content)
                records.append({
                    "effective_year": eff_year,
                    "effective_date": f"{eff_date_str[:4]}-{eff_date_str[4:6]}-{eff_date_str[6:8]}",
                    "monthly_wage": int(m_match.group(1)) if m_match else None,
                    "hourly_wage": int(h_match.group(1)) if h_match else None,
                })

    df_parsed = pd.DataFrame(records)
    df_yearly = (
        df_parsed.sort_values("effective_date")
        .groupby("effective_year", as_index=False)
        .agg({"effective_date": "last", "monthly_wage": "last", "hourly_wage": "last"})
    )
    df_yearly["monthly_wage"] = df_yearly["monthly_wage"].ffill().astype(int)
    df_yearly["hourly_wage"] = df_yearly["hourly_wage"].ffill().astype(int)

    base_monthly = df_yearly.loc[df_yearly["effective_year"] == 2015, "monthly_wage"].values[0]
    base_hourly = df_yearly.loc[df_yearly["effective_year"] == 2015, "hourly_wage"].values[0]

    df_yearly["monthly_wage_index"] = (df_yearly["monthly_wage"] / base_monthly * 100).round(2)
    df_yearly["hourly_wage_index"] = (df_yearly["hourly_wage"] / base_hourly * 100).round(2)
    df_yearly["monthly_yoy_pct"] = (df_yearly["monthly_wage"].pct_change() * 100).round(2)
    df_yearly["hourly_yoy_pct"] = (df_yearly["hourly_wage"].pct_change() * 100).round(2)

    return df_yearly


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("正在透過座標群聚引擎動態解析 17 份 PDF 與 1 份 CSV...")
    df_cost = parse_cost_structure_pdfs()
    df_obstacles = parse_operating_obstacles_pdfs()
    df_wage = parse_minimum_wage_csv()

    df_cost.to_csv(PROCESSED_DIR / "clean_cost_structure_2015_2024.csv", index=False, encoding="utf-8-sig")
    df_obstacles.to_csv(PROCESSED_DIR / "clean_operating_obstacles_2016_2024.csv", index=False, encoding="utf-8-sig")
    df_wage.to_csv(PROCESSED_DIR / "clean_minimum_wage_index_2015_2026.csv", index=False, encoding="utf-8-sig")

    if DB_PATH.exists():
        DB_PATH.unlink()

    with sqlite3.connect(DB_PATH) as conn:
        df_cost.to_sql("fact_cost_structure", conn, if_exists="replace", index=False)
        df_obstacles.to_sql("fact_operating_obstacles", conn, if_exists="replace", index=False)
        df_wage.to_sql("dim_minimum_wage", conn, if_exists="replace", index=False)

        conn.executescript("""
            DROP VIEW IF EXISTS vw_catering_benchmark_mart;
            CREATE VIEW vw_catering_benchmark_mart AS
            SELECT
                industry_category,
                COUNT(*) AS sample_years,
                ROUND(AVG(cogs_pct), 2) AS avg_cogs_pct,
                ROUND(AVG(labor_cost_pct), 2) AS avg_labor_cost_pct,
                ROUND(AVG(rent_pct), 2) AS avg_rent_pct,
                ROUND(AVG(other_opex_pct), 2) AS avg_other_opex_pct,
                ROUND(AVG(operating_margin_pct), 2) AS avg_operating_margin_pct,
                MAX(CASE WHEN survey_year = 2024 THEN cogs_pct END) AS latest_2024_cogs_pct,
                MAX(CASE WHEN survey_year = 2024 THEN labor_cost_pct END) AS latest_2024_labor_pct,
                MAX(CASE WHEN survey_year = 2024 THEN operating_margin_pct END) AS latest_2024_margin_pct
            FROM fact_cost_structure
            WHERE industry_category IN ('餐館業', '飲料店業')
              AND survey_year >= 2018
            GROUP BY industry_category;
        """)

        df_mart = pd.read_sql_query("SELECT * FROM vw_catering_benchmark_mart;", conn)

    print("\n=== 全動態 ETL 清洗與 SQLite 建庫成功 ===")
    print(f"1. fact_cost_structure 動態萃取筆數: {len(df_cost)} (8個調查年度 x 3業別)")
    print(f"2. fact_operating_obstacles 動態萃取筆數: {len(df_obstacles)} (9個調查年度 x 5維度)")
    print(f"3. dim_minimum_wage 動態萃取筆數: {len(df_wage)} (2015-2026)")
    print("\n=== 決策引擎預設業態基準參數 (vw_catering_benchmark_mart) ===")
    print(df_mart.to_string(index=False))


if __name__ == "__main__":
    main()