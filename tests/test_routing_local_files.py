"""Owner bug 2026-10-07: 'show my 3 newest files in downloads' was routed to web_search ('newest' cue) and the
pipeline then ran ~10 web searches for 200+ s. Questions about the owner's own PC locations stay on the PC."""
import pytest

from ishti.dispatcher import SKILLS
from ishti.tool_routing import route


@pytest.mark.parametrize("text,skill", [
    ("show my 3 newest files in downloads", "folders"),
    ("list the newest files in my downloads folder", "folders"),
    ("show newest files on my desktop", "folders"),
    ("delete the three newest files in my downloads", "fileops"),
    ("remove the old files from my desktop", "fileops"),
    ("latest news about ai", "web_search"),          # the web cue still works for real web questions
])
def test_local_locations_do_not_go_to_the_web(text, skill):
    assert route(text, SKILLS)["skill"] == skill
