"""Auth-scoping tests for GET /portfolios and the three per-portfolio
read endpoints (summary/performance/trades) in app/routes/portfolios.py.

Agent portfolios (owner_type="agent") must stay publicly readable; user
portfolios must require the owning user's token — 401 if missing/invalid,
403 if valid but for a different user, 200 for the actual owner.
"""

import pytest

USER_A_TOKEN = "test-user-a"
USER_B_TOKEN = "test-user-b"

ENDPOINTS = ["summary", "performance", "trades"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _own_portfolio_id(client, token: str) -> int:
    resp = client.get("/portfolios", headers=_auth(token))
    assert resp.status_code == 200
    mine = [p for p in resp.json() if p["owner_type"] == "user"]
    assert len(mine) == 1
    return mine[0]["id"]


def _an_agent_portfolio_id(client) -> int:
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    agents = [p for p in resp.json() if p["owner_type"] == "agent"]
    assert len(agents) == 3
    return agents[0]["id"]


def test_list_portfolios_unauthenticated_returns_only_agent_portfolios(client):
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert all(p["owner_type"] == "agent" for p in body)


def test_list_portfolios_authenticated_includes_own_plus_agent(client):
    resp = client.get("/portfolios", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 200
    owner_types = [p["owner_type"] for p in resp.json()]
    assert owner_types.count("user") == 1
    assert owner_types.count("agent") == 3


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_agent_portfolio_readable_with_no_token(client, endpoint):
    portfolio_id = _an_agent_portfolio_id(client)
    resp = client.get(f"/portfolios/{portfolio_id}/{endpoint}")
    assert resp.status_code == 200


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_user_portfolio_with_no_token_is_401(client, endpoint):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    resp = client.get(f"/portfolios/{portfolio_id}/{endpoint}")
    assert resp.status_code == 401


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_user_portfolio_with_malformed_header_is_401(client, endpoint):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    resp = client.get(
        f"/portfolios/{portfolio_id}/{endpoint}",
        headers={"Authorization": "not-a-bearer-token"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_user_portfolio_with_other_users_token_is_403(client, endpoint):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    resp = client.get(f"/portfolios/{portfolio_id}/{endpoint}", headers=_auth(USER_B_TOKEN))
    assert resp.status_code == 403


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_user_portfolio_with_owning_users_token_is_200(client, endpoint):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    resp = client.get(f"/portfolios/{portfolio_id}/{endpoint}", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 200


def test_unknown_portfolio_id_is_404_regardless_of_auth(client):
    resp = client.get("/portfolios/999999/summary")
    assert resp.status_code == 404
