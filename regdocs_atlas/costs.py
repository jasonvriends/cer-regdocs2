"""Usage-based cost estimation for Azure Content Understanding runs.

Content Understanding exposes document usage meters in analysis result JSON. The
actual dollar rates are deliberately configuration, not constants: Microsoft
pricing is region/currency/offer dependent and changes over time.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import open_ledger
from .paths import PROJECT_ROOT, resolve_stored_path


@dataclass(frozen=True)
class AzureContentUnderstandingRates:
    minimal_per_1000_usd: float | None
    basic_per_1000_usd: float | None
    standard_per_1000_usd: float | None

    @classmethod
    def from_env(cls) -> "AzureContentUnderstandingRates":
        def value(name: str) -> float | None:
            raw = os.environ.get(name)
            if raw in (None, ""):
                return None
            try:
                number = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a number") from exc
            if number < 0:
                raise ValueError(f"{name} cannot be negative")
            return number

        return cls(
            value("REGDOCS_AZURE_CU_MINIMAL_PER_1000_USD"),
            value("REGDOCS_AZURE_CU_BASIC_PER_1000_USD"),
            value("REGDOCS_AZURE_CU_STANDARD_PER_1000_USD"),
        )

    def to_dict(self) -> dict[str, float | None]:
        return {
            "minimal_per_1000_usd": self.minimal_per_1000_usd,
            "basic_per_1000_usd": self.basic_per_1000_usd,
            "standard_per_1000_usd": self.standard_per_1000_usd,
        }


USAGE_KEYS = {
    "minimal": ("documentPagesMinimal", "document_pages_minimal"),
    "basic": ("documentPagesBasic", "document_pages_basic"),
    "standard": ("documentPagesStandard", "document_pages_standard"),
}


def _number(mapping: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        raw = mapping.get(name)
        if raw is not None:
            try:
                return max(int(raw), 0)
            except (TypeError, ValueError):
                return 0
    return 0


def usage_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {name: 0 for name in USAGE_KEYS}
    return {name: _number(usage, keys) for name, keys in USAGE_KEYS.items()}


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def artifact_usage(raw_path: Path) -> dict[str, int]:
    payload = _load_json(raw_path)
    if payload is None:
        return {name: 0 for name in USAGE_KEYS}

    # Ranged PDFs preserve each Azure response. Summing the part usage avoids
    # undercounting when the canonical combined JSON inherited only one usage object.
    chunking = payload.get("regdocsChunking")
    parts = chunking.get("parts") if isinstance(chunking, dict) else None
    if isinstance(parts, list) and parts:
        total = {name: 0 for name in USAGE_KEYS}
        found = False
        for part in parts:
            if not isinstance(part, dict):
                continue
            stored = part.get("rawJsonPath") or part.get("raw_json_path")
            if not stored:
                continue
            part_payload = _load_json(resolve_stored_path(str(stored)))
            if part_payload is None:
                continue
            observed = usage_from_payload(part_payload)
            if any(observed.values()):
                found = True
            for name in total:
                total[name] += observed[name]
        if found:
            return total
    return usage_from_payload(payload)


def estimate_usage_cost(usage: dict[str, int], rates: AzureContentUnderstandingRates) -> tuple[float | None, list[str]]:
    missing: list[str] = []
    cost = 0.0
    for meter, rate in (
        ("minimal", rates.minimal_per_1000_usd),
        ("basic", rates.basic_per_1000_usd),
        ("standard", rates.standard_per_1000_usd),
    ):
        pages = int(usage.get(meter, 0) or 0)
        if not pages:
            continue
        if rate is None:
            missing.append(meter)
            continue
        cost += pages * rate / 1000.0
    if missing:
        return None, missing
    return round(cost, 6), []


def azure_run_cost_snapshot(db_path: Path, run_id: int, rates: AzureContentUnderstandingRates | None = None) -> dict[str, Any]:
    rates = rates or AzureContentUnderstandingRates.from_env()
    con = open_ledger(db_path, readonly=True)
    try:
        run = con.execute(
            "SELECT id,status,total_units,completed_units,summary_json FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        rows = con.execute(
            """
            SELECT document_id, raw_json_path
            FROM analyses
            WHERE run_id=? AND status='SUCCEEDED' AND raw_json_path IS NOT NULL
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    finally:
        con.close()

    total_usage = {name: 0 for name in USAGE_KEYS}
    priced_documents = 0
    usage_documents = 0
    estimated = 0.0
    missing_meters: set[str] = set()
    for row in rows:
        raw = resolve_stored_path(str(row["raw_json_path"]))
        usage = artifact_usage(raw)
        if not any(usage.values()):
            continue
        usage_documents += 1
        for name in total_usage:
            total_usage[name] += usage[name]
        document_cost, missing = estimate_usage_cost(usage, rates)
        if missing:
            missing_meters.update(missing)
        elif document_cost is not None:
            priced_documents += 1
            estimated += document_cost

    total_docs = int(run["total_units"] or 0)
    completed = int(run["completed_units"] or 0)
    estimated_value: float | None = round(estimated, 4) if usage_documents and not missing_meters else None
    projected: float | None = None
    if estimated_value is not None and priced_documents > 0 and total_docs > 0:
        projected = round((estimated / priced_documents) * total_docs, 4)

    return {
        "provider": "azure",
        "billing_model": "content_understanding_document_usage",
        "currency": "USD",
        "run_id": run_id,
        "documents_total": total_docs,
        "documents_completed": completed,
        "documents_with_usage": usage_documents,
        "usage": {
            "document_pages_minimal": total_usage["minimal"],
            "document_pages_basic": total_usage["basic"],
            "document_pages_standard": total_usage["standard"],
            "document_pages_total": sum(total_usage.values()),
        },
        "rates": rates.to_dict(),
        "estimated_cost_usd": estimated_value,
        "projected_total_cost_usd": projected,
        "missing_rate_meters": sorted(missing_meters),
        "pricing_status": "configured" if not missing_meters and any(v is not None for v in rates.to_dict().values()) else "n/a_unconfigured",
        "note": "Estimate from Azure result usage meters and user-configured rates; Azure invoice remains authoritative.",
    }


