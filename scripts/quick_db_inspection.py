"""
scripts/quick_db_inspection.py
"""
# --- src-layout shim ---
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1] 
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.serl_processing.helpers.db_inspect import list_all_tables, describe_table, database_size, table_storage_info
from src.serl_processing.helpers.utils import connect_db, load_config

print("\n\n=== Database List ===")
print(list_all_tables())                         # inventory of objects

print("\n\n=== Database Size ===")
print(database_size())                           # file/memory overview

