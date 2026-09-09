# Edition08 reference dictionaries

Every file in this folder except `serl_master_mapping_edition07.csv` and
`serl_2025_follow_up_survey_data_dictionary_edition07.csv` is the **real, authentic edition07
SERL documentation** — edition08 uses it directly (`dictionary_source_edition: "07"` in
[`src/serl_mock/edition.py`](../../../src/serl_mock/edition.py)), not a copy or placeholder.

## `serl_master_mapping_edition07.csv` / `serl_2025_follow_up_survey_data_dictionary_edition07.csv`

These two **are** placeholders, unlike their siblings above: straight copies of
[`data/reference/edition09/serl_master_mapping_edition09.csv`](../edition09/serl_master_mapping_edition09.csv)
and
[`data/reference/edition09/serl_2025_follow_up_survey_data_dictionary_edition09.csv`](../edition09/serl_2025_follow_up_survey_data_dictionary_edition09.csv)
respectively (both real content — see that folder's `README.md`), added only so a config enabling
`generate.harmonised_survey` / `generate.survey_2025` for edition08 doesn't crash looking for a
file at these paths (see `dictionary_source_edition` resolution in
`SERLContextualVariablesGenerator.__init__`).

There is no real edition07-vintage equivalent of either file: the SERL survey-harmonisation
methodology they're derived from — and the 2025 SERL Observatory survey they document — postdate
edition07/08 entirely (the 2025 survey couldn't have existed in edition07/08-era SERL data).
Generating `masterserl_surveys_edition08.csv` / `serl_2025_follow_up_survey_data_edition08.csv`
under this edition is therefore anachronistic — useful for exercising the mock pipeline and
nothing more, not something a real edition08 SERL release would ever contain. Replace them if
SERL ever publishes a genuine edition07/08-appropriate equivalent (unlikely, given the above).
