"""Is this install ready to start the paper run?

The checks themselves live in `app/preflight.py`, so this tool and
`GET /api/preflight` cannot disagree about what is outstanding. What is
here is the rendering: colour, the fix lines, and an exit code.

Nothing writes anything -- it reads config, the database and the
filesystem, plus one optional network probe with --network.

    python tools/preflight.py
    python tools/preflight.py --network     # also test Groq and Upstox reachability

Exit code is 0 when nothing blocking is outstanding, 1 otherwise, so it can
sit in front of a start script.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

NETWORK = "--network" in sys.argv

from app import config, preflight  # noqa: E402
from app.preflight import Check, Section  # noqa: E402

GREEN, RED, YELLOW, BLUE, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def render(sections: list[Section]) -> tuple[int, list[Check]]:
    outstanding: list[Check] = []

    for section in sections:
        print(f"\n{BLUE}{section.title}{RESET}")
        for c in section.checks:
            if c.ok:
                print(f"  {GREEN}[ok]{RESET}   {c.name}"
                      + (f" {DIM}-- {c.detail}{RESET}" if c.detail else ""))
                continue

            tone = RED if c.blocking else YELLOW
            tag = "[FAIL]" if c.blocking else "[warn]"
            print(f"  {tone}{tag}{RESET} {c.name}"
                  + (f" {DIM}-- {c.detail}{RESET}" if c.detail else ""))
            if c.optional_note:
                print(f"         {DIM}{c.optional_note}{RESET}")
            if c.fix:
                print(f"         {YELLOW}fix: {c.fix}{RESET}")
            outstanding.append(c)

    blocking = [c for c in outstanding if c.blocking]
    return len(blocking), outstanding


def main() -> int:
    print(f"{DIM}Paper run preflight -- kuch badla nahi jaata, sirf padha "
          f"jaata hai.{RESET}")

    sections = preflight.sections()
    if NETWORK:
        sections.append(asyncio.run(preflight.check_network()))

    blocking, outstanding = render(sections)

    print(f"\n{'=' * 66}")
    if not outstanding:
        print(f"{GREEN}Sab ready hai. Paper run shuru kar sakte hain:{RESET}")
        print(f"{DIM}  POST /api/feed/start        feed + agent instruments{RESET}")
        print(f"{DIM}  POST /api/execution/start   loop{RESET}")
        return 0

    if blocking:
        print(f"{RED}{blocking} cheez abhi baaki hai (blocking).{RESET}")
    else:
        print(f"{YELLOW}Blocking kuch nahi. "
              f"{len(outstanding)} optional cheez baaki hai.{RESET}")
        print(f"{DIM}Paper run shuru ho sakta hai, par upar wale warnings "
              f"padh lein -- wo batate hain ki kya kaam nahi karega.{RESET}")

    print(f"{DIM}Poora guide: docs/12-paper-run.md{RESET}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
