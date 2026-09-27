import unittest

from jarvis.distributed.agents import AgentInstance, AgentProvider, AgentRegistry, CliProvider, ExecutionRequest, ExecutionResult


class FakeProvider(AgentProvider):
    provider_id = "fake"

    def __init__(self, ok=True):
        self.ok = ok

    def discover(self):
        return [AgentInstance("fake@local", self.provider_id, "Fake", "test", capabilities={"reasoning", "coding"}, available=True, functional=True, status="READY", health="healthy", reason="test double")]

    def execute(self, instance, request):
        if not self.ok:
            return ExecutionResult(False, self.provider_id, instance.instance_id, error="intentional failure")
        return ExecutionResult(True, self.provider_id, instance.instance_id, content="real contract result")


class DistributedAgentTests(unittest.TestCase):
    def test_registry_matches_capabilities_and_explains_route(self):
        registry = AgentRegistry(None, [FakeProvider()])
        registry.discover()
        result = registry.execute(ExecutionRequest("t1", "prompt", required_capabilities=["coding"]))
        self.assertTrue(result.ok)
        self.assertEqual("fake@local", result.agent_instance_id)
        self.assertIn("required capabilities", registry.last_routing[-1]["reason"])

    def test_provider_failure_is_reported_without_fake_success(self):
        registry = AgentRegistry(None, [FakeProvider(False)])
        registry.discover()
        with self.assertRaisesRegex(RuntimeError, "agent_execution_failed"):
            registry.execute(ExecutionRequest("t1", "prompt", required_capabilities=["reasoning"]))

    def test_cli_discovery_is_real_and_does_not_expose_secrets(self):
        def which(name):
            return "/safe/{}".format(name)

        def runner(argv, **kwargs):
            self.assertFalse(kwargs.get("shell", False))
            if argv[-1] == "--version":
                return type("Completed", (), {"returncode": 0, "stdout": "codex 1.2\n", "stderr": ""})()
            return type("Completed", (), {"returncode": 0, "stdout": "VELKO_READY\n", "stderr": ""})()

        instance = CliProvider("codex", "codex", "Codex", which, runner).discover()[0]
        self.assertTrue(instance.available)
        self.assertEqual("codex 1.2", instance.metadata["version"])
        self.assertEqual("READY", instance.status)

    def test_cli_execution_uses_fixed_argv_without_shell(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            output = "codex 1.2" if argv[-1] == "--version" else ("VELKO_READY" if "VELKO_READY" in argv[-1] else "answer")
            return type("Completed", (), {"returncode": 0, "stdout": output, "stderr": ""})()

        provider = CliProvider("codex", "codex", "Codex", lambda name: "/safe/codex", runner)
        instance = provider.discover()[0]
        result = provider.execute(instance, ExecutionRequest("t", "inspect", workspace="/tmp"))
        self.assertTrue(result.ok)
        self.assertEqual(["/safe/codex", "exec", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "-c", "model_reasoning_effort=none", "inspect"], calls[-1][0])
        self.assertNotIn("shell", calls[-1][1])

    def test_forced_provider_rejects_other_agents(self):
        registry = AgentRegistry(None, [FakeProvider()])
        registry.discover()
        with self.assertRaisesRegex(RuntimeError, "no_available_agent"):
            registry.route(["coding"], forced_provider="ollama")


if __name__ == "__main__":
    unittest.main()
