
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CARRIL_DIRS = ("services", "endpoints", "workers")

WHITELIST: dict[str, dict[str, str]] = {
    "services/documents/signing/signing.py": {
        "mecanismo": "acquires_lock_here",
        "razon": (
            "Sync signing (_sign_common_signer): adquiere acquire_signing_lock_R2 "
            "antes de call_notary_sign_pdf en la misma función."
        ),
    },
    "workers/escri.py": {
        "mecanismo": "inprocess_upstream",
        "razon": (
            "Worker escri: los jobs sign / sign_common procesan documentos ya "
            "movidos a tosign/inprocess/ por el dispatcher / unified_signing. "
            "La serialización dentro del lote la cubre "
            "test_gdi215_serializacion_por_documento.py."
        ),
    },
    "services/documents/signing/numerator.py": {
        "mecanismo": "inprocess_upstream",
        "razon": (
            "Numerator async: se invoca DESDE el worker escri sobre el PDF ya "
            "descargado desde tosign/inprocess/. El lock lo tiene el worker."
        ),
    },
    "services/cases/cover_creator.py": {
        "mecanismo": "out_of_tosign_flow",
        "razon": (
            "CAEX: crea la carátula del expediente. El PDF sale de PDFComposer "
            "en memoria, se firma inline y va DIRECTO a oficial/ — nunca toca "
            "tosign/. No hay documento compartido que pueda pisarse: la "
            "invariante GDI-215 no aplica."
        ),
    },
    "services/cases/_document_creator_base.py": {
        "mecanismo": "out_of_tosign_flow",
        "razon": (
            "Helper de PV / informes de legajo: mismo esquema que cover_creator "
            "(PDFComposer → firma inline → oficial/)."
        ),
    },
    "services/documents/creation/tst_creator.py": {
        "mecanismo": "out_of_tosign_flow",
        "razon": (
            "TST (relleno de hueco global): documento del sistema, PDFComposer → "
            "firma inline → oficial/. No pasa por tosign/."
        ),
    },
    "services/documents/signing/citizen_signing.py": {
        "mecanismo": "inprocess_upstream",
        "razon": (
            "TAD ciudadano: HOY descarga del bucket tosign/ (get_tosign_url) y "
            "firma sin llamar a acquire_signing_lock_R2. Se acepta en la "
            "whitelist porque cada sesión TAD es del propio ciudadano firmando "
            "SU documento (no hay firmantes internos concurrentes) — pero "
            "queda anotado como HALLAZGO cross-carril: dos requests TAD del "
            "mismo ciudadano sobre el mismo documento no están serializados "
            "por el lock R2. Si el flujo TAD se amplía a otros firmantes "
            "sobre el mismo doc, este carril rompe GDI-215."
        ),
    },
}


def _iter_source_files() -> list[Path]:
    archivos: list[Path] = []
    for sub in CARRIL_DIRS:
        base = REPO_ROOT / sub
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if p.name == "notary_api.py":
                continue
            archivos.append(p)
    return archivos


def _grep_callers(pattern: str) -> set[str]:
    hits: set[str] = set()
    rx = re.compile(rf"(?<!def\s){re.escape(pattern)}\s*\(")
    for f in _iter_source_files():
        try:
            src = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if rx.search(src):
            hits.add(f.relative_to(REPO_ROOT).as_posix())
    return hits


class TestInventarioCallers:

    def test_todos_los_callers_estan_en_la_whitelist(self):
        callers = _grep_callers("call_notary_sign_pdf")
        no_cubiertos = sorted(callers - set(WHITELIST))
        assert not no_cubiertos, (
            "GDI-215: se detectaron llamadores NUEVOS de call_notary_sign_pdf "
            "que no están en la whitelist de "
            "tests/test_gdi215_invariante_cross_carril.py.\n"
            "Cada nuevo carril tiene que declarar cómo respeta la "
            "serialización por documento (acquires_lock_here / "
            "inprocess_upstream / out_of_tosign_flow) con su razón:\n  - "
            + "\n  - ".join(no_cubiertos)
        )

    def test_la_whitelist_no_tiene_entradas_muertas(self):
        callers = _grep_callers("call_notary_sign_pdf")
        muertas = sorted(set(WHITELIST) - callers)
        assert not muertas, (
            "Entradas de WHITELIST que ya no matchean con el árbol de "
            "fuentes (limpiar):\n  - " + "\n  - ".join(muertas)
        )


class TestOrdenAcquireAntesDeNotary:

    @pytest.mark.parametrize(
        "ruta",
        [
            r
            for r, meta in WHITELIST.items()
            if meta["mecanismo"] == "acquires_lock_here"
        ],
    )
    def test_acquire_precede_a_call_notary(self, ruta: str):
        path = REPO_ROOT / ruta
        src = path.read_text(encoding="utf-8")

        idx_lock = src.find("acquire_signing_lock_R2(")
        idx_notary = src.find("call_notary_sign_pdf(")
        assert idx_lock != -1, (
            f"{ruta}: declara 'acquires_lock_here' pero NO llama a "
            "acquire_signing_lock_R2. Reclasificar o corregir."
        )
        assert idx_lock < idx_notary, (
            f"{ruta}: acquire_signing_lock_R2 aparece DESPUÉS de "
            f"call_notary_sign_pdf en la fuente (idx lock={idx_lock}, "
            f"idx notary={idx_notary}). Rompería la invariante GDI-215."
        )


class TestContratoInprocessUpstream:

    @pytest.mark.parametrize(
        "ruta",
        [
            r
            for r, meta in WHITELIST.items()
            if meta["mecanismo"] == "inprocess_upstream"
        ],
    )
    def test_source_referencia_inprocess(self, ruta: str):
        src = (REPO_ROOT / ruta).read_text(encoding="utf-8")
        assert "inprocess" in src or "tosign" in src, (
            f"{ruta}: declara 'inprocess_upstream' pero su source no "
            "referencia inprocess/ ni tosign — probable rotura de la "
            "invariante o mala clasificación."
        )


class TestContratoFueraDelFlujoTosign:

    @pytest.mark.parametrize(
        "ruta",
        [
            r
            for r, meta in WHITELIST.items()
            if meta["mecanismo"] == "out_of_tosign_flow"
        ],
    )
    def test_no_baja_de_tosign(self, ruta: str):
        src = (REPO_ROOT / ruta).read_text(encoding="utf-8")
        sospechosos = [
            token
            for token in ("get_tosign_url", 'bucket="tosign"', "inprocess/")
            if token in src
        ]
        assert not sospechosos, (
            f"{ruta}: clasificado 'out_of_tosign_flow' pero el source "
            f"contiene señales del flujo compartido: {sospechosos}. "
            "Reclasificar; probablemente necesita 'inprocess_upstream' "
            "y adquirir/heredar el lock R2."
        )
