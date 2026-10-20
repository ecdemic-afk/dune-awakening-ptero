"""Tests for the pure parts of dunectl. Run: python -m unittest discover runtime/tests"""
import base64
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "core", "scripts")
sys.path.insert(0, os.path.join(ROOT, "runtime"))
sys.path.insert(0, SCRIPTS)

import admin_ini_merge  # noqa: E402
from dunectl import config, console, discord, doctor, gameplay, logstream, ports  # noqa: E402

SCHEMA = config.load_schema(SCRIPTS)


def validate(text):
    import tomllib
    return config.validate(tomllib.loads(text), text, SCHEMA, admin_ini_merge.normalize_value)


def token(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"xx.{body}.sig"


class ConfigTests(unittest.TestCase):
    def test_template_is_valid_and_complete(self):
        with open(config.TEMPLATE, encoding="utf-8") as fh:
            cfg, problems = validate(fh.read())
        self.assertEqual(problems, [])
        self.assertEqual(cfg["ports"]["game_slots"], 25)

    def test_startup_tab_overrides_instances(self):
        cfg, _ = validate("[instances]" + chr(10) + "deep_desert_dimensions = 3" + chr(10))
        problems, applied = config.apply_startup(cfg, {
            "WARM_MAPS": "Survival_1, Overmap", "DEEP_DESERT_COPIES": "1",
            "MAX_RUNNING_MAPS": "", "IDLE_MAP_STOP": "30m"})
        self.assertEqual(problems, [])
        self.assertEqual(cfg["instances"]["always_warm"], ["Survival_1", "Overmap"])
        self.assertEqual(cfg["instances"]["deep_desert_dimensions"], 1)
        self.assertEqual(cfg["instances"]["max_concurrent"], 8)  # empty = keep the file's value
        self.assertEqual(cfg["instances"]["auto_stop"], "30m")
        self.assertEqual(len(applied), 3)
        cfg, _ = validate("")
        problems, _ = config.apply_startup(cfg, {"WARM_MAPS": "none", "DEEP_DESERT_COPIES": "13",
                                                 "IDLE_MAP_STOP": "soon", "HARKO_COPIES": "5"})
        self.assertEqual(cfg["instances"]["always_warm"], [])
        self.assertTrue(any("DEEP_DESERT_COPIES" in p for p in problems))
        self.assertTrue(any("IDLE_MAP_STOP" in p for p in problems))
        self.assertTrue(any("dimension_slots" in p for p in problems))  # 1 DD + 5 Harko > 5 slots
        problems, _ = config.apply_startup(validate("")[0], {"WARM_MAPS": "Survival_1;rm"})
        self.assertTrue(problems)

    def test_typo_in_key_names_line_and_suggests(self):
        _, problems = validate("[instances]\n\nmax_concurent = 4\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("line 3", problems[0])
        self.assertIn("max_concurrent", problems[0])

    def test_unknown_section(self):
        _, problems = validate("[gamplay]\nmining_output = 2.0\n")
        self.assertIn("gameplay", problems[0])

    def test_wrong_type_and_bounds(self):
        _, problems = validate("[instances]\nmax_concurrent = \"8\"\n[backups]\nkeep = 0\n")
        self.assertTrue(any("whole number" in p for p in problems))
        self.assertTrue(any("between 1 and 365" in p for p in problems))

    def test_gameplay_accepts_schema_ids_and_rejects_bad_values(self):
        cfg, problems = validate("[gameplay]\nmining_output = 2.5\nsandworms_enabled = false\n")
        self.assertEqual(problems, [])
        self.assertEqual(cfg["gameplay"]["mining_output"], 2.5)
        _, problems = validate("[gameplay]\nmining_output = \"lots\"\n")
        self.assertIn("mining_output", problems[0])
        _, problems = validate("[gameplay]\nmining_outptu = 2\n")
        self.assertIn("did you mean `mining_output`", problems[0])

    def test_settings_owned_elsewhere_are_refused(self):
        _, problems = validate("[gameplay]\nserver_password = \"x\"\n")
        self.assertIn("Startup tab", problems[0])

    def test_too_many_dimensions_for_the_slots(self):
        _, problems = validate("[instances]\ndeep_desert_dimensions = 6\n")
        self.assertTrue(any("dimension_slots" in p for p in problems))

    def test_tcp_offset_overlapping_udp_range(self):
        _, problems = validate("[ports]\nmq_game_offset = 10\n")
        self.assertTrue(any("overlaps the game ports" in p for p in problems))

    def test_core_env(self):
        cfg = config.defaults()
        sv = config.startup_vars({"DUNE_TOKEN": "t", "SERVER_NAME": "S", "BRANCH": "ptc", "SERVER_PORT": "27000"})
        lay = ports.layout(27000, cfg["ports"], config.dimension_count(cfg))
        env = config.core_env(cfg, sv, "1.2.3.4", lay)
        self.assertEqual(env["SRCDS_APPID"], "3104830")
        self.assertEqual(env["K8S_POOL_GAME_PORT_BASE"], "27000")
        self.assertEqual(env["DUNE_DIM_GAME_PORT_BASE"], "27025")
        self.assertEqual(env["DUNE_MQ_GAME_PORT"], "27030")
        self.assertEqual(env["DUNE_EXTERNAL_IP"], "1.2.3.4")

    def test_seed_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(config.seed(d))
            with open(config.config_path(d), "w") as fh:
                fh.write("# mine\n")
            self.assertFalse(config.seed(d))
            with open(config.config_path(d)) as fh:
                self.assertEqual(fh.read(), "# mine\n")


class PortTests(unittest.TestCase):
    def test_layout_and_description(self):
        lay = ports.layout(27000, config.defaults()["ports"], 3)
        self.assertEqual((lay["block_first"], lay["block_last"]), (27000, 27039))
        text = " ".join(ports.describe(lay))
        self.assertIn("27000-27039", text)
        self.assertIn("UDP 27000-27027", text)

    def test_internal_port_clash(self):
        cfg = dict(config.defaults()["ports"], mq_game_offset=30)
        lay = ports.layout(8050, cfg, 0)  # mq_game = 8080 = gateway
        self.assertTrue(any("8080" in p for p in ports.problems(lay)))


class DoctorTests(unittest.TestCase):
    def test_token(self):
        self.assertEqual(doctor.check_token("", 0).status, doctor.FAIL)
        self.assertEqual(doctor.check_token("garbage", 0).status, doctor.FAIL)
        self.assertEqual(doctor.check_token(token({"HostId": "ABC"}), 0).status, doctor.OK)
        self.assertEqual(doctor.check_token(token({"HostId": "ABC", "exp": 10}), 100).status, doctor.FAIL)

    def test_ip(self):
        self.assertEqual(doctor.check_ip("auto", "8.8.8.8")[1], "8.8.8.8")
        self.assertEqual(doctor.check_ip("auto", None)[0].status, doctor.FAIL)
        self.assertEqual(doctor.check_ip("10.0.0.9", None)[0].status, doctor.FAIL)
        self.assertEqual(doctor.check_ip("8.8.8.8", "1.1.1.1")[0].status, doctor.WARN)

    def test_thread_limit(self):
        read = lambda v: (lambda p: v if p.endswith("pids.max") else None)
        self.assertEqual(doctor.check_threads(read("512\n")).status, doctor.WARN)
        self.assertIn("Restart in Pterodactyl", doctor.check_threads(read("512\n")).fix)
        self.assertEqual(doctor.check_threads(read("2048\n")).status, doctor.WARN)
        self.assertEqual(doctor.check_threads(read("max\n")).status, doctor.OK)

    def test_cpu_and_memory(self):
        self.assertEqual(doctor.check_cpu(lambda p: "processor : 0\nflags : sse avx\n").status, doctor.FAIL)
        self.assertEqual(doctor.check_cpu(lambda p: "processor : 0\nflags : sse avx2\n").status, doctor.OK)
        self.assertEqual(doctor.check_memory(lambda p: str(8 * doctor.GiB) if "memory.max" in p else None).status,
                         doctor.WARN)


class ConsoleTests(unittest.TestCase):
    def p(self, s):
        return console.parse(s, 600)

    def test_restart_forms(self):
        self.assertEqual(self.p("restart").delay, 600)
        self.assertFalse(self.p("restart").explicit)
        self.assertEqual(self.p("restart now").delay, 0)
        r = self.p("restart in 15m Nightly restart")
        self.assertEqual((r.delay, r.reason), (900, "Nightly restart"))
        self.assertEqual(self.p("/update 1h").delay, 3600)
        self.assertEqual(self.p("restart in soon").name, "error")

    def test_misc(self):
        self.assertEqual(self.p("announce Hello there").text, "Hello there")
        self.assertEqual(self.p("announce").name, "error")
        self.assertEqual(self.p("admin give me Spice 5").argv, ["give", "me", "Spice", "5"])
        self.assertEqual(self.p("frobnicate").name, "unknown")
        self.assertEqual(self.p("").name, "noop")
        self.assertEqual(self.p("logs verbose").verbose, True)

    def test_human(self):
        self.assertEqual(console.human(600), "10 min")
        self.assertEqual(console.human(3600), "1h")
        self.assertEqual(console.human(45), "45s")


class GameplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        st = os.path.join(self.tmp.name, "server", "state")
        os.makedirs(os.path.join(st, "ue5-saved", "UserSettings"))
        for rel, text in gameplay.FILES.items():
            with open(os.path.join(st, text), "w") as fh:
                fh.write("[ConsoleVariables]\n" if rel == "UserEngine" else "")
        self.st = st

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, key):
        with open(os.path.join(self.st, gameplay.FILES[key])) as fh:
            return fh.read()

    def test_apply_writes_settings_and_merges_overrides(self):
        cfg = config.defaults()
        cfg["gameplay"]["mining_output"] = 3.0
        cfg["gameplay"]["pvp_partitions"] = [8, 101]
        sv = config.startup_vars({"SERVER_NAME": "My Srv", "SERVER_PASSWORD": ""})
        logs = []
        with open(os.path.join(self.st, "UserOverrides.ini"), "w") as fh:
            fh.write("[/Script/X]\nA=1\n")
        stats = gameplay.apply(self.tmp.name, cfg, sv, SCHEMA, admin_ini_merge, lambda m, lv="INFO": logs.append(m))
        self.assertEqual(stats["failed"], 0, logs)
        self.assertIn('Bgd.ServerDisplayName="My Srv"', self.read("UserEngine"))
        self.assertIn("MaxConcurrentInstances=8", self.read("ondemand"))
        ug = self.read("UserGame")
        self.assertIn("m_PvpEnabledPartitions=8", ug)
        self.assertIn("SelectionRule=\"HomeDimension\"", ug)
        self.assertEqual(ug.count(gameplay.OVR_BEGIN), 1)
        self.assertIn("A=1", ug)
        # Applying twice must not duplicate the override block.
        gameplay.apply(self.tmp.name, cfg, sv, SCHEMA, admin_ini_merge, lambda m, lv="INFO": None)
        self.assertEqual(self.read("UserGame").count(gameplay.OVR_BEGIN), 1)


class LogTests(unittest.TestCase):
    def test_classify_and_label(self):
        self.assertEqual(logstream.classify("[13:01:02 27 ERR IGWO] boom"), "ERROR")
        self.assertEqual(logstream.classify("LogNet: Warning: slow"), "WARN")
        self.assertEqual(logstream.classify("[prestart] [INFO] fine"), "INFO")
        self.assertEqual(logstream.label("ue5-Survival_1-p2"), "ue5:Survival_1")
        self.assertEqual(logstream.label("ue5-DeepDesert_1-dim1-p101"), "ue5:DeepDesert_1-dim1")

    def test_quiet_filter_and_repeat_collapse(self):
        out = []
        f = logstream.Follower("/nonexistent", lambda t, m, lv: out.append((t, m)))
        f.line("director", "[1:2:3 1 INF X] chatter")
        f.line("director", "[1:2:3 1 ERR X] broken")
        f.line("director", "[1:2:3 1 ERR X] broken")
        f.line("director", "[1:2:3 1 ERR X] broken")
        f.line("director", "[1:2:3 1 ERR X] other")
        f.line("start-pg", "[start-pg] [INFO] postgres up")
        msgs = [m for _, m in out]
        self.assertNotIn("[1:2:3 1 INF X] chatter", msgs)
        self.assertEqual(msgs.count("[1:2:3 1 ERR X] broken"), 1)
        self.assertIn("(previous line repeated 2 more times)", msgs)
        self.assertIn("[start-pg] [INFO] postgres up", msgs)
        f.line("text-router", "[02:21:11 1 ERR Igwo] Missing required environment variable for IGWO. Found host: ")
        f.line("text-router", "      Failed to determine the https port for redirect.")
        self.assertFalse(any("IGWO" in m or "https port" in m for _, m in out))

    def test_levels_from_the_user_log(self):
        ue = "[2026.10.20-17.32.29:733][541][709][8]DuneAILod: Warning: ExitGhostModeClient() - Failed to disable"
        self.assertEqual(logstream.classify(ue), "WARN")  # was ERROR because of "Failed"
        self.assertEqual(logstream.classify("[2026.10.20-17.32.29:733][541][709][8]LogNet: failed to bind"), "INFO")
        self.assertEqual(logstream.classify("[2026.10.20-17.32.29:733][  0]LogX: Error: boom"), "ERROR")
        self.assertEqual(logstream.classify("fail: Microsoft.Something[3]"), "ERROR")
        out = []
        f = logstream.Follower("/nonexistent", lambda t, m, lv: out.append((m, lv)))
        f.line("ue5-DeepDesert_1-p8", ue)
        f.line("ue5-DeepDesert_1-p8", "[2026.10.20-17.32.29:733][541][709][8]LogX: Error: real problem")
        f.line("director", "[02:33:29 22 WRN Database] [61 occurrences]: Failed to process travel queue for partition 102.")
        f.line("director", "Error:Partition's ServerId is null or empty!")
        f.line("director", "[02:33:29 22 ERR Director] broken")
        f.line("director", "System.Exception: details")
        self.assertEqual([m for m, _ in out], ["[2026.10.20-17.32.29:733][541][709][8]LogX: Error: real problem",
                                               "[02:33:29 22 ERR Director] broken", "System.Exception: details"])
        self.assertEqual(out[-1][1], "ERROR")

    def test_early_unreal_startup_and_secrets(self):
        out = []
        f = logstream.Follower("/nonexistent", lambda t, m, lv: out.append((m, lv)))
        svc = "ue5-Survival_1-p1"
        f.line(svc, "LogFuncomLiveServices: Error: Setting 'GgwpApiKey' was not found in section 'X' of *Engine.ini")
        f.line(svc, "LogFuncomLiveServices: Error: Setting 'Other' was not found")
        f.line(svc, "LogOnlineSubsystemFls: Display: FLS Environment Initialized!")
        f.line(svc, "--- FLS ENVIRONMENT INFO ---")
        f.line(svc, "LogInit: Log: Build: 1.5.15.0")
        self.assertEqual([m for m, _ in out], ["LogFuncomLiveServices: Error: Setting 'Other' was not found"])
        f.verbose = True
        f.line(svc, "LogInit: Log: Command Line: Survival_1 -ini:engine:[FLS]:ServiceAuthToken=eyJhbGciOiJ.eyJIb3N0SWQiOi.sig"
                    " -ini:engine:[FLS]:ServerCommandsAuthToken=3fa3d4 -DatabasePassword=dune -Port=27035")
        shown = out[-1][0]
        self.assertNotIn("eyJ", shown)
        self.assertNotIn("3fa3d4", shown)
        self.assertNotIn("=dune", shown)
        self.assertIn("-Port=27035", shown)
        self.assertEqual(out[-1][1], "INFO")
        f.verbose = False
        n = len(out)
        for noise in ("[2026.10.20-17.43.36:730][106][974][101]LogMercuna: Error: Failed to merge nav grids A and B",
                      "[2026.10.20-17.43.27:723][  0][974]LogHttpListener: Error: HttpListener unable to bind to 127.0.0.1:10000",
                      "[2026.10.20-17.43.44:264][107][722][1]LogJourneySubsystem: Error: Ensure condition failed: x"):
            f.line(svc, noise)
        self.assertEqual(len(out), n)


class DiscordTests(unittest.TestCase):
    def test_filters_and_dedupes_without_network(self):
        n = discord.Notifier("", "S", {}, lambda *a: None)
        self.assertFalse(n.send("crash", "x"))  # no URL: nothing queued
        n = discord.Notifier.__new__(discord.Notifier)
        n.url, n.server, n.switches, n.recent = "https://example.invalid", "S", {"notify_players": False}, {}
        import queue
        n.q = queue.Queue()
        self.assertTrue(n.send("crash", "down", "why", "bad"))
        self.assertFalse(n.send("crash", "down", "why", "bad"))  # duplicate within 10 min
        self.assertFalse(n.send("player", "joined"))             # switched off
        body = n.q.get_nowait()
        self.assertEqual(body["embeds"][0]["color"], discord.COLORS["bad"])


if __name__ == "__main__":
    unittest.main()


class FakeServices:
    def __init__(self):
        self.st = {n: 100 for n in ("postgres", "mq-admin", "mq-game", "text-router", "director", "gateway",
                                    "fls-stub", "mock-k8s")}
        self.games = 3
        self.diag = ""
        self.stops = 0

    def status(self):
        return dict(self.st)

    def game_servers_alive(self):
        return self.games

    def diagnose(self, name):
        return self.diag

    def stop_all(self):
        self.stops += 1


class SupervisorPolicyTests(unittest.TestCase):
    def make(self):
        from dunectl import supervisor
        sup = supervisor.Supervisor.__new__(supervisor.Supervisor)
        sup.cfg = config.defaults()
        sup.svc = FakeServices()
        sup.watch_since, sup.reported, sup.crashes = {}, set(), []
        sup.started_at = 0
        sup.state = "running"
        sup.discord = discord.Notifier("", "S", {}, lambda *a: None)
        sup.events = []
        sup._restart_now = lambda reason, update=False: sup.events.append(("restart", reason))
        sup._hold = lambda why: sup.events.append(("hold", why))
        return sup, supervisor

    def test_healthy_does_nothing(self):
        sup, _ = self.make()
        sup._watch()
        self.assertEqual(sup.events, [])

    def test_critical_down_waits_for_grace_then_restarts(self):
        sup, mod = self.make()
        sup.svc.st["text-router"] = None
        t = [1000.0]
        mod.time.time = lambda: t[0]
        try:
            sup._watch()
            self.assertEqual(sup.events, [])          # inside grace
            t[0] += sup.cfg["advanced"]["core_down_grace"]
            sup._watch()
            self.assertEqual(sup.events, [("restart", "text-router stopped")])
        finally:
            import time as _t
            mod.time.time = _t.time

    def test_config_refusal_holds_instead_of_looping(self):
        sup, mod = self.make()
        sup.svc.st["gateway"] = None
        sup.svc.diag = "Funcom refused the token"
        t = [1000.0]
        mod.time.time = lambda: t[0]
        try:
            sup._watch()
            t[0] += 60
            sup._watch()
            self.assertEqual(sup.events[0][0], "hold")
            self.assertIn("gateway", sup.events[0][1])
        finally:
            import time as _t
            mod.time.time = _t.time

    def test_too_many_crashes_hold(self):
        sup, _ = self.make()
        for _ in range(sup.cfg["advanced"]["max_auto_restarts"]):
            sup._crash("boom")
        self.assertTrue(all(e[0] == "restart" for e in sup.events))
        sup._crash("boom")
        self.assertEqual(sup.events[-2][0], "hold")

    def test_all_game_servers_down_after_grace(self):
        sup, mod = self.make()
        sup.svc.games = 0
        g = sup.cfg["advanced"]["game_down_grace"]
        t = [10_000.0]
        mod.time.time = lambda: t[0]
        try:
            sup._watch()
            self.assertEqual(sup.events, [])
            t[0] += g
            sup._watch()
            self.assertEqual(sup.events[0][0], "restart")
        finally:
            import time as _t
            mod.time.time = _t.time


class RestoreResultTests(unittest.TestCase):
    def test_partition_key_refusals_are_success_anything_else_is_not(self):
        from dunectl.supervisor import restore_succeeded
        benign = ("pg_restore: error: could not execute query: ERROR:  cannot drop inherited constraint "
                  '"event_log_p9_pkey" of relation "event_log_p9"' + chr(10) +
                  "Command was: ALTER TABLE IF EXISTS ONLY dune.event_log_p9 DROP CONSTRAINT IF EXISTS event_log_p9_pkey;"
                  + chr(10) + "pg_restore: warning: errors ignored on restore: 1" + chr(10) +
                  "[admin-publish] ERROR db-restore: pg_restore failed")
        self.assertTrue(restore_succeeded(0, ""))
        self.assertTrue(restore_succeeded(1, benign))
        self.assertFalse(restore_succeeded(1, benign + chr(10) +
                                           'pg_restore: error: could not execute query: ERROR:  relation "x" does not exist'))
        self.assertFalse(restore_succeeded(1, "[admin-publish] ERROR db-restore: no such backup 'x'"))


class RestoreParseTests(unittest.TestCase):
    def test_restore_forms(self):
        c = console.parse("restore", 600)
        self.assertEqual((c.name, c.file, c.confirmed), ("restore", "", False))
        c = console.parse("restore dune-20261020-103446.dump", 600)
        self.assertEqual((c.file, c.confirmed), ("dune-20261020-103446.dump", False))
        c = console.parse("restore dune-20261020-103446.dump confirm", 600)
        self.assertTrue(c.confirmed)


class DimensionAndBudgetTests(unittest.TestCase):
    def test_plan_parks_copies_above_the_count_and_leaves_sietches(self):
        from dunectl import dims
        cfg = config.defaults()
        rows = dims.parse_rows("partition_id,map,dimension_index\n101,DeepDesert_1,1\n102,DeepDesert_1,2\n"
                               "103,DeepDesert_1,3\n111,SH_Arrakeen,1\n201,Survival_1,1\n")
        park, unpark = dims.plan(rows, cfg)       # defaults: 1 DD copy, 0 Arrakeen
        self.assertEqual(park, {102, 103, 111})
        self.assertEqual(unpark, {101})
        cfg["instances"]["deep_desert_dimensions"] = 3
        park, unpark = dims.plan(rows, cfg)
        self.assertEqual(park, {111})
        self.assertEqual(unpark, {101, 102, 103})

    def test_budget(self):
        mem = lambda gib: (lambda p: str(gib * doctor.GiB) if "memory.max" in p else None)
        self.assertEqual(doctor.check_budget(6, 8, mem(32)).status, doctor.WARN)   # what happened
        self.assertEqual(doctor.check_budget(4, 8, mem(32)).status, doctor.OK)     # old setup
        self.assertEqual(doctor.check_budget(10, 8, lambda p: "max").status, doctor.OK)
        # Memory 0 in Pterodactyl: judged against the machine's RAM instead.
        host = lambda p: "max" if "cgroup" in p else "MemTotal:       32000000 kB" + chr(10) + "MemFree: 1 kB"
        self.assertEqual(doctor.check_budget(6, 8, host).status, doctor.WARN)


class UpgradeTests(unittest.TestCase):
    def tarball(self, files: dict) -> bytes:
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def test_stage_and_swap_keep_the_binary_and_the_previous_copy(self):
        from dunectl import upgrade
        with tempfile.TemporaryDirectory() as base:
            for d, f in (("scripts", "old.sh"), ("runtime", "old.py"), ("data", "old.json"), ("mock-k8s", "a.go")):
                os.makedirs(os.path.join(base, d))
                open(os.path.join(base, d, f), "w").write("old")
            open(os.path.join(base, "scripts", "mock-k8s-go"), "w").write("BINARY")
            tb = self.tarball({"repo-abc/core/scripts/new.sh": b"new", "repo-abc/runtime/new.py": b"new",
                               "repo-abc/core/data/new.json": b"{}", "repo-abc/core/mock-k8s/a.go": b"changed"})
            new = upgrade.stage(base, tb)
            self.assertTrue(upgrade.mock_k8s_changed(base, new))
            upgrade.swap(base, new)
            self.assertTrue(os.path.exists(os.path.join(base, "scripts", "new.sh")))
            self.assertFalse(os.path.exists(os.path.join(base, "scripts", "old.sh")))
            self.assertEqual(open(os.path.join(base, "scripts", "mock-k8s-go")).read(), "BINARY")
            self.assertTrue(os.path.exists(os.path.join(base, ".upgrade", "previous", "runtime", "old.py")))

    def test_rejects_wrong_archive_and_path_traversal(self):
        from dunectl import upgrade
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(ValueError):
                upgrade.stage(base, self.tarball({"repo/README.md": b"x"}))
            with self.assertRaises(Exception):
                upgrade.stage(base, self.tarball({"repo/../../evil.sh": b"x"}))
            self.assertFalse(os.path.exists(os.path.join(os.path.dirname(base), "evil.sh")))

    def test_parse(self):
        c = console.parse("upgrade now", 600)
        self.assertEqual((c.name, c.delay), ("upgrade", 0))


class UpgradeOldPythonTests(UpgradeTests):
    """Debian bookworm's Python 3.11.2 has no extractall(filter=...): the manual check runs."""
    def setUp(self):
        import tarfile
        self._orig = tarfile.TarFile.extractall

        def no_filter(tf, path=".", members=None, *, numeric_owner=False, **kw):
            if "filter" in kw:
                raise TypeError("unexpected keyword argument 'filter'")
            return self._orig(tf, path, members, numeric_owner=numeric_owner, filter="fully_trusted")
        tarfile.TarFile.extractall = no_filter

    def tearDown(self):
        import tarfile
        tarfile.TarFile.extractall = self._orig


class FakeApp:
    def __init__(self):
        self.commands, self.saved = [], None

    def panel_status(self):
        return {"state": "running", "version": "test"}

    def panel_command(self, line):
        if line == "stop":
            return "use Pterodactyl's Stop button"
        self.commands.append(line)
        return None

    def config_text(self):
        return "[updates]" + chr(10) + "warn_minutes = 10" + chr(10)

    def save_config(self, text):
        _, problems = config.parse_text(text, SCRIPTS)
        if not problems:
            self.saved = text
        return problems


class PanelTests(unittest.TestCase):
    def setUp(self):
        import collections
        from dunectl import panel
        self.panel_mod = panel
        self.log = collections.deque([(1, 0.0, "dune", "INFO", "hello"), (2, 0.0, "dune", "WARN", "careful")])
        self.app = FakeApp()
        self.p = panel.Panel(self.app, 0, "correct horse battery", self.log, lambda *a: None)
        self.p.start()
        self.base = f"http://127.0.0.1:{self.p.httpd.server_address[1]}"
        self.cookie = ""

    def tearDown(self):
        self.p.stop()

    def call(self, path, body=None, header=True):
        import urllib.error
        import urllib.request
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Dune"] = "1"
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                code, raw, hdrs = res.status, res.read(), res.headers
        except urllib.error.HTTPError as err:
            code, raw, hdrs = err.code, err.read(), err.headers
        set_cookie = hdrs.get("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw

    def test_page_is_public_api_is_not(self):
        code, page = self.call("/")
        self.assertEqual(code, 200)
        self.assertIn(b"app.js", page)
        self.assertEqual(self.call("/api/status")[0], 401)
        self.assertEqual(self.call("/../dunectl/panel.py")[0], 404)

    def test_sign_in_then_use(self):
        self.assertEqual(self.call("/api/login", {"password": "correct horse battery"}, header=False)[0], 403)
        self.assertEqual(self.call("/api/login", {"password": "correct horse battery"})[0], 200)
        self.assertEqual(self.call("/api/status"), (200, {"state": "running", "version": "test"}))
        code, data = self.call("/api/log?after=1")
        self.assertEqual([r["line"] for r in data["lines"]], ["careful"])
        self.assertEqual(self.call("/api/command", {"line": "restart in 5m Test"})[0], 200)
        self.assertEqual(self.app.commands, ["restart in 5m Test"])
        self.assertEqual(self.call("/api/command", {"line": "stop"})[0], 400)
        self.assertEqual(self.call("/api/command", {"line": "backup"}, header=False)[0], 403)  # cross-site
        code, data = self.call("/api/config", {"text": "[updates]" + chr(10) + "warn_minuts = 5" + chr(10)})
        self.assertEqual(code, 400)
        self.assertIn("warn_minutes", data["problems"][0])  # the did-you-mean hint
        self.assertIsNone(self.app.saved)
        good = "[updates]" + chr(10) + "warn_minutes = 5" + chr(10)
        self.assertEqual(self.call("/api/config", {"text": good})[0], 200)
        self.assertEqual(self.app.saved, good)
        self.assertEqual(self.call("/api/logout", {})[0], 200)
        self.assertEqual(self.call("/api/status")[0], 401)

    def test_wrong_passwords_lock_out(self):
        clock = [1000.0]
        auth = self.panel_mod.Auth("secret-password", now=lambda: clock[0])
        for _ in range(self.panel_mod.MAX_FAILS):
            self.assertIsNone(auth.login("1.2.3.4", "nope"))
        self.assertTrue(auth.locked("1.2.3.4"))
        self.assertIsNone(auth.login("1.2.3.4", "secret-password"))  # even the right one, while locked
        self.assertFalse(auth.locked("5.6.7.8"))
        clock[0] += self.panel_mod.LOCK_SECS + 1
        token = auth.login("1.2.3.4", "secret-password")
        self.assertTrue(auth.valid(token))
        clock[0] += self.panel_mod.SESSION_SECS + 1
        self.assertFalse(auth.valid(token))


class PlayerToolsTests(unittest.TestCase):
    FLS = "DE0BCCAA2501BF22"

    def setUp(self):
        from dunectl import players
        self.mod = players
        self.base = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.base, "data", "admin"))
        with open(os.path.join(self.base, "data", "admin", "items.json"), "w", encoding="utf-8") as fh:
            json.dump([{"id": "Stilltent", "name": "Stilltent", "category": "gear"},
                       {"id": "WaterPack_Consumable", "name": "Cup of Water", "category": "consumables"}], fh)
        self.calls = []
        nl = chr(10)
        self.outputs = {
            "db-sql": (0, "fls_id,name,steam_id,life,online,last_activity" + nl
                       + self.FLS + ",Paul,7656,Alive,Online,2026-10-21 03:00:00+00" + nl
                       + "AAAABBBBCCCCDDDD,,7657,Alive,Offline," + nl, ""),
            "player-state": (0, "fls_id,account_id,platform_id,platform_name,online,life,last_activity,pc_actor_id,map"
                             + nl + self.FLS + ",1,7656,Steam,Online,Alive,2026-10-21,55,HaggaBasin" + nl, ""),
            "player-summary": (0, "solaris=1200" + nl + "char_xp=0" + nl + "align=1" + nl + "rep_1=50" + nl + "rep_2=0" + nl, ""),
            "inventory-list": (0, "item_id,inv_type,template_id,stack_size,quality,durability,max_durability,slot"
                               + nl + "9,Backpack,Stilltent,1,0,N/A,N/A,3" + nl, ""),
        }

        def run_op(argv, timeout):
            self.calls.append(argv)
            return self.outputs.get(argv[0], (0, "publish=ok", ""))
        self.tools = players.PlayerTools(self.base, run_op, lambda *a: None)

    def test_argv_validation(self):
        a = self.mod.action_argv
        self.assertEqual(a(self.FLS, "give-item", {"template": "Stilltent", "qty": "3"}),
                         ["give-item", self.FLS, "Stilltent", "3", "0"])
        for bad in ({"template": "x; rm -rf /"}, {"template": "Stilltent", "qty": 0}, {"template": "Stilltent", "qty": True}):
            with self.assertRaises(ValueError):
                a(self.FLS, "give-item", bad)
        with self.assertRaises(ValueError):
            a(self.FLS, "clean", {})  # destructive needs confirm
        self.assertEqual(a(self.FLS, "clean", {"confirm": True}), ["clean", self.FLS])
        self.assertEqual(a(self.FLS, "item-edit", {"item_id": 9, "stack": 4, "quality": 1}),
                         ["item-edit", "9", "stack=4", "quality=1"])
        with self.assertRaises(ValueError):
            a(self.FLS, "drop-database", {})

    def test_list_detail_and_kit(self):
        listed = self.tools.list()
        self.assertEqual([(p["name"], p["online"]) for p in listed], [("Paul", True), ("AAAABBBBCCCCDDDD", False)])
        d = self.tools.detail(self.FLS)
        self.assertTrue(d["online"])
        self.assertEqual(d["summary"]["solaris"], 1200)
        self.assertEqual(d["summary"]["houses"][0]["name"], "Atreides")
        self.assertEqual(d["inventory"][0]["name"], "Stilltent")
        self.tools.save_kits([{"name": "Water", "items": [{"id": "WaterPack_Consumable", "qty": 2}, {"id": "Stilltent"}]}])
        res = self.tools.act(self.FLS.lower(), "give-kit", {"kit": "Water"}, "1.2.3.4")
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls[-2:], [["give-item", self.FLS, "WaterPack_Consumable", "2", "0"],
                                           ["give-item", self.FLS, "Stilltent", "1", "0"]])
        with self.assertRaises(ValueError):
            self.tools.save_kits([{"name": "Bad", "items": [{"id": "NotAnItem", "qty": 1}]}])
        with self.assertRaises(ValueError):
            self.tools.act("not-an-id", "kick", {}, "x")
        self.assertEqual(self.tools.search_items("cup")[0]["id"], "WaterPack_Consumable")

    def test_ban_and_unban(self):
        res = self.tools.act(self.FLS, "ban", {"name": "Paul", "reason": "griefing", "hours": 24}, "1.2.3.4")
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls[-1], ["kick", self.FLS])
        self.assertTrue(self.tools.list()[0]["banned"])
        self.assertEqual(self.tools.bans()[0]["reason"], "griefing")
        self.tools.act(self.FLS, "unban", {}, "1.2.3.4")
        self.assertEqual(self.tools.bans(), [])


