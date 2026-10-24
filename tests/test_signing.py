"""Authenticode signing of the Windows package through SignPath (decision 19.27): which files go, and back."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "packaging"))
import build  # noqa: E402


def test_transport_names_go_there_and_back():
    for rel in ("python/python.exe", "python/Lib/site-packages/x/_y.cp313-win_amd64.pyd", "a/b.DLL",
                "uninst-6.4.3-abc.e32"):
        carried = build.transport_name(rel)
        assert carried.lower().endswith((".exe", ".dll"))  # what SignPath signs
        assert build.original_name(carried) == rel


def test_the_plan_takes_unsigned_pe_files_and_the_uninstaller(tmp_path):
    root = tmp_path / "windows"
    files = {"python/python.exe": "NotSigned", "python/Skipwise.exe": "NotSigned",
             "python/vcruntime140.dll": "Valid",  # Microsoft's own: keeps its signature
             "python/Lib/site-packages/m/_c.pyd": "NotSigned", "python/x.dll": "HashMismatch",
             "python/Lib/os.py": None, "dwyt-logo.ico": None}
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"MZ")
    uninst = tmp_path / "uninstaller" / "uninst-6.4.3-abc.e32"
    uninst.parent.mkdir()
    uninst.write_bytes(b"MZ")
    status = {root / rel: s for rel, s in files.items() if s}
    plan = build.signing_plan(root, [uninst], status)
    assert set(plan) == {"package/python/python.exe", "package/python/Skipwise.exe", "package/python/x.dll",
                         "package/python/Lib/site-packages/m/_c.pyd.dll", "uninstaller/uninst-6.4.3-abc.e32.exe"}
    assert plan["uninstaller/uninst-6.4.3-abc.e32.exe"] == str(uninst)

    signed = tmp_path / "signed"
    for inside in plan:
        (signed / inside).parent.mkdir(parents=True, exist_ok=True)
        (signed / inside).write_bytes(b"MZ signed")
    back = build.put_back(signed, plan)
    assert (root / "python/Lib/site-packages/m/_c.pyd").read_bytes() == b"MZ signed"
    assert uninst.read_bytes() == b"MZ signed" and len(back) == 5
    assert (root / "python/vcruntime140.dll").read_bytes() == b"MZ"

    (signed / "package/python/python.exe").unlink()
    with pytest.raises(SystemExit, match="did not come back"):
        build.put_back(signed, plan)


def test_the_installer_script_takes_a_signed_uninstaller_only_when_asked():
    iss = (Path(__file__).parent.parent / "packaging" / "windows" / "dwyt-runner.iss").read_text(encoding="utf-8-sig")
    block = iss.split("#ifdef SignUninstaller", 1)[1].split("#endif", 1)[0]
    assert "SignedUninstaller=yes" in block and "SignedUninstallerDir={#UninstDir}" in block


def test_the_signpath_configurations_match_the_build():
    from xml.etree import ElementTree
    ns = {"a": "http://signpath.io/artifact-configuration/v1"}
    folder = Path(__file__).parent.parent / "packaging" / "signpath"
    package = ElementTree.parse(folder / "package.xml").getroot()
    paths = [e.get("path") for e in package.iterfind(".//a:include", ns)]
    assert paths == ["package/**/*.exe", "package/**/*.dll", "uninstaller/*.exe"]  # what signing_plan names
    installer = ElementTree.parse(folder / "installer.xml").getroot()
    assert installer.find(".//a:pe-file", ns).get("path") == "skipwise-runner-setup-*.exe"
    workflow = (Path(__file__).parent.parent / ".github" / "workflows" / "release.yml").read_text()
    assert "artifact-configuration-slug: package" in workflow and "artifact-configuration-slug: installer" in workflow
