"""Plant the real-world preconditions the 250-task checklist needs, so no task is skipped for lack
of a fixture (Prompt 7 Step 2.2). Everything here is ADDITIVE and idempotent - it creates entity
records + relationship-graph edges the person/contacts/email tasks refer to by name, plus one
cardiologist-collaborator used by the full-scoped-deletion task (237). Nothing is deleted.

Run from the project root:
    venv\\Scripts\\python tests\\task_suite\\fixtures.py            # seed
    venv\\Scripts\\python tests\\task_suite\\fixtures.py --check    # report only, change nothing
    venv\\Scripts\\python tests\\task_suite\\fixtures.py --clean    # remove ONLY the seeded people

Which task needs which fixture is recorded in tests/task_suite/FIXTURES.md.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from ishti import rgraph  # noqa: E402
from ishti.arch2 import memory as M  # noqa: E402

# (name, relationship, [(rel, target, kind)], extra semantic line for entity-card tasks)
PEOPLE = [
    ("Bilal", "cousin", [("cousin", None, "person")], "Bilal is the owner's cousin."),
    ("Ahmed", "brother", [("brother", None, "person")], "Ahmed is the owner's brother."),
    ("Sara", "friend", [], "Sara is a friend the owner chats with on WhatsApp."),
    ("Sana", "client", [("works_at", "JQ AI Solutions", "organisation")],
     "Sana is the owner's client at JQ AI Solutions."),
    ("Faisal", "manager", [("works_at", "DevOps gig", "organisation")],
     "Faisal is the owner's manager at the DevOps gig."),
    ("Usman (Azure)", "colleague", [("works_at", "Azure project", "project")],
     "Usman is a colleague on the Azure project."),
    ("Usman (Zyntiqo)", "colleague", [("works_at", "Zyntiqo", "organisation")],
     "Usman is a colleague from Zyntiqo."),
    ("Ali", "classmate", [("works_at", "CardioSense", "project"), ("studies_at", "IMS", "organisation")],
     "Ali is the owner's classmate at IMS; they worked on CardioSense together."),
    ("Hamza", "friend", [], "Hamza is a friend, not part of any project."),
    ("Dr. Farhan Malik", "collaborator", [("works_on", "CardioSense", "project")],
     "Dr. Farhan Malik is the owner's cardiologist collaborator on CardioSense."),
]


def _seed(check: bool) -> int:
    added = 0
    notes = []
    for name, rel, edges, line in PEOPLE:
        exists = bool(M.find_entity(name))
        if not exists:
            notes.append(f"+ entity {name} ({rel})")
            added += 1
            if not check:
                M.upsert_entity(name, relationship=rel, confidence=1.0)
        if not check and not exists:
            # graph: kin/relationship edge me -[rel]-> person, plus any works_at/project edges
            if rel and rel != "unknown":
                rgraph.relate("me", rel, name)
            for erel, target, kind in edges:
                if target:
                    rgraph.relate(name, erel, target, kind_dst=kind)
            M.semantic(line, source="explicit_user", confidence=1.0)
    return added, notes


def _clean() -> int:
    removed = 0
    for name, *_ in PEOPLE:
        if M.find_entity(name):
            M.forget_hard(name)
            removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report only")
    ap.add_argument("--clean", action="store_true", help="remove the seeded people")
    args = ap.parse_args()
    if args.clean:
        n = _clean()
        print(f"[fixtures] removed {n} seeded person(s)")
        return
    added, notes = _seed(args.check)
    for n in notes:
        print("  " + n)
    print(f"[fixtures] {'would seed' if args.check else 'seeded'} {added} new person(s); "
          f"{len(PEOPLE) - added} already present")


if __name__ == "__main__":
    main()
