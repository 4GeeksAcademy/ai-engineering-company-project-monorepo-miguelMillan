"""El informe NIST y el inventario no pueden contradecir al código ni quedarse a medias."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "security"
REPORT, INVENTORY, RESPONSE = ((DOCS / name).read_text(encoding="utf-8") for name in ("nist-report.md", "ai-inventory.md", "incident-response.md"))
PEOPLE = ("Andrés Kim", "Valentina Cruz", "Miguel Torres", "Carlos Vega", "Sofía Ramos", "Ana Whitfield", "Thomas")


def rows(text):
    return [[c.strip() for c in line.strip().strip("|").split("|")] for line in text.splitlines()
            if line.startswith("|") and not re.match(r"^\|[-| ]+\|$", line)]


def section(text, start, end=r"^#{2,3} "):
    body = text[re.search(start, text, re.M).end():]
    stop = re.search(end, body, re.M)
    return body[:stop.start()] if stop else body


def test_the_report_names_the_frameworks_and_deadlines_of_the_context():
    for needed in ("RGPD", "CCPA/CPRA", "AEPD", "72 h", "California"):
        assert needed in REPORT and needed in RESPONSE + REPORT


def test_each_nist_function_has_a_prioritized_action_with_an_owner():
    for function in ("GOVERN", "IDENTIFY", "PROTECT", "DETECT", "RESPOND", "RECOVER"):
        actions = rows(section(REPORT, rf"^### {function}\b"))[1:]
        assert actions
        for priority, action, owner, state in actions:
            assert priority in ("P1", "P2", "P3") and action and any(p in owner for p in PEOPLE) and state[0] in "✅⏳"
            if state.startswith("⏳"):
                assert re.search(r"B-\d+", state), f"{function}: pendiente sin brecha: {action}"


def test_every_pending_gap_has_risk_mitigation_owner_and_date():
    gaps = {r[0]: r for r in rows(section(REPORT, r"^## 6\. Brechas"))[1:]}
    assert set(re.findall(r"\bB-\d+\b", REPORT + INVENTORY + RESPONSE)) <= set(gaps)
    for gap, risk, level, mitigation, owner, date in gaps.values():
        assert len(risk) > 20 and level in ("Alto", "Medio", "Bajo") and len(mitigation) > 20 and any(p in owner for p in PEOPLE) and date
    assert f"{len(gaps)} brechas" in REPORT


def test_every_component_has_an_owner_and_exists():
    table = rows(section(INVENTORY, r"^## 1\."))[1:]
    assert [r[0] for r in table] == ["Agente de soporte 24/7 (CX)", "Motor de selección de carrier", "RAG de políticas y SLA", "Motor de aprobación de devoluciones"]
    table += rows(section(INVENTORY, r"^## 2\."))[1:]
    assert len(table) >= 12
    for row in table:
        assert any(p in " ".join(row) for p in PEOPLE), row[0]
    for path in re.findall(r"`([\w./-]+/[\w.-]+\.py)`", INVENTORY):
        assert (ROOT / path).is_file(), path


def test_every_third_party_says_what_each_side_controls():
    for row in rows(section(INVENTORY, r"^## 3\."))[1:]:
        assert len(row) == 5 and all(row) and any(p in row[3] for p in PEOPLE), row[0]


def test_every_cited_line_is_the_control_it_claims():
    for path, line in re.findall(r"`([\w./-]+\.py):(\d+)`", REPORT):
        source = (ROOT / path).read_text(encoding="utf-8").splitlines()
        assert int(line) <= len(source) and re.match(r"\s*(async )?def |[A-Z_]+ = |\s*if ", source[int(line) - 1]), f"{path}:{line}"


def test_every_file_that_builds_or_calls_an_ai_system_is_in_the_inventory():
    import subprocess

    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "*.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    signature = re.compile(r"StateGraph\(|FastMCP\(|chat\.completions|TextEmbedding|AsyncOpenAI\(|\bllm_fn\b|from langgraph|from sklearn|QdrantClient\(")
    ai = [f for f in files if "tests/" not in f and (ROOT / f).is_file() and signature.search((ROOT / f).read_text(encoding="utf-8"))]
    assert len(ai) >= 14 and [f for f in ai if f"`{f}`" not in INVENTORY] == []


def test_the_report_itself_includes_the_context_3_inventory_with_owners():
    table = rows(section(REPORT, r"^### Inventario de CONTEXT\.md §3"))[1:]
    assert len(table) == 4 and all(r[1] in PEOPLE and r[2] in PEOPLE for r in table)


def test_the_links_resolve():
    for text in (REPORT, INVENTORY, RESPONSE):
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
            assert target.startswith("http") or (DOCS / target).exists(), target


def test_the_four_context_5_actions_are_answered():
    table = rows(section(REPORT, r"^## 5\. Acciones irreversibles"))[1:]
    assert len(table) == 4 and all(len(r) == 3 and r[2] for r in table)
