"""Entrena y evalúa el pronóstico mensual de ingresos consolidados de TrackFlow.

Ejecutar desde la raíz del monorepo:

    uv run python scripts/train_sales_forecast.py

Fuente: ``mock_data/raw/trackflow_sales.csv`` (el ``data/raw/`` del CONTEXT.md
corresponde a ``mock_data/raw/`` en este checkout). Columnas exactas:
``month, revenue_eur, shipments_processed, avg_revenue_per_shipment_eur, market``.
Target: ``revenue_eur`` de las filas ``market == "consolidated"``.

Elección del algoritmo — Random Forest (``RandomForestRegressor`` de scikit-learn)
en lugar de XGBoost:

* Tamaño de datos: 96 meses de entrenamiento (72 filas útiles tras exigir 24
  meses de historia para los lags). Con tan pocas filas el boosting sobreajusta
  con facilidad y exige ajustar learning rate, profundidad, regularización y
  early stopping; además no hay un conjunto de validación holgado para hacerlo
  sin tocar los 2 años de prueba.
* Explicabilidad: el bosque ofrece importancias de variables y, sobre todo, una
  distribución de predicciones por árbol que usamos para dibujar el área de
  variabilidad sin supuestos adicionales.
* Tiempo de ajuste: funciona bien con hiperparámetros por defecto razonables y
  entrena en segundos; no requiere búsqueda de hiperparámetros.
* Limitación conocida: los árboles no extrapolan fuera del rango visto. Como los
  ingresos crecen un 3–9 % anual, 2024–2025 están por encima de todo el histórico.
  Por eso el modelo NO predice EUR directamente: predice el crecimiento
  interanual log(revenue_t / revenue_{t-12}), una magnitud estacionaria, y luego
  se reconstruye revenue_t = revenue_{t-12} · exp(predicción).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "mock_data" / "raw" / "trackflow_sales.csv"
OUTPUT_DIR = ROOT / "reports" / "sales_forecast"

TARGET = "revenue_eur"
MAIN_MARKET = "consolidated"
NUMERIC_COLUMNS = ("revenue_eur", "shipments_processed", "avg_revenue_per_shipment_eur")
EXPECTED_COLUMNS = ("month", *NUMERIC_COLUMNS, "market")
FIRST_MONTH = pd.Timestamp("2016-01-01")
LAST_MONTH = pd.Timestamp("2025-12-01")
TRAIN_YEARS = 8
TEST_YEARS = 2
TRAIN_MONTHS = TRAIN_YEARS * 12
TEST_MONTHS = TEST_YEARS * 12
MIN_HISTORY = 24  # meses de historia necesarios para construir todas las features
BAND_QUANTILES = (0.10, 0.90)
RANDOM_STATE = 42

FEATURE_COLUMNS = [
    "month_sin",
    "month_cos",
    "is_black_friday_christmas",
    "is_february",
    "yoy_growth_lag_1",
    "yoy_growth_trailing_12",
    "seasonal_ratio_lag_12",
    "momentum_3_vs_12",
]


# --------------------------------------------------------------------------
# Carga, validación y tratamiento de nulos
# --------------------------------------------------------------------------
def load_sales_data(path: Path = DATA_PATH) -> pd.DataFrame:
    """Carga el CSV, valida el esquema y normaliza vacíos a NaN.

    No imputa todavía: la imputación se hace por partición después del split
    (ver :func:`impute_missing`) para que ningún valor de prueba influya en el
    entrenamiento ni viceversa. Los meses faltantes en 2016-01..2025-12 se
    reinsertan como filas vacías para que el split por años sea exacto.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = set(EXPECTED_COLUMNS).difference(raw.columns)
    if missing:
        raise ValueError(f"Faltan columnas requeridas en el CSV: {sorted(missing)}")

    frame = raw.loc[:, list(EXPECTED_COLUMNS)].apply(lambda col: col.str.strip())
    frame = frame.replace({"": np.nan, "nan": np.nan, "NaN": np.nan, "null": np.nan, "None": np.nan})
    frame["market"] = frame["market"].str.lower()
    frame = frame.loc[frame["market"].eq(MAIN_MARKET)].copy()
    frame["month"] = pd.to_datetime(frame["month"], format="%Y-%m-%d", errors="coerce")
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    # Una fila sin mes válido no puede ubicarse en la serie: se descarta y el
    # hueco reaparece como mes faltante (NaN) al reindexar.
    frame = frame.dropna(subset=["month"])
    if (frame["month"] != frame["month"].dt.to_period("M").dt.to_timestamp()).any():
        raise ValueError("Todos los meses deben tener formato YYYY-MM-01.")
    if frame["month"].duplicated().any():
        raise ValueError("Hay meses consolidated duplicados en el CSV.")

    # Restricción de negocio: revenue/volúmenes deben ser positivos. Un valor
    # no positivo es un dato corrupto y se trata igual que un vacío.
    for column in NUMERIC_COLUMNS:
        frame.loc[frame[column] <= 0, column] = np.nan

    months = pd.date_range(FIRST_MONTH, LAST_MONTH, freq="MS")
    out_of_range = ~frame["month"].isin(months)
    if out_of_range.any():
        raise ValueError("Hay meses fuera del rango esperado 2016-01 a 2025-12.")
    frame = frame.set_index("month").reindex(months).rename_axis("month").reset_index()
    frame["market"] = MAIN_MARKET
    return frame


