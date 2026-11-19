"""Pruebas de la evaluación temporal del pronóstico: orden cronológico y sin fuga por fold."""

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import KFold

import scripts.evaluate_sales_forecast as evaluation
from scripts.evaluate_sales_forecast import (
    assert_chronological_folds,
    fit_and_score,
    regression_errors,
    temporal_folds,
)
from scripts.train_sales_forecast import split_train_test


def synthetic_sales() -> pd.DataFrame:
    """120 meses con las columnas exactas del CONTEXT (crecimiento alterno + estacionalidad)."""
    months = pd.date_range("2016-01-01", "2025-12-01", freq="MS")
    yearly_growth = np.where((months.year - 2016) % 2 == 0, 1.09, 1.03)
    level = 760_000 * np.cumprod(np.where(months.month == 1, yearly_growth, 1.0))
    seasonal = np.where(months.month.isin([11, 12]), 1.30, np.where(months.month == 2, 0.88, 1.0))
    revenue = level * seasonal
    shipments = np.round(revenue / 15).astype(int)
    return pd.DataFrame({
        "month": months,
        "revenue_eur": revenue,
        "shipments_processed": shipments,
        "avg_revenue_per_shipment_eur": revenue / shipments,
        "market": "consolidated",
    })


def test_temporal_cv_preserves_chronological_order_in_every_fold():
    train, _ = split_train_test(synthetic_sales())
    folds = temporal_folds(len(train))

    assert len(folds) >= 5
    previous_validation = np.array([], dtype=int)
    previous_train_size = 0
    for train_idx, val_idx in folds:
        # Dentro de cada fold: orden estrictamente creciente y sin huecos (nada barajado).
        assert np.all(np.diff(train_idx) == 1)
        assert np.all(np.diff(val_idx) == 1)
        # Todo el entrenamiento es anterior a la validación del mismo fold.
        assert train_idx.max() < val_idx.min()
        # Ningún índice de un fold posterior aparece antes que uno de un fold anterior.
        if previous_validation.size:
            assert val_idx.min() > previous_validation.max()
            assert set(previous_validation).issubset(set(train_idx))
        # Ventana expansiva desde el primer mes.
        assert train_idx[0] == 0 and len(train_idx) > previous_train_size
        previous_validation, previous_train_size = val_idx, len(train_idx)

    # En fechas: cada fold valida un año completo, 2019..2023, en orden.
    validation_years = [sorted(train.iloc[val]["month"].dt.year.unique()) for _, val in folds]
    assert validation_years == [[2019], [2020], [2021], [2022], [2023]]
    # El set de prueba 2024–2025 nunca entra en la validación cruzada.
    assert max(train.iloc[folds[-1][1]]["month"]) == pd.Timestamp("2023-12-01")


def test_chronology_check_rejects_shuffled_or_overlapping_folds():
    shuffled = list(KFold(n_splits=5, shuffle=True, random_state=42).split(np.arange(96)))
    with pytest.raises(ValueError):
        assert_chronological_folds(shuffled)

    good = temporal_folds(96)
    reversed_order = list(reversed(good))
    with pytest.raises(ValueError):
        assert_chronological_folds(reversed_order)


def test_lag_and_rolling_features_do_not_leak_across_fold_boundary(monkeypatch):
    """Alterar por completo el fold de validación no cambia nada de lo que el modelo ve."""
    smaller = evaluation.build_model().set_params(regressor__n_estimators=20)
    monkeypatch.setattr(evaluation, "build_model", lambda: smaller)

    train, _ = split_train_test(synthetic_sales())
    train_idx, val_idx = temporal_folds(len(train))[2]
    history = train.iloc[train_idx]
    validation = train.iloc[val_idx]
    tampered = validation.assign(revenue_eur=validation["revenue_eur"] * 50)

    clean = fit_and_score(history, validation)
    leaked = fit_and_score(history, tampered)

    assert clean["train"] == leaked["train"]
    clean_pred = [m["revenue_eur_predicted"] for m in clean["validation_monthly"]]
    tampered_pred = [m["revenue_eur_predicted"] for m in leaked["validation_monthly"]]
    assert clean_pred == tampered_pred  # los lags del 2.º mes de validación no leen el real del 1.º
    assert clean["train_period"][1] < clean["validation_period"][0]


def test_regression_errors_reports_mae_and_rmse_in_eur_and_percent():
    actual = np.array([100.0, 100.0, 100.0, 100.0])
    predicted = np.array([110.0, 90.0, 100.0, 100.0])
    errors = regression_errors(actual, predicted)

    assert errors["mae_eur"] == pytest.approx(5.0)
    assert errors["rmse_eur"] == pytest.approx(np.sqrt(50.0))
    assert errors["mae_pct_of_mean_monthly_revenue"] == pytest.approx(5.0)
    assert errors["rmse_eur"] >= errors["mae_eur"]
