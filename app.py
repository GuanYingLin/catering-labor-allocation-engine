#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐飲門市人事成本最適資源配置系統 (Catering Labor Allocation & Marginal Decision Engine)
工業級重構版：動態 CSV 資料驅動架構、固定營業費用分離引擎、邊際貢獻率斷路防禦與自適應視覺化
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

# =============================================================================
# 系統預設容錯基準庫 (Fallback Defaults - 僅於外部 CSV 缺失或損毀時啟動)
# =============================================================================
FALLBACK_CONFIG: dict[str, Any] = {
    "ft_legal_multiplier": 1.20,
    "pt_legal_multiplier": 1.11,
    "ft_standard_hours": 176,
    "splh_idle_threshold": 650.0,
    "splh_optimal_upper": 800.0,
    "splh_overload_severe": 1000.0,
    "prime_cost_warning_threshold": 65.0,
    "cm_critical_threshold": 0.0,
    "cm_low_warning_threshold": 0.05,
    "vat_threshold": 200000.0,
    "alpha_exempt": 1.0,
    "alpha_small_biz": 0.99,
    "alpha_vat5": 1.0 / 1.05,
    "default_revenue": 409584,
    "default_cogs_pct": 35.75,
    "default_total_opex_pct": 10.0,
    "macro_rent_pct": 5.69,
    "macro_dep_pct": 1.40,
    "macro_other_opex_pct": 10.68,
    "macro_labor_pct": 22.90,
    "macro_cogs_pct": 44.97,
    "latest_cost_year": 2024,
    "latest_wage_year": 2026,
    "min_monthly_wage": 29500,
    "min_hourly_wage": 196,
    "monthly_wage_index": 147.44,
    "hourly_wage_index": 163.33,
    "default_ft_salaries": [44000, 38000, 36000],
    "default_curr_pt_hours": [60, 20],
    "default_add_pt_hours": [20, 60],
    "default_pt_hourly_offset": 4,
    "default_new_ft_offset": 2500,
    "peak_season_rev": 458027,
    "normal_season_rev": 406854,
    "low_season_rev": 320887,
    "peak_slot_rev_share_pct": 60.3,
    "peak_slot_traffic_share_pct": 60.6,
    "offpeak_slot_rev_share_pct": 17.6,
    "weighted_avg_ticket": 164.4,
    "weekend_avg_rev": 23107,
    "weekday_avg_rev": 15226,
    "weekend_weekday_ratio": 1.52,
    "weekday_ticket": 146,
    "weekend_ticket": 190,
    "latest_obstacle_year": 2024,
    "obs_labor_shortage_pct": 61.5,
    "obs_high_turnover_pct": 55.6,
    "obs_high_labor_cost_pct": 51.1,
    "obs_ingredient_volatility_pct": 65.6,
    "obs_high_rent_pct": 30.2,
}

CSV_FILENAMES: dict[str, str] = {
    "monthly": "clean_store_monthly_2025_2026.csv",
    "wage": "clean_minimum_wage_index_2015_2026.csv",
    "cost": "clean_cost_structure_2015_2024.csv",
    "timeslots": "clean_store_timeslots_2025_2026.csv",
    "day_type": "clean_store_day_type_2025_2026.csv",
    "obstacles": "clean_operating_obstacles_2016_2024.csv",
}

TAX_MODE_NONE = "稅前損益模式（不計營業稅）"
TAX_MODE_AUTO = "自動判定統一發票門檻（未達 20 萬課 1%，達 20 萬課 5%）"
TAX_MODE_VAT5 = "已使用統一發票（固定 5% 營業稅）"

COLOR_NAVY = "#1B365D"
COLOR_ORANGE = "#EA580C"
COLOR_SLATE = "#64748B"
COLOR_GREEN = "#15803D"
COLOR_RED = "#DC2626"
COLOR_BG_LIGHT = "#F8FAFC"
COLOR_BORDER = "#E2E8F0"


# =============================================================================
# 1. 動態路徑解析與 CSV 容錯讀取引擎
# =============================================================================
def resolve_csv_path(filename: str) -> Optional[Path]:
    """動態檢索標準寬表 CSV 路徑，支援多層資料夾與工作目錄自動搜尋。"""
    candidate_dirs: list[Path] = []
    try:
        script_dir = Path(__file__).resolve().parent
        candidate_dirs.extend([
            script_dir,
            script_dir / "data",
            script_dir / "data" / "clean",
            script_dir / "data" / "processed",
            script_dir / "clean_data",
            script_dir.parent,
            script_dir.parent / "data",
            script_dir.parent / "data" / "clean",
            script_dir.parent / "data" / "processed",
        ])
    except NameError:
        pass

    cwd = Path.cwd().resolve()
    candidate_dirs.extend([
        cwd,
        cwd / "data",
        cwd / "data" / "clean",
        cwd / "data" / "processed",
        cwd / "clean_data",
    ])

    visited: set[Path] = set()
    for folder in candidate_dirs:
        if folder in visited:
            continue
        visited.add(folder)
        target = folder / filename
        if target.is_file():
            return target
    return None


def read_csv_safely(filename: str) -> tuple[Optional[pd.DataFrame], Optional[Path]]:
    """安全載入單一 CSV 檔案，若檔案缺失、空白或格式異常則回傳 (None, None) 觸發 Fallback。"""
    resolved_path = resolve_csv_path(filename)
    if resolved_path is None:
        return None, None
    try:
        df = pd.read_csv(resolved_path, encoding="utf-8-sig")
        if df.empty:
            return None, resolved_path
        return df, resolved_path
    except Exception:
        return None, resolved_path


