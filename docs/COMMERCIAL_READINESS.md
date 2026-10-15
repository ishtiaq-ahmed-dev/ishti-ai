# Commercial readiness decisions

The initial pilot target is one privacy-sensitive business owner using Windows who needs to notice
overdue invoices and neglected leads, then review a concrete next action. Business Desk supplies this
observation/evidence/owner-review loop today; Urdu/Pashto and accessibility-first operation support
this segment. Collected money and won lead value remain separate, owner-reported measurements.
They are not verified revenue caused by ISHTI.

The October 13 independent audit correctly identifies a commercial licensing gate, but its statement
that the author cannot sell their own software is too broad. PolyForm governs the rights granted to
recipients. A copyright holder can separately license rights they own; they cannot license someone
else's contributions or assets without the required rights. The current licence is preserved while
ownership and bundled terms are unresolved. [PolyForm licence text](https://polyformproject.org/licenses/noncommercial/1.0.0).

## Concrete release gates

| Gate | Evidence required | Current state |
|---|---|---|
| Rights and commercial terms | Contributor ownership/assignments; Rare UI, model, asset and package redistribution review; explicit approved licence text | Open; inventory exists, no rights clearance |
| Trusted Windows distribution | Fresh source/build inventory, signed installer, secure update verification, rollback, clean-PC uninstall/upgrade tests | Open; bootstrap executable is a launcher, not a frozen copy of the app |
| Owner data protection | Every upload/chat/learning/backup path reviewed; recovery/rotation tested; full-disk encryption requirement explained | Partial; helpers do not establish all-store encryption |
| Runtime reliability | Repeated cold/warm boots; attribution-aware model/voice soak; reboot/sleep/resume worker checks | Partial; synthetic and offline gates remain separate |
| Accuracy and accessibility | Consented representative Urdu/Pashto data, human labels, frozen held-out scores; RTL and assistive-technology acceptance | Open; do not invent scores from routing or synthetic tests |
| Customer value | Consented pilot users; time saved, collected/won values, action usefulness, retention and willingness-to-pay | Unmeasured |
| Official communications | Supported WhatsApp/business API contracts and account-safe live acceptance | Open; unofficial bridge is unsuitable for product distribution |

### The one gate with a document behind it

"Owner data protection" above stays Partial, because most of it is encryption, retention, deletion and
export, and none of that is finished. The egress half of it is no longer a claim in prose:
`venv\Scripts\python tools\privacy_report.py --markdown docs\PRIVACY_REPORT.md` produces a
revision-stamped page a buyer or their auditor can read, and it distinguishes what it checked from what
it did not. That is the shape a sales conversation needs — not "your data is safe", but "here is the
list of every host this build can reach, here is the denylist demonstrated against a permissive skill
scope, here is every socket it binds, and here is what none of that covers." It is evidence for a
technical buyer, not a certification, and it is worth exactly as much as the source hash printed at the
top of it.

## Pilot sequence and acceptance

1. Clear rights, distribute a labelled private pilot build, and give each pilot owner a separate installation.
2. Start with owner-approved CSV exports; no customer messages or money moves automatically.
3. Measure two weeks of source freshness, observation reliability, accepted/dismissed decisions and reported outcomes.
4. Interview owners about one missed obligation the product prevented and one action they found useful.
5. Freeze the representative language/RTL evaluation set before tuning. Report per-language failures and sample sizes.
6. Make a pricing/hosting decision from measured value and costs. A powered-on, signed-in PC is required for the local observer.

Payment accounts, public telemetry, live customer outreach and a licence replacement are not configured
by code fixes. No claim of international launch readiness or investor demand follows from this audit pass.