class ScheduleTests(unittest.TestCase):
    def test_validate_and_due(self):
        import time as _t
        from dunectl import schedule
        cfg = schedule.validate({"restarts": [{"time": "4:00", "days": ["Mon", "wed"], "warn_minutes": 10}],
                                 "announcements": [{"time": "20:30", "message": "Double XP!"}],
                                 "backup_every_hours": 6})
        self.assertEqual(cfg["restarts"][0]["time"], "04:00")
        self.assertEqual(cfg["restarts"][0]["days"], ["mon", "wed"])
        monday_4am = _t.strptime("2026-10-19 04:00", "%Y-%m-%d %H:%M")  # a Monday
        fired = set()
        hits = schedule.due(cfg, monday_4am, fired)
        self.assertEqual([k for k, _, _ in hits], ["restarts"])
        fired.add(hits[0][1])
        self.assertEqual(schedule.due(cfg, monday_4am, fired), [])  # same minute never twice
        tuesday_4am = _t.strptime("2026-10-20 04:00", "%Y-%m-%d %H:%M")
        self.assertEqual(schedule.due(cfg, tuesday_4am, set()), [])
        any_day = _t.strptime("2026-10-24 20:30", "%Y-%m-%d %H:%M")
        self.assertEqual([k for k, _, _ in schedule.due(cfg, any_day, set())], ["announcements"])
        self.assertTrue(schedule.backup_due(cfg, None, 1000.0))
        self.assertFalse(schedule.backup_due(cfg, 1000.0, 1000.0 + 3600))
        self.assertTrue(schedule.backup_due(cfg, 1000.0, 1000.0 + 6 * 3600))
        for bad in ({"restarts": [{"time": "25:00"}]}, {"restarts": [{"time": "04:00", "days": ["funday"]}]},
                    {"announcements": [{"time": "04:00", "message": ""}]}, {"backup_every_hours": 500}):
            with self.assertRaises(ValueError):
                schedule.validate(bad)


