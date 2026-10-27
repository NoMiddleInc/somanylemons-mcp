import re
import unittest

from somanylemons_mcp.task_bridge import task_schemas

# The Claude directory requires tool descriptions with no instructions about model behavior or
# other tools. Descriptions say what a tool does and returns; guidance lives in the server instructions.
IMPERATIVE = {
    "call", "use", "show", "tell", "say", "ask", "offer", "present", "give", "follow", "pass",
    "report", "write", "read", "select", "choose", "copy", "reuse", "leave", "do", "never",
    "always", "keep", "only", "start", "preserve", "return", "submit", "set", "include",
}
ADDRESSING = re.compile(r"\b(you|your|the assistant|the model|tell the user|say so|ask the user|first choice)\b", re.I)


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.;:])\s+", " ".join(text.split())) if part.strip()]


class ToolDescriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_descriptions_state_what_tools_do_without_instructing_the_model(self):
        problems = []
        for tool in await task_schemas():
            texts = [("description", tool.description or "")]
            texts += [(f"param {name}", spec.get("description", "")) for name, spec in (tool.inputSchema.get("properties") or {}).items()]
            for where, text in texts:
                if ADDRESSING.search(text):
                    problems.append(f"{tool.name} {where}: {ADDRESSING.search(text).group(0)!r}")
                for sentence in sentences(text):
                    first = re.sub(r"[^a-z]", "", sentence.split()[0].lower())
                    if first in IMPERATIVE:
                        problems.append(f"{tool.name} {where}: {sentence[:80]!r}")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
