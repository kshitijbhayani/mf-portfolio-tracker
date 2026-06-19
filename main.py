"""Application entry point used for both `python main.py` and the PyInstaller build.

Pass ``--selftest [PDF PASSWORD]`` to verify the bundled environment without
launching the GUI: it reports whether the CAS-parsing stack loaded and, if a
statement is supplied, how many holdings/transactions parsed. Results are
written to ``--out FILE`` when given (needed for the windowed build, which has
no console).
"""

import sys


def _selftest(argv: list[str]) -> int:
    lines: list[str] = []
    out_path = None
    if "--out" in argv:
        out_path = argv[argv.index("--out") + 1]
        argv = [a for i, a in enumerate(argv)
                if a != "--out" and (i == 0 or argv[i - 1] != "--out")]

    try:
        from mf_tracker import cas_import
        lines.append(f"cas_import available: {cas_import.is_available()}")
        lines.append(f"missing packages: {cas_import.missing_packages()}")
        pos = [a for a in argv[1:] if not a.startswith("--")]
        if len(pos) >= 2:
            res = cas_import.parse_cas(pos[0], pos[1])
            lines.append(f"parsed: {res.file_type} {res.cas_type} | "
                         f"{len(res.transactions)} rows | {res.scheme_count} schemes")
        lines.append("SELFTEST OK")
        code = 0
    except Exception as exc:  # pragma: no cover - diagnostic path
        import traceback
        lines.append(f"SELFTEST FAILED: {exc!r}")
        lines.append(traceback.format_exc())
        code = 1

    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return code


def main() -> None:
    if "--selftest" in sys.argv:
        sys.exit(_selftest(sys.argv))
    from mf_tracker.app import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
