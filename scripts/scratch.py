"""
scripts/stratch.py
"""
# --- src-layout shim ---
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1] 
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_processing.helpers.db_inspect import list_all_tables, describe_table, database_size, table_storage_info
from src.serl_processing.helpers.utils import connect_db, load_config

cfg = load_config("duckdb.yaml")
con = connect_db(cfg)

print(con.execute("SELECT * FROM edition08.hh_electricity_clean LIMIT 20").fetchdf())


print(con.execute("SELECT * FROM edition08.hh_smartmeter_raw LIMIT 20").fetchdf())

print(con.execute("SELECT COUNT(Read_date) FROM edition08.hh_electricity_clean").fetchdf())