class WorldToolsTests(unittest.TestCase):
    def setUp(self):
        from dunectl import world
        self.world = world
        self.base = tempfile.mkdtemp()
        self.calls = []
        self.reply = (0, "", "")

        def run_op(argv, timeout):
            self.calls.append(argv)
            return self.reply
        self.tools = world.WorldTools(self.base, SCRIPTS, run_op, lambda *a: None)

    def test_catalogue_from_the_real_script(self):
        ops = {o["op"]: o for o in self.tools.ops()}
        self.assertIn("give-item", ops)
        self.assertEqual([a["name"] for a in ops["give-item"]["args"]], ["player_id", "ItemTemplate", "qty", "quality"])
        self.assertTrue(ops["give-item"]["args"][2]["optional"])
        self.assertTrue(ops["account-delete"]["danger"])
        self.assertNotIn("raw", ops)  # shell-only operations are hidden
        with self.assertRaises(ValueError):
            self.tools.run("give-item", ["only-one"], "x")
        with self.assertRaises(ValueError):
            self.tools.run("raw", ["{}"], "x")
        self.tools.run("kick", ["DE0BCCAA2501BF22"], "x")
        self.assertEqual(self.calls[-1], ["kick", "DE0BCCAA2501BF22"])

    def test_refill_queues_when_the_map_runs(self):
        self.reply = (1, "", "[admin-publish] ERROR base-water-refill: HaggaBasin (partition 1) has 1 live server(s)")
        res = self.tools.refill("42", "water", "1.2.3.4")
        self.assertTrue(res["queued"])
        self.tools.refill("42", "water", "1.2.3.4")  # queued once, not twice
        self.assertEqual(self.tools.queued("42"), ["water"])
        self.reply = (1, "", "ERROR base-fuel-refill: no generators or wind turbines at base 42")
        self.assertFalse(self.tools.refill("42", "fuel", "x")["ok"])  # other failures are not queued
        self.assertEqual(self.tools.queued(), ["water"])
        with self.assertRaises(ValueError):
            self.tools.refill("42; rm", "water", "x")


