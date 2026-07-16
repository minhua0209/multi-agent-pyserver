from sqlalchemy import create_engine, text

from app.core.models import Agent
from app.services.storage import agents_table, metadata
from scripts.seed_lifecycle_agents import LIFECYCLE_AGENT_SEEDS, seed_lifecycle_agents


def test_lifecycle_agent_seeds_cover_delivery_and_operations_stages() -> None:
    stages = {seed["metadata"]["stage"] for seed in LIFECYCLE_AGENT_SEEDS}
    assert stages == {"需求", "设计", "研发", "测试", "上线", "运维"}
    assert len(LIFECYCLE_AGENT_SEEDS) >= 18
    assert all(seed["agent_type"] == "processing" for seed in LIFECYCLE_AGENT_SEEDS)


def test_seed_lifecycle_agents_replaces_existing_database_agents(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'taskhub.db'}"
    engine = create_engine(database_url, future=True)
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            agents_table.insert().values(
                id="old_agent",
                payload='{"id":"old_agent"}',
                name="旧节点",
                description="旧节点",
                agent_type="processing",
                status="active",
            )
        )

    created_agents = seed_lifecycle_agents(database_url)

    with engine.begin() as connection:
        rows = connection.execute(text("select id, payload, name, agent_type from agents order by id")).mappings().all()

    expected_ids = sorted(seed["id"] for seed in LIFECYCLE_AGENT_SEEDS)
    assert [row["id"] for row in rows] == expected_ids
    assert "old_agent" not in expected_ids
    assert len(created_agents) == len(LIFECYCLE_AGENT_SEEDS)
    assert {row["agent_type"] for row in rows} == {"processing"}
    assert {Agent.model_validate_json(row["payload"]).id for row in rows} == set(expected_ids)
