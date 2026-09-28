"""Cross-client leakage and privacy boundaries, adversarially.

The checklist treats ANY cross-client leakage as a critical bug, and separately requires that sensitive facts about a
person never reach a client-facing context. The clients skill has its own self-check for the easy case (two clients
with clearly different names). This one attacks it:

  1. confusable names - "Ali" vs "Aliza Khan" vs "Aliza": a lookup for one must never return another's rows, not even
     when the name asked for is a prefix of another client's name.
  2. a client named like a family member - the owner's private relationship facts about "Ahmed" (kinship is private
     by default) must not appear in client "Ahmed"'s grounding, and rgraph's client-facing export must exclude them.
  3. sensitive events marked private (allow_private=0) stay out of the grounding a draft is built from.
  4. the owner's own preferences ARE allowed (they are the owner's business, per rgraph.export) - the rule must not be
     so strict that ISHTI forgets how the owner likes to write.
  5. sending a draft records against the SAME profile the draft was grounded in - no stray duplicate profile that
     would then diverge from the real client's history.

Run:  D:\\ISHTI\\venv\\Scripts\\python.exe D:\\ISHTI\\tests\\test_client_isolation.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# throwaway stores: this must never touch the owner's real clients or relationship graph
import ishti.facts as F  # noqa: E402

F._facts = F.Facts(os.path.join(tempfile.mkdtemp(), "clients.db"))
import ishti.rgraph as G  # noqa: E402

G.DB_DIR = tempfile.mkdtemp()
G.DB = os.path.join(G.DB_DIR, "rgraph.db")

from ishti.skills.clients import skill as C  # noqa: E402

try:
    # ---- 1. confusable names ---------------------------------------------------------------------------------
    assert "Client created" in C.run("new client Ali via whatsapp, contact 0300-1111111", {})["reply"]
    assert "Client created" in C.run("new client Aliza Khan via email, contact aliza@example.com", {})["reply"]
    C.run("log client message from Ali: needs 500 visiting cards, 15000 rupees", {})
    C.run("log client message from Aliza Khan: wants a teal landing page, 40000 budget", {})

    ali, aliza = C.grounding_for("Ali"), C.grounding_for("Aliza Khan")
    assert ali and aliza, (ali, aliza)
    assert "visiting cards" in ali and "15000" in ali, ali
    assert "teal" in aliza and "40000" in aliza, aliza
    assert "Aliza" not in ali and "aliza" not in ali.lower().split("client:")[-1], ali  # not even her name
    assert "teal" not in ali and "40000" not in ali, ali
    assert "visiting cards" not in aliza and "15000" not in aliza, aliza
    assert "ali" not in aliza.lower().replace("aliza", ""), aliza  # Ali's token only as part of her own name

    # a partial name ("Aliza" for "Aliza Khan") may resolve WITHIN that client, never to Ali ...
    partial = C.grounding_for("Aliza")
    assert partial and "teal" in partial and "visiting cards" not in partial, partial
    assert "15000" not in partial and "visiting cards" not in partial, partial
    assert "ali" not in partial.lower().replace("aliza", ""), partial   # Ali's token only inside her own name

    # ... but only when it is unambiguous. A prefix shared by two clients must resolve to NOTHING:
    # guessing between them is how cross-client leakage starts.
    assert "Client created" in C.run("new client Aliza Ahmed via email, contact a@example.com", {})["reply"]
    C.run("log client message from Aliza Ahmed: wants 3 posters", {})
    assert C.grounding_for("Aliza") is None, "an ambiguous name must never be guessed"
    assert C.grounding_for("Aliza Khan") is not None and "teal" in C.grounding_for("Aliza Khan")  # exact still wins
    r = C.run("client context Aliza", {})
    assert "No client named 'Aliza'" in r["reply"], r          # the safe answer: ask which one
    assert "teal" not in r["reply"] and "posters" not in r["reply"] and "visiting cards" not in r["reply"], r
    r = C.run("draft a reply to Aliza", {})
    assert "No client 'Aliza'" in r["reply"] and "teal" not in r["reply"], r
    # clean the second Aliza away so the rest of the test is unambiguous again
    _f = C._db()
    with _f.lock, _f.db:
        _f.db.execute("DELETE FROM client_events WHERE client_id IN (SELECT id FROM clients WHERE name=?)",
                      ("aliza ahmed",))
        _f.db.execute("DELETE FROM clients WHERE name=?", ("aliza ahmed",))
    assert C.grounding_for("Aliza") is not None  # unambiguous again
    # ---- 2. a client whose name matches a private family member ----------------------------------------------
    assert "Client created" in C.run("new client Ahmed via whatsapp, contact 0300-2222222", {})["reply"]
    C.run("log client message from Ahmed: needs the invoice reissued", {})
    G.relate("me", "brother", "ahmed")                                            # kin: private by default
    G.set_detail("me", "brother", "ahmed", "recovering from surgery, keep calls short")  # sensitive detail
    ahmed = C.grounding_for("Ahmed")
    assert "invoice reissued" in ahmed, ahmed
    assert "surgery" not in ahmed and "brother" not in ahmed.lower(), ahmed
    graph_text = " ".join(G.export("client"))                                     # what a client-facing answer may use
    assert "surgery" not in graph_text and "brother" not in graph_text.lower(), graph_text

    # ---- 3. an event marked private stays out of the draft grounding ----------------------------------------
    cid = C._client("Ali")["id"]
    C._event(cid, "note", "credit card 4111-1111-1111-1111, do not mention", private=1)
    alias_grounding = C.grounding_for("Ali")
    assert "4111" not in alias_grounding, alias_grounding
    assert "4111" in " ".join(e["body"] for e in C.context_load("Ali", allow_private=True)["events"]), \
        "the private row must still exist, it is only kept out of the reply grounding"

    # ---- 4. the owner's own preferences are allowed through (privacy must not break usefulness) --------------
    G.add_preference("me", "short, friendly updates", "pos")
    assert any("friendly updates" in line for line in G.export("client")), G.export("client")

    # ---- 5. sending must record against the SAME profile the draft came from --------------------------------
    session = {"draft": "Hi Aliza - the landing page goes live Friday.", "pending": None}
    r = C.run("send the draft to Aliza", session)
    assert "(yes/no)" in r["reply"] and session["pending"]["skill"] == "clients", r
    session["pending"]["action"]()                          # the owner says yes
    assert C.known_clients().count("aliza") == 0, C.known_clients()   # no stray duplicate profile
    sent = [e for e in C.context_load("Aliza Khan", allow_private=True)["events"]
            if e["kind"] == "sent"]
    assert sent and "landing page" in sent[0]["body"], sent

    print("[self-check] client isolation OK - no leakage across confusable names (an ambiguous prefix refuses), "
          "private family facts and private events stay out of client reply grounding and of the client-facing graph "
          "export, a sent draft logs against the profile it was drafted for, owner preferences still pass")
finally:
    for path in (F._facts.db_path if hasattr(F._facts, "db_path") else None, G.DB):
        try:
            os.remove(path)
        except (OSError, TypeError):
            pass
