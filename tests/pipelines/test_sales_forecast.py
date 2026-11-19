"""Pruebas del pronóstico de ventas: split 8/2 años, sin fuga de datos y nulos."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.train_sales_forecast import (
    EXPECTED_COLUMNS,
    build_model,
    forecast_recursive,
    impute_missing,
    load_sales_data,
    make_features,
    make_training_matrix,
    population_stability_index,
    split_train_test,
)


def synthetic_sales() -> pd.DataFrame:
    """120 meses con el formato exacto del CONTEXT.md (crecimiento + estacionalidad)."""
    months = pd.date_range("2016-01-01", "2025-12-01", freq="MS")
    seasonal = np.where(months.month.isin([11, 12]), 1.30, np.where(months.month == 2, 0.88, 1.0))
    revenue = 760_000 * (1.06 ** (np.arange(120) / 12)) * seasonal
    shipments = np.round(revenue / 15).astype(int)
    return pd.DataFrame({
        "month": months,
        "revenue_eur": revenue,
        "shipments_processed": shipments,
        "avg_revenue_per_shipment_eur": revenue / shipments,
        "market": "consolidated",
    })


def write_csv(frame: pd.DataFrame, path: Path) -> Path:
    out = frame.copy()
    out["month"] = out["month"].dt.strftime("%Y-%m-%d")
    out.loc[:, list(EXPECTED_COLUMNS)].to_csv(path, index=False)
    return path


def test_split_respects_eight_and_two_years_without_overlap():
    train, test = split_train_test(synthetic_sales())

    assert len(train) == 8 * 12
    assert len(test) == 2 * 12
    assert sorted(train["month"].dt.year.unique()) == list(range(2016, 2024))
    assert sorted(test["month"].dt.year.unique()) == [2024, 2025]
    assert train["month"].min() == pd.Timestamp("2016-01-01")
    assert train["month"].max() == pd.Timestamp("2023-12-01")
    assert test["month"].min() == pd.Timestamp("2024-01-01")
    assert test["month"].max() == pd.Timestamp("2025-12-01")
    # Sin fuga: ningún mes compartido y todo el train es estrictamente anterior.
    assert set(train["month"]).isdisjoint(set(test["month"]))
    assert train["month"].max() < test["month"].min()


def test_split_rejects_datasets_without_exactly_ten_years():
    with pytest.raises(ValueError):
        split_train_test(synthetic_sales().iloc[:108])


def test_model_never_sees_test_years():
    """Alterar por completo los años de prueba no cambia ni el train ni el pronóstico."""
    original = synthetic_sales()
    tampered = original.copy()
    is_test = tampered["month"].dt.year >= 2024
    tampered.loc[is_test, "revenue_eur"] *= 50

    train_a, test_a = split_train_test(original)
    train_b, test_b = split_train_test(tampered)
    x_a, y_a = make_training_matrix(train_a)
    x_b, y_b = make_training_matrix(train_b)
    pd.testing.assert_frame_equal(x_a, x_b)
    np.testing.assert_array_equal(y_a, y_b)

    model = build_model().set_params(regressor__n_estimators=20, regressor__n_jobs=1)  # n_jobs=1: suma determinista para comparar exacto
    model.fit(x_a, y_a)
    forecast_a = forecast_recursive(model, train_a, test_a)
    forecast_b = forecast_recursive(model, train_b, test_b)
    np.testing.assert_array_equal(forecast_a["prediction"], forecast_b["prediction"])
    np.testing.assert_array_equal(forecast_a["lower"], forecast_b["lower"])


def test_features_are_causal():
    train, _ = split_train_test(synthetic_sales())
    revenue = train["revenue_eur"].to_numpy()
    features, targets = make_training_matrix(train)

    # La fila t se construye solo con revenue[:t]; cambiar revenue[t] no la altera.
    t = 50
    altered = revenue.copy()
    altered[t:] *= 3
    assert make_features(train["month"].iloc[t], altered[:t]) == make_features(train["month"].iloc[t], revenue[:t])
    assert len(features) == len(targets) == len(train) - 24
    assert np.isclose(targets[0], np.log(revenue[24] / revenue[12]))


def test_missing_values_are_imputed_within_each_partition(tmp_path):
    sales = synthetic_sales()
    sales.loc[sales["month"] == "2023-12-01", "revenue_eur"] = np.nan  # último mes de train
    sales.loc[sales["month"] == "2020-06-01", "shipments_processed"] = np.nan
    sales = sales.loc[sales["month"] != "2019-03-01"]  # mes faltante
    loaded = load_sales_data(write_csv(sales, tmp_path / "sales.csv"))

    assert len(loaded) == 120  # el mes faltante se reinserta como NaN
    train_raw, test_raw = split_train_test(loaded)
    train, counts = impute_missing(train_raw)

    assert counts["revenue_eur"] == 2
    assert counts["shipments_processed"] == 2
    assert not train.isna().any().any()
    # El hueco de 2023-12 se rellena solo con train (valor de 2023-11), nunca con 2024-01.
    dec_2023 = train.loc[train["month"] == "2023-12-01", "revenue_eur"].iloc[0]
    nov_2023 = train_raw.loc[train_raw["month"] == "2023-11-01", "revenue_eur"].iloc[0]
    assert dec_2023 == nov_2023
    assert dec_2023 != test_raw["revenue_eur"].iloc[0]


def test_psi_is_zero_for_same_distribution_and_high_for_shift():
    reference = np.linspace(0, 1, 96)
    assert population_stability_index(reference, reference[::4]) < 0.05
    assert population_stability_index(reference, reference[::4] + 2) > 0.25