class GiftTests(PlayerToolsTests):
    def test_teleport_gift_and_welcome(self):
        self.assertEqual(self.mod.action_argv(self.FLS, "teleport", {"x": 10, "y": -20, "z": 3000}),
                         ["tpsafe", self.FLS, "10", "-20", "3000"])
        with self.assertRaises(ValueError):
            self.mod.action_argv(self.FLS, "teleport", {"x": "a", "y": 0, "z": 0})
        res = self.tools.gift({"target": "online", "template": "Stilltent", "qty": 2}, "x")
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls[-1], ["give-item", self.FLS, "Stilltent", "2", "0"])  # only Paul is online
        with self.assertRaises(ValueError):
            self.tools.gift({"target": "online", "template": "Nope"}, "x")
        with self.assertRaises(ValueError):
            self.tools.gift({"target": "online", "kit": "No such kit"}, "x")
        self.tools.save_kits([{"name": "Start", "items": [{"id": "Stilltent", "qty": 1}]}])
        w = self.tools.save_welcome({"enabled": True, "kit": "Start"}, "x")
        self.assertTrue(w["enabled"])
        import admin_welcome
        cfg = admin_welcome.load_config(self.base)
        first = cfg["active_version"]
        self.assertEqual(cfg["packages"][0]["items"], [{"template": "Stilltent", "qty": 1, "quality": 0}])
        self.tools.save_kits([{"name": "Start", "items": [{"id": "Stilltent", "qty": 3}]}])  # edit: no new round
        cfg = admin_welcome.load_config(self.base)
        self.assertEqual((cfg["active_version"], cfg["packages"][0]["items"][0]["qty"]), (first, 3))
        with self.assertRaises(ValueError):
            self.tools.save_welcome({"enabled": True, "kit": "Missing"}, "x")


