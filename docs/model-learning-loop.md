# Model learning loop

Lohela learns through immutable offline challengers. Settled outcomes never
rewrite published tickets or an existing prediction/model version.

## Lifecycle

1. The weekly scheduler creates a shadow challenger using 180 earlier training
   days and a later, untouched 30-day validation period.
2. Each market requires at least 100 training rows, 30 validation rows, and 10
   priced validation selections before it can pass.
3. Non-negative ensemble weights are fitted with regularization toward the
   documented default weights.
4. A Platt/logit probability calibrator is retained only when it improves the
   training Brier score.
5. The challenger must improve validation Brier score, avoid material
   calibration and ROI regression, and retain its complete validation metrics
   and ROI confidence interval.
6. Passing profiles remain `shadow_ready`. Nothing is activated automatically.
7. An operator explicitly promotes a completed learning run with a future
   effective date. The promotion is append-only and audited.
8. Future model runs resolve the latest effective promotion chain and record
   exact profile IDs, weights, calibrators, raw probabilities, and calibrated
   probabilities.

## Research API

All endpoints use the existing research-access control.

- `POST /api/v1/models/learning/train` creates and validates a challenger.
- `GET /api/v1/models/learning/status` returns runs, per-market evidence, and
  promotions.
- `POST /api/v1/models/learning/promote` activates only profiles that passed
  every validation check. `effective_from` must be after the validation period.

## Safety boundaries

- Training and validation windows must be ordered and non-overlapping.
- Only predictions created before kickoff are eligible.
- Odds are used for ROI only when captured before kickoff.
- Current-day results never affect current-day ticket generation.
- Calibration gates are scoped deterministically to the exact model version,
  with the promoted profile's base version used only as a documented fallback.
- The scheduler trains challengers but cannot promote them.
