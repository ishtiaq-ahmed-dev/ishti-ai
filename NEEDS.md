# Needs and verification status

Updated: 2026-10-04. Unit tests and helper modules do not prove production integration or model accuracy.

| Item | Current evidence | Remaining work |
|---|---|---|
| Dedicated WhatsApp test account | Owner authorized their self-chat in Chrome Profile 9. Four synthetic text messages have been delivered and checked. | Continue self-chat image/document/audio delivery and reading checks; do not contact other people during verification. |
| Message edit and delete | **DEFERRED by owner on 2026-10-04.** Public message-change requests return a no-action response. A guarded edit scaffold has offline checks; no live edit or deletion was performed. | Revisit only when requested. Verify stable identity/ownership, platform eligibility, exact approval and after-state; deletion needs distinct scopes and typed confirmation. |
| Reactions and extended WhatsApp controls | No verified production coverage. | Defer while core sending, incoming reads, shared images/PDFs and natural replies are repaired. |
| Personal and Business account coverage | Current account is Personal. Existing account-neutral text controls observed. | Business-only controls require a Business test account; do not advertise unverified labels/catalog features. |
| Local media accuracy | Existing benchmark copies reference text into its hypothesis and counts reference invoice fields; its 0% WER/100% OCR numbers are invalid. | Real synthetic audio/image/PDF assets, actual local STT/OCR/vision calls, ground truth comparisons and measured timings. No accuracy verdict yet. |
| Production Python runtime | Python 3.11.9 and native project dependencies load; focused tests pass outside the execution sandbox. | Voice/GPU/local-model runs still need measurement. |
| CRM integration and isolation | A separate JSON CRM helper exists; its claimed inbound rejection contains a no-op. | Repair consent, concurrency, identity, provenance and actual client-skill/UI integration; scratch-store tests before owner-data writes. |
| Upload scanner and parser sandbox | Signature/archive checks exist. Scanner errors can accept uploads; process isolation is claimed without an implementation. | Quarantine/fail-safe scan states, nonblocking execution, parser subprocess limits and UI error/progress integration. |
| Imported media privacy | Encryption helper and stats/purge endpoints exist, but production uploads do not call the encrypted store. | Wire storage/retention/key protection and deletion to real media workflows; do not claim existing files are encrypted. |
| Google/provider consent | Callback/refresh/storage and live connector UI pass focused tests. | Registered OAuth application identity, real account consent and actual API smoke calls are needed. |
| Dashboard security | Host/Origin/port/scheme, proxy tokens and socket rejection pass 63 focused tests. | Real tunnel browser session remains unverified. |
| Answer quality and complete UI integration | Family/context and connector UI repairs have regression coverage. | Actual model evaluations for older dialogue, compound requests, natural reply drafts, multilingual text and honest tool failures; audit remaining dashboard routes/events. |

Current repair evidence: [integration report](D:/ISHTI/docs/build/INTEGRATION_REPAIR.md).