@st.cache_data(show_spinner=False)
def load_empirical_parameters() -> dict[str, Any]:
    """自 6 份標準寬表 CSV 動態萃取實證基準參數；外部檔案異常時無縫回退至 FALLBACK_CONFIG。"""
    params: dict[str, Any] = dict(FALLBACK_CONFIG)
    csv_status: dict[str, dict[str, Any]] = {}
    monthly_options: dict[str, dict[str, float]] = {}

    # 1. 門市月別營收與成本表 (clean_store_monthly_2025_2026.csv)
    df_m, path_m = read_csv_safely(CSV_FILENAMES["monthly"])
    if df_m is not None and {"total_revenue", "cogs"}.issubset(df_m.columns):
        total_rev = float(df_m["total_revenue"].sum())
        total_cogs = float(df_m["cogs"].sum())
        mean_rev = int(round(float(df_m["total_revenue"].mean())))
        weighted_cogs_pct = (
            round(((total_cogs / total_rev) * 100.0) * 4.0) / 4.0
            if total_rev > 0
            else params["default_cogs_pct"]
        )
        params["default_revenue"] = mean_rev
        params["default_cogs_pct"] = float(weighted_cogs_pct)

        if "season_tier" in df_m.columns:
            peak_df = df_m[df_m["season_tier"].astype(str).str.contains("旺季", na=False)]
            norm_df = df_m[df_m["season_tier"].astype(str).str.contains("平季", na=False)]
            low_df = df_m[df_m["season_tier"].astype(str).str.contains("淡季", na=False)]
            if not peak_df.empty:
                params["peak_season_rev"] = int(round(float(peak_df["total_revenue"].mean())))
            if not norm_df.empty:
                params["normal_season_rev"] = int(round(float(norm_df["total_revenue"].mean())))
            if not low_df.empty:
                params["low_season_rev"] = int(round(float(low_df["total_revenue"].mean())))

        monthly_options["全期實測月均值 (12個月加權基準)"] = {
            "revenue": float(mean_rev),
            "cogs_pct": float(weighted_cogs_pct),
        }
        monthly_options["旺季實測均值 (≥44萬月份平均)"] = {
            "revenue": float(params["peak_season_rev"]),
            "cogs_pct": float(weighted_cogs_pct),
        }
        monthly_options["平季實測均值 (平季月份平均)"] = {
            "revenue": float(params["normal_season_rev"]),
            "cogs_pct": float(weighted_cogs_pct),
        }
        monthly_options["淡季實測均值 (<35萬月份平均)"] = {
            "revenue": float(params["low_season_rev"]),
            "cogs_pct": float(weighted_cogs_pct),
        }
        if "month_label" in df_m.columns:
            for _, row in df_m.iterrows():
                m_rev = float(row["total_revenue"])
                m_cogs = float(row["cogs"])
                m_cogs_pct = round(((m_cogs / m_rev) * 100.0) * 4.0) / 4.0 if m_rev > 0 else float(weighted_cogs_pct)
                m_cogs_pct = float(np.clip(m_cogs_pct, 15.0, 65.0))
                tier_str = f"｜{row['season_tier']}" if "season_tier" in df_m.columns else ""
                label = f"單月實測：{row['month_label']} (${int(round(m_rev)):,}{tier_str})"
                monthly_options[label] = {"revenue": m_rev, "cogs_pct": m_cogs_pct}

        csv_status["monthly"] = {"loaded": True, "rows": len(df_m), "path": str(path_m.name)}
    else:
        monthly_options["預設實測月均值 (Fallback)"] = {
            "revenue": float(params["default_revenue"]),
            "cogs_pct": float(params["default_cogs_pct"]),
        }
        csv_status["monthly"] = {"loaded": False, "rows": 0, "path": "Fallback"}

    params["monthly_options"] = monthly_options

    # 2. 基本工資歷年指數表 (clean_minimum_wage_index_2015_2026.csv)
    df_w, path_w = read_csv_safely(CSV_FILENAMES["wage"])
    if df_w is not None and {"effective_year", "monthly_wage", "hourly_wage"}.issubset(df_w.columns):
        df_w_sorted = df_w.sort_values("effective_year")
        latest_w = df_w_sorted.iloc[-1]
        params["latest_wage_year"] = int(latest_w["effective_year"])
        params["min_monthly_wage"] = int(round(float(latest_w["monthly_wage"])))
        params["min_hourly_wage"] = int(round(float(latest_w["hourly_wage"])))
        if "monthly_wage_index" in df_w_sorted.columns:
            params["monthly_wage_index"] = float(latest_w["monthly_wage_index"])
        if "hourly_wage_index" in df_w_sorted.columns:
            params["hourly_wage_index"] = float(latest_w["hourly_wage_index"])
        csv_status["wage"] = {"loaded": True, "rows": len(df_w), "path": str(path_w.name)}
    else:
        csv_status["wage"] = {"loaded": False, "rows": 0, "path": "Fallback"}

    # 3. 餐飲業宏觀成本結構表 (clean_cost_structure_2015_2024.csv)
    df_c, path_c = read_csv_safely(CSV_FILENAMES["cost"])
    if df_c is not None and {"survey_year", "industry_category", "revenue_mn", "rent_pct", "depreciation_mn", "other_opex_pct"}.issubset(df_c.columns):
        latest_c_year = int(df_c["survey_year"].max())
        df_c_latest = df_c[df_c["survey_year"] == latest_c_year]
        target_row = df_c_latest[df_c_latest["industry_category"] == "餐飲業總計"]
        if target_row.empty:
            target_row = df_c_latest
        r_c = target_row.iloc[0]
        rev_mn = float(r_c["revenue_mn"])
        rent_pct = float(r_c["rent_pct"])
        dep_pct = round((float(r_c["depreciation_mn"]) / rev_mn) * 100.0, 2) if rev_mn > 0 else params["macro_dep_pct"]
        params["latest_cost_year"] = latest_c_year
        params["macro_rent_pct"] = rent_pct
        params["macro_dep_pct"] = dep_pct
        params["macro_other_opex_pct"] = float(r_c["other_opex_pct"])
        if "labor_cost_pct" in r_c:
            params["macro_labor_pct"] = float(r_c["labor_cost_pct"])
        if "cogs_pct" in r_c:
            params["macro_cogs_pct"] = float(r_c["cogs_pct"])
        csv_status["cost"] = {"loaded": True, "rows": len(df_c), "path": str(path_c.name)}
    else:
        csv_status["cost"] = {"loaded": False, "rows": 0, "path": "Fallback"}

    # 動態衍生門市基準固定租金、折舊與變動營業費用率
    base_rev = float(params["default_revenue"])
    params["default_fixed_rent"] = int(round(base_rev * (params["macro_rent_pct"] / 100.0)))
    params["default_fixed_dep"] = int(round(base_rev * (params["macro_dep_pct"] / 100.0)))
    residual_var_opex = params["default_total_opex_pct"] - params["macro_rent_pct"] - params["macro_dep_pct"]
    params["default_var_opex_pct"] = float(
        np.clip(round(max(0.5, residual_var_opex) * 4.0) / 4.0, 0.5, 35.0)
    )

    # 4. 門市時段結構表 (clean_store_timeslots_2025_2026.csv)
    df_t, path_t = read_csv_safely(CSV_FILENAMES["timeslots"])
    if df_t is not None and {"slot_type", "revenue_share", "traffic_share", "actual_revenue", "actual_customers"}.issubset(df_t.columns):
        peak_slots = df_t[df_t["slot_type"] == "尖峰時段"]
        offpeak_slots = df_t[df_t["slot_type"] == "離峰時段"]
        params["peak_slot_rev_share_pct"] = round(float(peak_slots["revenue_share"].sum()) * 100.0, 1)
        params["peak_slot_traffic_share_pct"] = round(float(peak_slots["traffic_share"].sum()) * 100.0, 1)
        params["offpeak_slot_rev_share_pct"] = round(float(offpeak_slots["revenue_share"].sum()) * 100.0, 1)
        tot_cust = float(df_t["actual_customers"].sum())
        if tot_cust > 0:
            params["weighted_avg_ticket"] = round(float(df_t["actual_revenue"].sum()) / tot_cust, 1)
        csv_status["timeslots"] = {"loaded": True, "rows": len(df_t), "path": str(path_t.name)}
    else:
        csv_status["timeslots"] = {"loaded": False, "rows": 0, "path": "Fallback"}

    # 5. 門市平假日結構表 (clean_store_day_type_2025_2026.csv)
    df_d, path_d = read_csv_safely(CSV_FILENAMES["day_type"])
    if df_d is not None and {"day_category", "avg_daily_revenue", "avg_ticket_size"}.issubset(df_d.columns):
        wknd = df_d[df_d["day_category"] == "假日"]
        wkdy = df_d[df_d["day_category"] == "平日"]
        if not wknd.empty and not wkdy.empty:
            wknd_rev = float(wknd["avg_daily_revenue"].iloc[0])
            wkdy_rev = float(wkdy["avg_daily_revenue"].iloc[0])
            params["weekend_avg_rev"] = int(round(wknd_rev))
            params["weekday_avg_rev"] = int(round(wkdy_rev))
            params["weekend_weekday_ratio"] = round(wknd_rev / wkdy_rev, 2) if wkdy_rev > 0 else 1.52
            params["weekend_ticket"] = int(round(float(wknd["avg_ticket_size"].iloc[0])))
            params["weekday_ticket"] = int(round(float(wkdy["avg_ticket_size"].iloc[0])))
        csv_status["day_type"] = {"loaded": True, "rows": len(df_d), "path": str(path_d.name)}
    else:
        csv_status["day_type"] = {"loaded": False, "rows": 0, "path": "Fallback"}

    # 6. 餐飲業經營困境調查表 (clean_operating_obstacles_2016_2024.csv)
    df_o, path_o = read_csv_safely(CSV_FILENAMES["obstacles"])
    if df_o is not None and {"survey_year", "segment_name", "labor_shortage_pct", "high_turnover_pct", "high_labor_cost_pct"}.issubset(df_o.columns):
        latest_o_year = int(df_o["survey_year"].max())
        df_o_latest = df_o[(df_o["survey_year"] == latest_o_year) & (df_o["segment_name"] == "餐飲業總計")]
        if df_o_latest.empty:
            df_o_latest = df_o[df_o["survey_year"] == latest_o_year]
        if not df_o_latest.empty:
            r_o = df_o_latest.iloc[0]
            params["latest_obstacle_year"] = latest_o_year
            params["obs_labor_shortage_pct"] = float(r_o["labor_shortage_pct"])
            params["obs_high_turnover_pct"] = float(r_o["high_turnover_pct"])
            params["obs_high_labor_cost_pct"] = float(r_o["high_labor_cost_pct"])
            if "ingredient_volatility_pct" in r_o:
                params["obs_ingredient_volatility_pct"] = float(r_o["ingredient_volatility_pct"])
            if "high_rent_pct" in r_o:
                params["obs_high_rent_pct"] = float(r_o["high_rent_pct"])
        csv_status["obstacles"] = {"loaded": True, "rows": len(df_o), "path": str(path_o.name)}
    else:
        csv_status["obstacles"] = {"loaded": False, "rows": 0, "path": "Fallback"}

    params["csv_status"] = csv_status
    return params


