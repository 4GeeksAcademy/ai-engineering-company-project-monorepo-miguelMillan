"""Evaluación técnica del pronóstico de ingresos de TrackFlow (CONTEXT de evaluación).

Ejecutar desde la raíz del monorepo:

    uv run python scripts/evaluate_sales_forecast.py

Reutiliza sin cambios el modelo de ``scripts/train_sales_forecast.py``: target
``revenue_eur`` consolidado, features causales (lags, rodantes, calendario),
Random Forest y pronóstico recursivo. Solo trabaja sobre el set de
ENTRENAMIENTO 2016–2023: los años de prueba 2024–2025 no se tocan aquí.

Salidas en ``mock_data/eval/`` (el ``data/eval/`` del enunciado):
``cv_metrics.json``, ``learning_curve.png`` y ``learning_curve.json``.

Protocolo anti-fuga por fold
----------------------------
Las features de cada fila t se construyen con ``revenue[:t]``. Si se calcularan
sobre la serie completa antes de partir, el ``lag_1`` del segundo mes de
validación sería el ingreso REAL del primer mes de validación: el modelo
"vería" el fold futuro. Por eso, en cada fold:

1. La serie se corta en el inicio del fold de validación; la imputación y la
   matriz de entrenamiento se construyen solo con los meses anteriores.
2. Los 12 meses de validación se pronostican de forma recursiva: cada paso usa
   como historia el entrenamiento + sus propias predicciones, nunca un valor
   real del fold de validación (igual que en producción y en la prueba 2024–25).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train_sales_forecast import (  # noqa: E402
    DATA_PATH,
    MIN_HISTORY,
    TARGET,
    build_model,
    forecast_recursive,
    impute_missing,
    load_sales_data,
    make_training_matrix,
    split_train_test,
)

EVAL_DIR = ROOT / "mock_data" / "eval"
N_SPLITS = 5
VALIDATION_MONTHS = 12  # cada fold valida un año natural completo (incluye Nov–Dic y Feb)
LEARNING_CURVE_WINDOWS = (36, 48, 60, 72, 84)  # meses de historia de entrenamiento
LEARNING_CURVE_ORIGINS = (2021, 2022, 2023)  # años de validación de la curva


# --------------------------------------------------------------------------
# Folds temporales
# --------------------------------------------------------------------------
def temporal_folds(n_months: int, n_splits: int = N_SPLITS, test_size: int = VALIDATION_MONTHS) -> list[tuple[np.ndarray, np.ndarray]]:
    """Folds de ventana expansiva con ``TimeSeriesSplit`` (sin barajar).

    Con 96 meses de entrenamiento, 5 folds y validación de 12 meses, cada fold
    valida un año natural: 2019, 2020, 2021, 2022 y 2023, entrenando con todos
    los meses anteriores. Se valida explícitamente el orden cronológico.
    """
    splitter = TimeSeriesSplit(n_splits=n_splits, test_size=test_size)
    folds = [(train.copy(), val.copy()) for train, val in splitter.split(np.arange(n_months))]
    assert_chronological_folds(folds)
    return folds


def assert_chronological_folds(folds: list[tuple[np.ndarray, np.ndarray]]) -> None:
    """Falla si algún fold mezcla, baraja o adelanta índices."""
    previous_val_max = -1
    for number, (train, val) in enumerate(folds, start=1):
        for name, idx in (("train", train), ("validación", val)):
            if len(idx) == 0:
                raise ValueError(f"Fold {number}: {name} vacío.")
            if not np.array_equal(idx, np.arange(idx[0], idx[0] + len(idx))):
                raise ValueError(f"Fold {number}: {name} no es un bloque contiguo y ordenado (¿barajado?).")
        if train[0] != 0:
            raise ValueError(f"Fold {number}: la ventana expansiva debe empezar en el primer mes.")
        if train[-1] + 1 != val[0]:
            raise ValueError(f"Fold {number}: validación no empieza justo después del entrenamiento.")
        if val[0] <= previous_val_max:
            raise ValueError(f"Fold {number}: su validación se solapa o precede a la del fold anterior.")
        previous_val_max = val[-1]


# --------------------------------------------------------------------------
# Métricas y ajuste por fold
# --------------------------------------------------------------------------
def regression_errors(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    mean_revenue = float(actual.mean())
    mae = float(mean_absolute_error(actual, predicted))
    rmse = float(np.sqrt(mean_squared_error(actual, predicted)))
    return {
        "mae_eur": mae,
        "rmse_eur": rmse,
        "mae_pct_of_mean_monthly_revenue": mae / mean_revenue * 100,
        "rmse_pct_of_mean_monthly_revenue": rmse / mean_revenue * 100,
    }


def fit_and_score(history_raw: pd.DataFrame, validation_raw: pd.DataFrame) -> dict[str, Any]:
    """Entrena con ``history_raw`` y valida en ``validation_raw`` sin fuga.

    * Imputación por separado en cada bloque (como en el split 8/2).
    * Error de entrenamiento: predicción in-sample a un paso sobre las filas de
      entrenamiento (cada una con sus lags reales anteriores).
    * Error de validación: pronóstico recursivo de 12 meses desde el corte,
      sin leer ningún valor real del bloque de validación.
    """
    history, _ = impute_missing(history_raw.reset_index(drop=True))
    validation, _ = impute_missing(validation_raw.reset_index(drop=True))
    x_train, y_train = make_training_matrix(history)
    model = build_model()
    model.fit(x_train, y_train)

    revenue = history[TARGET].to_numpy(dtype=float)
    lag_12 = revenue[MIN_HISTORY - 12 : len(revenue) - 12]
    train_actual = revenue[MIN_HISTORY:]
    train_pred = lag_12 * np.exp(model.predict(x_train))

    forecast = forecast_recursive(model, history, validation)
    val_actual = validation[TARGET].to_numpy(dtype=float)
    return {
        "train_months": len(history),
        "train_rows": len(x_train),
        "train_period": [str(history["month"].iloc[0].date()), str(history["month"].iloc[-1].date())],
        "validation_period": [str(validation["month"].iloc[0].date()), str(validation["month"].iloc[-1].date())],
        "train": regression_errors(train_actual, train_pred),
        "validation": regression_errors(val_actual, forecast["prediction"]),
        "validation_monthly": [
            {
                "month": str(m.date()),
                "revenue_eur_actual": round(float(a), 2),
                "revenue_eur_predicted": round(float(p), 2),
                "error_eur": round(float(p - a), 2),
            }
            for m, a, p in zip(validation["month"], val_actual, forecast["prediction"])
        ],
    }


def alternating_growth_anchor(history: pd.DataFrame, validation_year: int) -> float:
    """Total anual esperado para ``validation_year`` según el patrón alterno del CONTEXT.

    El crecimiento real alterna entre X+Y y X−Y, así que el mejor estimador
    causal del crecimiento del año Y es el del año Y−2: total(Y−1) · (1 + g(Y−2)).
    Solo usa años completos anteriores al fold (necesita 3 años de historia).
    """
    annual = history.groupby(history["month"].dt.year)[TARGET].sum()
    needed = [validation_year - 1, validation_year - 2, validation_year - 3]
    if any(year not in annual.index for year in needed):
        raise ValueError(f"Se necesitan los años completos {needed[::-1]} antes de {validation_year}.")
    growth_two_years_ago = annual[validation_year - 2] / annual[validation_year - 3] - 1
    return float(annual[validation_year - 1] * (1 + growth_two_years_ago))


def corrective_experiment(train_raw: pd.DataFrame, cv: dict[str, Any]) -> dict[str, Any]:
    """Acción correctiva propuesta, evaluada en los mismos folds y sin fuga.

    Conserva el perfil mensual (estacionalidad) del pronóstico recursivo del
    Random Forest de cada fold y re-escala su total anual al ancla del
    crecimiento alterno (:func:`alternating_growth_anchor`). No toca 2024–2025.
    """
    rows = []
    for fold in cv["folds"]:
        monthly = pd.DataFrame(fold["validation_monthly"])
        year = int(fold["validation_period"][0][:4])
        history = train_raw.loc[train_raw["month"].dt.year < year]
        predicted = monthly["revenue_eur_predicted"].to_numpy(dtype=float)
        actual = monthly["revenue_eur_actual"].to_numpy(dtype=float)
        adjusted = predicted * alternating_growth_anchor(history, year) / predicted.sum()
        rows.append({
            "fold": fold["fold"],
            "validation_year": year,
            "baseline": fold["validation"],
            "anchored": regression_errors(actual, adjusted),
        })
    summary = {
        variant: {
            metric: summarize([row[variant][metric] for row in rows])
            for metric in ("mae_eur", "rmse_eur", "rmse_pct_of_mean_monthly_revenue")
        }
        for variant in ("baseline", "anchored")
    }
    return {"description": alternating_growth_anchor.__doc__.strip().splitlines()[0], "folds": rows, "summary_mean_std": summary}


def summarize(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    return {"mean": float(array.mean()), "std": float(array.std(ddof=1)) if len(array) > 1 else 0.0}


def cross_validate(train_raw: pd.DataFrame) -> dict[str, Any]:
    folds = temporal_folds(len(train_raw))
    results = []
    for number, (train_idx, val_idx) in enumerate(folds, start=1):
        result = fit_and_score(train_raw.iloc[train_idx], train_raw.iloc[val_idx])
        result["fold"] = number
        result["train_index"] = [int(train_idx[0]), int(train_idx[-1])]
        result["validation_index"] = [int(val_idx[0]), int(val_idx[-1])]
        results.append(result)

    summary = {
        split: {
            metric: summarize([r[split][metric] for r in results])
            for metric in ("mae_eur", "rmse_eur", "mae_pct_of_mean_monthly_revenue", "rmse_pct_of_mean_monthly_revenue")
        }
        for split in ("train", "validation")
    }
    return {
        "strategy": f"TimeSeriesSplit(n_splits={N_SPLITS}, test_size={VALIDATION_MONTHS}), ventana expansiva, sin shuffle",
        "folds": results,
        "summary_mean_std": summary,
    }


# --------------------------------------------------------------------------
# Curva de aprendizaje
# --------------------------------------------------------------------------
def learning_curve(train_raw: pd.DataFrame) -> list[dict[str, Any]]:
    """Error de entrenamiento y validación según los meses de historia.

    Para cada tamaño de ventana W y cada año de validación V (2021, 2022 y
    2023), se entrena con los W meses inmediatamente anteriores a V y se
    valida V de forma recursiva. Así el tamaño de entrenamiento crece sin
    cambiar la distancia al periodo validado; el punto de la curva es la media
    sobre los orígenes disponibles (± desviación estándar si hay más de uno).
    """
    months = train_raw["month"]
    points = []
    for window in LEARNING_CURVE_WINDOWS:
        runs = []
        for year in LEARNING_CURVE_ORIGINS:
            val_mask = months.dt.year == year
            start = int(np.flatnonzero(val_mask)[0])
            if start - window < 0:
                continue
            runs.append(fit_and_score(train_raw.iloc[start - window : start], train_raw.loc[val_mask]))
        if not runs:
            continue
        points.append({
            "train_months": window,
            "train_rows": runs[0]["train_rows"],
            "validation_years": [r["validation_period"][0][:4] for r in runs],
            "train_mae": summarize([r["train"]["mae_eur"] for r in runs]),
            "train_rmse": summarize([r["train"]["rmse_eur"] for r in runs]),
            "validation_mae": summarize([r["validation"]["mae_eur"] for r in runs]),
            "validation_rmse": summarize([r["validation"]["rmse_eur"] for r in runs]),
        })
    return points


def noise_floor(train_raw: pd.DataFrame) -> dict[str, float]:
    """Ruido mensual irreducible estimado en el set de entrenamiento.

    Desviación típica del log(revenue_eur) tras quitar el nivel de cada año y
    el perfil de cada mes (efectos fijos año + mes). Es la fluctuación ±5 % que
    el CONTEXT describe alrededor de la tendencia: ningún modelo con estas
    features debería bajar de ahí fuera de muestra.
    """
    data = train_raw.assign(log_revenue=np.log(train_raw[TARGET]), year=train_raw["month"].dt.year,
                            month_number=train_raw["month"].dt.month)
    residual = (data["log_revenue"] - data.groupby("year")["log_revenue"].transform("mean")
                - (data.groupby("month_number")["log_revenue"].transform("mean") - data["log_revenue"].mean()))
    pct = float(residual.std(ddof=1) * 100)
    validated = data.loc[data["year"] >= 2019, TARGET].mean()  # años validados en la CV
    return {"pct_of_monthly_revenue": pct, "eur_at_2019_2023_mean": float(pct / 100 * validated)}


def save_learning_curve(points: list[dict[str, Any]], cv: dict[str, Any], path: Path) -> None:
    train_color, val_color, ink = "#2563eb", "#d97706", "#1f2937"
    x = np.array([p["train_rows"] for p in points])
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharey=True)
    for ax, metric, label in ((axes[0], "rmse", "RMSE"), (axes[1], "mae", "MAE")):
        for split, color, name in (("train", train_color, "Entrenamiento (in-sample, 1 paso)"),
                                   ("validation", val_color, "Validación (recursiva 12 meses)")):
            mean = np.array([p[f"{split}_{metric}"]["mean"] for p in points]) / 1000
            std = np.array([p[f"{split}_{metric}"]["std"] for p in points]) / 1000
            ax.plot(x, mean, color=color, marker="o", markersize=8, linewidth=2, label=name)
            ax.fill_between(x, mean - std, mean + std, color=color, alpha=0.15, linewidth=0)
        cv_mean = cv["summary_mean_std"]["validation"][f"{metric}_eur"]["mean"] / 1000
        ax.axhline(cv_mean, color=ink, linestyle=":", linewidth=1.5, label=f"Validación media CV 5 folds ({cv_mean:.0f} k€)")
        if metric == "rmse":
            floor = cv["noise_floor"]["eur_at_2019_2023_mean"] / 1000
            ax.axhline(floor, color="#6b7280", linestyle="--", linewidth=1.2,
                       label=f"Suelo de ruido irreducible ≈{cv['noise_floor']['pct_of_monthly_revenue']:.1f} % ({floor:.0f} k€)")
        ax.set_title(f"{label} mensual de revenue_eur (k€)", loc="left", fontsize=11)
        ax.set_xlabel("Filas de entrenamiento (meses con 24 de historia previa)")
        ax.grid(True, axis="y", alpha=0.25)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for xi, p in zip(x, points):
            ax.annotate(f"n={len(p['validation_years'])}", (xi, 0), textcoords="offset points", xytext=(0, 4),
                        ha="center", fontsize=8, color="#6b7280", xycoords=("data", "axes fraction"))
    axes[0].set_ylabel("Error (miles de EUR)")
    axes[0].legend(frameon=False, loc="upper right", fontsize=9)
    fig.suptitle("TrackFlow — curva de aprendizaje del pronóstico consolidado (set de entrenamiento 2016–2023)",
                 x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


# --------------------------------------------------------------------------
# Orquestación
# --------------------------------------------------------------------------
def evaluate(data_path: Path = DATA_PATH, output_dir: Path = EVAL_DIR) -> dict[str, Any]:
    sales = load_sales_data(data_path)
    train_raw, _test_not_used = split_train_test(sales)  # 2024–2025 no participa en la evaluación
    cv = cross_validate(train_raw)
    cv["corrective_experiment"] = corrective_experiment(train_raw, cv)
    cv["noise_floor"] = noise_floor(train_raw)
    points = learning_curve(train_raw)

    output_dir.mkdir(parents=True, exist_ok=True)
    save_learning_curve(points, cv, output_dir / "learning_curve.png")
    with (output_dir / "cv_metrics.json").open("w", encoding="utf-8") as file:
        json.dump(cv, file, indent=2, ensure_ascii=False, allow_nan=False)
    with (output_dir / "learning_curve.json").open("w", encoding="utf-8") as file:
        json.dump(points, file, indent=2, ensure_ascii=False, allow_nan=False)
    return {"cv": cv, "learning_curve": points}


def main() -> None:
    results = evaluate()
    summary = results["cv"]["summary_mean_std"]
    print(results["cv"]["strategy"])
    for fold in results["cv"]["folds"]:
        print(
            f"fold {fold['fold']}: train {fold['train_period'][0]}..{fold['train_period'][1]} ({fold['train_rows']} filas) | "
            f"val {fold['validation_period'][0][:7]}..{fold['validation_period'][1][:7]} | "
            f"train MAE {fold['train']['mae_eur']:,.0f} RMSE {fold['train']['rmse_eur']:,.0f} | "
            f"val MAE {fold['validation']['mae_eur']:,.0f} RMSE {fold['validation']['rmse_eur']:,.0f} "
            f"({fold['validation']['rmse_pct_of_mean_monthly_revenue']:.1f}%)"
        )
    for split in ("train", "validation"):
        s = summary[split]
        print(
            f"{split:10}: MAE {s['mae_eur']['mean']:,.0f} ± {s['mae_eur']['std']:,.0f} EUR "
            f"({s['mae_pct_of_mean_monthly_revenue']['mean']:.2f} ± {s['mae_pct_of_mean_monthly_revenue']['std']:.2f} %) | "
            f"RMSE {s['rmse_eur']['mean']:,.0f} ± {s['rmse_eur']['std']:,.0f} EUR "
            f"({s['rmse_pct_of_mean_monthly_revenue']['mean']:.2f} ± {s['rmse_pct_of_mean_monthly_revenue']['std']:.2f} %)"
        )
    experiment = results["cv"]["corrective_experiment"]
    print("acción correctiva (ancla de crecimiento alterno):")
    for row in experiment["folds"]:
        print(
            f"  {row['validation_year']}: RMSE {row['baseline']['rmse_eur']:,.0f} -> {row['anchored']['rmse_eur']:,.0f} EUR | "
            f"MAE {row['baseline']['mae_eur']:,.0f} -> {row['anchored']['mae_eur']:,.0f} EUR"
        )
    for variant in ("baseline", "anchored"):
        s = experiment["summary_mean_std"][variant]
        print(f"  {variant:9}: RMSE {s['rmse_eur']['mean']:,.0f} ± {s['rmse_eur']['std']:,.0f} EUR | MAE {s['mae_eur']['mean']:,.0f} ± {s['mae_eur']['std']:,.0f} EUR")
    print("curva:")
    for p in results["learning_curve"]:
        print(
            f"  {p['train_months']} meses ({p['train_rows']} filas, val {','.join(p['validation_years'])}): "
            f"train RMSE {p['train_rmse']['mean']:,.0f} | val RMSE {p['validation_rmse']['mean']:,.0f} ± {p['validation_rmse']['std']:,.0f} | "
            f"train MAE {p['train_mae']['mean']:,.0f} | val MAE {p['validation_mae']['mean']:,.0f}"
        )


if __name__ == "__main__":
    main()
