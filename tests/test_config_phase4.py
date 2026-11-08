import tempfile
import unittest
from pathlib import Path
from limbo.config import ConfigError, load_config

class ConfigTests(unittest.TestCase):
    def write(self,directory,content):
        path=Path(directory)/"limbo.toml"; path.write_text(content,encoding="utf-8"); return path
    def test_missing_file_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            c=load_config(Path(d)/"missing.toml",environ={})
        self.assertIsNone(c.path); self.assertEqual(c.server.get("port",8000),8000)
    def test_all_sections(self):
        source='''[app]\nname="Demo"\ndebug=true\n[server]\nhost="0.0.0.0"\nport=9000\nworkers=4\n[frontend]\ndirectory="web"\nentry="src/main.js"\n[build]\nout_dir="dist"\nminify=true\n[static]\nurl_prefix="/assets"\ndirectory="public"\n[security]\ntrusted_hosts=["example.com"]\ncsrf=true\n[database]\npath="data/app.sqlite"\ntimeout=5.0\n[tunnel]\nenabled=false\n'''
        with tempfile.TemporaryDirectory() as d: c=load_config(self.write(d,source),environ={})
        self.assertEqual(c.app.get("name"),"Demo"); self.assertEqual(c.server.get("port"),9000)
        self.assertEqual(c.frontend.get("entry"),"src/main.js"); self.assertEqual(c.build.get("out_dir"),"dist")
        self.assertEqual(c.security.get("trusted_hosts"),["example.com"]); self.assertEqual(c.database.get("timeout"),5.0)
        self.assertFalse(c.tunnel.get("enabled"))
    def test_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,"[server]\nport=7000\n")
            c=load_config(p,environ={"LIMBO_SERVER_PORT":"7100"},overrides={"server":{"port":7200}})
        self.assertEqual(c.server.get("port"),7200); self.assertEqual(c.sources["server.port"],"cli")
    def test_environment_override(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.write(d,'[app]\nname="file"\n'); c=load_config(p,environ={"LIMBO_APP_NAME":"env"})
        self.assertEqual(c.app.get("name"),"env")
    def test_unknown_section_and_key(self):
        for source,pattern in (("[oops]\nx=1\n","Unknown configuration section"),("[server]\nporrt=8\n","Unknown key")):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(ConfigError,pattern): load_config(self.write(d,source),environ={})
    def test_types_and_bounds(self):
        for source in ('[server]\nport="8000"\n','[server]\nport=70000\n','[app]\ndebug=1\n','[security]\ntrusted_hosts="example.com"\n','[server]\nworkers=0\n'):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ConfigError): load_config(self.write(d,source),environ={})
    def test_invalid_env(self):
        with self.assertRaisesRegex(ConfigError,"LIMBO_SERVER_PORT"): load_config("missing.toml",environ={"LIMBO_SERVER_PORT":"eight"})
        with self.assertRaisesRegex(ConfigError,"LIMBO_APP_DEBUG"): load_config("missing.toml",environ={"LIMBO_APP_DEBUG":"maybe"})
    def test_bad_cli_override(self):
        with self.assertRaisesRegex(ConfigError,"Unknown CLI override"): load_config("missing.toml",environ={},overrides={"server":{"bind":"x"}})
    def test_frontend_runtime_keys_validate(self):
        source='''[frontend]\nruntime_modules="local"\nmodule_url_prefix="/_limbo_modules"\nallow_remote_imports=false\njsx_runtime="automatic"\njsx_import_source="preact"\n'''
        with tempfile.TemporaryDirectory() as d:
            config=load_config(self.write(d,source),environ={})
        self.assertEqual(config.frontend.get("jsx_import_source"),"preact")
        self.assertFalse(config.frontend.get("allow_remote_imports"))
        for bad in ('[frontend]\nruntime_modules="cdn"\n','[frontend]\nmodule_url_prefix="/a/b"\n','[frontend]\njsx_runtime="babel"\n'):
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ConfigError): load_config(self.write(d,bad),environ={})

if __name__=="__main__": unittest.main()
