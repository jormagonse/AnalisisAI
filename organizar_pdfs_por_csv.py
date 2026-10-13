# -*- coding: utf-8 -*-
"""Relaciona registros de evaluaciones_completo.csv con PDFs judiciales.

El programa conserva los PDF originales y crea una copia organizada por el
orden de aparición del CSV. Se consideran coincidencias fuertes y parciales:

    salida/
      001_<caratula>/
        registro_0001_id_22/
          fuertes/
          parciales/
          registro.json

La identificación combina texto extraído del PDF y nombre/ruta del archivo.
No depende de que el PDF tenga un nombre con el ID del CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - mensaje de instalación
    raise SystemExit("Falta pypdf. Instala con: python -m pip install pypdf") from exc


STOPWORDS = {
    "A", "AL", "ANTE", "BAJO", "CON", "CONTRA", "DE", "DEL", "DESDE",
    "DURANTE", "EL", "EN", "ENTRE", "EXPEDIENTE", "LA", "LAS", "LOS",
    "PARA", "POR", "S", "SIN", "SOBRE", "UN", "UNA", "Y", "N", "NUMERO",
    "CAUSA", "CARATULA", "JUZGADO", "PENAL", "GARANTIAS", "TRIBUNAL",
    "AUDIENCIA", "AUTO", "INTERLOCUTORIO", "RESOLUCION", "IMPUTADO",
    "IMPUTADA", "MINISTERIO", "PUBLICO", "FISCALIA", "ABOGADO", "ABOGADA",
    "DELITO", "CRIMEN", "HECHO", "PUNIBLE", "OTROS", "OTRAS",
}


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.upper().replace("N°", " NUMERO ").replace("Nº", " NUMERO ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: str, limit: int = 140) -> str:
    result = norm(value).lower().replace(" ", "_")
    result = re.sub(r"[^a-z0-9_-]+", "", result).strip("._-")
    return (result or "sin_caratula")[:limit]


def unique_tokens(value: str, minimum: int = 4) -> list[str]:
    return list(dict.fromkeys(
        token for token in norm(value).split()
        if len(token) >= minimum and token not in STOPWORDS
    ))


def field_tokens(value: str) -> set[str]:
    return set(unique_tokens(value))


def parse_date(value: str) -> set[str]:
    # Se buscan las tres formas habituales: yyyy-mm-dd, dd-mm-yyyy y dd/mm/yyyy.
    hits = set()
    for year, month, day in re.findall(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", value or ""):
        hits.add(f"{year}{int(month):02d}{int(day):02d}")
    for day, month, year in re.findall(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2})", value or ""):
        hits.add(f"{year}{int(month):02d}{int(day):02d}")
    return hits


def significant_numbers(value: str) -> list[str]:
    return list(dict.fromkeys(
        part for part in re.findall(r"\d+", value or "")
        if len(part) >= 3
    ))


def charge_tokens(caratula: str, name: str, offense: str) -> list[str]:
    """Obtiene el delito/descripción, separándolo del nombre de la persona."""
    # En las carátulas judiciales, lo que sigue a "S/" suele ser el hecho.
    after_s = re.split(r"\bS\s*/\s*", norm(caratula), maxsplit=1)
    description = after_s[1] if len(after_s) == 2 else norm(caratula)
    name_words = set(unique_tokens(name, minimum=4))
    return list(dict.fromkeys(
        word for word in unique_tokens(f"{description} {offense}", minimum=4)
        if word not in name_words
    ))


def safe_copy_name(path: Path, score: int, kind: str) -> str:
    return f"{kind}_{score:03d}_{path.name}"


def read_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return norm("\n".join(pages))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class PdfDocument:
    path: Path
    digest: str
    text: str
    source_paths: list[str]


def discover_pdfs(pdf_dir: Path) -> list[PdfDocument]:
    by_hash: dict[str, PdfDocument] = {}
    ignored = {"organizados_por_evaluacion", "organizados_por_evaluacion_v2"}
    paths = (
        path for path in pdf_dir.rglob("*.pdf")
        if not any(part in ignored for part in path.relative_to(pdf_dir).parts)
    )
    for path in sorted(paths):
        digest = sha256(path)
        if digest not in by_hash:
            try:
                text = read_pdf(path)
            except Exception as exc:
                text = ""
                error = f"error_lectura: {exc}"
            else:
                error = ""
            by_hash[digest] = PdfDocument(path, digest, text, [str(path)])
            if error:
                by_hash[digest].source_paths.append(error)
        else:
            by_hash[digest].source_paths.append(str(path))
    return list(by_hash.values())


def compare(row: dict[str, str], document: PdfDocument) -> dict[str, object]:
    # El nombre/ruta también es evidencia: algunos PDFs no tienen capa de texto.
    haystack = f"{norm(document.path)} {document.text}"
    name = norm(row.get("nombre_completo") or row.get("nombre_imputado"))
    case = norm(row.get("numero_expediente"))
    cover = norm(row.get("caratula"))
    court = norm(row.get("juzgado_tribunal"))
    offense = norm(row.get("categoria_delito"))
    pdf_numbers = set(significant_numbers(haystack))
    row_years = {x for x in re.findall(r"20\d{2}", case)}
    row_numbers = [x for x in significant_numbers(case) if x not in row_years]

    hits: dict[str, object] = {}
    score = 0
    field_count = 0

    ci = re.sub(r"\D", "", row.get("ci_pasaporte", ""))
    if len(ci) >= 5 and ci in re.sub(r"\D", "", haystack):
        score += 100
        field_count += 1
        hits["ci_pasaporte"] = ci

    exact_case = bool(case and case in haystack)
    if exact_case:
        score += 100
        field_count += 1
        hits["numero_expediente"] = case
    else:
        number_hits = sorted(set(row_numbers) & pdf_numbers)
        year_hits = sorted(set(row_years) & pdf_numbers)
        if number_hits and year_hits:
            score += 65
            field_count += 1
            hits["componentes_expediente"] = number_hits + year_hits

    if name and name in haystack:
        score += 85
        field_count += 1
        hits["nombre_completo"] = name
    else:
        name_words = unique_tokens(name, minimum=4)
        name_hits = [word for word in name_words if word in haystack]
        if len(name_hits) >= 2:
            score += min(60, 20 * len(name_hits))
            field_count += 1
            hits["nombre_parcial"] = name_hits

    cover_words = charge_tokens(cover, name, offense)
    cover_hits = [word for word in cover_words if word in haystack]
    if len(cover_hits) >= 2:
        score += min(30, 10 * len(cover_hits))
        field_count += 1
        hits["caratula"] = cover_hits

    court_words = field_tokens(court)
    court_hits = sorted(word for word in court_words if word in haystack)
    if len(court_hits) >= 2:
        score += 15
        field_count += 1
        hits["juzgado_tribunal"] = court_hits

    offense_words = set(charge_tokens(cover, name, offense))
    offense_hits = sorted(word for word in offense_words if word in haystack)
    if offense_hits:
        score += min(20, 10 * len(offense_hits))
        field_count += 1
        hits["delito"] = offense_hits

    date_hits = parse_date(row.get("fecha", "")) & parse_date(str(document.path))
    if date_hits:
        score += 12
        field_count += 1
        hits["fecha"] = sorted(date_hits)

    strong = bool(
        ci in re.sub(r"\D", "", haystack) if len(ci) >= 5 else False
    ) or exact_case or (name and name in haystack)
    identity_fields = {"ci_pasaporte", "numero_expediente", "componentes_expediente",
                       "nombre_completo", "nombre_parcial"}
    context_fields = {"caratula", "juzgado_tribunal", "delito", "fecha"}
    has_identity = bool(identity_fields & hits.keys())
    has_context = bool(context_fields & hits.keys())
    selected = strong or (score >= 35 and field_count >= 2 and has_identity and has_context)
    kind = "fuerte" if strong else ("parcial" if selected else "no_seleccionado")
    return {
        "estado": kind,
        "puntaje": score,
        "campos_coincidentes": field_count,
        "evidencia": hits,
        "texto_extraido": bool(document.text),
        "sha256": document.digest,
        "origenes_pdf": document.source_paths,
    }


def write_csv(path: Path, records: Iterable[dict[str, object]]) -> None:
    records = list(records)
    keys = sorted({key for record in records for key in record})
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--min-partial-score", type=int, default=35)
    args = parser.parse_args()

    with args.csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter=";"))

    documents = discover_pdfs(args.pdf_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_matches: list[dict[str, object]] = []

    for position, row in enumerate(rows, start=1):
        case_dir = args.out_dir / f"{position:03d}_{slug(row.get('caratula', ''))}"
        record_dir = case_dir / f"registro_{position:04d}_id_{slug(row.get('id', 'sin_id'))}"
        strong_dir = record_dir / "fuertes"
        partial_dir = record_dir / "parciales"
        match_records = []

        for document in documents:
            match = compare(row, document)
            if match["estado"] == "parcial" and match["puntaje"] < args.min_partial_score:
                match["estado"] = "no_seleccionado"
            if match["estado"] in {"fuerte", "parcial"}:
                target_dir = strong_dir if match["estado"] == "fuerte" else partial_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / safe_copy_name(document.path, int(match["puntaje"]), match["estado"])
                if not target.exists():
                    shutil.copy2(document.path, target)
                match["destino"] = str(target)
                match_records.append({"archivo": str(document.path), **match})

        summary = {
            "orden_csv": position,
            "id": row.get("id", ""),
            "caratula": row.get("caratula", ""),
            "nombre_completo": row.get("nombre_completo", ""),
            "numero_expediente": row.get("numero_expediente", ""),
            "ci_pasaporte": row.get("ci_pasaporte", ""),
            "juzgado_tribunal": row.get("juzgado_tribunal", ""),
            "fecha": row.get("fecha", ""),
            "cantidad_pdfs_unicos_analizados": len(documents),
            "cantidad_fuertes": sum(x["estado"] == "fuerte" for x in match_records),
            "cantidad_parciales": sum(x["estado"] == "parcial" for x in match_records),
            "coincidencias": match_records,
        }
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "registro.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        all_matches.append(summary)

    (args.out_dir / "resumen.json").write_text(
        json.dumps({"registros": all_matches, "pdfs_unicos": len(documents)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    flat = []
    for record in all_matches:
        for match in record["coincidencias"]:
            flat.append({
                "orden_csv": record["orden_csv"], "id": record["id"],
                "caratula": record["caratula"], "nombre_completo": record["nombre_completo"],
                "numero_expediente": record["numero_expediente"], **match,
            })
    write_csv(args.out_dir / "coincidencias.csv", flat)
    print(f"Registros CSV: {len(rows)}")
    print(f"PDFs únicos analizados: {len(documents)}")
    print(f"PDFs fuertes seleccionados: {sum(x['cantidad_fuertes'] for x in all_matches)}")
    print(f"PDFs parciales seleccionados: {sum(x['cantidad_parciales'] for x in all_matches)}")
    print(f"Salida: {args.out_dir}")


if __name__ == "__main__":
    main()
