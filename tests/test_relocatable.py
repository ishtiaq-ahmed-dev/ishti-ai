r"""The project can live in any folder: config.json's absolute D:\ISHTI paths are rebased to the real install folder."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_config_paths_follow_the_install_folder(monkeypatch):
    import ishti
    monkeypatch.setattr(ishti, "ROOT", r"C:\Apps\ISHTI")
    out = ishti._relocate({"a": [r"D:\ISHTI\data\x", "other"], "b": {"c": r"d:\ishti\y"}, "n": 3})
    assert out == {"a": [r"C:\Apps\ISHTI\data\x", "other"], "b": {"c": r"C:\Apps\ISHTI\y"}, "n": 3}


def test_nothing_in_this_checkout_points_outside_it():
    import ishti
    for key in ("db_path", "chroma_dir"):
        assert os.path.normcase(ishti.CONFIG["memory"][key]).startswith(os.path.normcase(ishti.ROOT)), key
