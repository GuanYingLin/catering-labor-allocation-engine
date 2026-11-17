import glob
import os
import shutil
import sqlite3
import pandas as pd

# 精準搜尋指定目錄，避免全機遞迴掃描
search_dirs = [
    "data/raw",
    ".",
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Desktop"),
]
excel_path = None
for d in search_dirs:
    matches = glob.glob(os.path.join(d, "麥味登門市營運績效分析總表_2025-2026*.xlsx"))
    if matches:
        excel_path = matches[0]
        break

if not excel_path:
    raise FileNotFoundError("在 data/raw、專案根目錄、Downloads 或 Desktop 皆找不到門市 Excel 檔案。")

# 若原始檔位於 Downloads 或 Desktop，自動複製一份至 data/raw 維持唯讀原始層完整
raw_target = os.path.join("data/raw", os.path.basename(excel_path))
if os.path.abspath(excel_path) != os.path.abspath(raw_target):
    shutil.copy2(excel_path, raw_target)
    excel_path = raw_target

print(f"[INFO] 讀取原始檔案: {excel_path}")
raw = pd.read_excel(excel_path)

# 1. 月度淡旺季明細表 (第 2~13 列)
monthly = raw.iloc[2:14, 0:8].copy()
monthly.columns = [
    "month_label", "operating_days", "total_revenue",
    "uber_revenue", "uber_share", "cogs", "gross_profit", "gross_margin"
]
for col in ["total_revenue", "uber_revenue", "cogs", "gross_profit"]:
    monthly[col] = (
        monthly[col].astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .astype(float)
    )
monthly["operating_days"] = monthly["operating_days"].astype(int)
monthly["uber_share"] = monthly["uber_share"].astype(float)
monthly["gross_margin"] = monthly["gross_margin"].astype(float)
monthly["avg_daily_revenue"] = (monthly["total_revenue"] / monthly["operating_days"]).round(0)
monthly["season_tier"] = monthly["total_revenue"].apply(
    lambda x: "旺季 (≥44萬)" if x >= 440000 else ("淡季 (<35萬)" if x < 350000 else "平季")
)

# 2. 平假日營運對比表 (第 19~20 列)
day_type = raw.iloc[19:21, 0:5].copy()
day_type.columns = [
    "day_category", "operating_days", "total_revenue",
    "avg_daily_revenue", "avg_ticket_size"
]
for col in ["total_revenue", "avg_daily_revenue", "avg_ticket_size"]:
    day_type[col] = (
        day_type[col].astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .astype(float)
    )
day_type["operating_days"] = day_type["operating_days"].astype(int)

# 3. 單日各時段營業收益與客流精算表 (第 38~42 列)
timeslots = raw.iloc[38:43, 0:6].copy()
timeslots.columns = [
    "time_slot", "revenue_share", "actual_revenue",
    "traffic_share", "actual_customers", "avg_ticket_size"
]
for col in ["actual_revenue", "avg_ticket_size"]:
    timeslots[col] = (
        timeslots[col].astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .astype(float)
    )
timeslots["revenue_share"] = timeslots["revenue_share"].astype(float)
timeslots["traffic_share"] = timeslots["traffic_share"].astype(float)
timeslots["actual_customers"] = timeslots["actual_customers"].astype(int)
timeslots["slot_type"] = timeslots["time_slot"].apply(
    lambda s: "尖峰時段" if (s.startswith("07:00") or s.startswith("11:00"))
    else ("轉折時段" if s.startswith("09:00") else "離峰時段")
)

# 寫入 SQLite 資料庫
db_candidates = glob.glob("data/db/*.db") + glob.glob("data/processed/*.sqlite")
db_path = db_candidates[0] if db_candidates else "data/db/catering_benchmark.db"

conn = sqlite3.connect(db_path)
monthly.to_sql("fact_store_monthly", conn, if_exists="replace", index=False)
day_type.to_sql("fact_store_day_type", conn, if_exists="replace", index=False)
timeslots.to_sql("fact_store_timeslots", conn, if_exists="replace", index=False)
conn.close()
print(f"[INFO] 已成功寫入 SQLite 資料表至: {db_path}")

# 匯出供 Tableau 直連之標準化 CSV
out_dir = "data/processed"
monthly.to_csv(os.path.join(out_dir, "clean_store_monthly_2025_2026.csv"), index=False, encoding="utf-8-sig")
day_type.to_csv(os.path.join(out_dir, "clean_store_day_type_2025_2026.csv"), index=False, encoding="utf-8-sig")
timeslots.to_csv(os.path.join(out_dir, "clean_store_timeslots_2025_2026.csv"), index=False, encoding="utf-8-sig")
print("[SUCCESS] 已匯出 3 份乾淨 CSV 至 data/processed/ 目錄。")
