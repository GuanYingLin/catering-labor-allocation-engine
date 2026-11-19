# 餐飲門市人事成本最適資源配置系統 (M-System)

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://catering-labor-allocation-engine.streamlit.app/)
> **線上互動決策引擎**：https://catering-labor-allocation-engine.streamlit.app/

## 專案概述
M-System 是一套針對餐飲業微觀營運開發的財務決策支援系統。透過整合經濟部歷年宏觀統計資料與門市 270 天微觀 POS 實測數據，建立邊際損益與產能評估模型，解決餐飲業在人力配置上「盲目擴張導致淨利稀釋」的核心命題。

## 商業價值與核心經濟模型
- **SPLH 產能邊界診斷**：定義每小時產值黃金區間，量化評估現有團隊承載力。
- **R1 邊際損益兩平門檻**：導入固定費用（租金與折舊）分離與統一發票使用門檻（20 萬元）稅制階梯變數，精準推算增聘正職或兼職所需之營收成長下限。
- **實質勞動成本校準**：還原雇主法定負擔乘數，消除名目薪資造成之財務偏誤與沉沒成本干擾。

## 系統架構與資料管線
系統採用端到端 (End-to-End) 邊緣運算設計，確保決策引擎具備防禦力與擴展性：

```mermaid
%%{init: {
  'theme': 'base',
  'themeVariables': {
    'primaryColor': '#ffffff',
    'primaryTextColor': '#1e293b',
    'primaryBorderColor': '#1b5e58',
    'lineColor': '#64748b',
    'fontSize': '14px',
    'fontFamily': 'Noto Sans TC, sans-serif'
  },
  'flowchart': {
    'curve': 'linear',
    'nodeSpacing': 25,
    'rankSpacing': 50
  }
}}%%
flowchart LR
   subgraph S1["<b>1. 資料擷取 (Extract)</b>"]
       direction TB
       A1["<b>門市 POS 實測</b><br/>270 營業日營運明細"]
       A2["<b>勞動部工資表 CSV</b><br/>2015–2026 歷年標準"]
       A3["<b>經濟部調查 PDF</b><br/>2015–2024 共 17 份"]
   end

   subgraph S2["<b>2. Python 清洗轉換 (Transform)</b>"]
       direction TB
       B1["<b>門市營運特徵萃取</b><br/>平假日 / 5大時段 / 淡旺季"]
       B2["<b>口徑校準與指數化</b><br/>2018 基準分隔 / 2015=100"]
       B3["<b>pdfplumber 座標解析</b><br/>跨頁斷行與代碼映射"]
   end

   subgraph S3["<b>3. 資料倉儲 (Load - SQLite)</b>"]
       direction TB
       C1[("<b>門市營運事實表 (3 Facts)</b><br/>day_type / monthly / timeslots")]
       C2[("<b>宏觀與成本表 (2 Facts + 1 Dim)</b><br/>cost_structure / obstacles / wage")]
       V1[["<b>vw_catering_benchmark_mart</b><br/>餐飲業基準分析視圖 (Data Mart)"]]
   end

   subgraph S4["<b>4. 商業交付 (Serve)</b>"]
       direction TB
       D1["<b>Streamlit 決策引擎</b><br/>雙維度產能與邊際排班"]
       D2["<b>Tableau 視覺化</b><br/>四大收支結構與波動分析"]
   end

   A1 --> B1
   A2 --> B2
   A3 --> B3
   B3 --> B2

   B1 --> C1
   B2 --> C2
   C2 --> V1

   C1 & V1 --> D1
   C2 & V1 --> D2

   style S1 fill:transparent,stroke:#1b5e58,stroke-width:2px,color:#1b5e58
   style S2 fill:transparent,stroke:#c28551,stroke-width:2px,color:#9a6234
   style S3 fill:transparent,stroke:#1b5e58,stroke-width:2px,color:#1b5e58
   style S4 fill:transparent,stroke:#c28551,stroke-width:2px,color:#9a6234

```

