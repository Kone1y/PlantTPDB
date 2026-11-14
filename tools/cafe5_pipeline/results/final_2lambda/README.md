# Two-rate CAFE5 run outputs

| File | Role |
|---|---|
| `rate_params.tsv` | two-stage lambda/epsilon/-lnL summary (joint_best + final_fixed_error) |
| `summary.tsv` | all 30 joint restarts, ranked by -lnL |
| `final_reps.summary.tsv` | all 20 fixed-error restarts, ranked by -lnL |
| `tree.lambda2.nwk` | lambda tree (lambda-2 group: Klebsormidium_nitens; Edaphochlamys_debaryana + Volvox_carteri_nagariensis clade incl. stem) |
| `tree.Myr.nwk` | CAFE input tree, scaled to 10-Myr units |
| `final/Base_family_results.fdr.tsv` | per-family P + BH-FDR (114 families) |
| `final/Base_asr.tre` | ancestral state reconstruction (per-family copy-number trees) |
| `cafe_input_matrix.filtered.tsv` | 147 x 26 model input matrix |
| `cafe_input_matrix.removed.tsv` | the 9 families removed by the >=100 filter |
| `family_filter_report.tsv` | per-family filter audit trail |
