"""
Wertet den JUnit-Report von pytest für GitHub Actions aus:
- Übersicht pro Testdatei in die Job-Zusammenfassung (GITHUB_STEP_SUMMARY)
- Fehlgeschlagene Tests als Annotation, damit sie direkt im PR erscheinen

Aufruf: python pytest_summary.py <junit.xml>
"""
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def main(path: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    out = open(summary_path, "a", encoding="utf-8") if summary_path else sys.stdout

    if not os.path.exists(path):
        out.write("### ❌ Tests\n\nKein Testreport erzeugt – pytest ist vorher abgebrochen, "
                  "siehe Log des Schritts.\n")
        return

    per_file: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    failures: list[tuple[str, str, str, str]] = []
    total_time = 0.0

    for suite in ET.parse(path).getroot().iter("testsuite"):
        total_time += float(suite.get("time", 0))
        for case in suite.iter("testcase"):
            file = case.get("file") or case.get("classname", "?").replace(".", "/") + ".py"
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is not None:
                status = "fehlgeschlagen"
                message = (problem.get("message") or "").strip()
                failures.append((file, case.get("line", ""), case.get("name", "?"), message))
            elif case.find("skipped") is not None:
                status = "übersprungen"
            else:
                status = "bestanden"
            per_file[file][status] += 1

    counts = defaultdict(int)
    for stats in per_file.values():
        for status, n in stats.items():
            counts[status] += n

    icon = "❌" if counts["fehlgeschlagen"] else "✅"
    out.write(
        f"### {icon} Tests: {counts['bestanden']} bestanden · "
        f"{counts['fehlgeschlagen']} fehlgeschlagen · "
        f"{counts['übersprungen']} übersprungen ({total_time:.1f} s)\n\n"
        "| Datei | bestanden | fehlgeschlagen | übersprungen |\n"
        "|---|---:|---:|---:|\n"
    )
    for file in sorted(per_file):
        s = per_file[file]
        out.write(f"| `{file}` | {s['bestanden']} | {s['fehlgeschlagen']} | {s['übersprungen']} |\n")

    if failures:
        out.write("\n#### Fehlgeschlagen\n\n")
        for file, line, name, message in failures:
            short = message.splitlines()[0][:300] if message else ""
            out.write(f"- `{file}::{name}`  \n  {short}\n")
            # Annotation; pytest zählt Zeilen ab 0, GitHub ab 1
            loc = f"file={file}" + (f",line={int(line) + 1}" if line.isdigit() else "")
            print(f"::error {loc},title={_escape(name, prop=True)}::"
                  f"{_escape(short or 'fehlgeschlagen')}")


def _escape(value: str, prop: bool = False) -> str:
    """Escaping für Workflow-Kommandos (::error ...)."""
    value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        value = value.replace(":", "%3A").replace(",", "%2C")
    return value


if __name__ == "__main__":
    main(sys.argv[1])
