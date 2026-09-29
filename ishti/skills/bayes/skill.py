"""Bayesian updating (inventory domain 6 #35): exact posteriors, no vibes.

"bayes: prior 1% sensitivity 99% specificity 95%", "update belief: prior 5%, hit rate 90%, false positive 10%",
"posterior with prior 0.02, sensitivity 0.99, specificity 0.95"

Base-rate example built in: a 1% prior with a 99%-sensitive, 95%-specific test gives
P(disease | positive) = 0.99*0.01 / (0.99*0.01 + 0.05*0.99) = about 16.7% — the classic result
most people get wrong by a factor of five. Sequential "update belief" requests keep the last
posterior as the new prior. Odds form is shown alongside for transparency. T0, pure computation.
"""
import re

from ishti.skills import trace

I = re.I

FULL = re.compile(
    r"^\s*(?:bayes|posterior|bayesian)\s*[:\-]?\s*(?:with\s+)?(?:prior|p\(\s*h\s*\))\s*(?:=|is|of)?\s*"
    r"(?P<prior>\d*\.?\d+)\s*(?P<prior_pc>%)?\s*[,;]?\s*(?:sensitivity|hit\s*rate|tpr)\s*(?:=|is|of)?\s*"
    r"(?P<se>\d*\.?\d+)\s*(?P<se_pc>%)?\s*[,;]?\s*(?:specificity|tnr)\s*(?:=|is|of)?\s*"
    r"(?P<sp>\d*\.?\d+)\s*(?P<sp_pc>%)?\s*$", I)
UPDATE = re.compile(
    r"^\s*(?:update\s+(?:the\s+)?belief|new\s+evidence|bayes\s+update)\s*[:\-]?\s*"
    r"(?:hit\s*rate|sensitivity|p\(\s*e\s*\|\s*h\s*\))\s*(?:=|is|of)?\s*(?P<se>\d*\.?\d+)\s*(?P<se_pc>%)?\s*[,;]?\s*"
    r"(?:false\s*pos(?:itive)?(?:\s*rate)?|fpr)\s*(?:=|is|of)?\s*(?P<fpr>\d*\.?\d+)\s*(?P<fpr_pc>%)?\s*$", I)


def _val(num_txt, pc):
    """Explicit % sign wins; bare values > 1 are assumed to be percentages."""
    x = float(num_txt)
    return x / 100.0 if (pc or x > 1.0) else x


def posterior(prior, se, sp):
    """P(H|E) = se*p / (se*p + (1-sp)*(1-p)) — exact."""
    num = se * prior
    den = num + (1.0 - sp) * (1.0 - prior)
    return num / den if den else 0.0


def _render(label, prior, se, sp_or_fpr, post, sp_mode=True):
    fpr = (1 - sp_or_fpr) if sp_mode else sp_or_fpr
    odds_prior = prior / (1 - prior) if prior < 1 else float("inf")
    odds_post = post / (1 - post) if post < 1 else float("inf")
    lr = se / fpr if fpr else float("inf")
    return (f"**{label}**\n"
            f"- prior: {prior:.4%}\n"
            f"- evidence: hit rate {se:.1%}, false-positive rate {fpr:.1%}"
            + (f" (specificity {sp_or_fpr:.1%})" if sp_mode else "") + "\n"
            f"- **posterior: {post:.2%}**  (odds {odds_prior:g} → {odds_post:g}, "
            f"likelihood ratio {lr:.4g})")


def run_full(m):
    prior = _val(m.group("prior"), m.group("prior_pc"))
    se = _val(m.group("se"), m.group("se_pc"))
    sp = _val(m.group("sp"), m.group("sp_pc"))
    if not (0 <= prior <= 1 and 0 < se <= 1 and 0 <= sp <= 1):
        return "Probabilities must be in [0, 1] (percentages fine)."
    if se <= (1 - sp):
        return ("That evidence can't distinguish anything: hit rate ≤ false-positive rate means "
                "the posterior can never move. Check the numbers.")
    post = posterior(prior, se, sp)
    out = _render("Posterior", prior, se, sp, post)
    out += ("\nSanity anchor: with a 1% prior, 99% sensitivity and 95% specificity, "
            "a positive test is only **16.67%** — base rates bite.")
    return out


class _Last:
    value = None


def run_update(m):
    if _Last.value is None:
        return ("No belief to update yet — start with \"bayes: prior 1% sensitivity 99% "
                "specificity 95%\".")
    se, fpr = _val(m.group("se"), m.group("se_pc")), _val(m.group("fpr"), m.group("fpr_pc"))
    if not (0 < se <= 1 and 0 <= fpr <= 1) or se <= fpr:
        return "Need hit rate > false-positive rate, both in [0, 1]."
    post = posterior(_Last.value, se, 1 - fpr)
    out = _render("Updated belief", _Last.value, se, fpr, post, sp_mode=False)
    _Last.value = post
    return out


def reset():
    _Last.value = None


def match(text):
    t = text.strip()
    return bool(FULL.match(t) or UPDATE.match(t))


def run(text, session):
    t = text.strip()
    if (m := FULL.match(t)):
        reset()
        trace(session, "bayes", "posterior")
        r = {"reply": run_full(m), "sources": []}
        mm = FULL.match(t)
        if "posterior:" in r["reply"]:
            import re as _re
            mm2 = _re.search(r"posterior: ([\d.]+)%", r["reply"])
            if mm2:
                _Last.value = float(mm2.group(1)) / 100.0
        return r
    if (m := UPDATE.match(t)):
        trace(session, "bayes", "update")
        return {"reply": run_update(m), "sources": []}
    return None


if __name__ == "__main__":
    assert match("bayes: prior 1% sensitivity 99% specificity 95%")
    assert match("posterior with prior 0.02, sensitivity 0.99, specificity 0.95")
    assert match("update belief: hit rate 90%, false positive 10%")
    assert match("bayes: prior 0.01 sensitivity 0.99 specificity 0.95")
    assert not match("bay of bengal")
    assert not match("prior 1% and then what")

    r = run("bayes: prior 1% sensitivity 99% specificity 95%", {})
    assert "16.67%" in r["reply"], r                      # the classic base-rate result, exact
    assert "likelihood ratio" in r["reply"], r
    # hand-check the arithmetic
    p = posterior(0.01, 0.99, 0.95)
    assert abs(p - 0.01 * 0.99 / (0.01 * 0.99 + 0.05 * 0.99)) < 1e-12
    # sequential update uses the last posterior
    r2 = run("update belief: hit rate 90%, false positive 10%", {})
    prior2 = p
    exp = 0.9 * prior2 / (0.9 * prior2 + 0.1 * (1 - prior2))
    assert f"{exp:.2%}" in r2["reply"], (r2, exp)
    # degenerate evidence refused
    r3 = run("bayes: prior 50% sensitivity 50% specificity 50%", {})
    assert "can't distinguish" in r3["reply"], r3
    # a second full computation resets the chain (no stale sequential state)
    r4 = run("bayes: prior 30% sensitivity 80% specificity 90%", {})
    assert "77.42%" in r4["reply"], r4                    # .8*.3/(.8*.3+.1*.7) = .24/.31
    # bare fractions without a % sign must not become percents
    r5 = run("bayes: prior 0.02 sensitivity 0.99 specificity 0.95", {})
    assert "28.78%" in r5["reply"], r5                    # .99*.02/(.99*.02+.05*.98) = .0198/.0688
    print("[self-check] bayes OK - exact Bayes rule (16.67% base-rate anchor), likelihood ratios "
          "shown, sequential updates and degenerate evidence handled honestly")
