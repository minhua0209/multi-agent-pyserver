from app.core.models import Agent, AgentTool, ToolCall, utc_now
from app.services.tool_executor import ToolExecutor


def test_tool_executor_runs_mock_tool() -> None:
    agent = Agent(
        id="agent_crm",
        name="CRM Agent",
        description="Uses CRM tools",
        capabilities=["crm"],
        tools=[
            AgentTool(
                name="crm_query",
                description="Query CRM",
                type="mock",
                config={"response": '{"customer_name": "Customer A", "level": "vip"}'},
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"}),
    )

    assert result.success is True
    assert result.tool_name == "crm_query"
    assert result.result == '{"customer_name": "Customer A", "level": "vip"}'


def test_tool_executor_rejects_unregistered_tool() -> None:
    agent = Agent(
        id="agent_crm",
        name="CRM Agent",
        description="Uses CRM tools",
        capabilities=["crm"],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="crm_query", arguments={"customer_id": "customer_a"}),
    )

    assert result.success is False
    assert "not registered" in result.error


def test_tool_executor_runs_mysql_tool(monkeypatch) -> None:
    class FakeCursor:
        description = [("customer_name",), ("level",)]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query):
            assert query == "select customer_name, level from customers where id = 'customer_a'"

        def fetchmany(self, size):
            assert size == 50
            return [("Customer A", "vip")]

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    def _connect(**kwargs):
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["user"] == "demo_user"
        assert kwargs["database"] == "demo_db"
        return FakeConnection()

    monkeypatch.setattr("app.services.tool_executor.pymysql.connect", _connect)
    agent = Agent(
        id="agent_mysql",
        name="MySQL Agent",
        description="Uses MySQL tools",
        capabilities=["mysql"],
        tools=[
            AgentTool(
                name="customer_query",
                description="Query customer from MySQL",
                type="mysql",
                config={
                    "host": "127.0.0.1",
                    "port": "3306",
                    "user": "demo_user",
                    "password": "demo_pass_123",
                    "database": "demo_db",
                    "query": "select customer_name, level from customers where id = '{customer_id}'",
                },
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="customer_query", arguments={"customer_id": "customer_a"}),
    )

    assert result.success is True
    assert result.result == '[{"customer_name": "Customer A", "level": "vip"}]'


def test_tool_executor_runs_smtp_email_tool(monkeypatch) -> None:
    sent_messages = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert host == "smtp.example.com"
            assert port == 587
            assert timeout == 30

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            assert username == "sender@example.com"
            assert password == "secret"

        def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr("app.services.tool_executor.smtplib.SMTP", FakeSMTP)
    agent = Agent(
        id="agent_email",
        name="Email Agent",
        description="Sends email",
        capabilities=["email"],
        tools=[
            AgentTool(
                name="send_email",
                description="Send an email",
                type="smtp_email",
                config={
                    "smtp_host": "smtp.example.com",
                    "smtp_port": "587",
                    "username": "sender@example.com",
                    "password": "secret",
                    "from": "sender@example.com",
                    "use_tls": "true",
                    "timeout_seconds": "30",
                },
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(
            tool_name="send_email",
            arguments={
                "to": "minh@getui.com",
                "subject": "Agent test email",
                "body": "This is a test email from the task agent.",
            },
        ),
    )

    assert result.success is True
    assert result.result == "Email sent to minh@getui.com"
    assert sent_messages[0]["From"] == "sender@example.com"
    assert sent_messages[0]["To"] == "minh@getui.com"
    assert sent_messages[0]["Subject"] == "Agent test email"
    assert sent_messages[0].get_content().strip() == "This is a test email from the task agent."


def test_tool_executor_rejects_smtp_email_tool_without_required_fields() -> None:
    agent = Agent(
        id="agent_email",
        name="Email Agent",
        description="Sends email",
        capabilities=["email"],
        tools=[
            AgentTool(
                name="send_email",
                type="smtp_email",
                config={"smtp_host": "smtp.example.com", "from": "sender@example.com"},
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="send_email", arguments={"to": "minh@getui.com"}),
    )

    assert result.success is False
    assert "subject" in result.error


def test_tool_executor_runs_file_write_tool(tmp_path) -> None:
    agent = Agent(
        id="agent_file",
        name="File Writer Agent",
        description="Writes reports to local files",
        capabilities=["write_report", "save_file"],
        tools=[
            AgentTool(
                name="file_write",
                description="Write content to a local file",
                type="file_write",
                config={"base_dir": str(tmp_path)},
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(
            tool_name="file_write",
            arguments={"filename": "reports/summary.md", "content": "hello report"},
        ),
    )

    assert result.success is True
    assert (tmp_path / "reports" / "summary.md").read_text() == "hello report"
    assert "summary.md" in result.result


def test_tool_executor_rejects_file_write_path_traversal(tmp_path) -> None:
    agent = Agent(
        id="agent_file",
        name="File Writer Agent",
        description="Writes reports to local files",
        capabilities=["write_report", "save_file"],
        tools=[
            AgentTool(
                name="file_write",
                type="file_write",
                config={"base_dir": str(tmp_path)},
            )
        ],
        created_at=utc_now(),
    )

    result = ToolExecutor().execute(
        agent,
        ToolCall(tool_name="file_write", arguments={"filename": "../escape.md", "content": "bad"}),
    )

    assert result.success is False
    assert "base_dir" in result.error
