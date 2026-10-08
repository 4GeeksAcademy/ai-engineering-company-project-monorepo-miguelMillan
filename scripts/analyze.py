import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from incidents_analysis import (
    CSVFormatError,
    analyze_csv_text,
    reference_mismatches,
    render_report,
    write_results_csv,
)


def main():
    parser = argparse.ArgumentParser(
        description="Valida y analiza un CSV local de incidencias de TrackFlow."
    )
    parser.add_argument("csv_path", help="Ruta al fichero CSV de incidencias")
    args = parser.parse_args()
    source_path = Path(args.csv_path)

    try:
        csv_text = source_path.read_text(encoding="utf-8-sig")
        report = analyze_csv_text(csv_text)
    except (OSError, CSVFormatError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    if source_path.name == "incidents-trackflow.csv":
        mismatches = reference_mismatches(report)
        if mismatches:
            print("Error: los resultados no coinciden con la referencia de CONTEXT.md.", file=sys.stderr)
            for mismatch in mismatches:
                print(f"  - {mismatch}", file=sys.stderr)
            return 2

    print(render_report(report, source_path.name))
    try:
        answer = input("¿Deseas exportar los resultados a CSV? [s/n] ").strip()
    except EOFError:
        answer = "n"
    if answer in {"s", "S"}:
        destination = Path("results.csv")
        write_results_csv(report, destination)
        print(f"Resultados exportados a {destination.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())