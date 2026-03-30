
# SERL Mock Data Generator  
*A lightweight framework for generating dummy SERL‑style datasets for local development, prototyping, and pipeline testing.*


## Overview
This project produces **dummy**, SERL‑inspired datasets:

### 1. **Half‑hourly Smart‑Meter Data**
Generated according to Edition 07/08‑style logic:
- UTC month cut‑off  
- BST/GMT local timestamps  
- Effective date rules  
- HH interval index (1–48)  
- Edition07‑aligned error flags  
- Gas energy conversion (CV = 39.5 MJ/m³)  
- Configurable number of households, period, seed  
- **Schema is fully hard‑coded** inside `generator_smartmeter.py`  

### 2. **SERL Contextual Data**
Generated to emulate:
- EPC dataset  
- SERL survey dataset  
- Participant summary  
- Follow‑up survey  
- List of exporters  

### 3. **PUPRN Master File**
All datasets use the **same household ID list** (`PUPRN`) ensuring cross‑dataset mergeability.

## 📁 Project Structure
```
project_root/
│
├─ config/
│   └─ serl_mock.yaml          # Single general configuration file
│
├─ data/
│   └─ mock/  
│        ├─ serl_smart_meter_hh_edition08/               # Smart‑meter CSV outputs
│        ├─ serl_follow_up_survey_2023_data_edition08    # Survey data
│        ├─ ...                                          # Other contextual outputs
│        └─ puprn_master.csv                             # Shared household IDs
│
├─ src/
│   └─ serl_mock/
│        ├─ generator_smartmeter.py           # Half-hourly generator
│        ├─ generator_contextual_data.py      # Contextual data generator
│        ├─ ids.py                            # PUPRN utilities
│        ├─ utils.py                          # Config + IO helpers
│        └─ paths.py                          # Project paths
│
└─ scripts/
     └─ generate_mock_data.py                 # Full pipeline runner
```

## Configuration
All global settings live in:
```
config/serl_mock.yaml
```
Example:
```yaml
n_households: 100
seed: 42
edition: "08"

start_year: 2019
end_year: 2019
```

## Running the Full Generation Pipeline
From the project root:
```bash
python scripts/generate_mock_data.py
```
This performs **three steps** automatically:

### **Step 1 — Generate PUPRN master list**
- Creates a deterministic list of household IDs.
- Writes it to `data/mock/puprn_master.csv`.

### **Step 2 — Generate Smart‑Meter Data**
- Reads `serl_mock.yaml`.
- Uses the **hard‑coded smart‑meter schema**.
- Applies Edition07 logic to timestamps & flags.
- Outputs monthly CSVs into:
  ```
  data/mock/serl_smart_meter_hh_edition08/
  ```

### **Step 3 — Generate Contextual Data**
- Uses the same PUPRN list.
- Writes EPC, survey, summary, follow‑up, exporters datasets into:
  ```
  data/mock/
  ```

## Example: Smart‑Meter Output Columns
Generated per half‑hour for every PUPRN:
- `Read_date_time_UTC`
- `Read_date_time_local` (BST/GMT)
- `Read_date_effective_local`
- `HH` (1–48 or NA)
- `Valid_read_time`  
- Electricity active/reactive import/export  
- Gas volume & converted Wh  
- Edition07‑style flags (`-1`, `-2`, `-5`, `1`, `0`)  

## Extending the Generators
### Add or modify contextual fields  
Edit `generator_contextual_data.py`.

### Adjust smart‑meter value ranges  
Edit `COLUMNS_SCHEMA` inside `generator_smartmeter.py`.

### Change time period or number of households  
Edit `serl_mock.yaml`.