def split_train_test(sales: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split cronológico: 8 primeros años (2016–2023) vs. 2 más recientes (2024–2025).

    Se corta por año calendario, no por posición, para que el límite sea
    explícito; y se verifica que no haya solapamiento temporal.
    """
    years = sorted(sales["month"].dt.year.unique())
    if len(years) != TRAIN_YEARS + TEST_YEARS:
        raise ValueError(f"Se esperan {TRAIN_YEARS + TEST_YEARS} años completos; hay {len(years)}.")
    test_years = years[-TEST_YEARS:]
    is_test = sales["month"].dt.year.isin(test_years)
    train = sales.loc[~is_test].sort_values("month").reset_index(drop=True)
    test = sales.loc[is_test].sort_values("month").reset_index(drop=True)
    if len(train) != TRAIN_MONTHS or len(test) != TEST_MONTHS:
        raise ValueError("El split debe tener 96 meses de entrenamiento y 24 de prueba.")
    if train["month"].max() >= test["month"].min():
        raise ValueError("El split temporal se solapa: habría fuga de datos.")
    return train, test


def impute_missing(partition: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Imputa NaN dentro de UNA partición, por interpolación temporal.

    Se llama por separado para train y para test: así un hueco de 2023 nunca se
    rellena con información de 2024 (y viceversa). En los extremos de la
    partición se usa el valor válido más cercano. Devuelve cuántos valores se
    imputaron por columna para dejarlo registrado en el reporte.
    """
    filled = partition.copy()
    counts: dict[str, int] = {}
    indexed = filled.set_index("month")
    for column in NUMERIC_COLUMNS:
        counts[column] = int(indexed[column].isna().sum())
        if indexed[column].notna().sum() == 0:
            raise ValueError(f"La partición no tiene ningún valor válido en {column}.")
        indexed[column] = indexed[column].interpolate(method="time", limit_direction="both")
    filled = indexed.reset_index()
    filled["shipments_processed"] = filled["shipments_processed"].round().astype(int)
    return filled, counts


# --------------------------------------------------------------------------
# Features causales (solo pasado) y escalado relativo
# --------------------------------------------------------------------------
def make_features(month: pd.Timestamp, history: list[float] | np.ndarray) -> dict[str, float]:
    """Features para el mes ``month`` usando SOLO ingresos anteriores (``history``).

    Todas son ratios/logs adimensionales: comparan la serie consigo misma en vez
    de usar EUR absolutos. Este es el escalado principal: elimina la magnitud
    (que crece año a año) y evita que el modelo compare euros de 2016 con euros
    de 2025, o euros con número de envíos.
    """
    h = np.asarray(history, dtype=float)
    if len(h) < MIN_HISTORY:
        raise ValueError(f"Se necesitan al menos {MIN_HISTORY} meses de historia.")
    last_12 = h[-12:]
    prior_12 = h[-24:-12]
    return {
        "month_sin": float(np.sin(2 * np.pi * month.month / 12)),
        "month_cos": float(np.cos(2 * np.pi * month.month / 12)),
        "is_black_friday_christmas": float(month.month in (11, 12)),
        "is_february": float(month.month == 2),
        "yoy_growth_lag_1": float(np.log(h[-1] / h[-13])),
        "yoy_growth_trailing_12": float(np.log(last_12.sum() / prior_12.sum())),
        "seasonal_ratio_lag_12": float(np.log(h[-12] / prior_12.mean())),
        "momentum_3_vs_12": float(np.log(h[-3:].mean() / last_12.mean())),
    }


def make_training_matrix(train: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Filas de entrenamiento: cada fila t ve solo revenue[0..t-1].

    Target = log(revenue_t / revenue_{t-12}) (crecimiento interanual).
    Las 24 primeras filas se omiten porque no tienen historia suficiente.
    """
    revenue = train[TARGET].to_numpy(dtype=float)
    rows, targets = [], []
    for t in range(MIN_HISTORY, len(train)):
        rows.append(make_features(train["month"].iloc[t], revenue[:t]))
        targets.append(np.log(revenue[t] / revenue[t - 12]))
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS), np.asarray(targets)


def build_model() -> Pipeline:
    # StandardScaler (ajustado solo con train) deja todas las features en
    # media 0 / desviación 1. Los árboles son invariantes a transformaciones
    # monótonas, así que no cambia sus splits, pero garantiza magnitudes
    # comparables si se cambia el estimador (p. ej. a uno lineal) y hace
    # interpretables las features escaladas en el reporte de PSI.
    return Pipeline([
        ("scaler", StandardScaler()),
        ("regressor", RandomForestRegressor(
            n_estimators=500,
            min_samples_leaf=3,
            max_features=0.6,
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )),
    ])


def forecast_recursive(model: Pipeline, train: pd.DataFrame, test: pd.DataFrame) -> dict[str, np.ndarray]:
    """Pronóstico a 24 meses SIN consumir ningún valor real de prueba.

    * Punto: recursivo con la media del bosque; cada paso añade su propia
      predicción a la historia.
    * Variabilidad: cada árbol genera su propia trayectoria recursiva (500
      trayectorias). El percentil 10–90 de esas trayectorias es el área de
      variabilidad; se ensancha con el horizonte porque el error se acumula.
    """
    scaler: StandardScaler = model.named_steps["scaler"]
    forest: RandomForestRegressor = model.named_steps["regressor"]
    trees = forest.estimators_
    base = train[TARGET].to_numpy(dtype=float)

    point_history = list(base)
    paths = np.tile(base, (len(trees), 1))
    point, path_values = [], []
    for month in test["month"]:
        x = scaler.transform(pd.DataFrame([make_features(month, point_history)], columns=FEATURE_COLUMNS))
        growth = float(forest.predict(x)[0])
        point.append(point_history[-12] * np.exp(growth))
        point_history.append(point[-1])

        x_paths = scaler.transform(pd.DataFrame(
            [make_features(month, path) for path in paths], columns=FEATURE_COLUMNS,
        ))
        tree_growth = np.array([tree.predict(x_paths[i : i + 1])[0] for i, tree in enumerate(trees)])
        step = paths[:, -12] * np.exp(tree_growth)
        paths = np.column_stack([paths, step])
        path_values.append(step)

    path_values = np.column_stack(path_values)  # (n_trees, 24)
    return {
        "prediction": np.asarray(point),
        "lower": np.quantile(path_values, BAND_QUANTILES[0], axis=0),
        "upper": np.quantile(path_values, BAND_QUANTILES[1], axis=0),
    }


# --------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------
def population_stability_index(reference: np.ndarray, observed: np.ndarray, bins: int | None = None) -> float:
    """PSI = Σ (obs% − ref%) · ln(obs% / ref%), con bins por cuantiles de referencia.

    Por defecto usa ~5 observaciones esperadas por bin (máx. 10). Con solo 24
    meses de prueba, 10 bins dejarían bins vacíos y el PSI se dispararía por
    ruido muestral, no por un cambio real de distribución.
    """
    reference = np.asarray(reference, dtype=float)
    observed = np.asarray(observed, dtype=float)
    if bins is None:
        bins = int(np.clip(len(observed) // 5, 2, 10))
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    ref_share = np.clip(np.histogram(reference, bins=edges)[0] / len(reference), 1e-4, None)
    obs_share = np.clip(np.histogram(observed, bins=edges)[0] / len(observed), 1e-4, None)
    return float(np.sum((obs_share - ref_share) * np.log(obs_share / ref_share)))


def psi_label(value: float) -> str:
    if value < 0.10:
        return "estable (<0.10)"
    if value < 0.25:
        return "cambio moderado (0.10–0.25)"
    return "cambio significativo (>0.25)"


def normalized_gini(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Gini normalizado: capacidad de ORDENAR los meses (1 = orden perfecto).

    Gini(actual, predicho) / Gini(actual, actual). Mide si el modelo pone los
    meses de mayor ingreso arriba y los de menor abajo, independientemente de
    la escala del error.
    """

    def gini(a: np.ndarray, p: np.ndarray) -> float:
        order = np.lexsort((np.arange(len(a)), -p))
        a_sorted = a[order]
        cumulative = np.cumsum(a_sorted) / a_sorted.sum()
        return float(np.sum(cumulative - np.arange(1, len(a) + 1) / len(a)) / len(a))

    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    return gini(actual, predicted) / gini(actual, actual)


def calculate_metrics(
    train: pd.DataFrame,
    test: pd.DataFrame,
    predictions: np.ndarray,
    train_growth: np.ndarray,
) -> dict[str, Any]:
    actual = test[TARGET].to_numpy(dtype=float)
    mean_revenue = float(actual.mean())
    mse = float(mean_squared_error(actual, predictions))

    # Línea base ingenua estacional con el mismo horizonte (sin ver la prueba):
    # repetir los 12 meses de 2023 para 2024 y 2025.
    naive = np.tile(train[TARGET].to_numpy(dtype=float)[-12:], len(actual) // 12)
    naive_mse = float(mean_squared_error(actual, naive))

    test_growth = np.log(actual / np.concatenate([train[TARGET].to_numpy(dtype=float)[-12:], actual[:-12]]))
    psi_growth = population_stability_index(train_growth, test_growth)
    psi_rps = population_stability_index(
        train["avg_revenue_per_shipment_eur"].to_numpy(dtype=float),
        test["avg_revenue_per_shipment_eur"].to_numpy(dtype=float),
    )
    psi_raw = population_stability_index(train[TARGET].to_numpy(dtype=float), actual)

    return {
        "mse_eur2": mse,
        "rmse_eur": float(np.sqrt(mse)),
        "mse_pct_of_mean_monthly_revenue": float(np.sqrt(mse) / mean_revenue * 100),
        "mse_normalized_pct": float(mse / mean_revenue**2 * 100),
        "mape_pct": float(np.mean(np.abs(actual - predictions) / actual) * 100),
        "mean_test_monthly_revenue_eur": mean_revenue,
        "k2_score": float(r2_score(actual, predictions)),
        "gini_normalized": normalized_gini(actual, predictions),
        "psi_yoy_growth_train_vs_test": psi_growth,
        "psi_yoy_growth_label": psi_label(psi_growth),
        "psi_avg_revenue_per_shipment_train_vs_test": psi_rps,
        "psi_avg_revenue_per_shipment_label": psi_label(psi_rps),
        "psi_revenue_eur_raw_train_vs_test": psi_raw,
        "baseline_seasonal_naive_mse_eur2": naive_mse,
        "baseline_seasonal_naive_mse_pct_of_mean": float(np.sqrt(naive_mse) / mean_revenue * 100),
    }


def pattern_check(sales: pd.DataFrame) -> dict[str, Any]:
    """Contrasta la serie con los patrones descritos en CONTEXT.md §4."""
    data = sales.assign(year=sales["month"].dt.year, m=sales["month"].dt.month)
    data["ratio"] = data[TARGET] / data.groupby("year")[TARGET].transform("mean")
    seasonal = {f"{m:02d}": round(float(data.loc[data["m"].eq(m), "ratio"].mean() - 1) * 100, 2) for m in range(1, 13)}
    annual = data.groupby("year")[TARGET].sum()
    growth = (annual.pct_change().dropna() * 100).round(2)
    return {
        "seasonal_pct_vs_annual_mean": seasonal,
        "annual_growth_pct": {str(k): float(v) for k, v in growth.items()},
    }


# --------------------------------------------------------------------------
# Visualización
# --------------------------------------------------------------------------
def save_plot(train: pd.DataFrame, test: pd.DataFrame, forecast: dict[str, np.ndarray], metrics: dict[str, Any], path: Path) -> None:
    ink, muted, accent = "#1f2937", "#9ca3af", "#2563eb"
    fig, ax = plt.subplots(figsize=(12, 6))
    context = train.tail(12)
    ax.plot(context["month"], context[TARGET] / 1e6, color=muted, linewidth=2, label="Real 2023 (entrenamiento, contexto)")
    ax.fill_between(test["month"], forecast["lower"] / 1e6, forecast["upper"] / 1e6, color=accent, alpha=0.18,
                    linewidth=0, label="Variabilidad P10–P90 (500 trayectorias de árboles)")
    ax.plot(test["month"], test[TARGET] / 1e6, color=ink, linewidth=2, marker="o", markersize=5, label="Real 2024–2025 (prueba)")
    ax.plot(test["month"], forecast["prediction"] / 1e6, color=accent, linewidth=2, marker="o", markersize=5,
            linestyle="--", label="Pronóstico Random Forest")
    ax.axvline(test["month"].iloc[0], color=muted, linewidth=1, linestyle=":")
    ax.set_title(
        "TrackFlow — ingresos consolidados mensuales: pronóstico vs. real (prueba 2024–2025)\n"
        f"RMSE {metrics['mse_pct_of_mean_monthly_revenue']:.1f}% del ingreso medio · "
        f"K2 {metrics['k2_score']:.2f} · Gini {metrics['gini_normalized']:.2f}",
        loc="left", fontsize=12,
    )
    ax.set_ylabel("Ingresos (millones EUR)")
    ax.grid(True, axis="y", alpha=0.25)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


# --------------------------------------------------------------------------
# Orquestación
# --------------------------------------------------------------------------
def train_and_evaluate(data_path: Path = DATA_PATH, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    sales = load_sales_data(data_path)
    train_raw, test_raw = split_train_test(sales)
    train, train_imputed = impute_missing(train_raw)
    test, test_imputed = impute_missing(test_raw)

    x_train, y_train = make_training_matrix(train)
    model = build_model()
    model.fit(x_train, y_train)
    forecast = forecast_recursive(model, train, test)
    metrics = calculate_metrics(train, test, forecast["prediction"], y_train)

    output_dir.mkdir(parents=True, exist_ok=True)
    plot_path = output_dir / "sales_forecast_test_2024_2025.png"
    save_plot(train, test, forecast, metrics, plot_path)
    pd.DataFrame({
        "month": test["month"].dt.strftime("%Y-%m-%d"),
        "revenue_eur_actual": test[TARGET].round(2),
        "revenue_eur_predicted": forecast["prediction"].round(2),
        "revenue_eur_p10": forecast["lower"].round(2),
        "revenue_eur_p90": forecast["upper"].round(2),
    }).to_csv(output_dir / "predictions_2024_2025.csv", index=False)

    importances = model.named_steps["regressor"].feature_importances_
    results: dict[str, Any] = {
        "model": "RandomForestRegressor (target: log crecimiento interanual)",
        "data_path": str(data_path.relative_to(ROOT)) if data_path.is_relative_to(ROOT) else str(data_path),
        "training_period": [str(train["month"].min().date()), str(train["month"].max().date())],
        "test_period": [str(test["month"].min().date()), str(test["month"].max().date())],
        "training_months": len(train),
        "training_rows_with_full_history": len(x_train),
        "test_months": len(test),
        "imputed_values": {"train": train_imputed, "test": test_imputed},
        "test_actuals_used_during_forecast": False,
        "metrics": metrics,
        "pattern_check": pattern_check(pd.concat([train, test], ignore_index=True)),
        "feature_importances": {
            name: round(float(value), 4)
            for name, value in sorted(zip(FEATURE_COLUMNS, importances), key=lambda item: -item[1])
        },
        "variability_band": "P10–P90 de 500 trayectorias recursivas (una por árbol); descriptiva, no un intervalo calibrado",
        "plot": str(plot_path.relative_to(ROOT)) if plot_path.is_relative_to(ROOT) else str(plot_path),
    }
    with (output_dir / "metrics.json").open("w", encoding="utf-8") as file:
        json.dump(results, file, indent=2, ensure_ascii=False, allow_nan=False)
    return results


def main() -> None:
    print(json.dumps(train_and_evaluate(), indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
