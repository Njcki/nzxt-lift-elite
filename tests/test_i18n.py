"""Localisation: every msgid has an Italian translation, placeholders match,
the language is picked from the environment, no Italian in English mode."""

from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import string
import unittest
from pathlib import Path
from unittest import mock

from nzxt_lift_elite import i18n
from nzxt_lift_elite.i18n_it import IT

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "nzxt_lift_elite").rglob("*.py")) + [ROOT / "cc-plugin" / "server.py"]

# Words that only show up in Italian text (not in the English msgids).
ITALIAN = re.compile(
    r"\b(il|lo|gli|della|delle|dei|non|nessun[oa]?|profil[oi]|tast[oi]|stadi[o]?|scritt[oa]|letto|atteso|"
    r"sono|già|più|perché|ricevitore|cavo|attivo|valore|errore|avviso|modifiche|riletto|verificat[oi]|"
    r"serve|deve|trovat[oa]|comando|fabbrica|colore|ripristin\w+|applica|applicat[oi]|annulla|rileggi|scegli|"
    r"impostazioni|salva\w*|scollegato|collegato|frequenza|tutti|tutto|questo|quello|rendi|subito|"
    r"Blu|Rosso|Verde|Giallo|Viola)\b",
    re.IGNORECASE,
)


def msgids() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, path.name)
    return found


def fields(text: str) -> set[str]:
    return {f for _t, f, _s, _c in string.Formatter().parse(text) if f}


class CatalogTest(unittest.TestCase):
    def test_every_msgid_translated(self):
        ids = msgids()
        self.assertGreater(len(ids), 250)
        missing = {k: v for k, v in ids.items() if k not in IT}
        self.assertEqual(missing, {})

    def test_both_languages(self):
        self.assertEqual(set(i18n.CATALOGS), {"it", "en"})
        for key in msgids():
            for lang in ("it", "en"):
                with self.subTest(key=key, lang=lang):
                    i18n.set_language(lang)
                    self.assertTrue(i18n._(key))
        i18n.set_language("it")

    def test_placeholders_match(self):
        for en, it in IT.items():
            with self.subTest(msgid=en):
                self.assertEqual(fields(en), fields(it))

    def test_msgids_are_english(self):
        for key in msgids():
            with self.subTest(msgid=key):
                self.assertIsNone(ITALIAN.search(key), key)

    def test_no_italian_literals_outside_catalogue(self):
        """Italian user text may only live in i18n_it.py (msgids are English)."""
        bad = []
        for path in SOURCES:
            if path.name == "i18n_it.py":
                continue
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module, ast.AsyncFunctionDef)):
                    if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                        docstrings.add(id(node.body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    if node.value in ("non-us-hash", "non-us-backslash", "Non-US # ~", "Non-US \\ |"):
                        continue
                    if ITALIAN.search(node.value) and " " in node.value.strip():
                        bad.append(f"{path.name}:{node.lineno}: {node.value[:70]}")
        self.assertEqual(bad, [])


class DetectTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("it")

    def detect(self, **env):
        clean = {k: "" for k in ("LIFT_ELITE_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")}
        clean.update(env)
        with mock.patch.dict(os.environ, clean), \
                mock.patch("nzxt_lift_elite.settings.load_settings", lambda: {"language": "auto"}):
            return i18n.detect_language()

    def test_system_locale(self):
        self.assertEqual(self.detect(LANG="it_IT.UTF-8"), "it")
        self.assertEqual(self.detect(LANG="en_US.UTF-8"), "en")
        self.assertEqual(self.detect(LANG="de_DE.UTF-8"), "en")
        self.assertEqual(self.detect(LANG="C"), "en")
        self.assertEqual(self.detect(LC_ALL="it_CH.UTF-8", LANG="en_US.UTF-8"), "it")
        self.assertEqual(self.detect(LANGUAGE="en:it", LANG="it_IT.UTF-8"), "en")

    def test_override(self):
        self.assertEqual(self.detect(LIFT_ELITE_LANG="en", LANG="it_IT.UTF-8"), "en")
        self.assertEqual(self.detect(LIFT_ELITE_LANG="it", LANG="en_US.UTF-8"), "it")
        self.assertEqual(self.detect(LIFT_ELITE_LANG="fr"), "en")

    def test_preference(self):
        with mock.patch.dict(os.environ, {"LIFT_ELITE_LANG": "", "LANG": "it_IT.UTF-8", "LANGUAGE": "", "LC_ALL": ""}), \
                mock.patch("nzxt_lift_elite.settings.load_settings", lambda: {"language": "en"}):
            self.assertEqual(i18n.detect_language(), "en")

    def test_settings_roundtrip(self):
        import tempfile

        from nzxt_lift_elite import settings

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            self.assertEqual(settings.load_settings()["auto_apply"], False)
            settings.save_setting("language", "en")
            settings.save_setting("auto_apply", True)
            data = settings.load_settings()
            self.assertEqual((data["language"], data["auto_apply"], data["theme"]), ("en", True, "default"))
            self.assertTrue((Path(tmp) / "nzxt-lift-elite" / "settings.json").is_file())


class EnglishCliTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "HOME": self.tmp.name})
        self.env.start()
        i18n.set_language("en")

    def tearDown(self):
        i18n.set_language("it")
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        from nzxt_lift_elite import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:  # --help
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_no_italian(self):
        from nzxt_lift_elite import cli

        runs = [
            ("--help",), ("--mock", "show"), ("--mock", "firmware-info"), ("--mock", "activate", "--profile", "2"),
            ("--mock", "--dry-run", "set", "--profile", "1", "--polling", "8000"),
            ("--mock", "restore-led-colors"), ("--mock", "reset-defaults", "--yes"), ("--mock", "actions", "volume"),
            ("--mock", "remap", "--profile", "2", "--button", "middle", "--action", "mute"),
            ("--mock", "set", "--profile", "9"), ("profiles",), ("devices",),
        ]
        sub = [a.dest for a in cli._build_parser()._actions if a.dest == "command"]
        self.assertTrue(sub)
        for name in ("show", "set", "remap", "activate", "save", "load", "restore-led-colors", "reset-defaults"):
            runs.append((name, "--help"))
        for argv in runs:
            with self.subTest(argv=argv):
                _code, out = self.run_cli(*argv)
                self.assertTrue(out.strip())
                self.assertIsNone(ITALIAN.search(out), out[:400])

    def test_italian_still_works(self):
        i18n.set_language("it")
        _code, out = self.run_cli("--mock", "show")
        self.assertIn("Profilo 1", out)


