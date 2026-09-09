# Edition09 reference dictionaries — placeholder

The real Edition09 SERL documentation isn't available yet. The dictionary files in this
folder are **copies of the edition08 dictionaries** (themselves sourced from the real
edition07 SERL documentation — see [`docs/05_epc_reference.md`](../../../docs/05_epc_reference.md)),
renamed to `_edition09` so `SERLContextualVariablesGenerator` has something real to read
instead of silently falling back to a minimal hardcoded field list.

Replace these with the real edition09 dictionaries once SERL publishes them.

## `serl_master_mapping_edition09.csv`

Unlike the placeholder dictionaries above, this one **is** real content: it's an export of the
"Master Mapping" sheet from
`docs/documentation/SERL/edition09/serl_mastermapping_methodology_edition01.xlsx` (SERL's actual
survey-harmonisation methodology workbook), one row per harmonised MasterSERL variable, recording
its domain and the original column name in each of the three raw surveys (Sign Up / 2023 / 2025;
blank means that survey never asked it). It's read by
`src/serl_mock/generator_contextual_data.py`'s "MasterSERL harmonised survey" section to drive
`masterserl_surveys_edition09.csv`, and its "Raw 2025 SERL Observatory survey" section (together
with the real paper questionnaire,
`docs/documentation/SERL/edition09/serl_2025_survey_PaperSurveyFinalCopy.pdf`) to drive
`serl_2025_follow_up_survey_data_edition09.csv` — see `docs/02_configuration.md`'s "MasterSERL
harmonised survey" and "Raw 2025 SERL Observatory survey" sections.

## `serl_2025_follow_up_survey_data_dictionary_edition09.csv`

Also real content, not a placeholder: a `Variable,Value,ValueDescription,QuestionOrMeaning,FreeText,Type`
reference dictionary for the raw 2025 survey — same format as
`serl_survey_data_dictionary_edition09.csv` / `serl_follow_up_survey_data_dictionary_edition09.csv`
above, so it's copied into every run's output the same way (Step 0 in
`scripts/generate_mock_data.py`). Question text comes from the master mapping's `concept` column;
value meanings are transcribed from the real paper questionnaire and cross-checked against the
harmonisation doc, matched to the exact codes `src/serl_mock/generator_contextual_data.py`'s
`TEMPLATE_OVERRIDES`/`NUMERIC_COUNT_VARS` actually sample from. Built by a one-off script (not
committed — see git history if it needs regenerating after a template change).