class ImageTests(unittest.TestCase):
    def test_lookup_cache_and_misses(self):
        from dunectl import images
        base = tempfile.mkdtemp()
        calls = []
        png = b"\x89PNG\r\n\x1a\nfake"

        def fetch(url, timeout=15):
            calls.append(url)
            if "item_id,eq,Stilltent" in url and "/consumables?" in url:
                return json.dumps({"list": [{"image": "T_UI_Stilltent.png"}]}).encode()
            if url.startswith(images.MEDIA):
                return png
            return json.dumps({"list": []}).encode()
        imgs = images.ItemImages(base, lambda i: "consumables", fetch)
        self.assertEqual(imgs.get("Stilltent"), (png, "image/png"))
        n = len(calls)
        self.assertEqual(imgs.get("Stilltent")[1], "image/png")
        self.assertEqual(len(calls), n)  # cached on disk
        self.assertIsNone(imgs.get("Unknown_Item"))
        n = len(calls)
        self.assertIsNone(imgs.get("Unknown_Item"))
        self.assertEqual(len(calls), n)  # a certain miss is remembered
        self.assertIsNone(imgs.get("../etc/passwd"))

        def offline(url, timeout=15):
            raise OSError("no network")
        flaky = images.ItemImages(tempfile.mkdtemp(), fetch=offline)
        self.assertIsNone(flaky.get("Stilltent"))
        self.assertFalse(os.path.exists(os.path.join(flaky.dir, "Stilltent.none")))  # retried later


