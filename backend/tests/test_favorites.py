"""Coverage for POST/DELETE/GET /favorites (app/routes/favorites.py).

Unlike stocks.py, every endpoint here requires auth — no public/optional
path exists (see the router's own module docstring for why). Follows
test_portfolio_auth.py's token convention (AUTH_DISABLED lets distinct
bearer tokens simulate distinct users), with its own tokens so this
file's DB rows never collide with that file's.

GET /favorites enriches with a real stock_data.quote() call (reusing
step 1's stock search/detail feature) — same "real network call, loose
structural assertions" approach as test_stocks.py, since no mocking
layer exists elsewhere in this suite to hook into.
"""

USER_A_TOKEN = "favorites-test-user-a"
USER_B_TOKEN = "favorites-test-user-b"

KNOWN_TICKER = "AAPL"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _cleanup(client, token: str, ticker: str) -> None:
    client.delete(f"/favorites/{ticker}", headers=_auth(token))


def test_add_favorite(client):
    _cleanup(client, USER_A_TOKEN, KNOWN_TICKER)
    resp = client.post(f"/favorites/{KNOWN_TICKER}", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 201
    body = resp.json()
    assert body["ticker"] == KNOWN_TICKER
    assert "created_at" in body
    _cleanup(client, USER_A_TOKEN, KNOWN_TICKER)


def test_add_duplicate_favorite_is_409_not_a_new_row(client):
    _cleanup(client, USER_A_TOKEN, KNOWN_TICKER)
    first = client.post(f"/favorites/{KNOWN_TICKER}", headers=_auth(USER_A_TOKEN))
    assert first.status_code == 201

    second = client.post(f"/favorites/{KNOWN_TICKER}", headers=_auth(USER_A_TOKEN))
    assert second.status_code == 409

    listed = client.get("/favorites", headers=_auth(USER_A_TOKEN)).json()
    matching = [f for f in listed if f["ticker"] == KNOWN_TICKER]
    assert len(matching) == 1
    _cleanup(client, USER_A_TOKEN, KNOWN_TICKER)


def test_remove_favorite(client):
    client.post(f"/favorites/{KNOWN_TICKER}", headers=_auth(USER_A_TOKEN))
    resp = client.delete(f"/favorites/{KNOWN_TICKER}", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 204

    listed = client.get("/favorites", headers=_auth(USER_A_TOKEN)).json()
    assert all(f["ticker"] != KNOWN_TICKER for f in listed)


def test_remove_favorite_not_favorited_is_404(client):
    _cleanup(client, USER_A_TOKEN, "MSFT")
    resp = client.delete("/favorites/MSFT", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 404


def test_list_favorites_with_data_includes_quote_fields(client):
    _cleanup(client, USER_A_TOKEN, KNOWN_TICKER)
    client.post(f"/favorites/{KNOWN_TICKER}", headers=_auth(USER_A_TOKEN))

    resp = client.get("/favorites", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 200
    body = resp.json()
    matching = [f for f in body if f["ticker"] == KNOWN_TICKER]
    assert len(matching) == 1
    fav = matching[0]
    assert "favorited_at" in fav
    assert isinstance(fav["price"], (int, float)) and fav["price"] > 0
    assert isinstance(fav["company_name"], str) and fav["company_name"]
    _cleanup(client, USER_A_TOKEN, KNOWN_TICKER)


def test_list_favorites_empty_for_user_with_none(client):
    resp = client.get("/favorites", headers=_auth(USER_B_TOKEN))
    assert resp.status_code == 200
    assert resp.json() == []


def test_favorites_are_isolated_per_user(client):
    _cleanup(client, USER_A_TOKEN, "GOOGL")
    _cleanup(client, USER_B_TOKEN, "GOOGL")
    client.post("/favorites/GOOGL", headers=_auth(USER_A_TOKEN))

    a_list = client.get("/favorites", headers=_auth(USER_A_TOKEN)).json()
    b_list = client.get("/favorites", headers=_auth(USER_B_TOKEN)).json()
    assert any(f["ticker"] == "GOOGL" for f in a_list)
    assert all(f["ticker"] != "GOOGL" for f in b_list)
    _cleanup(client, USER_A_TOKEN, "GOOGL")


def test_post_favorite_with_no_token_is_401(client):
    resp = client.post(f"/favorites/{KNOWN_TICKER}")
    assert resp.status_code == 401


def test_delete_favorite_with_no_token_is_401(client):
    resp = client.delete(f"/favorites/{KNOWN_TICKER}")
    assert resp.status_code == 401


def test_get_favorites_with_no_token_is_401(client):
    resp = client.get("/favorites")
    assert resp.status_code == 401


def test_post_favorite_with_malformed_header_is_401(client):
    resp = client.post(
        f"/favorites/{KNOWN_TICKER}", headers={"Authorization": "not-a-bearer-token"}
    )
    assert resp.status_code == 401


def test_delete_favorite_with_malformed_header_is_401(client):
    resp = client.delete(
        f"/favorites/{KNOWN_TICKER}", headers={"Authorization": "not-a-bearer-token"}
    )
    assert resp.status_code == 401


def test_get_favorites_with_malformed_header_is_401(client):
    resp = client.get("/favorites", headers={"Authorization": "not-a-bearer-token"})
    assert resp.status_code == 401
