# -*- coding: utf-8 -*-
"""Distribuye los PDFs de cada registro en subcarpetas de audiencia."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


CATEGORY_DIRS = {
    "imposicion": "audiencia_imposicion",
    "revision": "audiencia_revision",
    "ambas": "audiencia_ambas",
    "no_identificada": "audiencia_no_identificada",
    "sin_texto": "audiencia_no_identificada",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--collections-dir", type=Path, required=True)
    args = parser.parse_args()

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    classification = json.loads(args.classification.read_text(encoding="utf-8"))
    by_hash = {item["sha256"]: item for item in classification["pdfs"]}
    total = {key: 0 for key in CATEGORY_DIRS.values()}

    for record in summary["registros"]:
        order = int(record["orden_csv"])
        candidates = sorted(args.collections_dir.glob(f"{order:03d}_*/registro_{order:04d}_id_*/"))
        if not candidates:
            print(f"AVISO: no existe la carpeta del registro {order:04d}")
            continue
        record_dir = candidates[0]
        manifest = []
        seen = set()
        for position, match in enumerate(record["coincidencias"], start=1):
            digest = match.get("sha256")
            if digest in seen:
                continue
            seen.add(digest)
            classified = by_hash.get(digest, {})
            category = classified.get("categoria", "no_identificada")
            category_dir = CATEGORY_DIRS.get(category, "audiencia_no_identificada")
            target_dir = record_dir / category_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            source_candidates = [Path(match.get("destino", "")), Path(classified.get("archivo", ""))]
            source_candidates.extend(Path(path) for path in classified.get("origenes_pdf", []))
            source = next((path for path in source_candidates if path.is_file()), None)
            target = target_dir / f"{position:03d}_{Path(match['archivo']).name}"
            copied = False
            if source is not None:
                shutil.copy2(source, target)
                copied = True
                total[category_dir] += 1
            manifest.append({
                "orden_en_coleccion": position,
                "categoria": category,
                "archivo_original": match["archivo"],
                "archivo_copiado": str(target) if copied else "",
                "sha256": digest,
                "puntaje": match.get("puntaje"),
                "evidencia": classified.get("evidencia", {}),
                "error": classified.get("error", "") if not copied else "",
            })
        (record_dir / "audiencias.json").write_text(
            json.dumps({
                "orden_csv": order,
                "id": record["id"],
                "caratula": record["caratula"],
                "carpetas": {key: sum(item["categoria"] == key for item in manifest) for key in CATEGORY_DIRS},
                "pdfs": manifest,
            }, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print("PDFs organizados por audiencia:")
    for category, count in total.items():
        print(f"  {category}: {count}")


if __name__ == "__main__":
    main()