class VehicleAndLocationTests(unittest.TestCase):
    FLS = "DE0BCCAA2501BF22"

    def setUp(self):
        from dunectl import world
        self.base = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.base, "data", "admin"))
        with open(os.path.join(self.base, "data", "admin", "vehicles.json"), "w") as fh:
            json.dump([{"id": "Buggy", "templates": ["T3_Inventory", "T6_Combat"]}], fh)
        nl = chr(10)
        self.online = "Online"
        self.calls = []

        def run_op(argv, timeout):
            self.calls.append(argv)
            if argv[0] == "player-state":
                return 0, ("fls_id,account_id,platform_id,platform_name,online,life,last_activity,pc_actor_id,map" + nl
                           + f"{self.FLS},1,7,Steam,{self.online},Alive,x,5,HaggaBasin" + nl), ""
            if argv[0] == "map-markers":
                return 0, "id,name,online,partition,fls,x,y,z" + nl + f"1,Paul,Online,1,{self.FLS},1000,2000,300" + nl, ""
            return 0, "[admin-publish] OK " + argv[0], ""
        self.tools = world.WorldTools(self.base, SCRIPTS, run_op, lambda *a: None)

    def test_vehicle_spawns_beside_the_player(self):
        self.assertEqual(self.tools.vehicles()[0]["variants"][1]["name"], "Mk6 Combat")
        res = self.tools.spawn_vehicle(self.FLS, "Buggy", "T6_Combat", "x")
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls[-1], ["vehicle", self.FLS, "Buggy", "1600", "2000", "450", "T6_Combat"])
        with self.assertRaises(ValueError):
            self.tools.spawn_vehicle(self.FLS, "Buggy", "T9_Laser", "x")
        self.online = "Offline"
        with self.assertRaises(ValueError):
            self.tools.spawn_vehicle(self.FLS, "Buggy", "T6_Combat", "x")

    def test_locations_and_teleport(self):
        self.tools.save_location({"name": "Outpost", "from_player": self.FLS}, "x")
        self.tools.save_location({"name": "Deep spot", "map": "DeepDesert", "x": 1, "y": 2, "z": 3}, "x")
        self.assertEqual({l["name"] for l in self.tools.locations()}, {"Outpost", "Deep spot"})
        self.assertTrue(self.tools.teleport_to(self.FLS, "outpost", "x")["ok"])
        self.assertEqual(self.calls[-1], ["tpsafe", self.FLS, "1000", "2000", "300"])
        with self.assertRaises(ValueError):  # TeleportTo has no map: refuse a location on another map
            self.tools.teleport_to(self.FLS, "Deep spot", "x")
        self.tools.delete_location("Outpost", "x")
        self.assertEqual([l["name"] for l in self.tools.locations()], ["Deep spot"])


