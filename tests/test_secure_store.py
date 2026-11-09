"""restrict_to_owner(folder, inherit=True) locks a whole tree: existing children lose inherited broad entries (Users,
Authenticated Users) and keep the owner. Windows only; runs on a temp folder, never on data/."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")


def _trustees(path):
    import win32security
    dacl = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
                                              win32security.DACL_SECURITY_INFORMATION).GetSecurityDescriptorDacl()
    out = set()
    for i in range(dacl.GetAceCount()):
        sid = dacl.GetAce(i)[2]
        try:
            out.add(win32security.LookupAccountSid(None, sid)[0].lower())
        except Exception:
            out.add(win32security.ConvertSidToStringSid(sid))
    return out


def test_folder_lock_reaches_existing_children(tmp_path):
    import getpass
    from ishti.secure_store import restrict_to_owner
    child = tmp_path / "sub" / "store.db"
    child.parent.mkdir()
    child.write_text("x")
    assert restrict_to_owner(tmp_path, inherit=True)
    for p in (tmp_path, child):
        who = _trustees(p)
        assert getpass.getuser().lower() in who, (p, who)
        assert not who & {"users", "authenticated users", "everyone"}, (p, who)
    child.write_text("still writable by the owner")