def persist_run_cost(db_path: Path, run_id: int, cost: dict[str, Any]) -> None:
    con = open_ledger(db_path)
    try:
        row = con.execute("SELECT summary_json FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        try:
            summary = json.loads(row[0] or "{}")
        except json.JSONDecodeError:
            summary = {}
        if not isinstance(summary, dict):
            summary = {}
        summary["cost"] = cost
        con.execute("UPDATE runs SET summary_json=? WHERE id=?", (json.dumps(summary, sort_keys=True), run_id))
        con.commit()
    finally:
        con.close()


def persist_local_compute_cost(db_path: Path, run_id: int, provider: str = "docling") -> dict[str, Any]:
    value = {
        "provider": provider,
        "billing_model": "local_compute",
        "currency": "USD",
        "estimated_cost_usd": None,
        "projected_total_cost_usd": None,
        "pricing_status": "n/a_local_compute",
        "note": "No Azure Content Understanding service charge; local hardware/electricity cost is not estimated.",
    }
    persist_run_cost(db_path, run_id, value)
    return value


def latest_provider_run(db_path: Path, provider: str, *, after_run_id: int = 0) -> int | None:
    con = open_ledger(db_path, readonly=True)
    try:
        rows = con.execute(
            "SELECT id,stage,parameters_json FROM runs WHERE id>? ORDER BY id DESC",
            (after_run_id,),
        ).fetchall()
    finally:
        con.close()
    for row in rows:
        try:
            params = json.loads(row["parameters_json"] or "{}")
        except json.JSONDecodeError:
            params = {}
        actual = params.get("provider") if isinstance(params, dict) else None
        stage = str(row["stage"] or "")
        if actual == provider or (provider == "docling" and stage == "analyze_docling"):
            return int(row["id"])
    return None


def pricing_environment_help() -> dict[str, str]:
    return {
        "REGDOCS_AZURE_CU_MINIMAL_PER_1000_USD": "Azure Content Understanding Document: Minimal rate per 1,000 pages",
        "REGDOCS_AZURE_CU_BASIC_PER_1000_USD": "Azure Content Understanding Document: Basic rate per 1,000 pages",
        "REGDOCS_AZURE_CU_STANDARD_PER_1000_USD": "Azure Content Understanding Document: Standard rate per 1,000 pages",
    }