class PluginPageTest(unittest.TestCase):
    def test_both_dictionaries(self):
        html = (ROOT / "cc-plugin" / "ui" / "index.html").read_text(encoding="utf-8")
        blocks = {lang: re.search(rf"\n    {lang}: {{(.*?)\n    }},", html, re.S).group(1) for lang in ("en", "it")}
        keys = {lang: set(re.findall(r"^\s+(\w+): '", b, re.M)) for lang, b in blocks.items()}
        self.assertEqual(keys["en"], keys["it"])
        used = set(re.findall(r"data-i18n(?:-html)?=\"(\w+)\"", html)) | set(re.findall(r"T\('(\w+)'\)", html))
        self.assertTrue(used <= keys["en"], used - keys["en"])
        self.assertIsNone(ITALIAN.search(blocks["en"]))
        # no hard-coded Italian outside the dictionaries
        rest = html.replace(blocks["it"], "")
        self.assertIsNone(ITALIAN.search(re.sub(r"<style>.*?</style>", "", rest, flags=re.S)))

    def test_plugin_channels_english(self):
        import importlib.util
        import sys

        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "it")
        spec = importlib.util.spec_from_file_location("cc_server_i18n", ROOT / "cc-plugin" / "server.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        src = (ROOT / "cc-plugin" / "server.py").read_text(encoding="utf-8")
        self.assertIn('_("Polling rate (profile {n})"', src)
        self.assertEqual(i18n._("Polling rate (profile {n})", n=2), "Polling rate (profile 2)")
        i18n.set_language("it")
        self.assertEqual(i18n._("Polling rate (profile {n})", n=2), "Polling (profilo 2)")


if __name__ == "__main__":
    unittest.main()