class CatalogTests(PlayerToolsTests):
    def test_browse(self):
        r = self.tools.browse("", "consumables")
        self.assertEqual([i["id"] for i in r["items"]], ["WaterPack_Consumable"])
        self.assertEqual(dict(r["categories"])["gear"], 1)
        self.assertEqual(self.tools.browse("tent", "")["total"], 1)


class VehiclePictureTests(unittest.TestCase):
    def test_vehicle_uses_its_own_picture(self):
        from dunectl import images
        urls = []

        def fetch(url, timeout=15):
            urls.append(url)
            return b"\x89PNG-vehicle"
        imgs = images.ItemImages(tempfile.mkdtemp(), fetch=fetch)
        self.assertEqual(imgs.get("Vehicle_Buggy")[1], "image/png")
        self.assertEqual(urls, [images.wiki_file_url("T_UI_IconVehCHBuggyR_D.png")])


class ConfigFormTests(unittest.TestCase):
    def setUp(self):
        from dunectl import configform
        self.cf = configform
        with open(config.TEMPLATE, encoding="utf-8") as fh:
            self.text = fh.read()

    def test_describe_template(self):
        d = self.cf.describe(self.text, SCRIPTS, {"DEEP_DESERT_COPIES": "2"})
        secs = {s["id"]: {f["key"]: f for f in s["fields"]} for s in d["sections"]}
        dd = secs["instances"]["deep_desert_dimensions"]
        self.assertEqual((dd["type"], dd["min"], dd["max"], dd["startup_var"], dd["startup_value"]),
                         ("int", 0, 12, "DEEP_DESERT_COPIES", "2"))
        self.assertIn("RAM", dd["help"])  # help comes from the template's comments
        self.assertEqual(secs["advanced"]["director_log_level"]["type"], "choice")
        self.assertEqual(secs["instances"]["auto_stop"]["type"], "duration")
        self.assertEqual(secs["instances"]["always_warm"]["type"], "list")
        ids = {s["id"]: s for s in d["gameplay"]}
        self.assertIn("mining_output", ids)
        self.assertNotIn("server_password", ids)  # owned by the Startup tab
        self.assertEqual(ids["drop_equipment_on_death"]["type"], "choice")
        self.assertEqual(d["problems"], [])

    def test_edit_in_place_keeps_comments_and_validates(self):
        changes = self.cf.coerce({"instances": {"deep_desert_dimensions": 2, "always_warm": ["Survival_1", "Overmap"]},
                                  "gameplay": {"mining_output": 2, "sandworms_enabled": False},
                                  "updates": {"warn_minutes": 5}}, SCRIPTS)
        new = self.cf.set_values(self.text, changes)
        cfg, problems = config.parse_text(new, SCRIPTS)
        self.assertEqual(problems, [])
        self.assertEqual(cfg["instances"]["deep_desert_dimensions"], 2)
        self.assertEqual(cfg["instances"]["always_warm"], ["Survival_1", "Overmap"])
        self.assertEqual(cfg["gameplay"]["mining_output"], 2.0)
        self.assertIn("mining_output = 2.0", new.splitlines())
        # every comment of the template survives
        old_comments = [l for l in self.text.splitlines() if l.strip().startswith("#")]
        self.assertEqual(old_comments, [l for l in new.splitlines() if l.strip().startswith("#")])
        self.assertIn("mq_game_offset = 30     # RabbitMQ game broker", new)
        new2 = self.cf.set_values(new, {"ports": {"mq_game_offset": 33}, "gameplay": {"mining_output": None}})
        self.assertIn("mq_game_offset = 33     # RabbitMQ game broker", new2)  # trailing comment kept
        self.assertNotIn("mining_output = 2.0", new2.splitlines())  # the active line, not the template's example
        self.assertEqual(config.parse_text(new2, SCRIPTS)[1], [])
        with self.assertRaises(ValueError):
            self.cf.set_values(self.text, {"gameplay": {"bad key = 1\n[x": 1}})

    def test_multiline_array_and_missing_section(self):
        text = "[instances]\nalways_warm = [\n  \"Survival_1\",\n  \"Overmap\",\n]\nmax_concurrent = 8\n".replace("\n", chr(10))
        new = self.cf.set_values(text, {"instances": {"always_warm": ["Survival_1"]}, "backups": {"keep": 3}})
        self.assertEqual(config.parse_text(new, SCRIPTS)[0]["instances"]["always_warm"], ["Survival_1"])
        self.assertEqual(config.parse_text(new, SCRIPTS)[0]["backups"]["keep"], 3)
        self.assertIn("max_concurrent = 8", new)