# =============================================================================
# 2. 視覺主題與自適應 CSS 注入
# =============================================================================
def inject_presentation_theme() -> None:
    """注入與商業簡報一致之高對比樣式（深海軍藍 #1B365D ＋ 暖橘 #EA580C ＋ 純白底）"""
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: #FFFFFF;
            color: #1E293B;
        }}
        section[data-testid="stSidebar"] {{
            background-color: {COLOR_BG_LIGHT};
            border-right: 1px solid {COLOR_BORDER};
        }}
        h1 {{
            color: {COLOR_NAVY} !important;
            font-weight: 800 !important;
            letter-spacing: -0.02em;
            padding-bottom: 0.2rem;
        }}
        h2, h3 {{
            color: {COLOR_NAVY} !important;
            font-weight: 700 !important;
            border-left: 4px solid {COLOR_ORANGE};
            padding-left: 0.65rem;
            margin-top: 1.1rem !important;
        }}
        div[data-testid="stMetric"] {{
            background-color: {COLOR_BG_LIGHT};
            border: 1px solid {COLOR_BORDER};
            border-top: 4px solid {COLOR_NAVY};
            border-radius: 8px;
            padding: 14px 16px;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
        }}
        div[data-testid="stMetric"]:nth-of-type(4) {{
            border-top-color: {COLOR_ORANGE};
        }}
        div[data-testid="stMetricLabel"] p {{
            color: #475569 !important;
            font-weight: 600 !important;
            font-size: 0.9rem !important;
        }}
        div[data-testid="stMetricValue"] {{
            color: {COLOR_NAVY} !important;
            font-weight: 800 !important;
        }}
        div[data-testid="stDataFrame"] {{
            border: 1px solid {COLOR_BORDER};
            border-radius: 8px;
            overflow: hidden;
        }}
        .cost-breakdown-banner {{
            background-color: {COLOR_BG_LIGHT};
            border: 1px solid {COLOR_BORDER};
            border-left: 4px solid {COLOR_NAVY};
            border-radius: 6px;
            padding: 12px 16px;
            margin: 10px 0 16px 0;
            font-size: 0.94rem;
            color: #1E293B;
            line-height: 1.65;
        }}
        .r1-bias-banner {{
            background-color: #FFF7ED;
            border: 1px solid #FED7AA;
            border-left: 4px solid {COLOR_ORANGE};
            border-radius: 6px;
            padding: 10px 14px;
            margin: 8px 0 14px 0;
            font-size: 0.91rem;
            color: #7C2D12;
            line-height: 1.55;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# =============================================================================
# 3. 財務損益、固定費用分離與邊際貢獻率防禦核心演算法
# =============================================================================
def get_tax_alpha(revenue: float, tax_mode: str, params: dict[str, Any]) -> float:
    """計算含稅營收到除稅實質營收之轉換係數 (alpha)"""
    if tax_mode == TAX_MODE_NONE:
        return float(params["alpha_exempt"])
    if tax_mode == TAX_MODE_VAT5:
        return float(params["alpha_vat5"])
    return (
        float(params["alpha_small_biz"])
        if revenue <= float(params["vat_threshold"])
        else float(params["alpha_vat5"])
    )


def calc_marginal_contribution_rate(alpha: float, cogs_pct: float, var_opex_pct: float) -> float:
    """計算單位營收邊際貢獻率 (CM = alpha - (cogs_pct + var_opex_pct) / 100)"""
    return alpha - ((cogs_pct + var_opex_pct) / 100.0)


def calc_net_profit(
    revenue: float,
    cogs_pct: float,
    var_opex_pct: float,
    fixed_opex_amt: float,
    labor_cost: float,
    tax_mode: str,
    params: dict[str, Any],
) -> float:
    """計算扣除營業稅、食材、變動費用、固定費用與實質人事後之營業淨利"""
    alpha = get_tax_alpha(revenue, tax_mode, params)
    cm_rate = calc_marginal_contribution_rate(alpha, cogs_pct, var_opex_pct)
    return (revenue * cm_rate) - fixed_opex_amt - labor_cost


def solve_single_r1(
    target_profit: float,
    fixed_opex_amt: float,
    labor_cost: float,
    cogs_pct: float,
    var_opex_pct: float,
    alpha: float,
    cm_critical: float = 0.0,
) -> float:
    """給定固定除稅係數 alpha 求解最低維持淨利之營業額 R1；CM <= 0 時阻斷計算回傳 NaN"""
    cm_rate = calc_marginal_contribution_rate(alpha, cogs_pct, var_opex_pct)
    if cm_rate <= cm_critical:
        return float("nan")
    required_total_contribution = target_profit + fixed_opex_amt + labor_cost
    if required_total_contribution <= 0.0:
        return float("nan")
    return required_total_contribution / cm_rate


def calc_r1_threshold(
    pi0: float,
    fixed_opex_amt: float,
    labor_cost: float,
    cogs_pct: float,
    var_opex_pct: float,
    tax_mode: str,
    params: dict[str, Any],
) -> float:
    """求解維持基準淨利 pi0 所需之最低含稅月營業額門檻 R1（含起徵點階梯分段解析解）"""
    cm_critical = float(params["cm_critical_threshold"])
    vat_threshold = float(params["vat_threshold"])

    if tax_mode == TAX_MODE_NONE:
        return solve_single_r1(
            pi0, fixed_opex_amt, labor_cost, cogs_pct, var_opex_pct,
            float(params["alpha_exempt"]), cm_critical
        )

    if tax_mode == TAX_MODE_VAT5:
        return solve_single_r1(
            pi0, fixed_opex_amt, labor_cost, cogs_pct, var_opex_pct,
            float(params["alpha_vat5"]), cm_critical
        )

    r1_low = solve_single_r1(
        pi0, fixed_opex_amt, labor_cost, cogs_pct, var_opex_pct,
        float(params["alpha_small_biz"]), cm_critical
    )
    if not math.isnan(r1_low) and r1_low <= vat_threshold:
        return r1_low

    r1_high = solve_single_r1(
        pi0, fixed_opex_amt, labor_cost, cogs_pct, var_opex_pct,
        float(params["alpha_vat5"]), cm_critical
    )
    if math.isnan(r1_high):
        return float("nan")
    return max(r1_high, vat_threshold)


def classify_splh_status(splh: float, params: dict[str, Any]) -> tuple[str, str, str]:
    """依據 SPLH 四級門檻回傳 (產能效率定性, 簡短標籤, 現場狀態描述)"""
    idle_th = float(params["splh_idle_threshold"])
    opt_up = float(params["splh_optimal_upper"])
    sev_th = float(params["splh_overload_severe"])

    if splh < idle_th:
        return (
            "產能失衡 — 工時閒置與獲利侵蝕",
            f"低於 {int(idle_th)} 下限（工時閒置）",
            f"平均每工時產出低於 {int(idle_th)} 元，單位工時營收貢獻不足，離峰時段存在冗餘工時。",
        )
    if splh <= opt_up:
        return (
            "產能均衡 — 工時與營收配置適中",
            f"落於 {int(idle_th)}–{int(opt_up)} 黃金區間",
            f"平均每工時產出介於 {int(idle_th)} 至 {int(opt_up)} 元之間，單位工時負荷與出餐節奏處於最佳平衡帶。",
        )
    if splh <= sev_th:
        return (
            "產能警戒 — 尖峰負荷緊繃",
            f"介於 {int(opt_up)}–{int(sev_th):,}（負荷緊繃）",
            f"平均每工時產出突破 {int(opt_up)} 元上限，尖峰時段已處於高負荷狀態，出餐速度開始承壓。",
        )
    return (
        "嚴重過載 — 極度過勞與掉單風險",
        f"高於 {int(sev_th):,} 上限（嚴重過勞）",
        f"平均每工時產出超過 {int(sev_th):,} 元，遠超現場物理承載極限，具備高度掉單與離職風險。",
    )


# =============================================================================
# 4. 自適應 Altair 視覺化圖表引擎
# =============================================================================
def build_splh_zone_chart(
    curr_splh: float,
    splh_B_same_rev: float,
    splh_A_same_rev: float,
    splh_A_at_r1: float,
    valid_r1_A: bool,
    params: dict[str, Any],
) -> alt.LayerChart:
    """圖 A：各配置情境 SPLH 與四級產能區間定位圖（自適應容器寬度、動態留白防截字）"""
    idle_th = float(params["splh_idle_threshold"])
    opt_up = float(params["splh_optimal_upper"])
    sev_th = float(params["splh_overload_severe"])

    rows: list[dict[str, Any]] = [
        {
            "情境": "1. 未增員現況",
            "SPLH": round(curr_splh, 0),
            "顏色": COLOR_GREEN if idle_th <= curr_splh <= opt_up else COLOR_NAVY,
            "狀態": classify_splh_status(curr_splh, params)[1],
        },
        {
            "情境": "2. 方案 B (增兼職・營收持平)",
            "SPLH": round(splh_B_same_rev, 0),
            "顏色": COLOR_NAVY,
            "狀態": classify_splh_status(splh_B_same_rev, params)[1],
        },
        {
            "情境": "3. 方案 A (增正職・營收持平)",
            "SPLH": round(splh_A_same_rev, 0),
            "顏色": COLOR_ORANGE,
            "狀態": classify_splh_status(splh_A_same_rev, params)[1],
        },
    ]

    if valid_r1_A and not math.isnan(splh_A_at_r1) and splh_A_at_r1 > 0:
        rows.append({
            "情境": "4. 方案 A (達成 R1 門檻)",
            "SPLH": round(splh_A_at_r1, 0),
            "顏色": COLOR_SLATE,
            "狀態": classify_splh_status(splh_A_at_r1, params)[1],
        })

    df_splh = pd.DataFrame(rows)
    df_splh["標籤"] = df_splh["SPLH"].apply(lambda v: f"{int(v):,} 元/hr")

    max_val = float(df_splh["SPLH"].max()) if not df_splh.empty else sev_th
    x_max = max(max_val * 1.28, sev_th * 1.18)
    x_scale = alt.Scale(domain=[0.0, x_max], nice=False)

    zones_df = pd.DataFrame([
        {"x1": 0.0, "x2": idle_th, "fill": "#FEE2E2", "區間": f"<{int(idle_th)} 閒置區"},
        {"x1": idle_th, "x2": opt_up, "fill": "#DCFCE7", "區間": f"{int(idle_th)}–{int(opt_up)} 黃金區"},
        {"x1": opt_up, "x2": sev_th, "fill": "#FEF3C7", "區間": f"{int(opt_up)}–{int(sev_th):,} 警戒區"},
        {"x1": sev_th, "x2": x_max, "fill": "#FECACA", "區間": f">{int(sev_th):,} 過勞區"},
    ])
    zone_rects = (
        alt.Chart(zones_df)
        .mark_rect(opacity=0.52)
        .encode(
            x=alt.X("x1:Q", scale=x_scale, title="每工時產值 SPLH (元/hr)"),
            x2="x2:Q",
            color=alt.Color("fill:N", scale=None),
            tooltip=[alt.Tooltip("區間:N", title="產能區間")],
        )
    )

    rules_df = pd.DataFrame([
        {"x": idle_th, "門檻說明": f"{int(idle_th)} 元/hr 閒置下限"},
        {"x": opt_up, "門檻說明": f"{int(opt_up)} 元/hr 黃金上限"},
    ])
    threshold_rules = (
        alt.Chart(rules_df)
        .mark_rule(strokeDash=[5, 4], color=COLOR_NAVY, strokeWidth=1.6)
        .encode(
            x=alt.X("x:Q", scale=x_scale),
            tooltip=[alt.Tooltip("門檻說明:N", title="診斷門檻")],
        )
    )

    bars = (
        alt.Chart(df_splh)
        .mark_bar(height=24, cornerRadiusEnd=4)
        .encode(
            x=alt.X("SPLH:Q", scale=x_scale),
            y=alt.Y(
                "情境:N",
                sort=None,
                title=None,
                axis=alt.Axis(labelLimit=210, labelFontSize=11, labelFontWeight="bold", labelPadding=6),
            ),
            color=alt.Color("顏色:N", scale=None),
            tooltip=[
                alt.Tooltip("情境:N", title="配置情境"),
                alt.Tooltip("標籤:N", title="每工時產值"),
                alt.Tooltip("狀態:N", title="產能定性"),
            ],
        )
    )

    labels = (
        alt.Chart(df_splh)
        .mark_text(align="left", baseline="middle", dx=6, fontSize=11, fontWeight="bold", color="#1E293B")
        .encode(
            x=alt.X("SPLH:Q", scale=x_scale),
            y=alt.Y("情境:N", sort=None),
            text="標籤:N",
        )
    )

    chart_height = max(255, len(df_splh) * 58)
    title_suffix = "" if valid_r1_A else "（註：CM ≤ 0% 時 R1 不收斂，已自動隱藏第 4 列）"

    return (
        (zone_rects + threshold_rules + bars + labels)
        .properties(
            title=f"圖 A：各方案 SPLH 產能區間定位（綠帶為 {int(idle_th)}–{int(opt_up)} 黃金區）{title_suffix}",
            width="container",
            height=chart_height,
            autosize=alt.AutoSizeParams(type="fit", contains="padding"),
        )
        .configure_view(strokeWidth=0)
        .configure_title(color=COLOR_NAVY, fontSize=13, anchor="start", offset=8)
        .configure_axis(labelColor="#334155", titleColor=COLOR_NAVY, grid=True, gridColor="#F1F5F9")
    )


def build_profit_impact_chart(
    curr_net_profit: float,
    profit_B_same_rev: float,
    profit_A_same_rev: float,
    chart_height: int = 255,
) -> alt.LayerChart:
    """圖 B：增員對單月實質淨利衝擊圖（支援正負軸自適應、零軸基準線、文字防重疊）"""
    def _fmt_label(val: float, is_base: bool = False) -> str:
        if is_base:
            return f"${int(round(val)):,} (基準)"
        if abs(curr_net_profit) > 1e-6:
            chg_pct = ((val - curr_net_profit) / abs(curr_net_profit)) * 100.0
            return f"${int(round(val)):,} ({chg_pct:+.1f}%)"
        return f"${int(round(val)):,}"

    df_prof = pd.DataFrame([
        {
            "方案": "1. 未增員現況",
            "實質月淨利": round(curr_net_profit, 0),
            "標籤": _fmt_label(curr_net_profit, is_base=True),
            "顏色": COLOR_SLATE if curr_net_profit >= 0 else COLOR_RED,
        },
        {
            "方案": "2. 方案 B (增兼職)",
            "實質月淨利": round(profit_B_same_rev, 0),
            "標籤": _fmt_label(profit_B_same_rev),
            "顏色": COLOR_NAVY if profit_B_same_rev >= 0 else COLOR_RED,
        },
        {
            "方案": "3. 方案 A (增正職)",
            "實質月淨利": round(profit_A_same_rev, 0),
            "標籤": _fmt_label(profit_A_same_rev),
            "顏色": COLOR_ORANGE if profit_A_same_rev >= 0 else COLOR_RED,
        },
    ])

    min_v = float(df_prof["實質月淨利"].min())
    max_v = float(df_prof["實質月淨利"].max())
    span = max(abs(max_v - min_v), abs(max_v), abs(min_v), 15000.0)
    y_min = min(0.0, min_v - span * 0.28)
    y_max = max(0.0, max_v + span * 0.28)
    if y_max - y_min < 10000.0:
        y_max += 10000.0
    y_scale = alt.Scale(domain=[y_min, y_max], nice=False)

    bars = (
        alt.Chart(df_prof)
        .mark_bar(size=42, cornerRadius=4)
        .encode(
            x=alt.X(
                "方案:N",
                sort=None,
                title=None,
                scale=alt.Scale(paddingInner=0.35, paddingOuter=0.2),
                axis=alt.Axis(labelAngle=0, labelFontSize=11, labelFontWeight="bold", labelLimit=140),
            ),
            y=alt.Y("實質月淨利:Q", scale=y_scale, title="實質營業淨利 (元/月)", axis=alt.Axis(format="$,.0f")),
            color=alt.Color("顏色:N", scale=None),
            tooltip=[
                alt.Tooltip("方案:N", title="配置方案"),
                alt.Tooltip("標籤:N", title="單月實質淨利與變動"),
            ],
        )
    )

    zero_rule = (
        alt.Chart(pd.DataFrame({"y": [0.0]}))
        .mark_rule(color="#94A3B8", strokeWidth=1.2)
        .encode(y=alt.Y("y:Q", scale=y_scale))
    )

    baseline_rule = (
        alt.Chart(pd.DataFrame({"y": [curr_net_profit], "說明": [f"現況基準淨利 ${int(round(curr_net_profit)):,}"]}))
        .mark_rule(strokeDash=[5, 4], color=COLOR_SLATE, strokeWidth=1.5)
        .encode(
            y=alt.Y("y:Q", scale=y_scale),
            tooltip=[alt.Tooltip("說明:N", title="基準線")],
        )
    )

    pos_labels = (
        alt.Chart(df_prof[df_prof["實質月淨利"] >= 0])
        .mark_text(dy=-10, fontSize=11, fontWeight="bold", color="#1E293B")
        .encode(
            x=alt.X("方案:N", sort=None),
            y=alt.Y("實質月淨利:Q", scale=y_scale),
            text="標籤:N",
        )
    )

    neg_labels = (
        alt.Chart(df_prof[df_prof["實質月淨利"] < 0])
        .mark_text(dy=14, fontSize=11, fontWeight="bold", color=COLOR_RED)
        .encode(
            x=alt.X("方案:N", sort=None),
            y=alt.Y("實質月淨利:Q", scale=y_scale),
            text="標籤:N",
        )
    )

    return (
        (zero_rule + bars + baseline_rule + pos_labels + neg_labels)
        .properties(
            title="圖 B：若營業額未成長，增員對單月實質淨利之稀釋衝擊",
            width="container",
            height=chart_height,
            autosize=alt.AutoSizeParams(type="fit", contains="padding"),
        )
        .configure_view(strokeWidth=0)
        .configure_title(color=COLOR_NAVY, fontSize=13, anchor="start", offset=8)
        .configure_axis(gridColor="#E2E8F0", labelColor="#334155", titleColor=COLOR_NAVY)
    )


# =============================================================================
# 5. 主程式介面與互動式決策流程
# =============================================================================
def main() -> None:
    st.set_page_config(page_title="餐飲門市人事成本最適資源配置系統", layout="wide")
    inject_presentation_theme()

    params = load_empirical_parameters()
    ft_mult = float(params["ft_legal_multiplier"])
    pt_mult = float(params["pt_legal_multiplier"])
    ft_std_hours = int(params["ft_standard_hours"])
    min_monthly_wage = int(params["min_monthly_wage"])
    min_hourly_wage = int(params["min_hourly_wage"])
    wage_year = int(params["latest_wage_year"])
    cost_year = int(params["latest_cost_year"])
    vat_threshold = float(params["vat_threshold"])
    prime_warn_th = float(params["prime_cost_warning_threshold"])
    idle_th = float(params["splh_idle_threshold"])
    opt_up = float(params["splh_optimal_upper"])
    cm_crit = float(params["cm_critical_threshold"])
    cm_low_warn = float(params["cm_low_warning_threshold"])

    st.title("多請一個人到底划不划算？—— 餐飲門市人事成本最適資源配置系統")
    st.caption(
        "系統架構：整合 6 份標準寬表 CSV 動態基準、固定營業費用（租金＋折舊）分離引擎、邊際貢獻率斷路防禦與雙維度產能／損益兩平（R1）決策模型。"
    )
    st.divider()

    # =========================================================================
    # 左側邊欄：1. 門市營收與成本結構 -> 2. 現有勞動編制 -> 3. 增員擴張方案
    # =========================================================================
    with st.sidebar:
        st.header("1. 門市營收與成本結構")

        monthly_options: dict[str, dict[str, float]] = params["monthly_options"]
        option_keys = list(monthly_options.keys())
        selected_preset = st.selectbox(
            "載入 POS 實測月營收基準情境",
            options=option_keys,
            index=0,
            help="自動自 clean_store_monthly_2025_2026.csv 讀取各月與淡旺季實測基準，亦可於下方手動微調。",
        )
        preset_rev = int(round(monthly_options[selected_preset]["revenue"]))
        preset_cogs = float(monthly_options[selected_preset]["cogs_pct"])

        current_revenue = float(
            st.number_input(
                "基準含稅月營業額 (元)",
                min_value=50000,
                max_value=10000000,
                value=preset_rev,
                step=10000,
            )
        )

        cogs_pct = float(
            st.slider(
                "食材成本率 (%)",
                min_value=15.0,
                max_value=65.0,
                value=preset_cogs,
                step=0.25,
            )
        )
        cogs_amount = current_revenue * (cogs_pct / 100.0)
        st.caption(f"對應食材成本金額：**${int(round(cogs_amount)):,} 元/月**")

        st.markdown("---")
        separate_fixed_opex = st.toggle(
            "啟用「固定營業費用分離開關」（獨立租金與折舊，消除 R1 高估偏誤）",
            value=True,
            help=(
                f"依據 {cost_year} 年宏觀成本結構表，店面租金（約 {params['macro_rent_pct']:.2f}%）"
                f"與折舊（約 {params['macro_dep_pct']:.2f}%）不隨單月營收線性膨脹。"
                "開啟後將獨立為固定金額，僅變動營業費用隨營收變動，徹底消除傳統線性費率對 R1 之高估偏誤。"
            ),
        )

        if separate_fixed_opex:
            c_f1, c_f2 = st.columns(2)
            fixed_rent_amt = float(
                c_f1.number_input(
                    "固定月租金 (元)",
                    min_value=0,
                    max_value=2000000,
                    value=int(params["default_fixed_rent"]),
                    step=1000,
                    help=f"預設依基準月營收 × 宏觀租金率 {params['macro_rent_pct']:.2f}% 動態推算",
                )
            )
            fixed_dep_amt = float(
                c_f2.number_input(
                    "固定月折舊 (元)",
                    min_value=0,
                    max_value=1000000,
                    value=int(params["default_fixed_dep"]),
                    step=500,
                    help=f"預設依基準月營收 × 宏觀折舊率 {params['macro_dep_pct']:.2f}% 動態推算",
                )
            )
            fixed_opex_amt = fixed_rent_amt + fixed_dep_amt
            fixed_opex_equiv_pct = (fixed_opex_amt / current_revenue * 100.0) if current_revenue > 0 else 0.0

            var_opex_pct = float(
                st.slider(
                    "變動營業費用率 (%)（水電瓦斯、包材、雜支）",
                    min_value=0.0,
                    max_value=50.0,
                    value=float(params["default_var_opex_pct"]),
                    step=0.25,
                    help="已扣除獨立固定租金與折舊後，隨營業額線性變動之營業費用率。",
                )
            )
            var_opex_amt = current_revenue * (var_opex_pct / 100.0)
            total_opex_amt = fixed_opex_amt + var_opex_amt
            total_opex_equiv_pct = fixed_opex_equiv_pct + var_opex_pct
            st.caption(
                f"固定營業費用：**${int(round(fixed_opex_amt)):,} 元/月**（佔現況營收 `{fixed_opex_equiv_pct:.2f}%`）｜ "
                f"變動營業費用：**${int(round(var_opex_amt)):,} 元/月**（`{var_opex_pct:.2f}%`）｜ "
                f"合計：**${int(round(total_opex_amt)):,} 元/月**（`{total_opex_equiv_pct:.2f}%`）"
            )
        else:
            fixed_rent_amt = 0.0
            fixed_dep_amt = 0.0
            fixed_opex_amt = 0.0
            fixed_opex_equiv_pct = 0.0
            var_opex_pct = float(
                st.slider(
                    "其他營業費用率 (%)（含租金、折舊與變動雜支之單一線性假設）",
                    min_value=0.0,
                    max_value=50.0,
                    value=float(params["default_total_opex_pct"]),
                    step=0.25,
                    help="傳統單一線性費率模式：假設所有非人事、非食材營業費用皆隨營收同比例放大（將保守高估 R1 門檻）。",
                )
            )
            var_opex_amt = current_revenue * (var_opex_pct / 100.0)
            total_opex_amt = var_opex_amt
            total_opex_equiv_pct = var_opex_pct
            st.caption(f"對應其他營業費用金額：**${int(round(total_opex_amt)):,} 元/月**（全數視為線性變動）")

        st.markdown("---")
        tax_mode = st.selectbox(
            f"營業稅制（含 {int(vat_threshold // 10000)} 萬元起徵點階梯斷層）",
            [TAX_MODE_NONE, TAX_MODE_AUTO, TAX_MODE_VAT5],
            index=2,
        )
        curr_alpha = get_tax_alpha(current_revenue, tax_mode, params)
        tax_deduction = current_revenue * (1.0 - curr_alpha)
        curr_cm_rate = calc_marginal_contribution_rate(curr_alpha, cogs_pct, var_opex_pct)
        st.caption(
            f"除稅係數 α = `{curr_alpha:.4f}` ｜ 稅負扣除額：**${int(round(tax_deduction)):,} 元/月** ｜ "
            f"邊際貢獻率 CM = **`{curr_cm_rate * 100.0:.2f}%`**"
        )
        if curr_cm_rate <= cm_crit:
            st.error(
                f"🚨 **邊際貢獻率 ≤ 0 防禦觸發（CM = {curr_cm_rate * 100.0:.2f}%）**："
                "目前「食材成本率＋變動營業費用率」已超過或等於除稅營收比例，每多做 1 元營收皆產生零或負毛利，R1 斷路保護已啟動。"
            )
        elif curr_cm_rate <= cm_low_warn:
            st.warning(
                f"⚠️ **極低邊際貢獻率警戒（CM = {curr_cm_rate * 100.0:.2f}% ≤ 5%）**："
                "單位營收邊際貢獻極薄，R1 損益兩平乘數將呈指數級放大。"
            )

        st.divider()
        st.header("2. 現有勞動編制")
        st.info(
            "**模型計算規範**：\n"
            f"1. **本模型不計算加班費**：每位正職固定以每月標準工時 {ft_std_hours} 小時計入。\n"
            f"2. **請直接填寫「名目薪資」**：法遵下限自動對齊 {wage_year} 年基本工資（月薪 ${min_monthly_wage:,}、時薪 ${min_hourly_wage}），"
            f"系統於後端自動加計雇主勞健保與法定負擔（正職 ×{ft_mult:.2f}、兼職 ×{pt_mult:.2f}）。"
        )

        st.markdown("**現有正職（全職）配置**")
        default_ft_list: list[int] = list(params["default_ft_salaries"])
        default_ft_count = len(default_ft_list)
        default_ft_mean = int(round(float(np.mean(default_ft_list)))) if default_ft_count > 0 else min_monthly_wage

        ft_count = int(
            st.number_input("現有正職人數 (人)", min_value=0, max_value=50, value=default_ft_count, step=1)
        )
        ft_base_salary = int(
            st.number_input(
                "正職平均名目月薪 (元/人)",
                min_value=min_monthly_wage,
                max_value=100000,
                value=max(min_monthly_wage, default_ft_mean),
                step=1000,
                help=(
                    f"門市實測 {default_ft_count} 名正職名目月薪分別為 "
                    f"{'、'.join(f'{s:,}' for s in default_ft_list)} 元"
                    f"（合計 {sum(default_ft_list):,} 元，平均 {np.mean(default_ft_list):,.2f} 元/人）"
                ),
            )
        )

        st.markdown("**現有兼職配置（依人數逐一設定時薪與時數）**")
        default_curr_pt_h: list[int] = list(params["default_curr_pt_hours"])
        pt_count = int(
            st.number_input("現有兼職人數 (人)", min_value=0, max_value=20, value=len(default_curr_pt_h), step=1)
        )

        default_pt_wage = min_hourly_wage + int(params["default_pt_hourly_offset"])
        curr_pt_nominal = 0.0
        curr_pt_hours = 0.0
        for i in range(pt_count):
            label = chr(65 + i) if i < 26 else str(i + 1)
            default_h = default_curr_pt_h[i] if i < len(default_curr_pt_h) else 40
            c_w, c_h = st.columns(2)
            w_i = c_w.number_input(
                f"兼職 {label} 名目時薪",
                min_value=min_hourly_wage,
                max_value=500,
                value=default_pt_wage,
                step=5,
                key=f"curr_pt_w_{i}",
            )
            h_i = c_h.number_input(
                f"兼職 {label} 月工時(hr)",
                min_value=0,
                max_value=220,
                value=int(default_h),
                step=5,
                key=f"curr_pt_h_{i}",
            )
            curr_pt_nominal += float(w_i * h_i)
            curr_pt_hours += float(h_i)

        curr_ft_nominal = (
            float(sum(default_ft_list))
            if (ft_count == default_ft_count and ft_base_salary == default_ft_mean)
            else float(ft_count * ft_base_salary)
        )
        curr_ft_cost = curr_ft_nominal * ft_mult
        curr_pt_cost = curr_pt_nominal * pt_mult
        curr_total_labor = curr_ft_cost + curr_pt_cost
        curr_labor_pct = (curr_total_labor / current_revenue * 100.0) if current_revenue > 0 else 0.0

        st.markdown(
            f"""
            **現況人事成本彙總（含勞健保法定負擔）**：
            - 實質人事總成本：**`${int(round(curr_total_labor)):,} 元/月`** *(名目薪資 `${int(round(curr_ft_nominal + curr_pt_nominal)):,}` ＋ 法定負擔 `${int(round(curr_total_labor - curr_ft_nominal - curr_pt_nominal)):,}`)*
            - 佔含稅營業額比例：**`{curr_labor_pct:.2f}%`**
            """
        )

        st.divider()
        st.header("3. 增員擴張方案設定")
        st.caption(f"名目薪資下限已動態綁定 {wage_year} 年法定基本工資，系統自動加計雇主法定負擔。")

        st.markdown(f"**【方案 A】增聘正職人力（固定月標準工時 {ft_std_hours}hr）**")
        default_new_ft_sal = min_monthly_wage + int(params["default_new_ft_offset"])
        new_ft_salary = float(
            st.number_input(
                "擬增聘 1 名正職之名目月薪 (元)",
                min_value=min_monthly_wage,
                max_value=100000,
                value=default_new_ft_sal,
                step=1000,
            )
        )
        delta_cost_A = new_ft_salary * ft_mult
        st.caption(
            f"方案 A 新增實質人事成本：**+${int(round(delta_cost_A)):,} 元/月**（名目 ${int(new_ft_salary):,} × {ft_mult:.2f}）"
        )

        st.markdown("**【方案 B】增聘兼職人力（個別設定不同時數與時薪）**")
        default_add_pt_h: list[int] = list(params["default_add_pt_hours"])
        add_pt_count = int(
            st.number_input("擬增聘兼職人數 (人)", min_value=1, max_value=10, value=len(default_add_pt_h), step=1)
        )
        add_pt_nominal = 0.0
        add_pt_hours = 0.0
        for j in range(add_pt_count):
            label_b = chr(65 + j) if j < 26 else str(j + 1)
            default_add_h = default_add_pt_h[j] if j < len(default_add_pt_h) else 40
            cb_w, cb_h = st.columns(2)
            wb_j = cb_w.number_input(
                f"新增兼職 {label_b} 時薪",
                min_value=min_hourly_wage,
                max_value=500,
                value=default_pt_wage,
                step=5,
                key=f"add_pt_w_{j}",
            )
            hb_j = cb_h.number_input(
                f"新增兼職 {label_b} 月工時",
                min_value=5,
                max_value=220,
                value=int(default_add_h),
                step=5,
                key=f"add_pt_h_{j}",
            )
            add_pt_nominal += float(wb_j * hb_j)
            add_pt_hours += float(hb_j)

        delta_cost_B = add_pt_nominal * pt_mult
        st.caption(
            f"方案 B 新增實質人事成本：**+${int(round(delta_cost_B)):,} 元/月**（共 +{int(add_pt_hours)} hr，含 × {pt_mult:.2f} 法定負擔）"
        )

    # =========================================================================
    # 核心財務、固定費用分離偏誤修正與產能指標運算
    # =========================================================================
    curr_net_rev = current_revenue * curr_alpha
    curr_tax_amt = current_revenue - curr_net_rev
    curr_cogs_amt = current_revenue * (cogs_pct / 100.0)

    curr_total_hours = (ft_count * ft_std_hours) + curr_pt_hours
    curr_splh = current_revenue / curr_total_hours if curr_total_hours > 0 else 0.0
    curr_net_profit = calc_net_profit(
        current_revenue, cogs_pct, var_opex_pct, fixed_opex_amt, curr_total_labor, tax_mode, params
    )
    curr_net_margin = (curr_net_profit / current_revenue * 100.0) if current_revenue > 0 else 0.0
    curr_prime_cost_pct = cogs_pct + curr_labor_pct

    # 檢查邊際貢獻率狀態（考慮自動起徵點模式下 >20萬 區段之邊際貢獻率）
    target_alpha_for_r1 = (
        float(params["alpha_exempt"])
        if tax_mode == TAX_MODE_NONE
        else float(params["alpha_vat5"])
    )
    effective_cm_rate = calc_marginal_contribution_rate(curr_alpha, cogs_pct, var_opex_pct)
    vat5_cm_rate = calc_marginal_contribution_rate(target_alpha_for_r1, cogs_pct, var_opex_pct)

    # 方案 A（增聘 1 名正職）
    labor_A = curr_total_labor + delta_cost_A
    hours_A = curr_total_hours + ft_std_hours
    splh_A_same_rev = current_revenue / hours_A if hours_A > 0 else 0.0
    profit_A_same_rev = calc_net_profit(
        current_revenue, cogs_pct, var_opex_pct, fixed_opex_amt, labor_A, tax_mode, params
    )
    r1_A = calc_r1_threshold(
        curr_net_profit, fixed_opex_amt, labor_A, cogs_pct, var_opex_pct, tax_mode, params
    )
    valid_r1_A = (not math.isnan(r1_A)) and (r1_A > 0.0)
    req_growth_A = ((r1_A - current_revenue) / current_revenue * 100.0) if valid_r1_A else float("nan")
    splh_A_at_r1 = (r1_A / hours_A) if (valid_r1_A and hours_A > 0) else float("nan")

    # 方案 B（增聘兼職組合）
    labor_B = curr_total_labor + delta_cost_B
    hours_B = curr_total_hours + add_pt_hours
    splh_B_same_rev = current_revenue / hours_B if hours_B > 0 else 0.0
    profit_B_same_rev = calc_net_profit(
        current_revenue, cogs_pct, var_opex_pct, fixed_opex_amt, labor_B, tax_mode, params
    )
    r1_B = calc_r1_threshold(
        curr_net_profit, fixed_opex_amt, labor_B, cogs_pct, var_opex_pct, tax_mode, params
    )
    valid_r1_B = (not math.isnan(r1_B)) and (r1_B > 0.0)
    req_growth_B = ((r1_B - current_revenue) / current_revenue * 100.0) if valid_r1_B else float("nan")
    splh_B_at_r1 = (r1_B / hours_B) if (valid_r1_B and hours_B > 0) else float("nan")

    # 計算「未分離固定費用（全數視為線性費率）」之對照 R1，量化高估偏誤消除量
    r1_A_unseparated = calc_r1_threshold(
        curr_net_profit, 0.0, labor_A, cogs_pct, total_opex_equiv_pct, tax_mode, params
    )
    r1_B_unseparated = calc_r1_threshold(
        curr_net_profit, 0.0, labor_B, cogs_pct, total_opex_equiv_pct, tax_mode, params
    )
    bias_saved_A = (
        (r1_A_unseparated - r1_A)
        if (separate_fixed_opex and valid_r1_A and not math.isnan(r1_A_unseparated))
        else 0.0
    )
    bias_saved_B = (
        (r1_B_unseparated - r1_B)
        if (separate_fixed_opex and valid_r1_B and not math.isnan(r1_B_unseparated))
        else 0.0
    )

    _, splh_short_tag, _ = classify_splh_status(curr_splh, params)

    # =========================================================================
    # 主畫面第一區塊：未增員前現況診斷
    # =========================================================================
    st.subheader("一、 現況體質診斷（未增加人手前｜整合側邊欄 1＋2）")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "基準含稅月營業額",
        f"${int(round(current_revenue)):,} 元",
        delta=f"未增員總工時 {int(curr_total_hours)} hr/月",
        delta_color="off",
    )
    k2.metric(
        "現況實質人事總成本",
        f"${int(round(curr_total_labor)):,} 元",
        delta=f"佔營收 {curr_labor_pct:.1f}% ｜ 主要成本率 {curr_prime_cost_pct:.1f}%",
        delta_color="off",
    )
    k3.metric(
        "現況實質營業淨利",
        f"${int(round(curr_net_profit)):,} 元",
        delta=f"實質淨利率 {curr_net_margin:.2f}% ｜ 邊際貢獻率 {effective_cm_rate * 100.0:.2f}%",
        delta_color="normal" if (curr_net_profit >= 0 and effective_cm_rate > 0) else "inverse",
    )
    k4.metric(
        "現況每工時產值 (SPLH)",
        f"{curr_splh:.0f} 元/hr",
        delta=splh_short_tag,
        delta_color="off",
    )

    if separate_fixed_opex:
        opex_detail_html = (
            f"變動營業費用 <strong>${int(round(var_opex_amt)):,}</strong> 元（{var_opex_pct:.2f}%）、"
            f"獨立固定營業費用 <strong>${int(round(fixed_opex_amt)):,}</strong> 元"
            f"（租金 ${int(round(fixed_rent_amt)):,} ＋ 折舊 ${int(round(fixed_dep_amt)):,}，合計佔現況營收 {fixed_opex_equiv_pct:.2f}%）"
        )
    else:
        opex_detail_html = (
            f"單一線性其他營業費用 <strong>${int(round(total_opex_amt)):,}</strong> 元（{total_opex_equiv_pct:.2f}%，未分離固定租金與折舊）"
        )

    st.markdown(
        f"""
        <div class="cost-breakdown-banner">
            <strong>未增員前單月收支結構拆解</strong>：
            含稅營業額 <strong>${int(round(current_revenue)):,}</strong> 元 － 營業稅負 <strong>${int(round(curr_tax_amt)):,}</strong> 元（α = {curr_alpha:.4f}）＝ 除稅實質營收 <strong>${int(round(curr_net_rev)):,}</strong> 元；
            扣除食材成本 <strong>${int(round(curr_cogs_amt)):,}</strong> 元（{cogs_pct:.2f}%）、{opex_detail_html} 及實質人事成本 <strong>${int(round(curr_total_labor)):,}</strong> 元（{curr_labor_pct:.2f}%）後，
            <strong>實質營業淨利為 ${int(round(curr_net_profit)):,} 元（{curr_net_margin:.2f}%）</strong>，單位營收邊際貢獻率（CM）為 <strong>{effective_cm_rate * 100.0:.2f}%</strong>。
        </div>
        """,
        unsafe_allow_html=True,
    )

    if separate_fixed_opex and valid_r1_A and bias_saved_A > 0:
        bias_pct_A = (bias_saved_A / r1_A_unseparated * 100.0) if r1_A_unseparated > 0 else 0.0
        bias_pct_B = (bias_saved_B / r1_B_unseparated * 100.0) if (valid_r1_B and r1_B_unseparated > 0) else 0.0
        st.markdown(
            f"""
            <div class="r1-bias-banner">
                <strong>固定營業費用分離開關已生效（消除 R1 線性高估偏誤）</strong>：
                將每月固定店租（${int(round(fixed_rent_amt)):,}）與折舊（${int(round(fixed_dep_amt)):,}）自變動費率抽離後，
                邊際貢獻率由傳統線性假設之 <strong>{(curr_alpha - (cogs_pct + total_opex_equiv_pct) / 100.0) * 100.0:.2f}%</strong> 修正還原為真實之 <strong>{effective_cm_rate * 100.0:.2f}%</strong>；
                方案 A 之 R1 門檻由未分離前之 ${int(round(r1_A_unseparated)):,} 元下修至 <strong>${int(round(r1_A)):,} 元（消除高估偏誤 -${int(round(bias_saved_A)):,} 元 / -{bias_pct_A:.2f}%）</strong>，
                方案 B 之 R1 門檻下修至 <strong>${int(round(r1_B)):,} 元（消除高估偏誤 -${int(round(bias_saved_B)):,} 元 / -{bias_pct_B:.2f}%）</strong>。
            </div>
            """,
            unsafe_allow_html=True,
        )

    # =========================================================================
    # 主畫面第二區塊：納入增員方案邊際效應對照
    # =========================================================================
    st.subheader("二、 增員方案邊際效應對照（加入側邊欄 3｜方案 A 正職 vs. 方案 B 兼職）")

    is_cm_non_positive = (effective_cm_rate <= cm_crit) or (vat5_cm_rate <= cm_crit) or (not valid_r1_A) or (not valid_r1_B)
    if is_cm_non_positive:
        min_cost_cut_needed = max(
            0.0,
            ((cogs_pct + var_opex_pct) / 100.0 - min(curr_alpha, target_alpha_for_r1) + 0.01) * 100.0,
        )
        st.error(
            f"🚨 **【結構性倒掛防禦警示：邊際貢獻率 CM ≤ 0%】**\n\n"
            f"- **數學診斷**：目前邊際貢獻率 $\\text{{CM}} = \\alpha ({curr_alpha:.4f}) - [\\text{{食材成本率}} ({cogs_pct:.2f}\\%) + \\text{{變動營業費用率}} ({var_opex_pct:.2f}\\%)] = \\mathbf{{{effective_cm_rate * 100.0:.2f}\\% \\le 0\\%}}$"
            f"{f'（跨過起徵點 5% 稅率區段 CM = {vat5_cm_rate * 100.0:.2f}%）' if tax_mode == TAX_MODE_AUTO else ''}。\n"
            f"- **經濟意義**：每多創造 1 元營業額，扣除營業稅、食材與變動費用後之邊際毛利為 **`{effective_cm_rate:.4f} 元`（越賣越虧）**，不論營業額放大至任何規模皆無法打平新增人事成本（$\\lim_{{\\text{{CM}} \\to 0^+}} R_1 = \\infty$）。\n"
            f"- **強制處置**：系統已鎖定 $R_1$ 為「無法收斂 (CM ≤ 0% 邊際虧損)」，請立即將「食材成本率＋變動營業費用率」合計至少下調 **`{min_cost_cut_needed:.2f}` 個百分點** 以上，使邊際貢獻率重返正值區間。"
        )
    elif effective_cm_rate <= cm_low_warn:
        st.warning(
            f"⚠️ **【極端敏感度防禦警戒：0% < 邊際貢獻率 CM ({effective_cm_rate * 100.0:.2f}%) ≤ 5%】**\n\n"
            f"目前每 1 元營收僅能貢獻 `{effective_cm_rate * 100.0:.2f}%` 邊際毛利，代表每增加 1 元人事成本需放大 **`{1.0 / effective_cm_rate:.1f} 倍`** 營業額方能打平，推算之 $R_1$ 已超出一般門市物理產能上限。"
        )

    col_baseline_name = "未增員現況 (Baseline)"
    col_a_name = f"方案 A：增聘 1 名正職 (+{ft_std_hours} hr)"
    col_b_name = f"方案 B：增聘 {int(add_pt_count)} 名兼職 (+{int(add_pt_hours)} hr)"

    comp_df = pd.DataFrame(
        {
            "核心決策指標": [
                "1. 每月總投入工時",
                "2. 單月新增實質人事成本 (含勞健保)",
                "3. 每月實質人事總成本 / 佔現況營收比",
                "4. 單位營收邊際貢獻率 (CM = α - 食材% - 變動費用%)",
                "5. 若營業額未成長之實質月淨利",
                "6. 若營業額未成長之每工時產值 (SPLH)",
                "7. 打平新增人事之最低含稅月營收門檻 (R1)",
                "8. 固定費用分離消除之 R1 高估偏誤",
                "9. 達成 R1 門檻所需最低營收成長率",
                "10. 達成 R1 門檻時之每工時產值 (SPLH)",
            ],
            col_baseline_name: [
                f"{int(curr_total_hours)} hr",
                "0 元（基準線）",
                f"${int(round(curr_total_labor)):,} 元（{curr_labor_pct:.1f}%）",
                f"{effective_cm_rate * 100.0:.2f}%",
                f"${int(round(curr_net_profit)):,} 元",
                f"{curr_splh:.0f} 元/hr",
                f"${int(round(current_revenue)):,} 元" if not is_cm_non_positive else "結構性倒掛 (CM ≤ 0%)",
                "0 元（基準線）",
                "0.0%" if not is_cm_non_positive else "—",
                f"{curr_splh:.0f} 元/hr" if not is_cm_non_positive else "—",
            ],
            col_a_name: [
                f"{int(hours_A)} hr",
                f"+${int(round(delta_cost_A)):,} 元/月",
                f"${int(round(labor_A)):,} 元（{labor_A / current_revenue * 100.0:.1f}%）",
                f"{effective_cm_rate * 100.0:.2f}%",
                f"${int(round(profit_A_same_rev)):,} 元",
                f"{splh_A_same_rev:.0f} 元/hr",
                f"${int(round(r1_A)):,} 元" if valid_r1_A else "無法收斂 (CM ≤ 0% 邊際虧損)",
                (
                    f"-${int(round(bias_saved_A)):,} 元"
                    if (separate_fixed_opex and valid_r1_A and bias_saved_A > 0)
                    else ("未啟用分離" if not separate_fixed_opex else "—")
                ),
                f"+{req_growth_A:.1f}%" if valid_r1_A else "—",
                f"{splh_A_at_r1:.0f} 元/hr" if valid_r1_A else "—",
            ],
            col_b_name: [
                f"{int(hours_B)} hr",
                f"+${int(round(delta_cost_B)):,} 元/月",
                f"${int(round(labor_B)):,} 元（{labor_B / current_revenue * 100.0:.1f}%）",
                f"{effective_cm_rate * 100.0:.2f}%",
                f"${int(round(profit_B_same_rev)):,} 元",
                f"{splh_B_same_rev:.0f} 元/hr",
                f"${int(round(r1_B)):,} 元" if valid_r1_B else "無法收斂 (CM ≤ 0% 邊際虧損)",
                (
                    f"-${int(round(bias_saved_B)):,} 元"
                    if (separate_fixed_opex and valid_r1_B and bias_saved_B > 0)
                    else ("未啟用分離" if not separate_fixed_opex else "—")
                ),
                f"+{req_growth_B:.1f}%" if valid_r1_B else "—",
                f"{splh_B_at_r1:.0f} 元/hr" if valid_r1_B else "—",
            ],
        }
    )
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    # =========================================================================
    # 主畫面第三區塊：系統交付決策結論
    # =========================================================================
    st.subheader("三、 系統交付決策結論：產能區間定位與淨利稀釋視覺化")

    if curr_prime_cost_pct > prime_warn_th:
        st.warning(
            f"**現況雙維度診斷**：SPLH 為 **`{curr_splh:.0f} 元/hr`**（{splh_short_tag}），但主要成本率（食材＋實質人事）已達 **`{curr_prime_cost_pct:.1f}%` > `{prime_warn_th:.0f}%` 警戒線**，財務上不宜再承擔高額固定正職薪資。"
        )
    else:
        st.info(
            f"**現況雙維度診斷**：SPLH 為 **`{curr_splh:.0f} 元/hr`**（{splh_short_tag}），主要成本率為 **`{curr_prime_cost_pct:.1f}%`**（低於 `{prime_warn_th:.0f}%` 安全上限）。"
        )

    matched_chart_height = max(255, (4 if valid_r1_A else 3) * 58)
    c_viz1, c_viz2 = st.columns(2)
    with c_viz1:
        splh_chart = build_splh_zone_chart(
            curr_splh=curr_splh,
            splh_B_same_rev=splh_B_same_rev,
            splh_A_same_rev=splh_A_same_rev,
            splh_A_at_r1=splh_A_at_r1,
            valid_r1_A=valid_r1_A,
            params=params,
        )
        st.altair_chart(splh_chart, use_container_width=True)

    with c_viz2:
        profit_chart = build_profit_impact_chart(
            curr_net_profit=curr_net_profit,
            profit_B_same_rev=profit_B_same_rev,
            profit_A_same_rev=profit_A_same_rev,
            chart_height=matched_chart_height,
        )
        st.altair_chart(profit_chart, use_container_width=True)

    r1_a_str = f"**`${int(round(r1_A)):,} 元`（`+{req_growth_A:.1f}%`）**" if valid_r1_A else "`無法收斂 (CM ≤ 0%)`"
    r1_b_str = f"**`${int(round(r1_B)):,} 元`（`+{req_growth_B:.1f}%`）**" if valid_r1_B else "`無法收斂 (CM ≤ 0%)`"

    if is_cm_non_positive:
        st.error(
            f"**最終交付行動建議（【邊際貢獻率 CM ≤ 0% 斷路防禦】全面禁止增員與盲目擴張營收）**：\n"
            f"1. **停止增員與促銷擴張**：目前單位營收邊際貢獻率為 **`{effective_cm_rate * 100.0:.2f}%`**（$\\le 0\\%$），每增加 1 元營收將擴大虧損，任何增員方案均無法透過拉升營業額打平。\n"
            f"2. **優先重構變動成本結構**：立即盤點菜單定價、食材耗損率（目前 `{cogs_pct:.2f}%`）與變動費用率（目前 `{var_opex_pct:.2f}%`），優先下調高食材成本品項或調升毛利結構。\n"
            f"3. **重啟排班評估前提**：待邊際貢獻率修復至 `> 15%` 且主要成本率受控後，再重新評估兼職（方案 B）或正職（方案 A）擴編計畫。"
        )
    elif curr_splh < idle_th:
        target_rev_idle = curr_total_hours * idle_th
        excess_hours = curr_total_hours - (current_revenue / idle_th)
        st.error(
            f"**最終交付行動建議（禁止增員・優先排班瘦身或拉升營收）**：\n"
            f"1. **紅線警示**：現況 SPLH 僅 `{curr_splh:.0f} 元/hr`（已落入 `<{int(idle_th)}` 閒置帶），若再增員將使淨利進一步惡化。\n"
            f"2. **控費路徑**：縮減離峰兼職約 **`{int(round(excess_hours))} 小時/月`**（對齊 POS 離峰時段僅佔全店營收 `{params['offpeak_slot_rev_share_pct']:.1f}%` 之特徵），即可使 SPLH 重返 `{int(idle_th)} 元/hr` 健康門檻。\n"
            f"3. **開源路徑**：維持現有編制不增員，將含稅月營業額拉升至 **`${int(round(target_rev_idle)):,} 元`** 以上。"
        )
    elif curr_splh <= opt_up:
        max_rev_opt = curr_total_hours * opt_up
        st.success(
            f"**最終交付行動建議（不增員衝刺最大邊際利潤・擴編優先採方案 B 兼職）**：\n"
            f"1. **現有團隊最大潛力**：在不增員下，現有 `{int(curr_total_hours)} hr` 最高可承載月營收 **`${int(round(max_rev_opt)):,} 元`**（觸及 `{int(opt_up)} 元/hr` 上限），此區間邊際淨利最高。\n"
            f"2. **避開正職稀釋陷阱**：若盲目採【方案 A 增正職】，在營收未成長下 SPLH 將跌至 `{splh_A_same_rev:.0f} 元/hr`（閒置帶），且月淨利由 `${int(round(curr_net_profit)):,}` 驟降至 **`${int(round(profit_A_same_rev)):,} 元`**，必須增收至 {r1_a_str} 才能打平。\n"
            f"3. **最適擴編路徑**：針對雙尖峰時段（佔全店營收 `{params['peak_slot_rev_share_pct']:.1f}%`）與假日（日均營收為平日之 `{params['weekend_weekday_ratio']:.2f} 倍`）吃緊需求，優先採 **【方案 B 增兼職】**（損益門檻僅需 {r1_b_str}），待月營收穩定站穩 {r1_a_str} 以上再轉聘正職。"
        )
    else:
        min_add_hours_for_opt = (current_revenue / opt_up) - curr_total_hours
        st.warning(
            f"**最終交付行動建議（產能已超載・須增補至少 `{int(round(min_add_hours_for_opt))} hr/月`）**：\n"
            f"1. **產能診斷**：現況 SPLH 達 `{curr_splh:.0f} 元/hr`（突破 `{int(opt_up)}` 過勞警戒線），現場存在出餐延遲與掉單風險。\n"
            f"2. **方案門檻比較**：採【方案 B 增兼職】打平門檻為 {r1_b_str}（增員後 SPLH `{splh_B_same_rev:.0f} 元/hr`）；採【方案 A 增正職】打平門檻為 {r1_a_str}（增員後 SPLH `{splh_A_same_rev:.0f} 元/hr`）。\n"
            f"3. **執行策略**：{'優先以【方案 B 兼職】精準填補早午餐雙尖峰與假日缺口，避免離峰工時閒置。' if delta_cost_B < delta_cost_A else '因工時缺口已達全職規模，建議直接增聘正職穩定戰力。'}"
        )

    with st.expander("指標文獻、6 份標準寬表 CSV 動態校準依據與模型邊界條件"):
        csv_status = params["csv_status"]
        status_badges = " ｜ ".join(
            f"`{CSV_FILENAMES[k]}`: {'✅ 已載入' if v['loaded'] else '⚠️ Fallback'}"
            for k, v in csv_status.items()
        )
        st.markdown(
            f"""
            **動態資料源連線狀態（`resolve_csv_path` 檢索結果）**：
            {status_badges}

            1. **指標定義（SPLH, Sales Per Labor Hour）**：
               為國際餐飲與零售營運管理標準人效指標（美國全國餐飲協會 NRA 營運基準），計算公式為 $\\text{{SPLH}} = \\text{{含稅營業額}} \\div \\text{{總投入工時}}$，用於衡量每一小時勞動投入所創造之營收產值。
            2. **台灣餐飲業 {int(idle_th)}–{int(opt_up)} 元/hr 區間動態財務反推依據**：
               - **單位工時實質成本**：依 `clean_minimum_wage_index_2015_2026.csv` 最新 {wage_year} 年法定基準（時薪 {min_hourly_wage} 元、月薪 {min_monthly_wage:,} 元；指數分別達 {params['hourly_wage_index']:.2f} 與 {params['monthly_wage_index']:.2f}），計入雇主法定勞健保與 6% 勞退提撥後（兼職 ×{pt_mult:.2f}、正職 ×{ft_mult:.2f}），餐飲門市平均每工時實質人力成本約落於 **`225–260 元/hr`**。
               - **目標成本結構與經營困境對照**：依 `clean_cost_structure_2015_2024.csv`（{cost_year} 年平均食材成本率 `{params['macro_cogs_pct']:.2f}%`、名目人事費用率 `{params['macro_labor_pct']:.2f}%`、租金率 `{params['macro_rent_pct']:.2f}%`、折舊率 `{params['macro_dep_pct']:.2f}%`）及 `clean_operating_obstacles_2016_2024.csv`（{params['latest_obstacle_year']} 年高達 `{params['obs_labor_shortage_pct']:.1f}%` 業者面臨缺工、`{params['obs_high_turnover_pct']:.1f}%` 人員流動率高、`{params['obs_high_labor_cost_pct']:.1f}%` 人事成本過高），中小型餐飲門市健康之實質人事成本率上限約為 **`28%–35%`**（對應主要成本率 Prime Cost $\\le {prime_warn_th:.0f}\\%$）。
               - **門市 POS 實測校準**：依 `clean_store_timeslots_2025_2026.csv` 與 `clean_store_day_type_2025_2026.csv`，門市加權平均客單價為 **`{params['weighted_avg_ticket']:.1f} 元`**（平日 `{params['weekday_ticket']} 元`、假日 `{params['weekend_ticket']} 元`），雙尖峰時段（07:00–09:00、11:00–13:00）集中了 **`{params['peak_slot_rev_share_pct']:.1f}%`** 營收與 **`{params['peak_slot_traffic_share_pct']:.1f}%`** 客流，收斂設定黃金產能帶為 **`{int(idle_th)}–{int(opt_up)} 元/hr`**。
            3. **固定營業費用分離模型與邊際貢獻率防禦機制**：
               - **固定費用分離公式**：$\\Pi(R) = R \\cdot [\\alpha(R) - (c + v_{{\\text{{var}}}})] - (F_{{\\text{{rent}}}} + F_{{\\text{{dep}}}}) - L$，求解損益兩平門檻 $R_1 = \\frac{{\\Pi_0 + F_{{\\text{{rent}}}} + F_{{\\text{{dep}}}} + L_1}}{{\\alpha(R_1) - (c + v_{{\\text{{var}}}})}}$，消除傳統將店租與折舊視為變動費率所導致之 $R_1$ 虛胖偏誤。
               - **邊際貢獻率斷路保護**：當 $\\text{{CM}} = \\alpha - (c + v_{{\\text{{var}}}}) \\le 0$ 時，$\\frac{{\\partial \\Pi}}{{\\partial R}} \\le 0$，系統立即觸發斷路保護並阻斷無意義之負數或發散 $R_1$ 數值。
            """
        )


if __name__ == "__main__":
    main()