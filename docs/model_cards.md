# Local model cards

## IsolationForest

Historical provider snapshot at source start + 365 days; eligible providers have at least three recent claims. Inputs use a 90-day lookback and preceding 90-day comparison, specialty-median-normalized allowed/paid amounts, unique members/procedures, defined observable events, referral concentration and outcomes known by the cutoff. StandardScaler and IsolationForest are persisted together. Seed `20261008`; 200 trees; contamination 0.05. Scores are expressed as raw anomaly magnitude and historical reference percentile. At least 30 eligible providers are required.

Most recent provider values are descriptive signals. No claim is made that these are causal feature attributions from IsolationForest. The model does not read `ground_truth.csv`.

## 30/60/90-day classifiers

Three independent HistGradientBoostingClassifier models, each with a LogisticRegression benchmark. Target: repeat defined event when prior events exist; otherwise at least two events within the horizon. Defined events are consultation-code/documentation conflict, configured package separation and early refill. These are observable synthetic screening behaviors, not proven criminal activity.

Monthly provider-date snapshots use only service/submission records at or before cutoff, payments known by cutoff, referral dates within the window and historical outcomes whose availability dates have passed. Latest source data are assumed to reflect the records available on their source dates; missing record-version history is a limitation.

Training cutoffs end at start + 270 days. Validation cutoffs span start + 390 to +450 days. Test cutoffs start at +570 days and finish at least 90 days before source end. A common 90-day embargo prevents future label windows crossing split boundaries. Providers recur between chronological splits, and within-split windows overlap. This evaluates future behavior for known providers, not generalization to wholly unseen providers.

Each split needs at least 50 eligible rows and five samples from both classes. Otherwise a horizon is `INSUFFICIENT_DATA`. Features exclude all synthetic scenario and future labels. Defaults are fixed independently of test results. Models are not post-hoc calibrated; test calibration bins, Brier score and a logistic benchmark are exposed. Independently fitted horizons need not be monotonic.

## Persisted metadata

`model_versions` holds status, artifact path, seed, feature names, training/split dates, sample/class counts, PR-AUC, ROC-AUC where defined, Brier score, Precision@25, Recall@25, calibration bins, benchmarks and limitations. `risk_predictions` stores cutoff, horizon, provider, model version, value and eligible-data context. Files reside in `artifacts/models/`.

## Evaluation interpretation

Detection baselines evaluate the same service-date holdout after 2025-12-30. Rules have fixed synthetic-policy thresholds. IsolationForest training ends before the holdout; SupplyTrace uses a retrospective peer snapshot and must not be interpreted as a prospective replay. Ranking uses current evidence and all available model context.

Ground truth retains the distinction between injected suspicious patterns and the normal source population; historical investigation outcomes remain separately modeled operational records. The source does not supply separate labels for every possible benign anomaly. False-positive counts mean unlabelled-as-injected selections, not verified legitimate adjudications.

Synthetic scenario injection can make detection artificially easy. Forecast performance is modest. No real-world superiority, healthcare certification, clinical validation or production readiness is claimed.
