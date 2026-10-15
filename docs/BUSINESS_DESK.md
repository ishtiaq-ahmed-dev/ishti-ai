# Business Desk: observation, owner decisions and outcomes

Business Desk adds a local workspace for one business owner. It observes explicitly accepted invoice, lead and sales
exports, prioritizes evidence-backed opportunities, prepares work after an owner decision, and learns attention
priorities from recorded outcomes. Its initial target is an owner-led service business that already keeps leads,
invoices and sales in spreadsheets or exports. Customer value and willingness to pay remain hypotheses.

## Start with real evidence

1. Open **Business Desk** in the sidebar. Expand **Business profile & observation policy**. Save your name,
   business currency, IANA timezone and goal. Monetary records currently require at most two decimal places.
2. Accept a UTF-8 CSV snapshot, or explicitly approve one absolute local `.csv` path. There is one accepted source
   per type, up to 2,000 rows and 1 MB each. The UI shows column schemas. IDs must be unique within the source.
3. Enable observation and select **Observe now**. Review proposals, source names, row fields and SHA-256 snapshots.
4. Choose **Prepare draft** or dismiss with optional context. Acceptance rechecks the evidence. Drafts stay local;
   nothing is sent. If an accepted file changed, observe again and review the new evidence first.
5. After doing the work, record a useful/not-useful/unknown outcome, or a collection/won deal with a unique
   transaction or CRM reference. One outcome per decision is accepted. Collection cannot exceed the evidenced
   outstanding balance. Receipt references cannot be counted twice.

Use one consistent currency. Currency cannot change after evidence/history exists. No foreign exchange conversion
is inferred. Source removal withdraws unaccepted proposals; it does not delete outcome history or undo past work.
You can export the sensitive workspace locally from the desk. Keep that unencrypted export private.

## What it observes and learns

| Signal | Evidence and limits | Prepared work |
|---|---|---|
| Overdue invoice | Open, positive balance and due date before today's business-local date | Polite draft asking for payment status/reference |
| Neglected lead | New/contacted/qualified lead with last contact older than the owner policy | Follow-up draft and conversation check |
| Recorded sales drop | Last seven complete local days versus the preceding seven; today excluded | Check export completeness and business causes before changing spend |
| Stale/unavailable data | Old accepted snapshot or watched-file read/validation failure | Refresh source; business recommendations from it are paused |

Sales exports can omit transactions or refunds. A recorded drop does not prove the business declined. Lead values
are estimates, balances are exposure, won deal values are not recognized revenue, and owner-reported collections
do not prove ISHTI caused a payment. There is no cash forecast, profitability model or financial advice engine.

Five known owner-reported outcomes in a category permit a bounded priority adjustment of -10 to +10. Unknown
outcomes do not train the ranking. Acceptance/dismissal counts, outcome usefulness and the adjustment are visible.
Learning never increases action permissions. Dismissed proposals remain quiet for the same source snapshot;
changed evidence may reopen them. Accepted work is not silently repeated.

The consultation button makes one advisory request through the explicitly selected ISHTI model. In API mode, the
question, business profile, learning summary and up to ten proposals go to that provider. It has no tools, cannot
commit decisions, and cannot execute external actions. Routine observation uses no model/network calls.

## Continuous operation and honest availability

The app starts a lightweight observer on normal startup. No business database is created by that background
thread until the owner has configured a workspace. A 30-second heartbeat checks a durable cadence (5–1,440 min).
SQLite `BEGIN IMMEDIATE` serializes app and independent-worker cycles. A crash rolls back an unfinished cycle;
the next due cycle recomputes observations without replaying customer actions.

After configuring sources and enabling observation, opt into the standalone current-user Windows task:

```powershell
venv\Scripts\python tools\business_worker.py --install
venv\Scripts\python tools\business_worker.py --status
```

The task runs without the chat or app, starts at user logon, ignores duplicate instances and retries process failure
three times at one-minute intervals. It uses the same user's OS credential store. It does not run as administrator
or store a Windows password. `--install` refuses to overwrite an existing task. `--print-task` shows the definition
without registration. `--once` performs a single due check; `--run` starts the foreground worker.

The PC must be on and awake and the user session signed in. A sleeping/offline PC does not provide 24/7 service.
The desk shows paused, active, degraded or offline using a heartbeat, separate from the last observation time. An
always-on hosted service and uptime commitment are a separate deployment project. This is one owner/PC, not
tenant-isolated SaaS. A short supervision test does not establish multi-day reliability.

Pause observation in the desk to stop future cycles. ISHTI's Stop control also pauses the shared business policy
persistently. To remove independent supervision:

```powershell
venv\Scripts\python tools\business_worker.py --uninstall
```

Uninstallation stops the registered task and removes it. Pause first. `ISHTI_BUSINESS_DESK=0` disables business API operations and observation for processes
started with that environment flag. Already running processes must restart to read it. Code rollback requires
stopping/unregistering the standalone worker first; preserve database and OS storage key together.

## Storage, permissions and bounds

Business state—including accepted rows, paths, goals, drafts, decisions and receipt references—is sealed with
authenticated encryption in `data/business.db`, using the existing sensitive-storage OS-keystore key. Tests use
isolated scratch keys. Missing/invalid keys and tampering fail closed. No plaintext credential/data fallback is used.
An explicit export is plaintext and includes sensitive records. Original watched CSVs remain managed by their
owner and are not encrypted by this feature. This does not complete protection of ISHTI's other stores.

Business API endpoints independently require direct loopback and a matching/absent same-origin policy. Proxied
and remote requests are refused. Source paths are explicitly approved local CSVs; network shares, symlinks,
junctions, oversized files, bad dates, formula amounts, duplicate IDs and mixed currencies are refused. Watch
failure retains the previous snapshot but blocks proposals and acceptance against it. Local input text is data,
not executable code. Python file checks are not a hostile-process OS sandbox.

State is capped at 16 MB, decision records at 300 and source rows at 2,000 per type. Observation checks a cooperative
20-second budget between local reads; this is not a hard OS timeout for a stalled disk. At decision capacity the UI reports a visible capacity error rather
than silently dropping work. The latest 500 audit entries and 100 outcome receipts are shown; the sensitive
export contains retained outcome history. Large multi-year workspaces need a reviewed archival/migration feature.

## Verification and release gates

Tests exercise source validation, encryption/tampering, stale approvals, pause, repeated/outcome idempotency,
parallel runners, business timezone/sales windows, local API authority, optional advisory-only consultation,
and actual headless process restart. The Business Desk browser check uses the built UI and real feature API with
synthetic data; unrelated personal-app endpoints are fixture responses. No customer messages, payments or live
model calls occur in those checks.

Before paid operation, validate actual export conventions, customer permission and data lifecycle, unreadable
files/key recovery, clean Windows task installation, startup/reboot/sleep behavior and a multi-day soak. Before
introducing outbound connectors, require approved adapters, an explicit action policy, a spend/contact budget,
stable action IDs, receipts, reconciliation of ambiguous outcomes and independent negative tests. No model may
grant its own permission to send, spend, publish or mutate third-party systems.
