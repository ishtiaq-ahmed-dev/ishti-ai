# WhatsApp and local assistant research

Research checked: 2026-10-04. URLs below point to official WhatsApp/Meta sources unless marked otherwise.

## Verified product behavior

- WhatsApp Help Center says sent text messages can be edited for up to 15 minutes. The deletion article also says group
  admins have two days to request deletion of another member's message. These are documented limits, but ISHTI still
  needs to inspect actual message eligibility and the current UI before offering a live action.
  Source: [How to delete messages](https://faq.whatsapp.com/1370476507114859/?cms_platform=iphone&locale=en_US), checked 2026-10-04.
- Linked devices receive a locally stored, end-to-end encrypted copy of recent history, but not all messages/chats
  sync, and WhatsApp directs users to the primary phone for full history. Therefore a Web snapshot cannot be treated
  as a complete archive or proof that a message does not exist.
  Source: [About message history on linked devices](https://faq.whatsapp.com/653480766448040/?cms_platform=web&locale=sv_SE), checked 2026-10-04.
- WhatsApp documents voice messages as recordings made through the in-chat microphone control. Sending an audio
  attachment is not evidence that it arrived as a native voice note. The project has no test proving its generated OGG
  path arrives as a voice note on this account.
  Source: [How to send voice messages](https://faq.whatsapp.com/657157755756612/?cms_platform=web&locale=bg_BG), checked 2026-10-04.

## Account and integration constraints

- WhatsApp's consumer Terms of Service prohibit automated or other access/use in impermissible or unauthorized ways;
  the Help Center warns that automated scraping for unpermitted purposes can violate the Terms and may lead to a
  temporary or permanent ban. The Business App Terms specifically prohibit scraping/extraction and developing or
  using applications that interact with Business App Services without prior written consent. This makes DOM
  automation on a Personal account a material account/terms risk; human-paced actions do not remove that risk.
  Sources: [Terms of Service](https://www.whatsapp.com/legal/terms-of-service?lang=lt),
  [About harvesting personal information](https://faq.whatsapp.com/434518851968943),
  [WhatsApp Business App Terms](https://www.whatsapp.com/legal/WhatsApp-Terms-for-WhatsApp-Business-App), checked 2026-10-04.
- The official Business Solution Terms currently contain restrictions on AI providers when general-purpose AI is the
  primary functionality, with a stated regional exception for EEA and Brazil phone numbers. A local Personal account
  is not the Cloud API. The Business Platform must not be presented as a drop-in replacement without verifying
  jurisdiction, eligibility, terms, and the user's account migration constraints.
  Source: [WhatsApp Business Solution Terms](https://www.whatsapp.com/legal/business-solution-terms?lang=ur), checked 2026-10-04.
- Official Cloud API material describes outbound messaging and inbound/status delivery through webhooks. It is a
  separate business integration, not a supported full-history API for the user's Personal account.
  Source: [Meta's WhatsApp Business Platform API collection](https://www.postman.com/meta/whatsapp-business-platform/overview),
  checked 2026-10-04.

## Engineering conclusions

1. Keep automated WhatsApp interaction opt-in, visibly warn about account/terms risk, and default to read-only until
   account identity and a dedicated test destination are explicitly selected. Never automate a real-contact test.
2. Treat Web/linked-device transcript coverage as partial. Preserve source message IDs and timestamps where available,
   state when the view is incomplete, and do not infer absence from a missing result.
3. Keep actions unsupported until implemented and verified against observable postconditions. Remove canned group
   success replies. A generated audio file is a file unless a test confirms native voice-note semantics.
4. Business-only features are unavailable for the owner's stated Personal account and should be listed as blocked, not
   simulated. User stated “Personal” on 2026-10-04.
5. Live-account verification is pending: the owner said a dedicated self-chat/test account can be provided, but has not
   provided the details or connected it. Mock verification can proceed without those details.

## What was not measured

No accuracy, OCR, transcription latency, voice-note delivery, DOM drift, or live-account results are claimed from this
research pass. No authenticated real WhatsApp tab was operated.
