import sqlite3
import pandas as pd
from pathlib import Path
import os
import sys

# 確保路徑設定正確，指向專案根目錄
try:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
except IndexError:
    # 備用方案：如果腳本不在預期的目錄深度，嘗試以當前執行目錄作為根目錄
    PROJECT_ROOT = Path(os.getcwd())

DB_PATH = PROJECT_ROOT / "data" / "db" / "catering_benchmark.db"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

# 建立一個測試結果收集器
test_results = {"passed": 0, "failed": 0, "details": []}

def run_test(test_name, test_func):
    """執行單一測試並記錄結果"""
    print(f"Running: {test_name}...", end=" ")
    try:
        test_func()
        print("✅ Pass")
        test_results["passed"] += 1
    except AssertionError as e:
        print(f"❌ Fail ({e})")
        test_results["failed"] += 1
        test_results["details"].append(f"{test_name}: {e}")
    except Exception as e:
        print(f"💥 Error ({type(e).__name__}: {e})")
        test_results["failed"] += 1
        test_results["details"].append(f"{test_name}: {type(e).__name__}: {e}")

# ==========================================
# 測試項目定義
# ==========================================

def test_database_exists():
    assert DB_PATH.exists(), f"資料庫檔案未找到 ({DB_PATH})"

def test_fact_cost_structure_count():
    with sqlite3.connect(DB_PATH) as conn:
        count = pd.read_sql_query("SELECT count(*) FROM fact_cost_structure", conn).iloc[0, 0]
        assert count == 24, f"fact_cost_structure 筆數不符預期 (應為 24，實為 {count})"

def test_fact_operating_obstacles_count():
    with sqlite3.connect(DB_PATH) as conn:
        count = pd.read_sql_query("SELECT count(*) FROM fact_operating_obstacles", conn).iloc[0, 0]
        assert count == 45, f"fact_operating_obstacles 筆數不符預期 (應為 45，實為 {count})"

def test_dim_minimum_wage_count():
    with sqlite3.connect(DB_PATH) as conn:
        count = pd.read_sql_query("SELECT count(*) FROM dim_minimum_wage", conn).iloc[0, 0]
        assert count > 0, "dim_minimum_wage 表格為空"

def test_mart_view_validity():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM vw_catering_benchmark_mart LIMIT 5;", conn)
        assert not df.empty, "分析視圖 vw_catering_benchmark_mart 無資料或不存在"

def test_processed_csv_files():
    expected_files = [
        "clean_cost_structure_2015_2024.csv",
        "clean_operating_obstacles_2016_2024.csv",
        "clean_minimum_wage_index_2015_2026.csv",
        "clean_store_monthly_2025_2026.csv",
        "clean_store_day_type_2025_2026.csv",
        "clean_store_timeslots_2025_2026.csv"
    ]
    missing_files = []
    for f in expected_files:
        if not (PROCESSED_DIR / f).exists():
            missing_files.append(f)
    assert not missing_files, f"缺少以下 CSV 檔案: {', '.join(missing_files)}"

# ==========================================
# 執行所有測試
# ==========================================
if __name__ == "__main__":
    print("==================================================")
    print("啟動 ETL 管線與資料庫完整性測試")
    print(f"專案根目錄: {PROJECT_ROOT}")
    print("==================================================\n")

    run_test("資料庫檔案存在性", test_database_exists)
    
    # 只有在資料庫存在時才進行後續的資料庫內容測試
    if DB_PATH.exists():
        run_test("檢查 fact_cost_structure 筆數", test_fact_cost_structure_count)
        run_test("檢查 fact_operating_obstacles 筆數", test_fact_operating_obstacles_count)
        run_test("檢查 dim_minimum_wage 筆數", test_dim_minimum_wage_count)
        run_test("驗證分析視圖 vw_catering_benchmark_mart", test_mart_view_validity)
    
    run_test("檢查處理後的 CSV 檔案完整性", test_processed_csv_files)

    print("\n==================================================")
    print("測試結果摘要")
    print("==================================================")
    print(f"✅ 通過測試數: {test_results['passed']}")
    print(f"❌ 失敗測試數: {test_results['failed']}")
    
    if test_results["failed"] > 0:
        print("\n[失敗詳細資訊]")
        for detail in test_results["details"]:
            print(f"- {detail}")
        sys.exit(1) # 以錯誤碼結束
    else:
        print("\n🎉 所有測試皆已通過！系統準備就緒。")
        sys.exit(0) # 成功結束