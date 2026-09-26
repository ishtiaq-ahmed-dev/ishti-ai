"""WhatsApp skill: the browser engine, pointed at WhatsApp Web in the owner's Chrome Profile 9."""
import re

from ishti.skills.browser import skill as browser

TRIGGER = re.compile(r"\bwhats\s?app\b", re.I)
HINT = ("You are in WhatsApp Web, already logged in. To message someone: click the search box, type their "
        "name, open their chat from the results, click the message box, type the message and press Enter. "
        "To read messages: open the chat and read the latest messages from the page. Never send anything the "
        "owner did not ask for.")


def match(text):
    return bool(TRIGGER.search(text))


def run(text, session):
    return browser.run(text, session, server="whatsapp", start_url="https://web.whatsapp.com", hint=HINT)
