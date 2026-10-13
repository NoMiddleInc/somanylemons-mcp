"""People cards: an MCP App that shows a search's people with their headshots in the chat.

The cards add faces to the result; the assistant still writes the contact table, so nothing
is lost where a host does not show the widget. Photos load from ProducerSpark's own address.
"""
from importlib.resources import files

PEOPLE_URI = "ui://producerspark/people.html"
PEOPLE_MIME = "text/html;profile=mcp-app"
PEOPLE_META = {"ui": {
    "csp": {"resourceDomains": ["https://api.producerspark.com"]},
    "prefersBorder": True,
}}
# Older hosts read the flat key.
PEOPLE_UI = {"ui": {"resourceUri": PEOPLE_URI}, "ui/resourceUri": PEOPLE_URI}


def people_html() -> str:
    return files("somanylemons_mcp").joinpath("task_tools/people.html").read_text()


def register(server) -> None:
    @server.resource(PEOPLE_URI, name="people", mime_type=PEOPLE_MIME, meta=PEOPLE_META)
    def people() -> str:
        return people_html()