1. **資料擷取 (Extract)**：多源擷取官方調查 PDF、勞動部工資 CSV 與門市 POS Excel 原始檔。
2. **轉換與清洗 (Transform)**：
   - 採用像素座標群聚演算法，精準解析非結構化 PDF 跨頁表格與斷行位移。
   - 運用跨源時序對齊技術，標記 2018 營所稅推估期並將離散之法定工資調漲公告轉化為連續時間網格。
3. **資料倉儲 (Load)**：以 SQLite 建立星狀倉儲，透過分析視圖鎖定顆粒度，建立單一真實數據源 (SSOT)。
4. **決策交付 (Serve)**：結合 Streamlit 與 Tableau，提供具備防呆邊界檢核之動態參數儀表板。

## 雙軌交付架構：商業決策與底層工程
本專案採商業實證與技術開發雙軌並行，完整目錄結構與資產映射如下：

```text
catering-labor-allocation-engine/
├── data/
│   ├── raw/
│   │   ├── moea_catering_survey_2015_2024/            # 經濟部《餐飲業經營實況調查》歷年報告 (共 17 份 PDF)
│   │   ├── mol_minimum_wage_2015_2026.csv             # 勞動部歷年基本工資調整公告標準 (2015–2026)
│   │   └── 麥味登門市營運績效分析總表_2025-2026.xlsx  # 連鎖早午餐門市 270 營業日 POS 營運實測原始檔
│   ├── db/
│   │   └── catering_benchmark.db                      # 集中儲存 5 張事實表、1 張維度表與正規化分析視圖 (vw_catering_benchmark_mart)
│   └── processed/
│       ├── clean_cost_structure_2015_2024.csv
│       ├── clean_operating_obstacles_2016_2024.csv
│       ├── clean_minimum_wage_index_2015_2026.csv
│       ├── clean_store_day_type_2025_2026.csv
│       ├── clean_store_monthly_2025_2026.csv
│       └── clean_store_timeslots_2025_2026.csv
├── docs/
│   └── pitch/
│       ├── M-System.pdf                               # 結訓發表 16 頁商業與技術簡報 (含主簡報 10 頁與附錄 6 頁)
│       ├── gamma_outline.md                           # Gamma 簡報結構與 SSOT 製作規範
│       ├── speaker_notes.txt                          # 5 分鐘 (300 秒) 逐頁口語發表講稿
│       ├── qa_list.txt                                # 評審防禦問答清單 (13 題實證回應)
│       └── assets/                                    # 決策防禦實證資產庫
│           ├── 01_sqlite_schema_and_mart_view_2.jpg         # 附錄 1：SQLite 星狀倉儲與分析視圖實證
│           ├── 02_sql_left_join_timeline_verification_2.jpg # 附錄 2：跨源 LEFT JOIN 時間軸對齊實證
│           ├── 04_pdf_pixel_clustering_algorithm_2.jpg      # 附錄 3：PDF 像素座標群聚演算法實證
│           ├── 03_etl_assertion_check_len_24_2.jpg          # 附錄 4-1：防禦性 ETL 維度斷言校驗實證
│           ├── 05_etl_dynamic_path_search_2.jpg             # 附錄 4-2：白名單路徑解析與原始層歸檔實證
│           └── 06_test_pipeline_execution_result_2.jpg      # 附錄 5：自動化測試管線 Exit Code 0 全綠實證
└── src/
    ├── etl/
    │   ├── build_database.py                          # 執行 2018 營所稅口徑分隔、指數化與 SQLite 建表 (含 PDF 群聚解析)
    │   ├── etl_store_benchmark.py                     # 清洗門市 POS Excel 並同步寫入 SQLite 與標準 CSV
    │   └── test_pipeline.py                           # 自動化 ETL 管線與資料庫完整性檢驗腳本 (6 項測試)
    └── app/
        └── app.py                                     # Streamlit 互動式人力配置與損益模擬系統
```
