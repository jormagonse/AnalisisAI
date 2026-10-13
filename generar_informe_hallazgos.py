# -*- coding: utf-8 -*-
"""Genera un informe Markdown/JSON de los hallazgos de la clasificación."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def evidence_text(evidence: dict[str, object]) -> str:
    parts = []
    labels = {
        "ci_pasaporte": "CI/pasaporte",
        "numero_expediente": "número de expediente",
        "componentes_expediente": "componentes de expediente",
        "nombre_completo": "nombre completo",
        "nombre_parcial": "nombre parcial",
        "caratula": "carátula",
        "juzgado_tribunal": "juzgado/tribunal",
        "delito": "delito",
        "fecha": "fecha",
    }
    for key, value in evidence.items():
        label = labels.get(key, key)
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        parts.append(f"{label}: {value}")
    return "; ".join(parts) or "sin evidencia detallada"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(args.summary.read_text(encoding="utf-8"))
    records = data["registros"]
    all_matches = [match for record in records for match in record["coincidencias"]]
    unique_by_hash = {}
    for match in all_matches:
        unique_by_hash.setdefault(match.get("sha256"), match)

    state_counts = Counter(match["estado"] for match in all_matches)
    record_counts = {
        "registros_csv": len(records),
        "pdfs_unicos_analizados": data.get("pdfs_unicos", 0),
        "pdfs_unicos_seleccionados": len(unique_by_hash),
        "coincidencias_fuertes": state_counts["fuerte"],
        "coincidencias_parciales": state_counts["parcial"],
        "registros_sin_coincidencias": sum(not record["coincidencias"] for record in records),
        "pdfs_sin_capa_texto": sum(not match.get("texto_extraido", False) for match in all_matches),
    }

    by_pdf = defaultdict(list)
    for record in records:
        for match in record["coincidencias"]:
            by_pdf[match.get("sha256")].append({
                "orden_csv": record["orden_csv"],
                "id": record["id"],
                "caratula": record["caratula"],
                "match": match,
            })

    lines = [
        "# Informe de hallazgos: PDFs relacionados con evaluaciones",
        "",
        "## Resumen ejecutivo",
        "",
        f"- Registros del CSV: **{record_counts['registros_csv']}**",
        f"- PDFs únicos analizados: **{record_counts['pdfs_unicos_analizados']}**",
        f"- PDFs únicos seleccionados en al menos un caso: **{record_counts['pdfs_unicos_seleccionados']}**",
        f"- Coincidencias fuertes: **{record_counts['coincidencias_fuertes']}**",
        f"- Coincidencias parciales: **{record_counts['coincidencias_parciales']}**",
        f"- Registros sin coincidencias: **{record_counts['registros_sin_coincidencias']}**",
        f"- PDFs seleccionados sin capa de texto: **{record_counts['pdfs_sin_capa_texto']}**",
        "",
        "Una coincidencia fuerte se basa en CI, expediente exacto o identidad muy clara. Una coincidencia parcial reúne identidad y contexto, por ejemplo nombre junto con delito, carátula, juzgado o fecha. Los puntajes sirven para priorizar la revisión y no sustituyen la lectura jurídica del documento.",
        "",
        "## Hallazgos por registro del CSV",
        "",
    ]

    for record in records:
        matches = sorted(
            record["coincidencias"],
            key=lambda item: (item["estado"] != "fuerte", -item["puntaje"], item["archivo"]),
        )
        lines.extend([
            f"### {int(record['orden_csv']):03d}. {record['caratula'] or '(sin carátula)'}",
            "",
            f"- ID CSV: `{record['id']}`",
            f"- Persona: {record['nombre_completo'] or '(sin nombre)'}, CI/pasaporte: `{record['ci_pasaporte'] or 'n/a'}`",
            f"- Expediente: `{record['numero_expediente'] or 'n/a'}`",
            f"- Juzgado/tribunal: {record['juzgado_tribunal'] or 'n/a'}",
            f"- Fecha del registro: `{record['fecha'] or 'n/a'}`",
            f"- PDFs fuertes: **{record['cantidad_fuertes']}**; parciales: **{record['cantidad_parciales']}**",
            "",
        ])
        if not matches:
            lines.extend(["> Sin PDFs seleccionados.", ""])
            continue
        lines.extend(["| Tipo | Puntaje | PDF | Elementos identificados |", "|---|---:|---|---|"])
        for match in matches:
            file_name = Path(match["archivo"]).name
            origin_count = len(match.get("origenes_pdf", []))
            origin_note = f" ({origin_count} origen(es))" if origin_count else ""
            lines.append(
                f"| {match['estado']} | {match['puntaje']} | `{file_name}`{origin_note} | "
                f"{evidence_text(match.get('evidencia', {}))} |"
            )
        lines.append("")

    lines.extend(["## PDFs únicos y casos relacionados", ""])
    for digest, relations in sorted(by_pdf.items(), key=lambda item: str(item[1][0]["match"]["archivo"]).lower()):
        first = relations[0]["match"]
        lines.extend([
            f"### `{Path(first['archivo']).name}`",
            "",
            f"- SHA-256: `{digest}`",
            f"- Origen(es): {', '.join('`' + path + '`' for path in first.get('origenes_pdf', []))}",
            f"- Capa de texto extraída: {'sí' if first.get('texto_extraido') else 'no'}",
            "- Casos relacionados:",
        ])
        for relation in sorted(relations, key=lambda item: item["orden_csv"]):
            match = relation["match"]
            lines.append(
                f"  - CSV **{int(relation['orden_csv']):03d}**, ID `{relation['id']}`, "
                f"**{match['estado']}**, puntaje {match['puntaje']}: {evidence_text(match.get('evidencia', {}))}"
            )
        lines.append("")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "informe_hallazgos.md").write_text("\n".join(lines), encoding="utf-8")
    (args.out_dir / "informe_hallazgos.json").write_text(
        json.dumps({"resumen": record_counts, "registros": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Informe Markdown: {args.out_dir / 'informe_hallazgos.md'}")
    print(f"Informe JSON: {args.out_dir / 'informe_hallazgos.json'}")


if __name__ == "__main__":
    main()
