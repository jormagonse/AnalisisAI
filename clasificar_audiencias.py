# -*- coding: utf-8 -*-
"""Identifica audiencias de imposición y revisión de medidas en PDFs seleccionados."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from pypdf import PdfReader


PATTERNS = {
    "imposicion": [
        re.compile(r"AUDIENCIA.{0,120}IMPOSICION.{0,120}MEDID", re.I | re.S),
        re.compile(r"IMPOSICION.{0,80}MEDIDAS? CAUTELAR", re.I | re.S),
    ],
    "revision": [
        re.compile(r"AUDIENCIA.{0,120}REVISION.{0,120}MEDID", re.I | re.S),
        re.compile(r"REVISION.{0,100}MEDIDA.{0,100}(CAUTELAR|COERCION)", re.I | re.S),
    ],
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value.upper()).strip()


def read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def snippets(text: str, patterns: list[re.Pattern[str]], limit: int = 3) -> list[str]:
    found = []
    normalized = normalize(text)
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            start = max(0, match.start() - 90)
            end = min(len(normalized), match.end() + 120)
            snippet = normalized[start:end].strip()
            if snippet not in found:
                found.append(snippet)
            if len(found) >= limit:
                return found
    return found


def classify(text: str) -> tuple[str, dict[str, list[str]]]:
    evidence = {key: snippets(text, patterns) for key, patterns in PATTERNS.items()}
    has_imposition = bool(evidence["imposicion"])
    has_review = bool(evidence["revision"])
    if has_imposition and has_review:
        category = "ambas"
    elif has_imposition:
        category = "imposicion"
    elif has_review:
        category = "revision"
    else:
        category = "no_identificada"
    return category, evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.summary.read_text(encoding="utf-8"))
    selected: dict[str, dict[str, object]] = {}
    for record in data["registros"]:
        for match in record["coincidencias"]:
            digest = match.get("sha256")
            if digest not in selected:
                selected[digest] = {
                    "sha256": digest,
                    "archivo": match["archivo"],
                    "origenes_pdf": match.get("origenes_pdf", []),
                    "relaciones": [],
                }
            selected[digest]["relaciones"].append({
                "orden_csv": record["orden_csv"],
                "id": record["id"],
                "caratula": record["caratula"],
                "tipo_coincidencia": match["estado"],
                "puntaje": match["puntaje"],
            })

    results = []
    for index, item in enumerate(selected.values(), start=1):
        candidate_paths = [Path(path) for path in item["origenes_pdf"] if Path(path).is_file()]
        if not candidate_paths and Path(item["archivo"]).is_file():
            candidate_paths = [Path(item["archivo"])]
        path = candidate_paths[0] if candidate_paths else Path(item["archivo"])
        error = ""
        text = ""
        if path.is_file():
            try:
                text = read_pdf(path)
            except Exception as exc:
                error = str(exc)
        else:
            error = "archivo no encontrado"
        category, evidence = classify(text) if text else ("sin_texto", {"imposicion": [], "revision": []})
        results.append({
            "numero": index,
            "sha256": item["sha256"],
            "archivo": str(path),
            "nombre_pdf": path.name,
            "origenes_pdf": item["origenes_pdf"],
            "categoria": category,
            "texto_extraido": bool(text),
            "error": error,
            "evidencia": evidence,
            "relaciones_csv": sorted(item["relaciones"], key=lambda relation: relation["orden_csv"]),
        })

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "clasificacion_audiencias.json").write_text(
        json.dumps({"resumen": dict(Counter(result["categoria"] for result in results)), "pdfs": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (args.out_dir / "clasificacion_audiencias.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["numero", "categoria", "nombre_pdf", "archivo", "sha256", "relaciones_csv", "evidencia"])
        writer.writeheader()
        for result in results:
            writer.writerow({
                "numero": result["numero"],
                "categoria": result["categoria"],
                "nombre_pdf": result["nombre_pdf"],
                "archivo": result["archivo"],
                "sha256": result["sha256"],
                "relaciones_csv": "; ".join(f"{r['orden_csv']} (ID {r['id']})" for r in result["relaciones_csv"]),
                "evidencia": " | ".join(result["evidencia"].get("imposicion", []) + result["evidencia"].get("revision", [])),
            })

    lines = [
        "# Clasificación de audiencias en PDFs seleccionados",
        "",
        "La clasificación se basa en expresiones encontradas en el texto extraído de cada PDF.",
        "",
        "## Resumen",
        "",
    ]
    counts = Counter(result["categoria"] for result in results)
    for category in ("imposicion", "revision", "ambas", "no_identificada", "sin_texto"):
        lines.append(f"- {category}: **{counts[category]}**")
    lines.extend(["", "## Detalle por PDF", ""])
    for result in sorted(results, key=lambda item: (item["categoria"], item["nombre_pdf"].lower())):
        lines.extend([
            f"### {result['numero']:03d}. `{result['nombre_pdf']}` — **{result['categoria']}**",
            "",
            f"- Ruta analizada: `{result['archivo']}`",
            f"- SHA-256: `{result['sha256']}`",
            f"- Texto extraído: {'sí' if result['texto_extraido'] else 'no'}",
            "- Registros CSV relacionados: " + "; ".join(
                f"orden {relation['orden_csv']}, ID {relation['id']}, {relation['tipo_coincidencia']} ({relation['puntaje']} puntos)"
                for relation in result["relaciones_csv"]
            ),
        ])
        for kind in ("imposicion", "revision"):
            for snippet in result["evidencia"].get(kind, []):
                lines.append(f"- Evidencia {kind}: {snippet}")
        if result["error"]:
            lines.append(f"- Error: {result['error']}")
        lines.append("")
    (args.out_dir / "informe_audiencias.md").write_text("\n".join(lines), encoding="utf-8")
    print("PDFs clasificados:", len(results))
    print("Resumen:", dict(counts))
    print("Salida:", args.out_dir)


if __name__ == "__main__":
    main()
